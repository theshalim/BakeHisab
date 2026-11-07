from django.contrib import admin

from apps.factories.admin import FactoryScopedAdmin

from .models import MaterialCategory, MaterialPrice, Product, ProductVariant, RawMaterial, Recipe, RecipeLine, Unit


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "dimension", "factor_to_base")


class PriceInline(admin.TabularInline):
    model = MaterialPrice
    extra = 1
    fields = ("price", "effective_from", "created_by", "created_at")
    readonly_fields = ("created_by", "created_at")


@admin.register(RawMaterial)
class RawMaterialAdmin(FactoryScopedAdmin):
    list_display = ("name", "category", "unit", "factory", "current_price", "is_active")
    list_filter = ("factory", "category", "is_active")
    list_editable = ("category",)
    search_fields = ("name",)
    inlines = [PriceInline]

    @admin.display(description="Price today (Tk)")
    def current_price(self, obj):
        from django.utils import timezone
        return obj.price_on(timezone.localdate())

    def save_formset(self, request, form, formset, change):
        for obj in formset.save(commit=False):
            if isinstance(obj, MaterialPrice) and not obj.created_by_id:
                obj.created_by = request.user
            obj.save()
        formset.save_m2m()
        for obj in formset.deleted_objects:
            obj.delete()


@admin.register(MaterialCategory)
class MaterialCategoryAdmin(FactoryScopedAdmin):
    list_display = ("name", "sort_order", "factory", "material_count")
    list_editable = ("sort_order",)
    list_filter = ("factory",)

    @admin.display(description="Materials (কয়টি)")
    def material_count(self, obj):
        return obj.materials.count()


class VariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1


@admin.register(Product)
class ProductAdmin(FactoryScopedAdmin):
    list_display = ("name", "factory", "is_active")
    list_filter = ("factory", "is_active")
    inlines = [VariantInline]


class RecipeLineInline(admin.TabularInline):
    model = RecipeLine
    extra = 3


@admin.register(Recipe)
class RecipeAdmin(FactoryScopedAdmin):
    factory_path = "variant__product__factory"
    list_display = ("variant", "version", "batch_yield", "effective_from", "is_active",
                    "labour_per_batch", "electricity_per_batch", "other_per_batch")
    list_filter = ("is_active", "variant__product__factory")
    inlines = [RecipeLineInline]
    fieldsets = (
        (None, {"fields": ("variant", "version", "batch_yield", "effective_from", "is_active")}),
        ("Standard cost per batch (Labour / Electricity / Other only — LPG & Packaging go in the lines below)",
         {"fields": ("labour_per_batch", "electricity_per_batch", "other_per_batch")}),
        (None, {"fields": ("notes",)}),
    )
