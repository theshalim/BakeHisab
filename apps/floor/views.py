"""
Simple Bangla screens for factory-floor users (Production Manager / Operator).

The whole job is 3 steps:
  1. নতুন উৎপাদন: type quantities -> see the calculation -> "উৎপাদন শুরু করুন"
  2. (production runs)
  3. "শেষ করুন": form is pre-filled with the estimate; change only what differed.

Every edit is logged (who, when, field: old -> new) and shown on the History page.
All rules live in apps/production/services.py — these views only collect input.
"""
import uuid
from datetime import date as _date
from decimal import Decimal, InvalidOperation

from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import urlencode

from apps.catalog.models import ProductVariant, RawMaterial
from apps.costing import engine as E
from apps.costing.engine import CalculationError
from apps.factories.models import Factory, user_factory_ids
from apps.production import services as S
from apps.production.models import ProductionPlan, Status

USER_ERRORS = (CalculationError, S.StateError, PermissionDenied)
PER_PAGE = 3

STATUS_BN = {
    Status.DRAFT: ("খসড়া (Draft)", "draft"),
    Status.IN_PRODUCTION: ("চলছে (Running)", "running"),
    Status.COMPLETED: ("শেষ (Completed)", "done"),
}

ACTION_BN = {
    "CREATE": "তৈরি করা হয়েছে (Created)",
    "EDIT": "পরিমাণ/তারিখ বদলানো হয়েছে (Edited)",
    "START_PRODUCTION": "উৎপাদন শুরু (Production started)",
    "ACTUALS": "আসল হিসাব সেভ (Actuals saved)",
    "COMPLETE": "উৎপাদন শেষ (Completed)",
    "CORRECTION": "সংশোধন — শেষ হওয়ার পর (Correction)",
}


def _factories(user):
    ids = user_factory_ids(user, S.PLAN_EDIT_ROLES)
    qs = Factory.objects.filter(is_active=True)
    return qs if ids is None else qs.filter(id__in=ids)


def _plan_for(user, pk):
    plan = get_object_or_404(ProductionPlan.objects.select_related("factory"), pk=pk)
    if not _factories(user).filter(pk=plan.factory_id).exists():
        raise PermissionDenied
    return plan


def _dec(raw):
    raw = (raw or "").strip().replace(",", "")
    if raw == "":
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise CalculationError(f"'{raw}' সংখ্যা নয় (not a number)।")


def can_edit(user, plan) -> bool:
    """Draft/Running: production users of the factory. Completed: Super Admin only (with a reason)."""
    if plan.status == Status.COMPLETED:
        return user.is_superuser
    return True  # _plan_for already checked factory access


def edit_url(plan):
    from django.urls import reverse
    if plan.status == Status.DRAFT:
        return reverse("floor:edit", args=[plan.pk])
    if plan.status == Status.IN_PRODUCTION:
        return reverse("floor:finish", args=[plan.pk])
    return reverse("floor:correct", args=[plan.pk])


# --------------------------------------------------------------------------
NO_CATEGORY = "অন্যান্য (Other)"


def _grouped(rows):
    """Sort material rows by category (admin's sort order), adding 'category' and a subtotal per group."""
    cats = {m.id: m.category for m in RawMaterial.objects.filter(id__in=[r["id"] for r in rows])
            .select_related("category")}
    for r in rows:
        c = cats.get(r["id"])
        r["category"] = c.name if c else NO_CATEGORY
        r["_key"] = (c.sort_order if c else 10**6, r["category"], r["name"])
    rows.sort(key=lambda r: r["_key"])
    totals = {}
    for r in rows:
        if "cost" in r:
            totals[r["category"]] = totals.get(r["category"], 0) + r["cost"]
    for r in rows:
        r["category_total"] = totals.get(r["category"])
    return rows


def _calc_context(est, inp, names):
    """Turn engine output into simple rows for the templates."""
    info = inp.material_info
    materials = _grouped([
        {"id": m, "name": info[m][0], "qty": E.display_qty(q, info[m][2]), "unit": info[m][1],
         "cost": est.material_costs[m]}
        for m, q in est.materials.items()
    ])
    products = [
        {"name": names[ln.line_key], "qty": ln.per_piece_qty, "cost": ln.total_cost,
         "per_piece": ln.cost_per_piece}
        for ln in est.lines
    ]
    return {"est": est, "materials": materials, "products": products}


def _page(request, qs, param):
    """One paginated list. Keeps the other list's page number in the links."""
    page = Paginator(qs, PER_PAGE).get_page(request.GET.get(param))

    def link(n):
        q = request.GET.copy()
        q[param] = n
        return "?" + q.urlencode()

    page.prev_url = link(page.previous_page_number()) if page.has_previous() else None
    page.next_url = link(page.next_page_number()) if page.has_next() else None
    return page


# --------------------------------------------------------------------------
@login_required
def home(request):
    factories = _factories(request.user)
    plans = (ProductionPlan.objects.filter(factory__in=factories).select_related("factory")
             .prefetch_related("lines__variant__product").order_by("-production_date", "-id"))
    running = _page(request, plans.exclude(status=Status.COMPLETED), "r")
    done_qs, filt = _apply_filters(request, plans.filter(status=Status.COMPLETED), factories)
    done = _page(request, done_qs, "d")

    def rows(page):
        out = []
        for p in page:
            label, css = STATUS_BN[p.status]
            out.append({"plan": p, "label": label, "css": css,
                        "summary": ", ".join(f"{ln.variant} {ln.planned_qty:.0f}" for ln in p.lines.all()),
                        "edit_url": edit_url(p) if can_edit(request.user, p) else None,
                        "history_count": p.audit_logs.count()})
        return out

    return render(request, "floor/home.html", {
        "running": running, "running_rows": rows(running),
        "done": done, "done_rows": rows(done),
        "no_factory": not factories.exists(),
        "f": filt,
    })


def _apply_filters(request, plans, factories):
    """Finder on the Completed list: plan ID (PRD-...) and/or production date. Nothing more."""
    f = {"q": request.GET.get("q", "").strip(), "date": request.GET.get("date", "")}
    if f["q"]:
        plans = plans.filter(plan_no__icontains=f["q"])
    if f["date"]:
        try:
            plans = plans.filter(production_date=_date.fromisoformat(f["date"]))
        except ValueError:
            f["date"] = ""
    f["active"] = bool(f["q"] or f["date"])
    return plans, f


@login_required
def plan_form(request, pk=None):
    """New plan, or edit a Draft. One page: every product with a quantity box."""
    plan = _plan_for(request.user, pk) if pk else None
    if plan and plan.status != Status.DRAFT:
        return redirect("floor:detail", pk=plan.pk)

    factories = list(_factories(request.user))
    if not factories:
        raise PermissionDenied
    if plan:
        factory = plan.factory
    else:
        fid = request.POST.get("factory") or request.GET.get("factory")
        factory = next((f for f in factories if str(f.id) == fid), factories[0])

    variants = list(ProductVariant.objects.filter(
        product__factory=factory, is_active=True, product__is_active=True).select_related("product"))
    existing = {ln.variant_id: ln.planned_qty for ln in plan.lines.all()} if plan else {}

    ctx = {"plan": plan, "factory": factory, "factories": factories,
           "date": plan.production_date if plan else timezone.localdate(),
           "local_uuid": request.POST.get("local_uuid") or uuid.uuid4()}
    error = None

    if request.method == "POST":
        action = request.POST.get("action", "calc")
        try:
            day = _date.fromisoformat(request.POST.get("date") or str(timezone.localdate()))
            ctx["date"] = day
            pairs = []
            for v in variants:
                q = _dec(request.POST.get(f"qty_{v.id}"))
                existing[v.id] = q
                if q is not None and q != 0:
                    pairs.append((v, q))
            if not pairs:
                raise CalculationError("অন্তত একটি product-এর পরিমাণ লিখুন (Enter at least one quantity)।")
            S._validate_lines(factory, pairs)

            if action == "calc":
                inp = S.live_inputs_for([(v.id, v, q) for v, q in pairs])
                est = E.estimate(inp.lines, inp.prices)
                ctx.update(_calc_context(est, inp, {v.id: str(v) for v, _ in pairs}))
                ctx["calculated"] = True
            else:
                if plan:
                    S.replace_draft_lines(user=request.user, plan=plan, lines=pairs, production_date=day)
                else:
                    plan, _ = S.create_plan(user=request.user, factory=factory, production_date=day,
                                            lines=pairs, local_uuid=ctx["local_uuid"])
                if action == "start" and plan.status == Status.DRAFT:
                    S.start_production(user=request.user, plan=plan)
                return redirect("floor:detail", pk=plan.pk)
        except USER_ERRORS as e:
            error = str(e)

    ctx["rows"] = [{"v": v, "qty": existing.get(v.id)} for v in variants]
    ctx["error"] = error
    return render(request, "floor/plan_form.html", ctx)


@login_required
def plan_detail(request, pk):
    plan = _plan_for(request.user, pk)
    error = None
    if request.method == "POST" and request.POST.get("action") == "start":
        try:
            S.start_production(user=request.user, plan=plan)
            return redirect("floor:detail", pk=plan.pk)
        except USER_ERRORS as e:
            error = str(e)

    names = {ln.id: str(ln.variant) for ln in plan.lines.select_related("variant__product")}
    label, css = STATUS_BN[plan.status]
    ctx = {"plan": plan, "label": label, "css": css, "error": error,
           "edit_url": edit_url(plan) if can_edit(request.user, plan) else None,
           "history_count": plan.audit_logs.count()}
    try:
        est, inp = S.estimate_plan(plan)
        ctx.update(_calc_context(est, inp, names))
    except CalculationError as e:
        ctx["error"] = str(e)

    if plan.status == Status.COMPLETED:
        est, act, _ = S.actual_plan(plan)
        ctx["variance"] = S.variance_rows(est, act)
        ctx["actual_products"] = [
            {"name": names[ln.line_key], "good": ln.per_piece_qty, "cost": ln.total_cost,
             "per_piece": ln.cost_per_piece, "est_per_piece": est.line(ln.line_key).cost_per_piece}
            for ln in act.lines
        ]
    return render(request, "floor/plan_detail.html", ctx)


# --------------------------------------------------------------------------
# Actual entry (Running) and correction (Completed, Super Admin) share one form
# --------------------------------------------------------------------------
def _read_actuals(request, est, lines):
    return dict(
        materials={mid: _dec(request.POST.get(f"mat_{mid}")) for mid in est.materials},
        labour=_dec(request.POST.get("labour")), electricity=_dec(request.POST.get("electricity")),
        other=_dec(request.POST.get("other")),
        outputs={ln.id: (_dec(request.POST.get(f"prod_{ln.id}")), _dec(request.POST.get(f"rej_{ln.id}")) or 0)
                 for ln in lines},
    )


def _actual_form_ctx(request, plan, est, inp):
    lines = list(plan.lines.select_related("variant__product"))
    saved_mats = {a.material_id: a.quantity for a in plan.actual_materials.all()}

    def pick(saved, fallback):
        return saved if saved is not None else fallback

    info = inp.material_info
    ctx = {
        "plan": plan,
        "products": [{"ln": ln, "produced": pick(ln.produced_qty, ln.planned_qty),
                      "rejected": pick(ln.rejected_qty, 0)} for ln in lines],
        "materials": _grouped([{"id": mid, "name": info[mid][0], "unit": info[mid][1],
                                "est": E.display_qty(q, info[mid][2]),
                                "value": pick(saved_mats.get(mid), E.display_qty(q, info[mid][2])),
                                "step": "1" if info[mid][2] else "any"}
                               for mid, q in est.materials.items()]),
        "costs": [{"key": c, "label": S.STD_LABELS[c], "est": E.money(est.standard[c]),
                   "value": pick(getattr(plan, f"actual_{c}"), E.money(est.standard[c]))}
                  for c in E.STANDARD_COMPONENTS],
    }
    if request.method == "POST":  # keep what the user typed on error
        for p in ctx["products"]:
            p["produced"] = request.POST.get(f"prod_{p['ln'].id}", p["produced"])
            p["rejected"] = request.POST.get(f"rej_{p['ln'].id}", p["rejected"])
        for m in ctx["materials"]:
            m["value"] = request.POST.get(f"mat_{m['id']}", m["value"])
        for c in ctx["costs"]:
            c["value"] = request.POST.get(c["key"], c["value"])
        ctx["reason"] = request.POST.get("reason", "")
    return ctx


@login_required
def finish(request, pk):
    """Actual entry, pre-filled with the estimate. Change only what was different."""
    plan = _plan_for(request.user, pk)
    if plan.status != Status.IN_PRODUCTION:
        return redirect("floor:detail", pk=plan.pk)
    inp = S.snapshot_inputs(plan)
    est = E.estimate(inp.lines, inp.prices)
    error, warnings = None, []

    if request.method == "POST":
        try:
            S.record_actuals(user=request.user, plan=plan,
                             **_read_actuals(request, est, list(plan.lines.all())))
            if request.POST.get("action") == "complete":
                warnings = S.actual_warnings(plan)
                if not warnings or request.POST.get("confirm") == "1":
                    S.complete_production(user=request.user, plan=plan)
                    return redirect("floor:detail", pk=plan.pk)
            else:
                return redirect("floor:home")
        except USER_ERRORS as e:
            error = str(e)

    ctx = _actual_form_ctx(request, plan, est, inp)
    ctx.update(error=error, warnings=warnings)
    return render(request, "floor/finish.html", ctx)


@login_required
def correct(request, pk):
    """Change a Completed plan. Super Admin only, reason required, fully logged (BR-11)."""
    plan = _plan_for(request.user, pk)
    if plan.status != Status.COMPLETED:
        return redirect("floor:detail", pk=plan.pk)
    if not request.user.is_superuser:
        raise PermissionDenied("শেষ হওয়া উৎপাদন শুধু Super Admin বদলাতে পারেন।")
    inp = S.snapshot_inputs(plan)
    est = E.estimate(inp.lines, inp.prices)
    error = None

    if request.method == "POST":
        try:
            S.correct_completed(user=request.user, plan=plan, reason=request.POST.get("reason", ""),
                                **_read_actuals(request, est, list(plan.lines.all())))
            return redirect("floor:history", pk=plan.pk)
        except USER_ERRORS as e:
            error = str(e)

    ctx = _actual_form_ctx(request, plan, est, inp)
    ctx.update(error=error, warnings=[], correcting=True)
    return render(request, "floor/finish.html", ctx)


@login_required
def history(request, pk):
    plan = _plan_for(request.user, pk)
    page = _page(request, plan.audit_logs.select_related("user").order_by("-created_at", "-id"), "page")
    page.per_page_note = None
    entries = []
    for log in page:
        ch = log.changes or {}
        summary = []
        if log.action == "COMPLETE" and "actual_total" in ch:
            summary.append(f"হিসাব (Estimated) {ch.get('estimated_total')} Tk → আসল (Actual) {ch.get('actual_total')} Tk")
        entries.append({
            "log": log,
            "action": ACTION_BN.get(log.action, log.action),
            "css": {"CORRECTION": "bad", "COMPLETE": "good", "START_PRODUCTION": "info"}.get(log.action, ""),
            "diff": ch.get("diff", []),
            "summary": summary,
        })
    label, css = STATUS_BN[plan.status]
    return render(request, "floor/history.html", {
        "plan": plan, "label": label, "css": css, "entries": entries, "page": page,
    })


# --------------------------------------------------------------------------
# Print / PDF — one sheet per stage
# --------------------------------------------------------------------------
PRINT_TITLES = {
    Status.DRAFT: ("উৎপাদন পরিকল্পনা", "Production Plan",
                   "খসড়া — এখনো শুরু হয়নি। দাম ও রেসিপি আজকের হিসাবে।"),
    Status.IN_PRODUCTION: ("কাঁচামাল চাহিদাপত্র ও উৎপাদন শিট", "Material Requisition & Production Sheet",
                           "স্টোর থেকে কাঁচামাল দেওয়ার সময় ও উৎপাদন শেষে খালি ঘরগুলো হাতে লিখুন।"),
    Status.COMPLETED: ("উৎপাদন ও খরচ প্রতিবেদন", "Production & Cost Report",
                       "হিসাব (Estimated) ও আসল (Actual) খরচের তুলনা।"),
}


@login_required
def print_plan(request, pk):
    """A4 print sheet. Opens the browser's print box at once; 'Save as PDF' there gives a PDF."""
    plan = _plan_for(request.user, pk)
    lines = list(plan.lines.select_related("variant__product"))
    names = {ln.id: str(ln.variant) for ln in lines}
    est, inp = S.estimate_plan(plan)
    title_bn, title_en, note = PRINT_TITLES[plan.status]
    label, css = STATUS_BN[plan.status]
    ctx = {"plan": plan, "label": label, "css": css, "title_bn": title_bn, "title_en": title_en, "note": note,
           "printed_at": timezone.localtime(), "auto": request.GET.get("auto") == "1",
           "lines": lines}
    ctx.update(_calc_context(est, inp, names))

    if plan.status == Status.COMPLETED:
        est, act, inp = S.actual_plan(plan)
        info = inp.material_info
        ctx["mat_rows"] = _grouped([
            {"id": m, "name": info[m][0], "unit": info[m][1],
             "est": E.display_qty(q, info[m][2]), "act": E.display_qty(act.materials.get(m, 0), info[m][2]),
             "est_cost": est.material_costs[m], "cost": act.material_costs.get(m, 0)}
            for m, q in est.materials.items()
        ])
        ctx["variance"] = S.variance_rows(est, act)
        ctx["out_rows"] = [
            {"name": names[ln.id], "planned": ln.planned_qty, "produced": ln.produced_qty,
             "rejected": ln.rejected_qty, "good": ln.good_qty,
             "est_pp": est.line(ln.id).cost_per_piece, "act_pp": act.line(ln.id).cost_per_piece}
            for ln in lines
        ]
    return render(request, "floor/print.html", ctx)


class LoginView(auth_views.LoginView):
    """'মনে রাখুন (Remember me)' unticked -> logged out when the browser closes."""
    template_name = "floor/login.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.request.POST.get("remember") != "1":
            self.request.session.set_expiry(0)
        return response
