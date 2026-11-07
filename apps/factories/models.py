from django.conf import settings
from django.db import models


class Factory(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "factories"

    def __str__(self):
        return self.name


class Role(models.TextChoices):
    # Super Admin = Django superuser (is_superuser), not a membership role.
    FACTORY_ADMIN = "FACTORY_ADMIN", "Factory Admin"
    PRODUCTION_MANAGER = "PRODUCTION_MANAGER", "Production Manager"
    PRODUCTION_OPERATOR = "PRODUCTION_OPERATOR", "Production Operator"
    COST_MANAGER = "COST_MANAGER", "Accounts / Cost Manager"
    MANAGEMENT = "MANAGEMENT", "Management"


PRODUCTION_ROLES = {Role.PRODUCTION_MANAGER, Role.PRODUCTION_OPERATOR}  # D-10
WEB_ROLES = {Role.FACTORY_ADMIN, Role.COST_MANAGER, Role.MANAGEMENT}


class FactoryMembership(models.Model):
    """Which user may work in which factory, and as what (BR-12)."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="memberships")
    factory = models.ForeignKey(Factory, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=30, choices=Role.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "factory", "role"], name="uniq_membership"),
        ]

    def __str__(self):
        return f"{self.user} @ {self.factory} ({self.get_role_display()})"


def user_factory_ids(user, roles=None):
    """Factory ids the user may access. Superusers see everything (None)."""
    if user.is_superuser:
        return None
    qs = FactoryMembership.objects.filter(user=user, is_active=True, factory__is_active=True)
    if roles:
        qs = qs.filter(role__in=roles)
    return set(qs.values_list("factory_id", flat=True))


def user_has_role(user, factory, roles) -> bool:
    if user.is_superuser:
        return True
    return FactoryMembership.objects.filter(
        user=user, factory=factory, role__in=roles, is_active=True
    ).exists()
