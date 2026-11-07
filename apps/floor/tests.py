"""
The simple floor screens, driven like a real user would, plus the 3 fixes:
(ক) variance split by component, (খ) whole numbers for pieces, (গ) soft warnings.
"""
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.catalog.sample_data import load_sample
from apps.costing import engine as E
from apps.costing.engine import CalculationError
from apps.factories.models import FactoryMembership, Role
from apps.production import services as S
from apps.production.models import AuditLog, ProductionPlan, Status

User = get_user_model()


class FloorFlowTests(TestCase):
    def setUp(self):
        self.factory, self.mats, self.v = load_sample()
        self.user = User.objects.create_user("rahim", password="x")
        FactoryMembership.objects.create(user=self.user, factory=self.factory, role=Role.PRODUCTION_OPERATOR)
        self.client.force_login(self.user)

    def qty_post(self, action, **extra):
        data = {"action": action, "date": "2026-11-07", "factory": self.factory.id,
                "local_uuid": "11111111-1111-1111-1111-111111111111",
                f"qty_{self.v['Cake 300 gm'].id}": "2000", f"qty_{self.v['Bon Ruti 50 gm'].id}": "500",
                f"qty_{self.v['Bon Ruti 120 gm'].id}": "600", f"qty_{self.v['Bread 400 gm'].id}": "500"}
        data.update(extra)
        return self.client.post("/new/", data)

    def test_login_required(self):
        self.client.logout()
        self.assertRedirects(self.client.get("/"), "/login/?next=/")

    def test_whole_flow_three_steps(self):
        # Step 1a: type quantities -> "হিসাব দেখুন" (nothing saved yet)
        r = self.qty_post("calc")
        page = r.content.decode()
        self.assertIn("155,416.30", page)
        self.assertIn("উৎপাদন শুরু করুন (Start Production)", page)
        self.assertEqual(ProductionPlan.objects.count(), 0)

        # Step 1b: "Production শুরু করুন" -> saved AND locked in one tap
        r = self.qty_post("start")
        plan = ProductionPlan.objects.get()
        self.assertRedirects(r, f"/p/{plan.pk}/")
        self.assertEqual(plan.status, Status.IN_PRODUCTION)

        # double tap / resend does not make a second plan
        self.qty_post("start")
        self.assertEqual(ProductionPlan.objects.count(), 1)

        # Step 3: finish form comes pre-filled with the estimate
        page = self.client.get(f"/p/{plan.pk}/finish/").content.decode()
        self.assertIn('value="430.5"', page)    # flour
        self.assertIn('value="21350"', page)    # labour

        # user changes only flour and the bread rejects, keeps the rest
        est, inp = S.estimate_plan(plan)
        data = {"action": "complete"}
        for ln in plan.lines.all():
            data[f"prod_{ln.id}"] = str(int(ln.planned_qty))
            data[f"rej_{ln.id}"] = "10" if ln.variant == self.v["Bread 400 gm"] else "0"
        for mid, q in est.materials.items():
            data[f"mat_{mid}"] = str(E.display_qty(q, inp.material_info[mid][2]))
        data[f"mat_{self.mats['Flour'].id}"] = "440"
        data.update(labour="21350", electricity="6290", other="4185")
        r = self.client.post(f"/p/{plan.pk}/finish/", data)
        self.assertRedirects(r, f"/p/{plan.pk}/")
        plan.refresh_from_db()
        self.assertEqual(plan.status, Status.COMPLETED)

        page = self.client.get(f"/p/{plan.pk}/").content.decode()
        self.assertIn("হিসাবের চেয়ে বেশি", page)
        self.assertIn("+617.50", page)  # 9.5 KG extra flour x 65 Tk — the only difference

    def test_warning_asks_once_then_completes(self):
        self.qty_post("start")
        plan = ProductionPlan.objects.get()
        est, inp = S.estimate_plan(plan)
        data = {"action": "complete", "labour": "0", "electricity": "6290", "other": "4185"}
        for ln in plan.lines.all():
            data[f"prod_{ln.id}"], data[f"rej_{ln.id}"] = str(int(ln.planned_qty)), "0"
        for mid, q in est.materials.items():
            data[f"mat_{mid}"] = str(E.display_qty(q, inp.material_info[mid][2]))

        page = self.client.post(f"/p/{plan.pk}/finish/", data).content.decode()
        self.assertIn("একবার দেখে নিন", page)
        self.assertIn("শ্রমিক (Labour) খরচ 0", page)
        plan.refresh_from_db()
        self.assertEqual(plan.status, Status.IN_PRODUCTION)   # not completed yet

        r = self.client.post(f"/p/{plan.pk}/finish/", dict(data, confirm="1"))
        self.assertRedirects(r, f"/p/{plan.pk}/")
        plan.refresh_from_db()
        self.assertEqual(plan.status, Status.COMPLETED)

    def test_fraction_egg_rejected_on_screen(self):
        self.qty_post("start")
        plan = ProductionPlan.objects.get()
        r = self.client.post(f"/p/{plan.pk}/finish/", {"action": "save",
                                                        f"mat_{self.mats['Egg'].id}": "2399.9992"})
        self.assertIn("পূর্ণসংখ্যা", r.content.decode())

    def test_blank_quantities_show_friendly_error(self):
        r = self.client.post("/new/", {"action": "calc", "date": "2026-11-07", "factory": self.factory.id})
        self.assertIn("অন্তত একটি product", r.content.decode())

    def test_draft_save_then_start_from_detail(self):
        self.qty_post("draft")
        plan = ProductionPlan.objects.get()
        self.assertEqual(plan.status, Status.DRAFT)
        self.client.post(f"/p/{plan.pk}/", {"action": "start"})
        plan.refresh_from_db()
        self.assertEqual(plan.status, Status.IN_PRODUCTION)

    def test_other_factory_plan_is_hidden(self):
        other, _, ov = load_sample("FB", "Factory B")
        boss = User.objects.create_superuser("boss", password="x")
        import datetime
        plan, _ = S.create_plan(user=boss, factory=other, production_date=datetime.date(2026, 11, 7),
                                lines=[(ov["Bread 400 gm"], D("10"))])
        self.assertEqual(self.client.get(f"/p/{plan.pk}/").status_code, 403)


class FixTests(TestCase):
    def setUp(self):
        self.factory, self.mats, self.v = load_sample()
        self.admin = User.objects.create_superuser("admin", password="x")
        import datetime
        self.plan, _ = S.create_plan(user=self.admin, factory=self.factory, production_date=datetime.date(2026, 11, 7),
                                     lines=[(self.v["Bread 400 gm"], D("500")), (self.v["Cake 300 gm"], D("2000"))])
        S.start_production(user=self.admin, plan=self.plan)

    def test_kha_fraction_of_pieces_refused(self):
        with self.assertRaises(CalculationError):
            S.record_actuals(user=self.admin, plan=self.plan, materials={self.mats["Egg"].id: D("2399.9992")})
        with self.assertRaises(CalculationError):
            line = self.plan.lines.first()
            S.record_actuals(user=self.admin, plan=self.plan, outputs={line.id: (D("10.5"), D("0"))})
        # weight can still have decimals
        S.record_actuals(user=self.admin, plan=self.plan, materials={self.mats["Salt"].id: D("2.9998")})

    def test_kha_planned_qty_must_be_whole(self):
        import datetime
        with self.assertRaises(CalculationError):
            S.create_plan(user=self.admin, factory=self.factory, production_date=datetime.date(2026, 11, 7),
                          lines=[(self.v["Bread 400 gm"], D("10.5"))])

    def test_ka_variance_split_shows_material_separately(self):
        # the user's real case: labour/electricity/other left at 0
        est = S.estimate_plan(self.plan)[0]
        S.record_actuals(user=self.admin, plan=self.plan, materials=dict(est.materials),
                         labour=D("0"), electricity=D("0"), other=D("0"),
                         outputs={ln.id: (ln.planned_qty, D("0")) for ln in self.plan.lines.all()})
        est, act, _ = S.actual_plan(self.plan)
        rows = {r["label"]: r["v"] for r in S.variance_rows(est, act)}
        self.assertEqual(rows["কাঁচামাল (Raw material)"].amount, D("0"))
        self.assertEqual(rows["শ্রমিক (Labour)"].amount, D("-18500"))

    def test_ga_warnings_zero_cost_and_big_difference(self):
        est = S.estimate_plan(self.plan)[0]
        mats = dict(est.materials)
        mats[self.mats["Flour"].id] = D("3650")   # 10x typo of 365
        S.record_actuals(user=self.admin, plan=self.plan, materials=mats,
                         labour=D("0"), electricity=D("5550"), other=D("3700"),
                         outputs={ln.id: (ln.planned_qty, D("0")) for ln in self.plan.lines.all()})
        warns = S.actual_warnings(self.plan)
        self.assertEqual(len(warns), 2)
        self.assertTrue(any("Labour" in w for w in warns))
        self.assertTrue(any("Flour" in w and "অনেক পার্থক্য" in w for w in warns))
        S.complete_production(user=self.admin, plan=self.plan)  # warnings never block


class LoginTests(TestCase):
    def setUp(self):
        User.objects.create_user("karim", password="pass12345")

    def test_login_page_renders(self):
        page = self.client.get("/login/").content.decode()
        self.assertIn("ঢুকুন (Login)", page)
        self.assertIn("মনে রাখুন (Remember me)", page)

    def test_remember_me_keeps_session(self):
        self.client.post("/login/", {"username": "karim", "password": "pass12345", "remember": ["0", "1"]})
        self.assertFalse(self.client.session.get_expire_at_browser_close())

    def test_remember_zero_session_ends_on_close(self):
        self.client.post("/login/", {"username": "karim", "password": "pass12345", "remember": "0"})
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_wrong_password_message(self):
        page = self.client.post("/login/", {"username": "karim", "password": "nope"}).content.decode()
        self.assertIn("ভুল (Wrong username or password)", page)


class ThemeTests(TestCase):
    def test_admin_uses_same_theme_and_login(self):
        r = self.client.get("/admin/")
        self.assertRedirects(r, "/admin/login/?next=/admin/", target_status_code=302)
        self.assertRedirects(self.client.get("/admin/login/?next=/admin/"), "/login/?next=/admin/")
        User.objects.create_superuser("boss", password="x")
        self.client.post("/login/", {"username": "boss", "password": "x", "remember": ["0", "1"], "next": "/admin/"})
        page = self.client.get("/admin/").content.decode()
        self.assertIn("--header-bg: #0d2f3f", page)
        self.assertIn("উৎপাদন স্ক্রিন (Production Screen)", page)

    def test_floor_uses_navy_and_amber(self):
        User.objects.create_user("x", password="x")
        self.client.login(username="x", password="x")
        page = self.client.get("/").content.decode()
        self.assertIn("--navy: #0d2f3f", page)
        self.assertIn("--accent: #f6b21a", page)



class CategoryAndConfirmTests(TestCase):
    def setUp(self):
        self.factory, self.mats, self.v = load_sample()
        self.user = User.objects.create_superuser("s", password="x")
        self.client.force_login(self.user)

    def calc(self, variant, qty="100"):
        return self.client.post("/new/", {"action": "calc", "date": "2026-11-07", "factory": self.factory.id,
                                          f"qty_{self.v[variant].id}": qty}).content.decode()

    def test_materials_grouped_by_category_in_admin_order(self):
        page = self.calc("Cake 300 gm")
        order = [page.index(t) for t in ("শুকনো উপকরণ (Dry Ingredients)", "Flour", "তরল ও ডিম (Wet Ingredients)",
                                          "Egg", "জ্বালানি (Fuel)", "LPG", "প্যাকেজিং (Packaging)")]
        self.assertEqual(order, sorted(order))

    def test_material_without_category_goes_to_other(self):
        self.mats["Salt"].category = None
        self.mats["Salt"].save()
        self.assertIn("অন্যান্য (Other)", self.calc("Bread 400 gm"))

    def test_confirm_popup_on_important_buttons(self):
        self.assertIn('data-confirm="উৎপাদন শুরু করবেন? (Start production?)"', self.calc("Bread 400 gm"))
        import datetime
        plan, _ = S.create_plan(user=self.user, factory=self.factory, production_date=datetime.date(2026, 11, 7),
                                lines=[(self.v["Bread 400 gm"], D("100"))])
        S.start_production(user=self.user, plan=plan)
        self.assertIn('data-confirm="উৎপাদন কি শেষ হয়েছে? (Is production finished?)"',
                      self.client.get(f"/p/{plan.pk}/").content.decode())
        self.assertIn('data-confirm="উৎপাদন শেষ করবেন? (Complete production?)"',
                      self.client.get(f"/p/{plan.pk}/finish/").content.decode())



class PaginationEditHistoryTests(TestCase):
    """3 per page, View/Edit/History on every card, every edit logged with user + time + old -> new."""

    def setUp(self):
        import datetime
        self.day = datetime.date(2026, 11, 7)
        self.factory, self.mats, self.v = load_sample()
        self.op = User.objects.create_user("rahim", password="x", first_name="Rahim")
        FactoryMembership.objects.create(user=self.op, factory=self.factory, role=Role.PRODUCTION_OPERATOR)
        self.admin = User.objects.create_superuser("boss", password="x")
        self.client.force_login(self.op)

    def make(self, qty="100"):
        plan, _ = S.create_plan(user=self.op, factory=self.factory, production_date=self.day,
                                lines=[(self.v["Bread 400 gm"], D(qty))])
        return plan

    def complete(self, plan):
        S.start_production(user=self.op, plan=plan)
        est = S.estimate_plan(plan)[0]
        S.record_actuals(user=self.op, plan=plan, materials=dict(est.materials), labour=est.standard["labour"],
                         electricity=est.standard["electricity"], other=est.standard["other"],
                         outputs={ln.id: (ln.planned_qty, D("0")) for ln in plan.lines.all()})
        S.complete_production(user=self.op, plan=plan)

    def test_three_per_page_and_lists_page_separately(self):
        for _ in range(7):
            self.make()
        for _ in range(4):
            self.complete(self.make())
        page = self.client.get("/").content.decode()
        self.assertEqual(page.count('class="card plan'), 6)          # 3 running + 3 completed
        self.assertIn("পাতা (Page) <b>1</b> / 3", page)              # 7 running -> 3 pages
        self.assertIn("পাতা (Page) <b>1</b> / 2", page)              # 4 completed -> 2 pages
        page = self.client.get("/?r=3&d=2").content.decode()
        self.assertEqual(page.count('class="card plan'), 2)          # 1 running + 1 completed
        self.assertIn("?r=2&amp;d=2", page)                         # Prev keeps the other list's page

    def test_card_has_view_edit_history(self):
        self.make()
        page = self.client.get("/").content.decode()
        for t in ("দেখুন (View)", "বদলান (Edit)", "ইতিহাস (History)"):
            self.assertIn(t, page)

    def test_draft_edit_logs_who_when_what(self):
        plan = self.make("100")
        self.client.post(f"/p/{plan.pk}/edit/", {"action": "draft", "date": "2026-11-08",
                                                 "factory": self.factory.id,
                                                 f"qty_{self.v['Bread 400 gm'].id}": "150",
                                                 f"qty_{self.v['Cake 300 gm'].id}": "20"})
        log = AuditLog.objects.get(plan=plan, action="EDIT")
        self.assertEqual(log.user, self.op)
        self.assertIsNotNone(log.created_at)
        diff = {d["field"]: (d["old"], d["new"]) for d in log.changes["diff"]}
        self.assertEqual(diff["তারিখ (Date)"], ("2026-11-07", "2026-11-08"))
        self.assertEqual(diff["পরিমাণ (Qty) · Bread – 400 gm"], ("100", "150"))
        self.assertEqual(diff["পরিমাণ (Qty) · Cake – 300 gm"], ("—", "20"))
        page = self.client.get(f"/p/{plan.pk}/history/").content.decode()
        self.assertIn("পরিমাণ/তারিখ বদলানো হয়েছে (Edited)", page)
        self.assertIn("Rahim", page)
        self.assertIn("150", page)

    def test_saving_without_changes_adds_no_history(self):
        plan = self.make("100")
        S.replace_draft_lines(user=self.op, plan=plan, lines=[(self.v["Bread 400 gm"], D("100"))])
        self.assertFalse(AuditLog.objects.filter(plan=plan, action="EDIT").exists())

    def test_actual_edit_logged(self):
        plan = self.make()
        S.start_production(user=self.op, plan=plan)
        S.record_actuals(user=self.op, plan=plan, labour=D("700"), materials={self.mats["Flour"].id: D("26")})
        S.record_actuals(user=self.op, plan=plan, labour=D("650"))
        logs = list(AuditLog.objects.filter(plan=plan, action="ACTUALS").order_by("id"))
        self.assertEqual(len(logs), 2)
        last = {d["field"]: (d["old"], d["new"]) for d in logs[1].changes["diff"]}
        self.assertEqual(last, {"খরচ (Cost) · শ্রমিক (Labour)": ("700", "650")})

    def test_completed_locked_for_normal_user(self):
        plan = self.make()
        self.complete(plan)
        page = self.client.get("/").content.decode()
        self.assertIn("লক (Locked)", page)
        self.assertEqual(self.client.get(f"/p/{plan.pk}/correct/").status_code, 403)

    def test_super_admin_correction_needs_reason_and_is_logged(self):
        plan = self.make()
        self.complete(plan)
        self.client.force_login(self.admin)
        est = S.estimate_plan(plan)[0]
        data = {"labour": "999", "electricity": str(est.standard["electricity"]), "other": str(est.standard["other"])}
        for ln in plan.lines.all():
            data[f"prod_{ln.id}"], data[f"rej_{ln.id}"] = "100", "0"
        for mid, q in est.materials.items():
            data[f"mat_{mid}"] = str(q)
        page = self.client.post(f"/p/{plan.pk}/correct/", dict(data, reason="")).content.decode()
        self.assertIn("কারণ", page)
        self.assertFalse(AuditLog.objects.filter(plan=plan, action="CORRECTION").exists())

        r = self.client.post(f"/p/{plan.pk}/correct/", dict(data, reason="বিল পরে এসেছে"))
        self.assertRedirects(r, f"/p/{plan.pk}/history/")
        log = AuditLog.objects.get(plan=plan, action="CORRECTION")
        self.assertEqual((log.user, log.reason), (self.admin, "বিল পরে এসেছে"))
        diff = {d["field"]: (d["old"], d["new"]) for d in log.changes["diff"]}
        self.assertEqual(diff["খরচ (Cost) · শ্রমিক (Labour)"], ("500", "999"))
        page = self.client.get(f"/p/{plan.pk}/history/").content.decode()
        self.assertIn("বিল পরে এসেছে", page)
        self.assertIn("সংশোধন", page)

    def test_history_shows_whole_life_newest_first(self):
        plan = self.make()
        self.complete(plan)
        actions = list(AuditLog.objects.filter(plan=plan).order_by("-created_at", "-id").values_list("action", flat=True))
        self.assertEqual(actions, ["COMPLETE", "ACTUALS", "START_PRODUCTION", "CREATE"])
        page1 = self.client.get(f"/p/{plan.pk}/history/").content.decode()
        self.assertIn("উৎপাদন শেষ (Completed)", page1)         # newest on page 1 (3 per page)
        self.assertNotIn("তৈরি করা হয়েছে (Created)", page1)
        self.assertIn("তৈরি করা হয়েছে (Created)", self.client.get(f"/p/{plan.pk}/history/?page=2").content.decode())


class ManyProductsFormTests(TestCase):
    def setUp(self):
        self.factory, self.mats, self.v = load_sample()
        self.client.force_login(User.objects.create_superuser("s", password="x"))

    def test_search_and_filter_appear_only_when_many_products(self):
        page = self.client.get("/new/").content.decode()
        self.assertNotIn('id="q"', page)                       # 4 products: no search needed
        from apps.catalog.models import ProductVariant
        bread = self.v["Bread 400 gm"].product
        for g in ("200 gm", "600 gm", "800 gm"):
            ProductVariant.objects.create(product=bread, name=g)
        page = self.client.get("/new/").content.decode()
        self.assertIn("পণ্য খুঁজুন (Search product)", page)
        self.assertIn("শুধু লেখা গুলো (Filled only)", page)
        self.assertEqual(page.count('class="grp"'), 3)          # Bon Ruti, Bread, Cake headers
        self.assertEqual(page.count('data-name='), 7)



class HomeFilterTests(TestCase):
    """The home finder is just plan ID + date."""

    def setUp(self):
        import datetime
        self.factory, self.mats, self.v = load_sample()
        self.u = User.objects.create_superuser("s", password="x")
        self.client.force_login(self.u)
        def mk(day, variant):
            plan = S.create_plan(user=self.u, factory=self.factory, production_date=day,
                                 lines=[(self.v[variant], D("10"))])[0]
            S.start_production(user=self.u, plan=plan)
            est = S.estimate_plan(plan)[0]
            S.record_actuals(user=self.u, plan=plan, materials=dict(est.materials), labour=D("0"),
                             electricity=D("0"), other=D("0"),
                             outputs={ln.id: (ln.planned_qty, D("0")) for ln in plan.lines.all()})
            return S.complete_production(user=self.u, plan=plan)
        self.p1 = mk(datetime.date(2026, 11, 1), "Bread 400 gm")
        self.p2 = mk(datetime.date(2026, 11, 5), "Cake 300 gm")
        self.p3 = mk(datetime.date(2026, 11, 7), "Bread 400 gm")

    def test_finder_sits_in_completed_heading_and_running_list_is_untouched(self):
        import datetime
        draft = S.create_plan(user=self.u, factory=self.factory, production_date=datetime.date(2026, 10, 1),
                              lines=[(self.v["Bread 400 gm"], D("1"))])[0]
        page = self.client.get("/?q=zzz").content.decode()
        self.assertIn(draft.plan_no, page)                       # running list not filtered
        self.assertLess(page.index("শেষ হয়েছে (Completed)"), page.index('name="q"'))

    def shown(self, qs):
        page = self.client.get("/" + qs).content.decode()
        return {p.plan_no for p in (self.p1, self.p2, self.p3) if p.plan_no in page}

    def test_only_id_and_date_fields(self):
        page = self.client.get("/").content.decode()
        self.assertIn('name="q"', page)
        self.assertIn('name="date"', page)
        for gone in ('name="from"', 'name="to"', 'name="product"', "এই সপ্তাহ"):
            self.assertNotIn(gone, page)

    def test_by_date(self):
        self.assertEqual(self.shown("?date=2026-11-05"), {self.p2.plan_no})

    def test_by_id(self):
        self.assertEqual(self.shown(f"?q={self.p3.plan_no}"), {self.p3.plan_no})
        self.assertEqual(self.shown("?q=20261105"), {self.p2.plan_no})   # part of the ID works too

    def test_clear_and_nothing_found(self):
        page = self.client.get("/?q=zzz").content.decode()
        self.assertIn("কিছু পাওয়া যায়নি", page)
        self.assertIn('title="মুছুন (Clear)"', page)
        self.assertNotIn('title="মুছুন (Clear)"', self.client.get("/").content.decode())

    def test_columns_cannot_be_pushed_wider(self):
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)", self.client.get("/").content.decode())

    def test_form_result_panel_has_more_hint_and_bottom_space(self):
        page = self.client.post("/new/", {"action": "calc", "date": "2026-11-07", "factory": self.factory.id,
                                          f"qty_{self.v['Bread 400 gm'].id}": "100"}).content.decode()
        self.assertIn("নিচে আরো আছে (Scroll for more)", page)
        self.assertNotIn(".wide .wrap { padding-bottom: 24px; }", page)



class PrintTests(TestCase):
    """One print sheet per stage."""

    def setUp(self):
        import datetime
        self.factory, self.mats, self.v = load_sample()
        self.u = User.objects.create_superuser("s", password="x")
        self.client.force_login(self.u)
        self.plan, _ = S.create_plan(user=self.u, factory=self.factory, production_date=datetime.date(2026, 11, 7),
                                     lines=[(self.v["Bread 400 gm"], D("500")), (self.v["Cake 300 gm"], D("2000"))])

    def sheet(self, auto=False):
        return self.client.get(f"/p/{self.plan.pk}/print/" + ("?auto=1" if auto else "")).content.decode()

    def test_draft_sheet(self):
        page = self.sheet()
        self.assertIn("উৎপাদন পরিকল্পনা", page)
        self.assertIn(self.plan.plan_no, page)
        self.assertIn("140,335.00", page)        # Bread 17935 + Cake 122400
        self.assertIn("প্রস্তুতকারী", page)       # signature line
        auto_js = "setTimeout(function () { window.print(); }"
        self.assertNotIn(auto_js, page)              # only ?auto=1 opens the print box by itself
        self.assertIn(auto_js, self.sheet(auto=True))

    def test_running_sheet_has_blank_columns_to_fill(self):
        S.start_production(user=self.u, plan=self.plan)
        page = self.sheet()
        self.assertIn("কাঁচামাল চাহিদাপত্র", page)
        self.assertIn("দেওয়া হয়েছে (Issued)", page)
        self.assertIn('class="blank"', page)

    def test_completed_sheet_has_variance(self):
        S.start_production(user=self.u, plan=self.plan)
        est = S.estimate_plan(self.plan)[0]
        mats = dict(est.materials)
        mats[self.mats["Flour"].id] = D("401.5")
        S.record_actuals(user=self.u, plan=self.plan, materials=mats, labour=D("19425"),
                         electricity=D("5550"), other=D("3700"),
                         outputs={ln.id: (ln.planned_qty, D("10") if ln.variant == self.v["Bread 400 gm"] else D("40"))
                                  for ln in self.plan.lines.all()})
        S.complete_production(user=self.u, plan=self.plan)
        page = self.sheet()
        self.assertIn("উৎপাদন ও খরচ প্রতিবেদন", page)
        self.assertIn("140,335.00", page)   # estimated
        # actual: bread 18872.5 (extra 12.5 KG flour, labour +5%) + cake 124760 (extra 24 KG flour)
        self.assertIn("143,632.50", page)
        self.assertIn("+3,297.50", page)    # variance = 143632.5 - 140335
        self.assertIn("38.52", page)        # bread actual per good piece = 18872.5 / 490

    def test_print_button_on_cards_and_detail(self):
        self.assertIn(f"/p/{self.plan.pk}/print/?auto=1", self.client.get("/").content.decode())  # draft card
        self.assertIn(f"/p/{self.plan.pk}/print/?auto=1", self.client.get(f"/p/{self.plan.pk}/").content.decode())

    def test_other_factory_cannot_print(self):
        other_user = User.objects.create_user("o", password="x")
        self.client.force_login(other_user)
        self.assertEqual(self.client.get(f"/p/{self.plan.pk}/print/").status_code, 403)



class AppNameTests(TestCase):
    def test_bakehisab_name_everywhere(self):
        self.assertIn("বেকহিসাব", self.client.get("/login/").content.decode())
        u = User.objects.create_superuser("n", password="x")
        self.client.force_login(u)
        for url in ("/", "/admin/"):
            page = self.client.get(url).content.decode()
            self.assertIn("বেকহিসাব", page, url)
            self.assertIn("BakeHisab", page, url)
            self.assertNotIn("বেকারি হিসাব", page, url)
