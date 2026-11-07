"""
Master data: Units, Raw Materials, Prices, Products, Variants, Recipes (BOM).
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.factories.models import Factory

QTY = dict(max_digits=18, decimal_places=4)  # quantities: 4 decimals (D-11)
MONEY = dict(max_digits=16, decimal_places=4)  # money stored precisely, shown at 2


class Dimension(models.TextChoices):
    MASS = "MASS", "Weight"
    VOLUME = "VOLUME", "Volume"
    COUNT = "COUNT", "Count"


class Unit(models.Model):
    """
    KG/GM, L/ML, PCS. Conversion only within the same dimension (D-04):
    no KG <-> Litre (no density in v1).
    """
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=50)
    dimension = models.CharField(max_length=10, choices=Dimension.choices)
    factor_to_base = models.DecimalField(
        max_digits=18, decimal_places=6,
        help_text="How many base units in 1 of this unit. KG=1, GM=0.001, L=1, ML=0.001, PCS=1",
    )

    class Meta:
        ordering = ["dimension", "code"]
        verbose_name = "Unit (KG, GM, L, PCS)"
        verbose_name_plural = "Units (KG, GM, L, PCS)"
        constraints = [models.CheckConstraint(condition=Q(factor_to_base__gt=0), name="unit_factor_gt_0")]

    def __str__(self):
        return self.code

    @property
    def is_count(self) -> bool:
        return self.dimension == Dimension.COUNT

    def convert(self, qty: Decimal, to_unit: "Unit") -> Decimal:
        if self.dimension != to_unit.dimension:
            raise ValidationError(f"{self.code} থেকে {to_unit.code}-এ convert করা যায় না।")
        return Decimal(qty) * self.factor_to_base / to_unit.factor_to_base


class MaterialCategory(models.Model):
    """Groups raw materials on screens and reports, e.g. শুকনো উপকরণ (Dry), জ্বালানি (Fuel)."""
    factory = models.ForeignKey(Factory, on_delete=models.PROTECT, related_name="material_categories")
    name = models.CharField(max_length=80)
    sort_order = models.PositiveIntegerField(default=10, help_text="ছোট সংখ্যা আগে দেখাবে (Smaller shows first)")

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Raw material category (ক্যাটাগরি)"
        verbose_name_plural = "Raw material categories (ক্যাটাগরি)"
        constraints = [models.UniqueConstraint(fields=["factory", "name"], name="uniq_category_name")]

    def __str__(self):
        return self.name


class RawMaterial(models.Model):
    factory = models.ForeignKey(Factory, on_delete=models.PROTECT, related_name="raw_materials")
    category = models.ForeignKey(MaterialCategory, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="materials")
    name = models.CharField(max_length=120)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT,
                             help_text="Stock/price unit. Price is per 1 of this unit.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Raw material ও দাম"
        verbose_name_plural = "Raw materials ও দাম"
        constraints = [models.UniqueConstraint(fields=["factory", "name"], name="uniq_material_name")]

    def __str__(self):
        return f"{self.name} ({self.unit.code})"

    def clean(self):
        if self.category_id and self.factory_id and self.category.factory_id != self.factory_id:
            raise ValidationError({"category": "এই ক্যাটাগরি অন্য কারখানার (Category belongs to another factory)।"})

    def price_on(self, day):
        """Applicable price = latest price whose effective date <= day."""
        p = self.prices.filter(effective_from__lte=day).order_by("-effective_from").first()
        return p.price if p else None


class MaterialPrice(models.Model):
    """Price history. Never edit an old row — add a new effective date (BR-08)."""
    material = models.ForeignKey(RawMaterial, on_delete=models.CASCADE, related_name="prices")
    price = models.DecimalField(**MONEY, help_text="Taka per 1 material unit")
    effective_from = models.DateField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["material", "-effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["material", "effective_from"], name="uniq_price_date"),
            models.CheckConstraint(condition=Q(price__gte=0), name="price_gte_0"),
        ]

    def __str__(self):
        return f"{self.material.name}: {self.price} from {self.effective_from}"


class Product(models.Model):
    factory = models.ForeignKey(Factory, on_delete=models.PROTECT, related_name="products")
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Product ও Variant"
        verbose_name_plural = "Products ও Variants"
        constraints = [models.UniqueConstraint(fields=["factory", "name"], name="uniq_product_name")]

    def __str__(self):
        return self.name


class ProductVariant(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="variants")
    name = models.CharField(max_length=60, help_text='e.g. "300 gm"')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["product__name", "name"]
        constraints = [models.UniqueConstraint(fields=["product", "name"], name="uniq_variant_name")]

    def __str__(self):
        return f"{self.product.name} – {self.name}"

    @property
    def factory(self):
        return self.product.factory

    def active_recipe(self, day):
        """Latest active recipe effective on `day` (BR-03)."""
        return (
            self.recipes.filter(is_active=True, effective_from__lte=day)
            .order_by("-effective_from", "-version")
            .first()
        )


class Recipe(models.Model):
    """
    BOM for one variant: materials needed for `batch_yield` pieces, plus
    standard Labour/Electricity/Other cost per batch (D-05).
    LPG and Packaging go in as RecipeLines, not here (D-01).
    """
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="recipes")
    version = models.PositiveIntegerField()
    batch_yield = models.DecimalField(**QTY, help_text="Pieces produced by one batch")
    effective_from = models.DateField()
    is_active = models.BooleanField(default=True)
    labour_per_batch = models.DecimalField(**MONEY, default=0)
    electricity_per_batch = models.DecimalField(**MONEY, default=0)
    other_per_batch = models.DecimalField(**MONEY, default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["variant", "-version"]
        verbose_name = "Recipe / BOM"
        verbose_name_plural = "Recipes / BOM"
        constraints = [
            models.UniqueConstraint(fields=["variant", "version"], name="uniq_recipe_version"),
            models.CheckConstraint(condition=Q(batch_yield__gt=0), name="recipe_yield_gt_0"),
            models.CheckConstraint(
                condition=Q(labour_per_batch__gte=0, electricity_per_batch__gte=0, other_per_batch__gte=0),
                name="recipe_std_cost_gte_0",
            ),
        ]

    def __str__(self):
        return f"{self.variant} v{self.version}"


class RecipeLine(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="lines")
    material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT, related_name="recipe_lines")
    quantity = models.DecimalField(**QTY, help_text="Per batch, in the unit chosen here")
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT,
                             help_text="Must be same kind as the material's unit (KG/GM, L/ML, PCS)")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["recipe", "material"], name="uniq_recipe_material"),
            models.CheckConstraint(condition=Q(quantity__gte=0), name="recipe_qty_gte_0"),
        ]

    def __str__(self):
        return f"{self.material.name}: {self.quantity} {self.unit.code}"

    def clean(self):
        if self.material_id and self.unit_id:
            if self.unit.dimension != self.material.unit.dimension:
                raise ValidationError({
                    "unit": f"{self.material.name}-এর unit {self.material.unit.code}; "
                            f"{self.unit.code} ব্যবহার করা যাবে না (KG↔Litre conversion নেই)।"
                })
        if self.material_id and self.recipe_id:
            if self.material.factory_id != self.recipe.variant.product.factory_id:
                raise ValidationError({"material": "এই material অন্য factory-র।"})

    def quantity_in_material_unit(self) -> Decimal:
        return self.unit.convert(self.quantity, self.material.unit)
