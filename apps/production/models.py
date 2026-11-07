"""
Production plans, their locked recipe/price snapshot, actuals, and audit log.

Status flow (D-09): DRAFT -> IN_PRODUCTION -> COMPLETED
- DRAFT: everything editable; calculation uses current recipe + price.
- IN_PRODUCTION: recipe + price snapshot locked (D-06); plan quantities locked;
  actual consumption / produced / rejected still editable.
- COMPLETED: no normal edits; only an audited superuser correction (BR-11).

All state changes go through apps/production/services.py.
"""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.catalog.models import MONEY, QTY, ProductVariant, RawMaterial, Recipe
from apps.factories.models import Factory


class Status(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    IN_PRODUCTION = "IN_PRODUCTION", "In Production"
    COMPLETED = "COMPLETED", "Completed"


class ProductionPlan(models.Model):
    # Created on the phone, even offline. Re-sending the same UUID never
    # creates a duplicate (BR-13, D-07).
    local_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    factory = models.ForeignKey(Factory, on_delete=models.PROTECT, related_name="plans")
    production_date = models.DateField()
    # PRD-YYYYMMDD-NNN, given by the server only, sequence per factory+date (D-07).
    plan_no = models.CharField(max_length=30, blank=True, null=True, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    locked_at = models.DateTimeField(null=True, blank=True, editable=False)
    locked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="+", editable=False)
    completed_at = models.DateTimeField(null=True, blank=True, editable=False)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="+", editable=False)

    # Actual standard costs for the whole plan; split per line by the engine (D-05).
    actual_labour = models.DecimalField(**MONEY, null=True, blank=True)
    actual_electricity = models.DecimalField(**MONEY, null=True, blank=True)
    actual_other = models.DecimalField(**MONEY, null=True, blank=True)

    class Meta:
        ordering = ["-production_date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["factory", "plan_no"], name="uniq_plan_no_per_factory",
                                    condition=Q(plan_no__isnull=False)),
        ]

    def __str__(self):
        return self.plan_no or f"Draft {str(self.local_uuid)[:8]}"


class ProductionLine(models.Model):
    plan = models.ForeignKey(ProductionPlan, on_delete=models.CASCADE, related_name="lines")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT)
    planned_qty = models.DecimalField(**QTY)

    # Actual output (D-03), entered while IN_PRODUCTION.
    produced_qty = models.DecimalField(**QTY, null=True, blank=True)
    rejected_qty = models.DecimalField(**QTY, null=True, blank=True)

    # ---- snapshot, filled when the plan is locked (D-06, BR-14) ----
    recipe = models.ForeignKey(Recipe, null=True, blank=True, on_delete=models.PROTECT, editable=False)
    recipe_version = models.PositiveIntegerField(null=True, blank=True, editable=False)
    batch_yield = models.DecimalField(**QTY, null=True, blank=True, editable=False)
    labour_per_batch = models.DecimalField(**MONEY, null=True, blank=True, editable=False)
    electricity_per_batch = models.DecimalField(**MONEY, null=True, blank=True, editable=False)
    other_per_batch = models.DecimalField(**MONEY, null=True, blank=True, editable=False)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "variant"], name="uniq_plan_variant"),
            models.CheckConstraint(condition=Q(planned_qty__gt=0), name="planned_qty_gt_0"),
            models.CheckConstraint(condition=Q(produced_qty__isnull=True) | Q(produced_qty__gte=0),
                                   name="produced_gte_0"),
            models.CheckConstraint(condition=Q(rejected_qty__isnull=True) | Q(rejected_qty__gte=0),
                                   name="rejected_gte_0"),
        ]

    def __str__(self):
        return f"{self.variant} × {self.planned_qty}"

    def clean(self):
        for f in ("planned_qty", "produced_qty", "rejected_qty"):
            v = getattr(self, f)
            if v is not None and v != v.to_integral_value():
                raise ValidationError({f: "পিসের সংখ্যা পূর্ণসংখ্যা হতে হবে।"})

    @property
    def good_qty(self):
        if self.produced_qty is None or self.rejected_qty is None:
            return None
        return self.produced_qty - self.rejected_qty


class LineRecipeItemSnapshot(models.Model):
    """Recipe quantity per batch, converted to the material's unit, frozen at lock."""
    line = models.ForeignKey(ProductionLine, on_delete=models.CASCADE, related_name="recipe_items")
    material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT)
    quantity_per_batch = models.DecimalField(max_digits=22, decimal_places=8)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["line", "material"], name="uniq_line_material_snap")]


class PlanPriceSnapshot(models.Model):
    """Material price frozen at lock; used for BOTH estimated and actual cost."""
    plan = models.ForeignKey(ProductionPlan, on_delete=models.CASCADE, related_name="price_snapshots")
    material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT)
    unit_code = models.CharField(max_length=10)
    unit_is_count = models.BooleanField(default=False)
    unit_price = models.DecimalField(**MONEY)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["plan", "material"], name="uniq_plan_price_snap")]


class ActualMaterial(models.Model):
    """Total actual consumption of one material for the whole plan (D-02)."""
    plan = models.ForeignKey(ProductionPlan, on_delete=models.CASCADE, related_name="actual_materials")
    material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT)
    quantity = models.DecimalField(**QTY)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["plan", "material"], name="uniq_actual_material"),
            models.CheckConstraint(condition=Q(quantity__gte=0), name="actual_qty_gte_0"),
        ]

    def clean(self):
        if self.material_id and self.quantity is not None and self.material.unit.is_count:
            if self.quantity != self.quantity.to_integral_value():
                raise ValidationError({"quantity": f"{self.material.name} গোনা হয় ({self.material.unit.code}), পূর্ণসংখ্যা লিখুন।"})


class AuditLog(models.Model):
    plan = models.ForeignKey(ProductionPlan, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="audit_logs")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=40)
    reason = models.TextField(blank=True)
    changes = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.plan}"
