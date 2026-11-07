"""
All production state changes go through here (API, admin and tests alike),
so business rules live in one place.
"""
from __future__ import annotations

import uuid as uuid_mod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import ProductVariant
from apps.costing import engine
from apps.costing.engine import CalculationError, LineInput, LineOutput, RecipeItem
from apps.factories.models import PRODUCTION_ROLES, Factory, Role, user_has_role

from .models import (
    ActualMaterial, AuditLog, LineRecipeItemSnapshot, PlanPriceSnapshot,
    ProductionLine, ProductionPlan, Status,
)

PLAN_EDIT_ROLES = PRODUCTION_ROLES | {Role.FACTORY_ADMIN}


def is_whole(value) -> bool:
    d = Decimal(value)
    return d == d.to_integral_value()


class StateError(ValueError):
    """Action not allowed in the plan's current status."""


def _sync(original: ProductionPlan, fresh: ProductionPlan) -> ProductionPlan:
    """Refresh the caller's object so a stale in-memory status can't be used."""
    fresh.refresh_from_db()
    if original is not fresh:
        original.refresh_from_db()
    return fresh


def _require(user, factory, roles):
    if user is None or not user_has_role(user, factory, roles):
        raise PermissionDenied("এই factory-তে এই কাজের অনুমতি আপনার নেই। [BR-12]")


# --------------------------------------------------------------------------
# Plan number (D-07)
# --------------------------------------------------------------------------
def _assign_plan_no(plan: ProductionPlan):
    """PRD-YYYYMMDD-NNN, sequence per factory + production date."""
    Factory.objects.select_for_update().get(pk=plan.factory_id)  # serialize per factory
    prefix = f"PRD-{plan.production_date:%Y%m%d}-"
    last = (
        ProductionPlan.objects.filter(factory_id=plan.factory_id, plan_no__startswith=prefix)
        .order_by("-plan_no").values_list("plan_no", flat=True).first()
    )
    seq = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    plan.plan_no = f"{prefix}{seq:03d}"


# --------------------------------------------------------------------------
# Edit history: every change is logged as "field: old -> new" with user + time
# --------------------------------------------------------------------------
def _fmt(v):
    if v is None:
        return "—"
    if isinstance(v, Decimal):
        return f"{v.normalize():f}"
    return str(v)


def plan_state(plan: ProductionPlan) -> dict:
    """Readable snapshot of everything a user can edit, as {label: value}."""
    plan = ProductionPlan.objects.get(pk=plan.pk)
    st = {"তারিখ (Date)": plan.production_date.isoformat()}
    for ln in plan.lines.select_related("variant__product").order_by("id"):
        st[f"পরিমাণ (Qty) · {ln.variant}"] = _fmt(ln.planned_qty)
    if plan.status != Status.DRAFT:
        for ln in plan.lines.select_related("variant__product").order_by("id"):
            st[f"বানানো (Produced) · {ln.variant}"] = _fmt(ln.produced_qty)
            st[f"বাতিল (Rejected) · {ln.variant}"] = _fmt(ln.rejected_qty)
        for a in plan.actual_materials.select_related("material__unit").order_by("material__name"):
            st[f"কাঁচামাল (Material) · {a.material.name}"] = f"{_fmt(a.quantity)} {a.material.unit.code}"
        for comp, label in STD_LABELS.items():
            st[f"খরচ (Cost) · {label}"] = _fmt(getattr(plan, f"actual_{comp}"))
    return st


def diff_states(before: dict, after: dict) -> list[dict]:
    keys = list(before) + [k for k in after if k not in before]
    return [{"field": k, "old": before.get(k, "—"), "new": after.get(k, "—")}
            for k in keys if before.get(k, "—") != after.get(k, "—")]


def _log(plan, user, action, before=None, reason="", **extra):
    """Write one history row. Returns None (and writes nothing) if an edit changed nothing."""
    diff = diff_states(before or {}, plan_state(plan)) if before is not None else []
    if before is not None and not diff and action in ("EDIT", "ACTUALS"):
        return None
    return AuditLog.objects.create(plan=plan, user=user, action=action, reason=reason,
                                   changes={"diff": diff, **extra})


# --------------------------------------------------------------------------
# Create / edit draft
# --------------------------------------------------------------------------
@transaction.atomic
def create_plan(*, user, factory: Factory, production_date: date,
                lines: list[tuple[ProductVariant, Decimal]], local_uuid=None) -> tuple[ProductionPlan, bool]:
    """
    Returns (plan, created). Same local_uuid sent again -> the existing plan
    is returned unchanged, never a duplicate (BR-13).
    """
    _require(user, factory, PLAN_EDIT_ROLES)
    if local_uuid:
        existing = ProductionPlan.objects.filter(local_uuid=local_uuid).first()
        if existing:
            if existing.factory_id != factory.id:
                raise PermissionDenied("এই UUID অন্য factory-র plan-এর।")
            return existing, False
    _validate_lines(factory, lines)

    plan = ProductionPlan(factory=factory, production_date=production_date, created_by=user,
                          local_uuid=local_uuid or uuid_mod.uuid4())
    _assign_plan_no(plan)
    plan.save()
    for variant, qty in lines:
        ProductionLine.objects.create(plan=plan, variant=variant, planned_qty=Decimal(qty))
    _log(plan, user, "CREATE", before={})
    return plan, True


@transaction.atomic
def replace_draft_lines(*, user, plan: ProductionPlan, lines: list[tuple[ProductVariant, Decimal]],
                        production_date: date | None = None):
    original = plan
    plan = ProductionPlan.objects.select_for_update().get(pk=plan.pk)
    _require(user, plan.factory, PLAN_EDIT_ROLES)
    if plan.status != Status.DRAFT:
        raise StateError("Production শুরু হওয়ার পর plan-এর quantity বদলানো যাবে না। [D-09]")
    _validate_lines(plan.factory, lines)
    before = plan_state(plan)
    if production_date and production_date != plan.production_date:
        plan.production_date = production_date
        plan.save(update_fields=["production_date", "updated_at"])
    plan.lines.all().delete()
    for variant, qty in lines:
        ProductionLine.objects.create(plan=plan, variant=variant, planned_qty=Decimal(qty))
    _log(plan, user, "EDIT", before=before)
    return _sync(original, plan)


def _validate_lines(factory, lines):
    if not lines:
        raise CalculationError("অন্তত একটি product যোগ করুন।")
    seen = set()
    for variant, qty in lines:
        if variant.product.factory_id != factory.id:
            raise PermissionDenied(f"{variant} এই factory-র product নয়।")
        if not (variant.is_active and variant.product.is_active):
            raise CalculationError(f"{variant} inactive। [BR-02]")
        if variant.id in seen:
            raise CalculationError(f"{variant} দুইবার যোগ করা হয়েছে।")
        seen.add(variant.id)
        if Decimal(qty) <= 0:
            raise CalculationError(f"{variant}: quantity 0-এর বেশি হতে হবে। [BR-05]")
        if not is_whole(qty):
            raise CalculationError(f"{variant}: পিসের সংখ্যা পূর্ণসংখ্যা হতে হবে।")


# --------------------------------------------------------------------------
# Engine inputs
# --------------------------------------------------------------------------
@dataclass
class EngineInputs:
    lines: list[LineInput]
    prices: dict  # material_id -> unit price
    material_info: dict  # material_id -> (name, unit_code, is_count)


def live_inputs(plan: ProductionPlan, day: date | None = None) -> EngineInputs:
    """Current active recipe + price (used while DRAFT, and at lock time)."""
    pairs = [(ln.id, ln.variant, ln.planned_qty) for ln in plan.lines.select_related("variant__product")]
    return live_inputs_for(pairs, day)


def live_inputs_for(pairs, day: date | None = None) -> EngineInputs:
    """pairs = [(line_key, variant, planned_qty)] — also used for an unsaved preview."""
    day = day or timezone.localdate()
    lines, prices, info = [], {}, {}
    for key, variant, qty in pairs:
        recipe = variant.active_recipe(day)
        if recipe is None:
            raise CalculationError(f"{variant}-এর কোনো recipe সেট করা নেই। Admin-কে জানান। [BR-03]")
        items = []
        for rl in recipe.lines.select_related("material__unit", "unit"):
            m = rl.material
            if m.id not in prices:
                price = m.price_on(day)
                if price is None:
                    raise CalculationError(f"{m.name}-এর দাম সেট করা নেই। Admin-কে জানান। [BR-07]")
                prices[m.id] = price
                info[m.id] = (m.name, m.unit.code, m.unit.is_count)
            items.append(RecipeItem(m.id, rl.quantity_in_material_unit()))
        lines.append(LineInput(
            line_key=key, planned_qty=Decimal(qty), batch_yield=recipe.batch_yield,
            items=items, labour_per_batch=recipe.labour_per_batch,
            electricity_per_batch=recipe.electricity_per_batch, other_per_batch=recipe.other_per_batch,
        ))
    return EngineInputs(lines, prices, info)


def snapshot_inputs(plan: ProductionPlan) -> EngineInputs:
    """Frozen recipe + price saved at lock time (BR-14)."""
    prices, info = {}, {}
    for s in plan.price_snapshots.select_related("material"):
        prices[s.material_id] = s.unit_price
        info[s.material_id] = (s.material.name, s.unit_code, s.unit_is_count)
    lines = []
    for line in plan.lines.prefetch_related("recipe_items"):
        lines.append(LineInput(
            line_key=line.id, planned_qty=line.planned_qty, batch_yield=line.batch_yield,
            items=[RecipeItem(i.material_id, i.quantity_per_batch) for i in line.recipe_items.all()],
            labour_per_batch=line.labour_per_batch, electricity_per_batch=line.electricity_per_batch,
            other_per_batch=line.other_per_batch,
        ))
    return EngineInputs(lines, prices, info)


def inputs_for(plan: ProductionPlan) -> EngineInputs:
    status = ProductionPlan.objects.values_list("status", flat=True).get(pk=plan.pk)
    return live_inputs(plan) if status == Status.DRAFT else snapshot_inputs(plan)


def estimate_plan(plan: ProductionPlan) -> tuple[engine.PlanCost, EngineInputs]:
    inp = inputs_for(plan)
    return engine.estimate(inp.lines, inp.prices), inp


# --------------------------------------------------------------------------
# DRAFT -> IN_PRODUCTION  (lock snapshot, D-06)
# --------------------------------------------------------------------------
@transaction.atomic
def start_production(*, user, plan: ProductionPlan) -> ProductionPlan:
    original = plan
    plan = ProductionPlan.objects.select_for_update().get(pk=plan.pk)
    _require(user, plan.factory, PLAN_EDIT_ROLES)
    if plan.status != Status.DRAFT:
        raise StateError("শুধু Draft plan-ই production-এ পাঠানো যায়।")

    day = timezone.localdate()
    inp = live_inputs(plan, day)
    engine.estimate(inp.lines, inp.prices)  # validate before locking

    for line in plan.lines.select_related("variant"):
        recipe = line.variant.active_recipe(day)
        line.recipe = recipe
        line.recipe_version = recipe.version
        line.batch_yield = recipe.batch_yield
        line.labour_per_batch = recipe.labour_per_batch
        line.electricity_per_batch = recipe.electricity_per_batch
        line.other_per_batch = recipe.other_per_batch
        line.save()
        for rl in recipe.lines.select_related("material__unit", "unit"):
            LineRecipeItemSnapshot.objects.create(
                line=line, material=rl.material, quantity_per_batch=rl.quantity_in_material_unit())
    for mid, price in inp.prices.items():
        _, unit_code, is_count = inp.material_info[mid]
        PlanPriceSnapshot.objects.create(plan=plan, material_id=mid, unit_code=unit_code,
                                         unit_is_count=is_count, unit_price=price)

    plan.status = Status.IN_PRODUCTION
    plan.locked_at = timezone.now()
    plan.locked_by = user
    plan.save()
    _log(plan, user, "START_PRODUCTION", prices={str(k): str(v) for k, v in inp.prices.items()})
    return _sync(original, plan)


# --------------------------------------------------------------------------
# Actuals (IN_PRODUCTION only; partial saves allowed)
# --------------------------------------------------------------------------
def _write_actuals(plan, materials, labour, electricity, other, outputs):
    snaps = {sn.material_id: sn for sn in plan.price_snapshots.select_related("material")}
    snap_materials = set(snaps)
    for mid, qty in (materials or {}).items():
        if mid not in snap_materials:
            raise CalculationError(f"Material {mid} এই plan-এর কোনো recipe-তে নেই।")
        if qty is None:
            ActualMaterial.objects.filter(plan=plan, material_id=mid).delete()
            continue
        if Decimal(qty) < 0:
            raise CalculationError("Actual quantity negative হতে পারবে না।")
        if snaps[mid].unit_is_count and not is_whole(qty):
            raise CalculationError(f"{snaps[mid].material.name} {snaps[mid].unit_code}-এ গোনা হয়, তাই পূর্ণসংখ্যা লিখুন।")
        ActualMaterial.objects.update_or_create(plan=plan, material_id=mid,
                                                defaults={"quantity": Decimal(qty)})
    for field, val in (("actual_labour", labour), ("actual_electricity", electricity),
                       ("actual_other", other)):
        if val is not None:
            if Decimal(val) < 0:
                raise CalculationError("Actual cost negative হতে পারবে না।")
            setattr(plan, field, Decimal(val))
    plan.save()

    lines = {ln.id: ln for ln in plan.lines.all()}
    for line_id, (produced, rejected) in (outputs or {}).items():
        line = lines.get(line_id)
        if line is None:
            raise CalculationError(f"Line {line_id} এই plan-এর নয়।")
        p = Decimal(produced) if produced is not None else line.produced_qty
        r = Decimal(rejected) if rejected is not None else line.rejected_qty
        if (p is not None and p < 0) or (r is not None and r < 0):
            raise CalculationError("Produced/Rejected negative হতে পারবে না।")
        if (p is not None and not is_whole(p)) or (r is not None and not is_whole(r)):
            raise CalculationError(f"{line.variant}: Produced/Rejected পূর্ণসংখ্যা হতে হবে।")
        if p is not None and r is not None and r > p:
            raise CalculationError(f"{line.variant}: Rejected, Produced-এর চেয়ে বেশি হতে পারবে না।")
        line.produced_qty, line.rejected_qty = p, r
        line.save()


@transaction.atomic
def record_actuals(*, user, plan: ProductionPlan, materials=None, labour=None,
                   electricity=None, other=None, outputs=None) -> ProductionPlan:
    """
    materials: {material_id: total qty}; outputs: {line_id: (produced, rejected)}.
    Anything left as None is kept as before.
    """
    original = plan
    plan = ProductionPlan.objects.select_for_update().get(pk=plan.pk)
    _require(user, plan.factory, PLAN_EDIT_ROLES)
    if plan.status != Status.IN_PRODUCTION:
        raise StateError("Actual এন্ট্রি শুধু In Production অবস্থায় করা যায়। [D-09]")
    before = plan_state(plan)
    _write_actuals(plan, materials, labour, electricity, other, outputs)
    _log(plan, user, "ACTUALS", before=before)
    return _sync(original, plan)


def actual_plan(plan: ProductionPlan) -> tuple[engine.PlanCost, engine.PlanCost, EngineInputs]:
    """(estimate, actual, inputs). Raises CalculationError if actuals incomplete."""
    if plan.status == Status.DRAFT:
        raise StateError("Draft plan-এর actual হিসাব হয় না।")
    inp = snapshot_inputs(plan)
    est = engine.estimate(inp.lines, inp.prices)
    mats = {a.material_id: a.quantity for a in plan.actual_materials.all()}
    std = {"labour": plan.actual_labour, "electricity": plan.actual_electricity,
           "other": plan.actual_other}
    outputs = {}
    for line in plan.lines.all():
        if line.produced_qty is None or line.rejected_qty is None:
            raise CalculationError(f"{line.variant}: Produced ও Rejected quantity দিন।")
        outputs[line.id] = LineOutput(line.produced_qty, line.rejected_qty)
    act = engine.actual(est, inp.prices, mats, std, outputs)
    return est, act, inp


# --------------------------------------------------------------------------
# IN_PRODUCTION -> COMPLETED
# --------------------------------------------------------------------------
@transaction.atomic
def complete_production(*, user, plan: ProductionPlan) -> ProductionPlan:
    original = plan
    plan = ProductionPlan.objects.select_for_update().get(pk=plan.pk)
    _require(user, plan.factory, PLAN_EDIT_ROLES)
    if plan.status != Status.IN_PRODUCTION:
        raise StateError("শুধু In Production plan complete করা যায়।")
    est, act, _ = actual_plan(plan)  # blocks completion if anything is missing
    plan.status = Status.COMPLETED
    plan.completed_at = timezone.now()
    plan.completed_by = user
    plan.save()
    _log(plan, user, "COMPLETE", estimated_total=str(engine.money(est.total_cost)),
         actual_total=str(engine.money(act.total_cost)))
    return _sync(original, plan)


# --------------------------------------------------------------------------
# Correction after completion — superuser only, always audited (BR-11)
# --------------------------------------------------------------------------
def _actuals_state(plan):
    return {
        "materials": {str(a.material_id): str(a.quantity) for a in plan.actual_materials.all()},
        "labour": str(plan.actual_labour), "electricity": str(plan.actual_electricity),
        "other": str(plan.actual_other),
        "outputs": {str(ln.id): [str(ln.produced_qty), str(ln.rejected_qty)] for ln in plan.lines.all()},
    }


@transaction.atomic
def correct_completed(*, user, plan: ProductionPlan, reason: str, materials=None, labour=None,
                      electricity=None, other=None, outputs=None) -> ProductionPlan:
    if not (user and user.is_superuser):
        raise PermissionDenied("Completed production শুধু Super Admin সংশোধন করতে পারেন। [BR-11]")
    if not (reason or "").strip():
        raise CalculationError("সংশোধনের কারণ লিখতে হবে।")
    original = plan
    plan = ProductionPlan.objects.select_for_update().get(pk=plan.pk)
    if plan.status != Status.COMPLETED:
        raise StateError("এই function শুধু Completed plan-এর জন্য।")
    before = _actuals_state(plan)
    before_readable = plan_state(plan)
    _write_actuals(plan, materials, labour, electricity, other, outputs)
    plan.refresh_from_db()
    actual_plan(plan)  # corrected data must still be complete & valid
    _log(plan, user, "CORRECTION", before=before_readable, reason=reason.strip(),
         raw_before=before, raw_after=_actuals_state(plan))
    return _sync(original, plan)


# --------------------------------------------------------------------------
# Checks & summaries for the screens
# --------------------------------------------------------------------------
STD_LABELS = {"labour": "শ্রমিক (Labour)", "electricity": "বিদ্যুৎ (Electricity)", "other": "অন্যান্য (Other)"}
BIG_DIFF = Decimal("0.20")  # warn if a material is 20% off the estimate


def actual_warnings(plan: ProductionPlan) -> list[str]:
    """
    Soft warnings before completing — they never block (D-09).
    Catches the usual typing mistakes: a cost left at 0, or a 10x typo.
    """
    inp = snapshot_inputs(plan)
    est = engine.estimate(inp.lines, inp.prices)
    out = []
    for comp, label in STD_LABELS.items():
        val = getattr(plan, f"actual_{comp}")
        if val is not None and val == 0 and est.standard[comp] > 0:
            out.append(f"{label} খরচ 0 লেখা হয়েছে, কিন্তু হিসাবে {engine.money(est.standard[comp])} Tk ধরা ছিল।")
    for a in plan.actual_materials.all():
        e = est.materials.get(a.material_id, Decimal(0))
        name, unit, is_count = inp.material_info[a.material_id]
        if e > 0 and abs(a.quantity - e) / e > BIG_DIFF:
            out.append(f"{name}: হিসাব ছিল {engine.display_qty(e, is_count)} {unit}, "
                       f"লেখা হয়েছে {engine.display_qty(a.quantity, is_count)} {unit} — অনেক পার্থক্য।")
    return out


def variance_rows(est: engine.PlanCost, act: engine.PlanCost) -> list[dict]:
    """Material vs each standard cost, separately — so a 0 labour can't hide a material overrun."""
    cmp = engine.compare(est, act)
    rows = [{"label": "কাঁচামাল (Raw material)", "v": cmp.material}]
    rows += [{"label": STD_LABELS[c], "v": cmp.standard[c]} for c in engine.STANDARD_COMPONENTS]
    rows.append({"label": "মোট (Total)", "v": cmp.total, "total": True})
    return rows
