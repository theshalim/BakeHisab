import datetime
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.catalog.bakery_catalog import CATEGORIES, MATERIALS, RECIPES, load_bakery_catalog
from apps.catalog.models import RawMaterial, Recipe
from apps.costing import engine as E
from apps.production import services as S

User = get_user_model()


class BakeryCatalogTests(TestCase):
    def test_loads_and_every_recipe_calculates(self):
        factory, mats, variants = load_bakery_catalog()
        self.assertEqual(RawMaterial.objects.filter(factory=factory).count(), 8 + len(MATERIALS))
        self.assertEqual(Recipe.objects.filter(variant__product__factory=factory).count(), 4 + len(RECIPES))
        for key, v in variants.items():
            inp = S.live_inputs_for([(v.id, v, D("100"))])
            est = E.estimate(inp.lines, inp.prices)
            self.assertGreater(est.total_cost, 0, key)
            self.assertGreater(est.cost_per_piece, 0, key)

    def test_every_material_has_a_category_and_price(self):
        factory, mats, _ = load_bakery_catalog()
        for m in RawMaterial.objects.filter(factory=factory):
            self.assertIsNotNone(m.category, m.name)
            self.assertIsNotNone(m.price_on(datetime.date(2026, 11, 7)), m.name)

    def test_running_twice_creates_no_duplicates(self):
        load_bakery_catalog()
        n = RawMaterial.objects.count(), Recipe.objects.count()
        load_bakery_catalog()
        self.assertEqual((RawMaterial.objects.count(), Recipe.objects.count()), n)

    def test_hand_check_butter_cookies_cost(self):
        _, _, v = load_bakery_catalog()
        var = v["Butter Cookies (বাটার কুকিজ) 200 gm"]
        inp = S.live_inputs_for([(var.id, var, D("100"))])   # exactly 1 batch
        est = E.estimate(inp.lines, inp.prices)
        # flour 12x65=780, butter 5x1100=5500, icing 5x160=800, egg 40x12=480, milk powder 850,
        # baking powder 0.1x450=45, salt 0.05x40=2, vanilla 0.1Lx1200=120, LPG 2x120=240,
        # pouch 100x4=400, label 100x0.5=50  -> 9267 ; + labour 600 + elec 200 + other 100 = 10167
        self.assertEqual(est.total_cost, D("10167"))
        self.assertEqual(est.cost_per_piece, D("101.67"))


class AdminWordsTests(TestCase):
    def setUp(self):
        load_bakery_catalog()
        self.client.force_login(User.objects.create_superuser("a", password="x"))

    def test_no_go_and_no_dashes_on_admin_pages(self):
        for url in ["/admin/catalog/materialcategory/", "/admin/catalog/rawmaterial/",
                    "/admin/catalog/recipe/add/", "/admin/catalog/rawmaterial/add/",
                    "/admin/production/productionplan/"]:
            page = self.client.get(url).content.decode()
            self.assertNotIn(">Go<", page, url)
            self.assertNotIn("---------", page, url)
        page = self.client.get("/admin/catalog/rawmaterial/").content.decode()
        self.assertIn("▶ চালান (Run)", page)
        self.assertIn("— একটি কাজ বাছুন (Choose an action) —", page)
        self.assertIn("কাজ (Action):", page)
        self.assertIn("খুঁজুন (Search)", page)
        self.assertIn("বাছাই", page)
        self.assertIn("— বাছুন (Select) —", self.client.get("/admin/catalog/rawmaterial/add/").content.decode())
