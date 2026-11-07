from django.contrib import admin

from .models import Factory, FactoryMembership, user_factory_ids


class FactoryScopedAdmin(admin.ModelAdmin):
    """Non-superusers only see/choose their own factories' data (BR-12)."""
    factory_path = "factory"  # lookup from this model to Factory

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        ids = user_factory_ids(request.user)
        return qs if ids is None else qs.filter(**{f"{self.factory_path}__in": ids})

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        ids = user_factory_ids(request.user)
        if ids is not None:
            if db_field.name == "factory":
                kwargs["queryset"] = Factory.objects.filter(id__in=ids)
            elif hasattr(db_field.related_model, "factory"):
                kwargs["queryset"] = db_field.related_model.objects.filter(factory_id__in=ids)
            elif db_field.name == "variant":
                kwargs["queryset"] = db_field.related_model.objects.filter(product__factory_id__in=ids)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class MembershipInline(admin.TabularInline):
    model = FactoryMembership
    extra = 1


@admin.register(Factory)
class FactoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    inlines = [MembershipInline]

    def has_add_permission(self, request):
        return request.user.is_superuser  # only Super Admin creates factories

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(FactoryMembership)
class FactoryMembershipAdmin(FactoryScopedAdmin):
    list_display = ("user", "factory", "role", "is_active")
    list_filter = ("factory", "role", "is_active")
