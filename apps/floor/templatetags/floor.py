from decimal import Decimal

from django import template

from apps.costing import engine as E

register = template.Library()


@register.filter
def tk(value):
    """1,23,591.30 style is confusing for some; use plain 123,591.30."""
    if value is None:
        return "–"
    return f"{E.money(value):,.2f}"


@register.filter
def pct(value):
    if value is None:
        return "–"
    v = E.percent(value)
    return f"{'+' if v > 0 else ''}{v}%"


@register.filter
def signed_tk(value):
    if value is None:
        return "–"
    v = E.money(value)
    return f"{'+' if v > 0 else ''}{v:,.2f}"


@register.filter
def num(value):
    """Plain number without trailing zeros: 2000.0000 -> 2000, 4.0750 -> 4.075"""
    if value is None:
        return ""
    d = Decimal(value).normalize()
    return f"{d:f}"
