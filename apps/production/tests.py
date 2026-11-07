"""
Workflow tests against the real database: snapshot locking, plan numbers,
offline duplicate protection, status rules, permissions, audited corrections.
"""
import uuid
from datetime import date
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from apps.catalog.models import MaterialPrice, RecipeLine, Unit
from apps.catalog.sample_data import load_sample
from apps.costing import engine as E
from apps.costing.engine import CalculationError
from apps.factories.models import FactoryMembership, Role

from . import services as S
from .models import AuditLog, Status

User = get_user_model()
TODAY = date(2026, 11, 7)


class Base(TestCase):
    def setUp(self):
        self.factory, self.mats, self.v = load_sample()
        self.manager = User.objects.create_user("manager", password="x")
        FactoryMembership.objects.create(user=self.manager, factory=self.factory,
                                         role=Role.PRODUCTION_MANAGER)
        self.admin = User.objects.create_superuser("admin", password="x")

    def blueprint_plan(self, **kw):
        plan, _ = S.create_plan(user=self.manager, factory=self.factory, production_date=TODAY, lines=[
            (self.v["Cake 300 gm"], D("2000")), (self.v["Bon Ruti 50 gm"], D("500")),
            (self.v["Bon Ruti 120 gm"], D("600")), (self.v["Bread 400 gm"], D("500")),
        ], **kw)
        return plan

    def fill_actuals_as_estimated(self, plan):
        est, _ = S.estimate_plan(plan)
        S.record_actuals(
            user=self.manager, plan=plan, materials=dict(est.materials),
            labour=est.standard["labour"], electricity=est.standard["electricity"],
            other=est.standard["other"],
            outputs={ln.id: (ln.planned_qty, D("0")) for ln in plan.lines.all()},
        )


class EstimateFromDatabaseTests(Base):
    def test_blueprint_plan_matches_hand_calculation(self):
        est, inp = S.estimate_plan(self.blueprint_plan())
        self.assertEqual(est.total_cost, D("155416.3"))
        self.assertEqual(est.materials[self.mats["Flour"].id], D("430.5"))

    def test_d04_gram_and_ml_converted_to_material_unit(self):
        # Bon Ruti 50gm: 500 ML oil and 150 GM yeast per batch -> 0.5 L and 0.15 KG
        est, _ = S.estimate_plan(self.blueprint_plan())
        br50 = [ln for ln in est.lines][1]
        self.assertEqual(br50.materials[self.mats["Oil"].id], D("1.25"))     # 2.5 x 0.5 L
        self.assertEqual(br50.materials[self.mats["Yeast"].id], D("0.375"))  # 2.5 x 0.15 KG

    def test_d04_kg_to_litre_recipe_line_refused(self):
        recipe = self.v["Bread 400 gm"].recipes.first()
        line = RecipeLine(recipe=recipe, material=self.mats["Oil"], quantity=D("1"),
                          unit=Unit.objects.get(code="KG"))
        with self.assertRaises(ValidationError):
            line.full_clean()

    def test_br03_no_active_recipe_blocks(self):
        plan = self.blueprint_plan()
        self.v["Bread 400 gm"].recipes.update(is_active=False)
        with self.assertRaises(CalculationError):
            S.estimate_plan(plan)


class PlanNumberAndSyncTests(Base):
    def test_d07_plan_numbers_sequence_per_factory_and_date(self):
        p1, p2 = self.blueprint_plan(), self.blueprint_plan()
        self.assertEqual(p1.plan_no, "PRD-20261107-001")
        self.assertEqual(p2.plan_no, "PRD-20261107-002")
        other, _, ov = load_sample("FB", "Factory B")
        FactoryMembership.objects.create(user=self.manager, factory=other, role=Role.PRODUCTION_MANAGER)
        pb, _ = S.create_plan(user=self.manager, factory=other, production_date=TODAY,
                              lines=[(ov["Bread 400 gm"], D("10"))])
        self.assertEqual(pb.plan_no, "PRD-20261107-001")  # Factory B has its own sequence

    def test_br13_same_uuid_synced_twice_creates_no_duplicate(self):
        u = uuid.uuid4()
        p1 = self.blueprint_plan(local_uuid=u)
        p2, created = S.create_plan(user=self.manager, factory=self.factory, production_date=TODAY,
                                    lines=[(self.v["Bread 400 gm"], D("1"))], local_uuid=u)
        self.assertFalse(created)
        self.assertEqual(p1.pk, p2.pk)
        self.assertEqual(p2.lines.count(), 4)


class SnapshotTests(Base):
    def test_d06_draft_follows_current_price(self):
        plan = self.blueprint_plan()
        MaterialPrice.objects.create(material=self.mats["Flour"], price=D("100"),
                                     effective_from=date(2026, 6, 1))
        est, _ = S.estimate_plan(plan)
        self.assertEqual(est.material_costs[self.mats["Flour"].id], D("43050"))  # 430.5 x 100

    def test_d06_locked_plan_ignores_later_price_and_recipe_changes(self):
        plan = self.blueprint_plan()
        S.start_production(user=self.manager, plan=plan)
        MaterialPrice.objects.create(material=self.mats["Flour"], price=D("999"),
                                     effective_from=date(2026, 6, 1))
        RecipeLine.objects.filter(recipe__variant=self.v["Bread 400 gm"],
                                  material=self.mats["Flour"]).update(quantity=D("50"))
        est, _ = S.estimate_plan(plan)
        self.assertEqual(est.total_cost, D("155416.3"))  # unchanged

    def test_snapshot_stores_recipe_version(self):
        plan = self.blueprint_plan()
        S.start_production(user=self.manager, plan=plan)
        self.assertTrue(all(ln.recipe_version == 1 for ln in plan.lines.all()))


class StatusRuleTests(Base):
    def test_d09_quantities_locked_after_start(self):
        plan = self.blueprint_plan()
        S.start_production(user=self.manager, plan=plan)
        with self.assertRaises(S.StateError):
            S.replace_draft_lines(user=self.manager, plan=plan, lines=[(self.v["Bread 400 gm"], D("1"))])

    def test_actuals_not_allowed_in_draft(self):
        plan = self.blueprint_plan()
        with self.assertRaises(S.StateError):
            S.record_actuals(user=self.manager, plan=plan, labour=D("1"))

    def test_complete_blocked_until_actuals_complete(self):
        plan = self.blueprint_plan()
        S.start_production(user=self.manager, plan=plan)
        S.record_actuals(user=self.manager, plan=plan, labour=D("100"))  # partial save is fine
        with self.assertRaises(CalculationError):
            S.complete_production(user=self.manager, plan=plan)
        plan.refresh_from_db()
        self.assertEqual(plan.status, Status.IN_PRODUCTION)

    def test_full_flow_to_completed(self):
        plan = self.blueprint_plan()
        S.start_production(user=self.manager, plan=plan)
        self.fill_actuals_as_estimated(plan)
        S.complete_production(user=self.manager, plan=plan)
        plan.refresh_from_db()
        self.assertEqual(plan.status, Status.COMPLETED)
        est, act, _ = S.actual_plan(plan)
        self.assertEqual(E.compare(est, act).total.amount, D("0"))

    def test_br11_completed_no_normal_edit(self):
        plan = self.blueprint_plan()
        S.start_production(user=self.manager, plan=plan)
        self.fill_actuals_as_estimated(plan)
        S.complete_production(user=self.manager, plan=plan)
        with self.assertRaises(S.StateError):
            S.record_actuals(user=self.manager, plan=plan, labour=D("1"))


class CorrectionAndPermissionTests(Base):
    def completed_plan(self):
        plan = self.blueprint_plan()
        S.start_production(user=self.manager, plan=plan)
        self.fill_actuals_as_estimated(plan)
        return S.complete_production(user=self.manager, plan=plan)

    def test_br11_correction_only_superuser_with_reason_and_audited(self):
        plan = self.completed_plan()
        with self.assertRaises(PermissionDenied):
            S.correct_completed(user=self.manager, plan=plan, reason="x", labour=D("1"))
        with self.assertRaises(CalculationError):
            S.correct_completed(user=self.admin, plan=plan, reason="  ", labour=D("1"))
        S.correct_completed(user=self.admin, plan=plan, reason="Labour bill late", labour=D("22000"))
        log = AuditLog.objects.get(plan=plan, action="CORRECTION")
        self.assertEqual(log.reason, "Labour bill late")
        self.assertEqual(log.changes["raw_before"]["labour"], "21350.0000")
        self.assertEqual(log.changes["raw_after"]["labour"], "22000.0000")

    def test_br12_user_cannot_touch_other_factory(self):
        other, _, ov = load_sample("FB", "Factory B")
        with self.assertRaises(PermissionDenied):
            S.create_plan(user=self.manager, factory=other, production_date=TODAY,
                          lines=[(ov["Bread 400 gm"], D("10"))])

    def test_management_role_cannot_create_plan(self):
        boss = User.objects.create_user("boss", password="x")
        FactoryMembership.objects.create(user=boss, factory=self.factory, role=Role.MANAGEMENT)
        with self.assertRaises(PermissionDenied):
            S.create_plan(user=boss, factory=self.factory, production_date=TODAY,
                          lines=[(self.v["Bread 400 gm"], D("10"))])

    def test_product_from_other_factory_refused(self):
        _, _, ov = load_sample("FB", "Factory B")
        with self.assertRaises(PermissionDenied):
            S.create_plan(user=self.manager, factory=self.factory, production_date=TODAY,
                          lines=[(ov["Bread 400 gm"], D("10"))])


class AdminSmokeTests(Base):
    """Admin pages open and the calculation panel shows the right totals."""

    def test_admin_pages_and_actions(self):
        self.client.force_login(self.admin)
        plan = self.blueprint_plan()
        for url in ["/admin/", "/admin/production/productionplan/", "/admin/catalog/recipe/",
                    "/admin/catalog/rawmaterial/", f"/admin/catalog/recipe/{self.v['Bread 400 gm'].recipes.first().pk}/change/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        page = self.client.get(f"/admin/production/productionplan/{plan.pk}/change/").content.decode()
        self.assertIn("155416.30", page)          # estimated total
        self.assertIn("2400", page)               # eggs
        r = self.client.post("/admin/production/productionplan/", {
            "action": "action_start", "_selected_action": [plan.pk]}, follow=True)
        self.assertEqual(r.status_code, 200)
        plan.refresh_from_db()
        self.assertEqual(plan.status, Status.IN_PRODUCTION)
        page = self.client.get(f"/admin/production/productionplan/{plan.pk}/change/").content.decode()
        self.assertIn("locked snapshot", page)
        self.assertIn("অসম্পূর্ণ", page)          # actuals not entered yet

    def test_non_superuser_sees_only_own_factory(self):
        other, _, ov = load_sample("FB", "Factory B")
        fa_admin = User.objects.create_user("fa", password="x", is_staff=True)
        FactoryMembership.objects.create(user=fa_admin, factory=self.factory, role=Role.FACTORY_ADMIN)
        from django.contrib.auth.models import Permission
        fa_admin.user_permissions.set(Permission.objects.filter(codename__in=["view_productionplan"]))
        self.blueprint_plan()
        FactoryMembership.objects.create(user=self.manager, factory=other, role=Role.PRODUCTION_MANAGER)
        S.create_plan(user=self.manager, factory=other, production_date=TODAY, lines=[(ov["Bread 400 gm"], D("1"))])
        self.client.force_login(fa_admin)
        page = self.client.get("/admin/production/productionplan/").content.decode()
        self.assertIn("1 production plan", page)
