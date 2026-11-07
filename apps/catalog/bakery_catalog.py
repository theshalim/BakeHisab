"""
Full bakery SAMPLE catalog on top of sample_data.py: 5 more products (with flavour
variants), every common bakery raw material, flavour essences and packaging.

Quantities and prices are realistic guesses for a Bangladeshi bakery (2026),
NOT the factory's real recipes. Change them in Admin when the real numbers arrive.
Safe to run many times: existing rows are never overwritten.

    manage.py seed_sample      (loads sample_data + this file)
"""
from decimal import Decimal as D

from .models import MaterialCategory, MaterialPrice, Product, ProductVariant, RawMaterial, Recipe, RecipeLine, Unit
from .sample_data import PRICE_DATE, load_sample

# name, sort order. The first four already exist from sample_data (kept as they are).
CATEGORIES = [
    ("শুকনো উপকরণ (Dry Ingredients)", 1),
    ("তরল ও ডিম (Wet Ingredients)", 2),
    ("দুধ, মাখন ও চর্বি (Dairy & Fats)", 3),
    ("বেকিং এডিটিভ (Baking Additives)", 4),
    ("ফ্লেভার ও এসেন্স (Flavours & Essences)", 5),
    ("টপিং ও ফিলিং (Toppings & Fillings)", 6),
    ("জ্বালানি (Fuel)", 7),
    ("প্যাকেজিং (Packaging)", 8),
]
DRY, WET, DAIRY, ADD, FLAV, TOP, FUEL, PACK = (c for c, _ in CATEGORIES)

# name, unit, Tk per unit, category
MATERIALS = [
    # dry
    ("Atta (আটা)", "KG", "60", DRY),
    ("Icing Sugar (আইসিং সুগার)", "KG", "160", DRY),
    ("Corn Flour (কর্ন ফ্লাওয়ার)", "KG", "180", DRY),
    ("Custard Powder (কাস্টার্ড পাউডার)", "KG", "400", DRY),
    ("Cocoa Powder (কোকো পাউডার)", "KG", "900", DRY),
    ("Milk Powder (গুঁড়া দুধ)", "KG", "850", DRY),
    ("Whipping Cream Powder (হুইপিং ক্রিম পাউডার)", "KG", "900", DRY),
    # dairy & fats
    ("Butter (মাখন)", "KG", "1100", DAIRY),
    ("Margarine (মার্জারিন)", "KG", "320", DAIRY),
    ("Dalda / Vegetable Ghee (ডালডা)", "KG", "250", DAIRY),
    ("Liquid Milk (তরল দুধ)", "L", "90", DAIRY),
    # additives
    ("Baking Powder (বেকিং পাউডার)", "KG", "450", ADD),
    ("Baking Soda (বেকিং সোডা)", "KG", "150", ADD),
    ("Bread Improver (ব্রেড ইমপ্রুভার)", "KG", "600", ADD),
    ("Cake Gel / Emulsifier (কেক জেল)", "KG", "700", ADD),
    ("Preservative (প্রিজারভেটিভ)", "KG", "800", ADD),
    ("Food Colour (ফুড কালার)", "KG", "2000", ADD),
    # flavours
    ("Vanilla Essence (ভ্যানিলা এসেন্স)", "L", "1200", FLAV),
    ("Strawberry Essence (স্ট্রবেরি এসেন্স)", "L", "1500", FLAV),
    ("Pineapple Essence (আনারস এসেন্স)", "L", "1400", FLAV),
    ("Orange Essence (কমলা এসেন্স)", "L", "1400", FLAV),
    ("Chocolate Compound (চকলেট কম্পাউন্ড)", "KG", "750", FLAV),
    # toppings & fillings
    ("Mixed Fruit Jam (মিক্সড ফ্রুট জ্যাম)", "KG", "400", TOP),
    ("Raisins (কিশমিশ)", "KG", "900", TOP),
    ("Cashew Nut (কাজু বাদাম)", "KG", "1600", TOP),
    ("Tutti Frutti (টুটি ফ্রুটি)", "KG", "500", TOP),
    ("Sesame (তিল)", "KG", "350", TOP),
    # packaging
    ("Cake Box (কেক বক্স)", "PCS", "12", PACK),
    ("Biscuit Pouch (বিস্কুট পাউচ)", "PCS", "4", PACK),
    ("Toast Bag (টোস্ট ব্যাগ)", "PCS", "3", PACK),
    ("Muffin Paper Cup (মাফিন কাপ)", "PCS", "1", PACK),
    ("Paper Tray (পেপার ট্রে)", "PCS", "1.5", PACK),
    ("Cream Roll Wrapper (রোল র‍্যাপার)", "PCS", "0.8", PACK),
    ("Label Sticker (লেবেল স্টিকার)", "PCS", "0.5", PACK),
]

# (product, variant): batch yield, (labour, electricity, other) per batch, [(material, qty, unit)]
# "Packaging" here is the generic poly bag from sample_data.
RECIPES = {
    # 1. Butter Cookies — 200 gm pack
    ("Butter Cookies (বাটার কুকিজ)", "200 gm"): ("100", ("600", "200", "100"), [
        ("Flour", "12", "KG"), ("Butter (মাখন)", "5", "KG"), ("Icing Sugar (আইসিং সুগার)", "5", "KG"),
        ("Egg", "40", "PCS"), ("Milk Powder (গুঁড়া দুধ)", "1", "KG"), ("Baking Powder (বেকিং পাউডার)", "100", "GM"),
        ("Salt", "50", "GM"), ("Vanilla Essence (ভ্যানিলা এসেন্স)", "100", "ML"), ("LPG", "2", "KG"),
        ("Biscuit Pouch (বিস্কুট পাউচ)", "100", "PCS"), ("Label Sticker (লেবেল স্টিকার)", "100", "PCS"),
    ]),
    # 2. Toast — 300 gm pack
    ("Toast (টোস্ট)", "300 gm"): ("100", ("700", "250", "120"), [
        ("Flour", "20", "KG"), ("Sugar", "4", "KG"), ("Dalda / Vegetable Ghee (ডালডা)", "2.5", "KG"),
        ("Egg", "30", "PCS"), ("Yeast", "300", "GM"), ("Milk Powder (গুঁড়া দুধ)", "800", "GM"),
        ("Salt", "200", "GM"), ("Bread Improver (ব্রেড ইমপ্রুভার)", "100", "GM"), ("Sesame (তিল)", "300", "GM"),
        ("LPG", "4", "KG"), ("Toast Bag (টোস্ট ব্যাগ)", "100", "PCS"), ("Label Sticker (লেবেল স্টিকার)", "100", "PCS"),
    ]),
    # 3. Cream Roll — 60 gm, two flavours
    ("Cream Roll (ক্রিম রোল)", "60 gm – Vanilla (ভ্যানিলা)"): ("200", ("500", "150", "80"), [
        ("Flour", "7", "KG"), ("Margarine (মার্জারিন)", "4", "KG"), ("Sugar", "1", "KG"), ("Salt", "50", "GM"),
        ("Whipping Cream Powder (হুইপিং ক্রিম পাউডার)", "1.2", "KG"), ("Icing Sugar (আইসিং সুগার)", "1", "KG"),
        ("Vanilla Essence (ভ্যানিলা এসেন্স)", "50", "ML"), ("Preservative (প্রিজারভেটিভ)", "20", "GM"),
        ("LPG", "1.5", "KG"), ("Cream Roll Wrapper (রোল র‍্যাপার)", "200", "PCS"),
    ]),
    ("Cream Roll (ক্রিম রোল)", "60 gm – Strawberry (স্ট্রবেরি)"): ("200", ("500", "150", "80"), [
        ("Flour", "7", "KG"), ("Margarine (মার্জারিন)", "4", "KG"), ("Sugar", "1", "KG"), ("Salt", "50", "GM"),
        ("Whipping Cream Powder (হুইপিং ক্রিম পাউডার)", "1.2", "KG"), ("Icing Sugar (আইসিং সুগার)", "1", "KG"),
        ("Strawberry Essence (স্ট্রবেরি এসেন্স)", "60", "ML"), ("Food Colour (ফুড কালার)", "5", "GM"),
        ("Preservative (প্রিজারভেটিভ)", "20", "GM"), ("LPG", "1.5", "KG"),
        ("Cream Roll Wrapper (রোল র‍্যাপার)", "200", "PCS"),
    ]),
    # 4. Muffin — 70 gm, two flavours
    ("Muffin (মাফিন)", "70 gm – Chocolate (চকলেট)"): ("100", ("350", "120", "60"), [
        ("Flour", "3", "KG"), ("Sugar", "2.5", "KG"), ("Egg", "50", "PCS"), ("Oil", "1.5", "L"),
        ("Liquid Milk (তরল দুধ)", "1", "L"), ("Cocoa Powder (কোকো পাউডার)", "400", "GM"),
        ("Chocolate Compound (চকলেট কম্পাউন্ড)", "500", "GM"), ("Baking Powder (বেকিং পাউডার)", "80", "GM"),
        ("Cake Gel / Emulsifier (কেক জেল)", "100", "GM"), ("LPG", "1.2", "KG"),
        ("Muffin Paper Cup (মাফিন কাপ)", "100", "PCS"), ("Packaging", "100", "PCS"),
    ]),
    ("Muffin (মাফিন)", "70 gm – Vanilla (ভ্যানিলা)"): ("100", ("350", "120", "60"), [
        ("Flour", "3.3", "KG"), ("Sugar", "2.5", "KG"), ("Egg", "50", "PCS"), ("Oil", "1.5", "L"),
        ("Liquid Milk (তরল দুধ)", "1", "L"), ("Vanilla Essence (ভ্যানিলা এসেন্স)", "40", "ML"),
        ("Tutti Frutti (টুটি ফ্রুটি)", "300", "GM"), ("Baking Powder (বেকিং পাউডার)", "80", "GM"),
        ("Cake Gel / Emulsifier (কেক জেল)", "100", "GM"), ("LPG", "1.2", "KG"),
        ("Muffin Paper Cup (মাফিন কাপ)", "100", "PCS"), ("Packaging", "100", "PCS"),
    ]),
    # 5. Danish Pastry — 80 gm, pineapple
    ("Danish Pastry (ড্যানিশ পেস্ট্রি)", "80 gm – Pineapple (আনারস)"): ("100", ("500", "150", "80"), [
        ("Flour", "4.5", "KG"), ("Butter (মাখন)", "1.5", "KG"), ("Margarine (মার্জারিন)", "1", "KG"),
        ("Sugar", "600", "GM"), ("Egg", "20", "PCS"), ("Yeast", "100", "GM"),
        ("Milk Powder (গুঁড়া দুধ)", "200", "GM"), ("Salt", "40", "GM"),
        ("Mixed Fruit Jam (মিক্সড ফ্রুট জ্যাম)", "1", "KG"), ("Pineapple Essence (আনারস এসেন্স)", "30", "ML"),
        ("LPG", "1.2", "KG"), ("Paper Tray (পেপার ট্রে)", "100", "PCS"), ("Packaging", "100", "PCS"),
    ]),
    # flavour variants of the existing Cake
    ("Cake", "300 gm – Chocolate (চকলেট)"): ("50", ("400", "120", "80"), [
        ("Flour", "5.5", "KG"), ("Sugar", "5", "KG"), ("Egg", "60", "PCS"), ("Oil", "2", "L"),
        ("Cocoa Powder (কোকো পাউডার)", "600", "GM"), ("Baking Powder (বেকিং পাউডার)", "100", "GM"),
        ("Cake Gel / Emulsifier (কেক জেল)", "150", "GM"), ("LPG", "2", "KG"), ("Cake Box (কেক বক্স)", "50", "PCS"),
    ]),
    ("Cake", "300 gm – Fruit (ফ্রুট)"): ("50", ("400", "120", "80"), [
        ("Flour", "5.5", "KG"), ("Sugar", "5", "KG"), ("Egg", "60", "PCS"), ("Butter (মাখন)", "1.5", "KG"),
        ("Raisins (কিশমিশ)", "500", "GM"), ("Cashew Nut (কাজু বাদাম)", "250", "GM"),
        ("Tutti Frutti (টুটি ফ্রুটি)", "500", "GM"), ("Orange Essence (কমলা এসেন্স)", "40", "ML"),
        ("Baking Powder (বেকিং পাউডার)", "100", "GM"), ("LPG", "2", "KG"), ("Cake Box (কেক বক্স)", "50", "PCS"),
    ]),
}


def load_bakery_catalog(factory_code="FA", factory_name="Factory A"):
    factory, mats, variants = load_sample(factory_code, factory_name)
    units = {u.code: u for u in Unit.objects.all()}

    cats = {}
    for cname, order in CATEGORIES:
        c, created = MaterialCategory.objects.get_or_create(factory=factory, name=cname,
                                                            defaults={"sort_order": order})
        if not created and c.sort_order != order:  # keep the new order (Dairy, Additives... in between)
            c.sort_order = order
            c.save(update_fields=["sort_order"])
        cats[cname] = c

    for name, unit, price, cname in MATERIALS:
        m, _ = RawMaterial.objects.get_or_create(factory=factory, name=name,
                                                 defaults={"unit": units[unit], "category": cats[cname]})
        MaterialPrice.objects.get_or_create(material=m, effective_from=PRICE_DATE, defaults={"price": D(price)})
        mats[name] = m

    for (pname, vname), (yld, (lab, ele, oth), lines) in RECIPES.items():
        product, _ = Product.objects.get_or_create(factory=factory, name=pname)
        variant, _ = ProductVariant.objects.get_or_create(product=product, name=vname)
        recipe, created = Recipe.objects.get_or_create(
            variant=variant, version=1,
            defaults=dict(batch_yield=D(yld), effective_from=PRICE_DATE, labour_per_batch=D(lab),
                          electricity_per_batch=D(ele), other_per_batch=D(oth),
                          notes="SAMPLE recipe — replace with the factory's real numbers."),
        )
        if created:
            for mname, qty, ucode in lines:
                rl = RecipeLine(recipe=recipe, material=mats[mname], quantity=D(qty), unit=units[ucode])
                rl.full_clean()
                rl.save()
        variants[f"{pname} {vname}"] = variant
    return factory, mats, variants
