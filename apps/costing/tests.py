"""
Engine tests with HAND-CALCULATED expected numbers (sample recipes in
apps/catalog/sample_data.py). If the engine changes and these fail, the
engine is wrong, not the test — re-check the arithmetic in the comments.
"""
from decimal import Decimal as D

from django.test import SimpleTestCase

from apps.costing import engine as E
from apps.costing.engine import CalculationError, LineInput, LineOutput, RecipeItem

PRICES = {"Flour": D("65"), "Sugar": D("130"), "Egg": D("12"), "Oil": D("180"),
          "Yeast": D("400"), "Salt": D("40"), "LPG": D("120"), "Packaging": D("2")}


def items(**kw):
    return [RecipeItem(k, D(v)) for k, v in kw.items()]


CAKE = dict(batch_yield=D("50"), labour_per_batch=D("400"), electricity_per_batch=D("120"),
            other_per_batch=D("80"),
            items=items(Flour="6", Sugar="5", Egg="60", Oil="2", LPG="2", Packaging="50"))
# Bon Ruti 50gm recipe is 500 ML oil, 150 GM yeast, 100 GM salt -> already converted here
BR50 = dict(batch_yield=D("200"), labour_per_batch=D("300"), electricity_per_batch=D("80"),
            other_per_batch=D("50"),
            items=items(Flour="7", Sugar="1", Oil="0.5", Yeast="0.15", Salt="0.1", LPG="1.5",
                        Packaging="200"))
BR120 = dict(batch_yield=D("100"), labour_per_batch=D("350"), electricity_per_batch=D("90"),
             other_per_batch=D("60"),
             items=items(Flour="8", Sugar="1.2", Oil="0.6", Yeast="0.2", Salt="0.12", LPG="1.8",
                         Packaging="100"))
BREAD = dict(batch_yield=D("100"), labour_per_batch=D("500"), electricity_per_batch=D("150"),
             other_per_batch=D("100"),
             items=items(Flour="25", Sugar="2", Oil="1", Yeast="0.5", Salt="0.3", LPG="3",
                         Packaging="100"))


def blueprint_plan():
    """Blueprint example: Cake 2000, Bon Ruti 50gm 500, Bon Ruti 120gm 600, Bread 500."""
    return [
        LineInput("cake", D("2000"), **CAKE),    # 40 batches
        LineInput("br50", D("500"), **BR50),     # 2.5 batches (fractional, not rounded up)
        LineInput("br120", D("600"), **BR120),   # 6 batches
        LineInput("bread", D("500"), **BREAD),   # 5 batches
    ]


class EstimateTests(SimpleTestCase):
    def setUp(self):
        self.est = E.estimate(blueprint_plan(), PRICES)

    def test_uc11_bread_formula_from_blueprint(self):
        # (500 / 100) x 25 = 125 KG flour, x2 = 10 sugar, x1 = 5 oil
        bread = self.est.line("bread")
        self.assertEqual(bread.materials["Flour"], D("125"))
        self.assertEqual(bread.materials["Sugar"], D("10"))
        self.assertEqual(bread.materials["Oil"], D("5"))

    def test_br06_common_materials_are_aggregated(self):
        m = self.est.materials
        self.assertEqual(m["Flour"], D("430.5"))      # 240 + 17.5 + 48 + 125
        self.assertEqual(m["Sugar"], D("219.7"))      # 200 + 2.5 + 7.2 + 10
        self.assertEqual(m["Egg"], D("2400"))         # cake only: 40 x 60
        self.assertEqual(m["Oil"], D("89.85"))        # 80 + 1.25 + 3.6 + 5
        self.assertEqual(m["Yeast"], D("4.075"))      # 0.375 + 1.2 + 2.5
        self.assertEqual(m["Salt"], D("2.47"))        # 0.25 + 0.72 + 1.5
        self.assertEqual(m["LPG"], D("109.55"))       # 80 + 3.75 + 10.8 + 15
        self.assertEqual(m["Packaging"], D("3600"))   # 2000 + 500 + 600 + 500

    def test_d05_fractional_batch_not_rounded(self):
        br50 = self.est.line("br50")
        self.assertEqual(br50.materials["Flour"], D("17.5"))   # 2.5 x 7, not 3 x 7
        self.assertEqual(br50.standard["labour"], D("750"))    # 2.5 x 300

    def test_material_cost_totals(self):
        # 27982.5 + 28561 + 28800 + 16173 + 1630 + 98.8 + 13146 + 7200
        self.assertEqual(self.est.total_material_cost, D("123591.3"))

    def test_standard_cost_totals(self):
        self.assertEqual(self.est.standard["labour"], D("21350"))      # 16000+750+2100+2500
        self.assertEqual(self.est.standard["electricity"], D("6290"))  # 4800+200+540+750
        self.assertEqual(self.est.standard["other"], D("4185"))        # 3200+125+360+500

    def test_grand_total_and_per_piece(self):
        self.assertEqual(self.est.total_cost, D("155416.3"))
        self.assertEqual(self.est.total_qty, D("3600"))
        # Bread: material 14185 + standard 3750 = 17935 / 500 = 35.87
        self.assertEqual(self.est.line("bread").total_cost, D("17935"))
        self.assertEqual(self.est.line("bread").cost_per_piece, D("35.87"))
        # Cake: material 98400 + standard 24000 = 122400 / 2000 = 61.2
        self.assertEqual(self.est.line("cake").total_cost, D("122400"))
        self.assertEqual(self.est.line("cake").cost_per_piece, D("61.2"))

    def test_line_totals_add_up_to_plan_total(self):
        self.assertEqual(sum(ln.total_cost for ln in self.est.lines), self.est.total_cost)


class EstimateValidationTests(SimpleTestCase):
    def test_br05_zero_quantity_rejected(self):
        with self.assertRaises(CalculationError):
            E.estimate([LineInput("bread", D("0"), **BREAD)], PRICES)

    def test_negative_quantity_rejected(self):
        with self.assertRaises(CalculationError):
            E.estimate([LineInput("bread", D("-5"), **BREAD)], PRICES)

    def test_br04_zero_yield_rejected(self):
        bad = dict(BREAD, batch_yield=D("0"))
        with self.assertRaises(CalculationError):
            E.estimate([LineInput("bread", D("100"), **bad)], PRICES)

    def test_br07_missing_price_rejected(self):
        prices = {k: v for k, v in PRICES.items() if k != "Yeast"}
        with self.assertRaises(CalculationError):
            E.estimate([LineInput("bread", D("100"), **BREAD)], prices)

    def test_empty_plan_rejected(self):
        with self.assertRaises(CalculationError):
            E.estimate([], PRICES)


class ActualTests(SimpleTestCase):
    """Plan: Bread 500 + Cake 2000."""

    def setUp(self):
        self.est = E.estimate([LineInput("bread", D("500"), **BREAD),
                               LineInput("cake", D("2000"), **CAKE)], PRICES)
        # Estimated: Flour 365 (bread 125, cake 240), Sugar 210, Egg 2400 (cake only),
        # Oil 85, Yeast 2.5, Salt 1.5, LPG 95, Packaging 2500.
        self.actual_mats = {
            "Flour": D("401.5"),   # 10% more than 365
            "Sugar": D("210"), "Egg": D("2500"), "Oil": D("85"), "Yeast": D("2.5"),
            "Salt": D("1.5"), "LPG": D("95"), "Packaging": D("2500"),
        }
        # Estimated labour 2500 + 16000 = 18500; actual is 5% more.
        self.actual_std = {"labour": D("19425"), "electricity": D("5550"), "other": D("3700")}
        self.outputs = {"bread": LineOutput(D("500"), D("10")),   # good 490
                        "cake": LineOutput(D("2000"), D("40"))}   # good 1960
        self.act = E.actual(self.est, PRICES, self.actual_mats, self.actual_std, self.outputs)

    def test_d02_split_by_estimated_material_share_not_planned_qty(self):
        # Flour split 125:240, so bread gets 401.5 x 125/365 = 137.5 and cake 264.
        # (Planned-qty split would wrongly give bread only 20% = 80.3.)
        self.assertEqual(self.act.line("bread").materials["Flour"], D("137.5"))
        self.assertEqual(self.act.line("cake").materials["Flour"], D("264"))

    def test_d02_material_used_by_one_product_goes_only_to_it(self):
        self.assertEqual(self.act.line("cake").materials["Egg"], D("2500"))
        self.assertNotIn("Egg", self.act.line("bread").materials)

    def test_d05_labour_split_by_estimated_labour_share(self):
        self.assertEqual(self.act.line("bread").standard["labour"], D("2625"))   # 2500 x 1.05
        self.assertEqual(self.act.line("cake").standard["labour"], D("16800"))   # 16000 x 1.05

    def test_line_actual_totals(self):
        # Bread: material 14185 + 12.5 extra flour x 65 (812.5) = 14997.5; std 2625+750+500
        self.assertEqual(self.act.line("bread").total_cost, D("18872.5"))
        # Cake: 98400 + 24 flour x 65 (1560) + 100 egg x 12 (1200) = 101160; std 16800+4800+3200
        self.assertEqual(self.act.line("cake").total_cost, D("125960"))
        self.assertEqual(self.act.total_cost, D("144832.5"))

    def test_br10_actual_per_piece_uses_good_qty(self):
        self.assertEqual(E.money(self.act.line("bread").cost_per_piece), D("38.52"))  # 18872.5/490
        self.assertEqual(E.money(self.act.line("cake").cost_per_piece), D("64.27"))   # 125960/1960

    def test_variance(self):
        cmp = E.compare(self.est, self.act)
        self.assertEqual(cmp.total.estimated, D("140335"))      # 17935 + 122400
        self.assertEqual(cmp.total.amount, D("4497.5"))         # 144832.5 - 140335
        self.assertEqual(E.percent(cmp.total.percent), D("3.20"))
        self.assertEqual(cmp.lines["bread"].amount, D("937.5"))  # 18872.5 - 17935

    def test_split_parts_always_add_back_to_total(self):
        act = E.actual(self.est, PRICES, dict(self.actual_mats, Flour=D("400")),
                       self.actual_std, self.outputs)
        parts = act.line("bread").materials["Flour"] + act.line("cake").materials["Flour"]
        self.assertEqual(parts, D("400"))

    def test_missing_material_blocks(self):
        mats = {k: v for k, v in self.actual_mats.items() if k != "Salt"}
        with self.assertRaises(CalculationError):
            E.actual(self.est, PRICES, mats, self.actual_std, self.outputs)

    def test_material_not_in_any_recipe_blocks(self):
        with self.assertRaises(CalculationError):
            E.actual(self.est, PRICES, dict(self.actual_mats, Butter=D("5")), self.actual_std, self.outputs)

    def test_missing_labour_blocks_but_zero_is_ok(self):
        with self.assertRaises(CalculationError):
            E.actual(self.est, PRICES, self.actual_mats, {"electricity": D("1"), "other": D("1")},
                     self.outputs)
        E.actual(self.est, PRICES, self.actual_mats, {"labour": D("0"), "electricity": D("0"),
                                                      "other": D("0")}, self.outputs)

    def test_rejected_more_than_produced_blocks(self):
        outputs = dict(self.outputs, bread=LineOutput(D("10"), D("20")))
        with self.assertRaises(CalculationError):
            E.actual(self.est, PRICES, self.actual_mats, self.actual_std, outputs)

    def test_zero_good_qty_gives_no_per_piece(self):
        outputs = dict(self.outputs, bread=LineOutput(D("100"), D("100")))
        act = E.actual(self.est, PRICES, self.actual_mats, self.actual_std, outputs)
        self.assertIsNone(act.line("bread").cost_per_piece)


class DisplayTests(SimpleTestCase):
    def test_count_units_round_up(self):
        self.assertEqual(E.display_qty(D("2050.5"), True), D("2051"))
        self.assertEqual(E.display_qty(D("2050.01"), True), D("2051"))
        self.assertEqual(E.display_qty(D("2050"), True), D("2050"))

    def test_weight_shows_grams(self):
        self.assertEqual(E.display_qty(D("4.0754"), False), D("4.075"))

    def test_money_two_decimals_half_up(self):
        self.assertEqual(E.money(D("35.875")), D("35.88"))
        self.assertEqual(E.money(D("38.515306")), D("38.52"))
