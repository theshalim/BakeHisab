"""
SAMPLE data from the blueprint (products) + made-up recipes and prices.
NOT the factory's real recipes — replace via Admin when real numbers arrive.
Used by `manage.py seed_sample` and by the tests.
"""
from datetime import date
from decimal import Decimal as D

from apps.factories.models import Factory

from .models import Dimension, MaterialCategory, MaterialPrice, Product, ProductVariant, RawMaterial, Recipe, RecipeLine, Unit

UNITS = [
    ("KG", "Kilogram", Dimension.MASS, D("1")),
    ("GM", "Gram", Dimension.MASS, D("0.001")),
    ("L", "Litre", Dimension.VOLUME, D("1")),
    ("ML", "Millilitre", Dimension.VOLUME, D("0.001")),
    ("PCS", "Piece", Dimension.COUNT, D("1")),
]

# name, sort order
CATEGORIES = [
    ("শুকনো উপকরণ (Dry Ingredients)", 1),
    ("তরল ও ডিম (Wet Ingredients)", 2),
    ("জ্বালানি (Fuel)", 3),
    ("প্যাকেজিং (Packaging)", 4),
]

# name, unit, price (Tk per unit), category index
MATERIALS = [
    ("Flour", "KG", D("65"), 0),
    ("Sugar", "KG", D("130"), 0),
    ("Egg", "PCS", D("12"), 1),
    ("Oil", "L", D("180"), 1),
    ("Yeast", "KG", D("400"), 0),
    ("Salt", "KG", D("40"), 0),
    ("LPG", "KG", D("120"), 2),
    ("Packaging", "PCS", D("2"), 3),
]

# (product, variant): yield, (labour, electricity, other) per batch, [(material, qty, unit)]
RECIPES = {
    ("Cake", "300 gm"): (D("50"), (D("400"), D("120"), D("80")), [
        ("Flour", "6", "KG"), ("Sugar", "5", "KG"), ("Egg", "60", "PCS"), ("Oil", "2", "L"),
        ("LPG", "2", "KG"), ("Packaging", "50", "PCS"),
    ]),
    ("Bon Ruti", "50 gm"): (D("200"), (D("300"), D("80"), D("50")), [
        ("Flour", "7", "KG"), ("Sugar", "1", "KG"), ("Oil", "500", "ML"), ("Yeast", "150", "GM"),
        ("Salt", "100", "GM"), ("LPG", "1.5", "KG"), ("Packaging", "200", "PCS"),
    ]),
    ("Bon Ruti", "120 gm"): (D("100"), (D("350"), D("90"), D("60")), [
        ("Flour", "8", "KG"), ("Sugar", "1.2", "KG"), ("Oil", "0.6", "L"), ("Yeast", "0.2", "KG"),
        ("Salt", "0.12", "KG"), ("LPG", "1.8", "KG"), ("Packaging", "100", "PCS"),
    ]),
    ("Bread", "400 gm"): (D("100"), (D("500"), D("150"), D("100")), [
        ("Flour", "25", "KG"), ("Sugar", "2", "KG"), ("Oil", "1", "L"), ("Yeast", "0.5", "KG"),
        ("Salt", "0.3", "KG"), ("LPG", "3", "KG"), ("Packaging", "100", "PCS"),
    ]),
}

PRICE_DATE = date(2026, 1, 1)


def load_sample(factory_code="FA", factory_name="Factory A"):
    units = {}
    for code, name, dim, factor in UNITS:
        units[code], _ = Unit.objects.get_or_create(
            code=code, defaults={"name": name, "dimension": dim, "factor_to_base": factor})
    factory, _ = Factory.objects.get_or_create(code=factory_code, defaults={"name": factory_name})
    cats = []
    for cname, order in CATEGORIES:
        c, _ = MaterialCategory.objects.get_or_create(factory=factory, name=cname,
                                                      defaults={"sort_order": order})
        cats.append(c)
    mats = {}
    for name, unit, price, ci in MATERIALS:
        m, _ = RawMaterial.objects.get_or_create(factory=factory, name=name,
                                                 defaults={"unit": units[unit], "category": cats[ci]})
        if m.category_id is None:  # older databases seeded before categories existed
            m.category = cats[ci]
            m.save(update_fields=["category"])
        MaterialPrice.objects.get_or_create(material=m, effective_from=PRICE_DATE,
                                            defaults={"price": price})
        mats[name] = m
    variants = {}
    for (pname, vname), (yld, (lab, ele, oth), lines) in RECIPES.items():
        product, _ = Product.objects.get_or_create(factory=factory, name=pname)
        variant, _ = ProductVariant.objects.get_or_create(product=product, name=vname)
        recipe, created = Recipe.objects.get_or_create(
            variant=variant, version=1,
            defaults=dict(batch_yield=yld, effective_from=PRICE_DATE, labour_per_batch=lab,
                          electricity_per_batch=ele, other_per_batch=oth),
        )
        if created:
            for mname, qty, ucode in lines:
                rl = RecipeLine(recipe=recipe, material=mats[mname], quantity=D(qty), unit=units[ucode])
                rl.full_clean()
                rl.save()
        variants[f"{pname} {vname}"] = variant
    return factory, mats, variants
