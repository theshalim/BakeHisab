"""
Calculation engine — the core business logic (see DECISIONS.md).

Pure Python, no Django/database. Every number is a Decimal and nothing is
rounded inside a calculation (D-11). Rounding happens only in the display
helpers at the bottom of this file.

The server's result is the source of truth (D-08). The Flutter app may port
this file for offline preview; the same test cases in tests.py must pass there.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Hashable, Mapping, Sequence

ZERO = Decimal("0")
HUNDRED = Decimal("100")

# Standard (non-material) cost components, entered per batch (D-05).
# LPG and Packaging are NOT here — they are recipe line items (D-01).
STANDARD_COMPONENTS = ("labour", "electricity", "other")


class CalculationError(ValueError):
    """Raised when inputs break a business rule. Message is user-facing."""


def D(value) -> Decimal:
    """Convert to Decimal safely (via str, so 0.1 stays 0.1)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class RecipeItem:
    material_id: Hashable
    # Already converted to the raw material's own unit (e.g. 150 GM -> 0.15 KG).
    quantity_per_batch: Decimal


@dataclass(frozen=True)
class LineInput:
    """One product variant in a production plan, with its recipe."""
    line_key: Hashable
    planned_qty: Decimal
    batch_yield: Decimal
    items: Sequence[RecipeItem]
    labour_per_batch: Decimal = ZERO
    electricity_per_batch: Decimal = ZERO
    other_per_batch: Decimal = ZERO

    def standard_per_batch(self) -> dict[str, Decimal]:
        return {
            "labour": D(self.labour_per_batch),
            "electricity": D(self.electricity_per_batch),
            "other": D(self.other_per_batch),
        }


@dataclass(frozen=True)
class LineOutput:
    """What actually came out of the oven for one line."""
    produced_qty: Decimal
    rejected_qty: Decimal

    @property
    def good_qty(self) -> Decimal:
        return D(self.produced_qty) - D(self.rejected_qty)


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------
@dataclass
class LineCost:
    line_key: Hashable
    materials: dict  # material_id -> quantity (material unit)
    material_costs: dict  # material_id -> money
    standard: dict  # component -> money
    total_material_cost: Decimal
    total_standard_cost: Decimal
    total_cost: Decimal
    per_piece_qty: Decimal  # planned qty (estimate) or good qty (actual)
    cost_per_piece: Decimal | None


@dataclass
class PlanCost:
    lines: list[LineCost]
    materials: dict  # consolidated material_id -> quantity (BR-06)
    material_costs: dict
    standard: dict
    total_material_cost: Decimal
    total_standard_cost: Decimal
    total_cost: Decimal
    total_qty: Decimal
    cost_per_piece: Decimal | None

    def line(self, key) -> LineCost:
        for ln in self.lines:
            if ln.line_key == key:
                return ln
        raise KeyError(key)


@dataclass
class Variance:
    estimated: Decimal
    actual: Decimal
    amount: Decimal
    percent: Decimal | None


@dataclass
class Comparison:
    total: Variance
    material: Variance
    standard: dict  # component -> Variance
    lines: dict = field(default_factory=dict)  # line_key -> Variance


# --------------------------------------------------------------------------
# Estimate  (UC-11, UC-12)
# --------------------------------------------------------------------------
def estimate(lines: Sequence[LineInput], prices: Mapping) -> PlanCost:
    """
    Required material = (planned qty / batch yield) x material per batch.
    Fractional batches are allowed and never rounded up (D-05).
    Standard cost = batches x cost per batch.
    Estimated cost per piece = line total / planned qty (D-05).
    """
    if not lines:
        raise CalculationError("Production plan-এ অন্তত একটি product লাগবে।")

    seen = set()
    line_costs: list[LineCost] = []
    for ln in lines:
        if ln.line_key in seen:
            raise CalculationError(f"একই product দুইবার আছে: {ln.line_key}")
        seen.add(ln.line_key)

        qty = D(ln.planned_qty)
        yld = D(ln.batch_yield)
        if qty <= 0:
            raise CalculationError(f"Quantity অবশ্যই 0-এর বেশি হতে হবে ({ln.line_key})। [BR-05]")
        if yld <= 0:
            raise CalculationError(f"Recipe batch yield অবশ্যই 0-এর বেশি ({ln.line_key})। [BR-04]")

        batches = qty / yld
        mats, mat_costs = {}, {}
        for item in ln.items:
            per_batch = D(item.quantity_per_batch)
            if per_batch < 0:
                raise CalculationError(f"Recipe quantity negative হতে পারবে না ({ln.line_key})।")
            if item.material_id not in prices:
                raise CalculationError(
                    f"Raw material {item.material_id}-এর কোনো দাম সেট করা নেই। [BR-07]"
                )
            q = batches * per_batch
            mats[item.material_id] = mats.get(item.material_id, ZERO) + q
        for mid, q in mats.items():
            mat_costs[mid] = q * D(prices[mid])

        std = {c: batches * v for c, v in ln.standard_per_batch().items()}
        line_costs.append(_line_cost(ln.line_key, mats, mat_costs, std, qty))

    return _sum_plan(line_costs)


# --------------------------------------------------------------------------
# Actual  (UC-14, UC-17)
# --------------------------------------------------------------------------
def actual(
    est: PlanCost,
    prices: Mapping,
    actual_materials: Mapping,
    actual_standard: Mapping,
    outputs: Mapping,
) -> PlanCost:
    """
    Actual cost from consolidated actual consumption.

    - Each material's actual total is split across lines in proportion to that
      line's ESTIMATED quantity of the same material (D-02).
    - Each standard component's actual total is split in proportion to the
      line's ESTIMATED amount of that component (D-05).
    - If the estimated base is zero, the split falls back to planned qty share.
    - Actual cost uses the locked snapshot prices (D-06).
    - Actual cost per piece = line actual total / good qty (BR-10).
    """
    _validate_actual_inputs(est, actual_materials, actual_standard, outputs)

    keys = [ln.line_key for ln in est.lines]
    planned_share = {ln.line_key: ln.per_piece_qty for ln in est.lines}

    alloc_mats = {k: {} for k in keys}
    for mid, total_actual in actual_materials.items():
        base = {ln.line_key: ln.materials.get(mid, ZERO) for ln in est.lines}
        for k, q in _allocate(D(total_actual), base, planned_share).items():
            if q or mid in est.line(k).materials:
                alloc_mats[k][mid] = q

    alloc_std = {k: {} for k in keys}
    for comp in STANDARD_COMPONENTS:
        base = {ln.line_key: ln.standard[comp] for ln in est.lines}
        for k, v in _allocate(D(actual_standard[comp]), base, planned_share).items():
            alloc_std[k][comp] = v

    line_costs = []
    for k in keys:
        mats = alloc_mats[k]
        costs = {mid: q * D(prices[mid]) for mid, q in mats.items()}
        line_costs.append(_line_cost(k, mats, costs, alloc_std[k], outputs[k].good_qty))
    return _sum_plan(line_costs)


def compare(est: PlanCost, act: PlanCost) -> Comparison:
    """Variance = actual - estimated. Positive means overspent (Story 04)."""
    return Comparison(
        total=_variance(est.total_cost, act.total_cost),
        material=_variance(est.total_material_cost, act.total_material_cost),
        standard={c: _variance(est.standard[c], act.standard[c]) for c in STANDARD_COMPONENTS},
        lines={
            ln.line_key: _variance(ln.total_cost, act.line(ln.line_key).total_cost)
            for ln in est.lines
        },
    )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _validate_actual_inputs(est, actual_materials, actual_standard, outputs):
    missing = [m for m in est.materials if m not in actual_materials]
    if missing:
        raise CalculationError(f"এই raw material-এর actual consumption দেওয়া হয়নি: {missing}")
    extra = [m for m in actual_materials if m not in est.materials]
    if extra:
        raise CalculationError(
            f"এই material plan-এর কোনো recipe-তে নেই, তাই ভাগ করা যাবে না: {extra}"
        )
    for mid, q in actual_materials.items():
        if D(q) < 0:
            raise CalculationError(f"Actual quantity negative হতে পারবে না ({mid})।")

    for comp in STANDARD_COMPONENTS:
        if comp not in actual_standard or actual_standard[comp] is None:
            raise CalculationError(f"Actual {comp} cost দেওয়া হয়নি (না থাকলে 0 লিখুন)।")
        if D(actual_standard[comp]) < 0:
            raise CalculationError(f"Actual {comp} cost negative হতে পারবে না।")

    for ln in est.lines:
        out = outputs.get(ln.line_key)
        if out is None:
            raise CalculationError(f"Produced/Rejected quantity দেওয়া হয়নি ({ln.line_key})।")
        p, r = D(out.produced_qty), D(out.rejected_qty)
        if p < 0 or r < 0:
            raise CalculationError(f"Produced/Rejected negative হতে পারবে না ({ln.line_key})।")
        if r > p:
            raise CalculationError(f"Rejected, Produced-এর চেয়ে বেশি হতে পারবে না ({ln.line_key})।")


def _allocate(total: Decimal, base: dict, fallback: dict) -> dict:
    """
    Split `total` across keys in proportion to `base`. The last key gets the
    remainder so the parts always add back to exactly `total`.
    """
    weights = base if sum(base.values(), ZERO) > 0 else fallback
    wsum = sum(weights.values(), ZERO)
    keys = list(weights)
    out, running = {}, ZERO
    for i, k in enumerate(keys):
        if i == len(keys) - 1:
            out[k] = total - running
        else:
            out[k] = total * weights[k] / wsum
            running += out[k]
    return out


def _line_cost(key, mats, mat_costs, std, per_piece_qty) -> LineCost:
    tm = sum(mat_costs.values(), ZERO)
    ts = sum(std.values(), ZERO)
    total = tm + ts
    return LineCost(
        line_key=key,
        materials=mats,
        material_costs=mat_costs,
        standard=std,
        total_material_cost=tm,
        total_standard_cost=ts,
        total_cost=total,
        per_piece_qty=per_piece_qty,
        cost_per_piece=(total / per_piece_qty) if per_piece_qty > 0 else None,
    )


def _sum_plan(line_costs: list[LineCost]) -> PlanCost:
    mats, mat_costs = {}, {}
    std = {c: ZERO for c in STANDARD_COMPONENTS}
    for ln in line_costs:
        for mid, q in ln.materials.items():
            mats[mid] = mats.get(mid, ZERO) + q
        for mid, c in ln.material_costs.items():
            mat_costs[mid] = mat_costs.get(mid, ZERO) + c
        for comp in STANDARD_COMPONENTS:
            std[comp] += ln.standard.get(comp, ZERO)
    tm = sum(mat_costs.values(), ZERO)
    ts = sum(std.values(), ZERO)
    total_qty = sum((ln.per_piece_qty for ln in line_costs), ZERO)
    return PlanCost(
        lines=line_costs,
        materials=mats,
        material_costs=mat_costs,
        standard=std,
        total_material_cost=tm,
        total_standard_cost=ts,
        total_cost=tm + ts,
        total_qty=total_qty,
        cost_per_piece=((tm + ts) / total_qty) if total_qty > 0 else None,
    )


def _variance(est: Decimal, act: Decimal) -> Variance:
    amount = act - est
    return Variance(est, act, amount, (amount / est * HUNDRED) if est else None)


# --------------------------------------------------------------------------
# Display rounding — the ONLY place numbers get rounded (D-11)
# --------------------------------------------------------------------------
def money(value: Decimal | None) -> Decimal | None:
    """Taka for display: 2 decimals, half-up."""
    return None if value is None else D(value).quantize(Decimal("0.01"), ROUND_HALF_UP)


def percent(value: Decimal | None) -> Decimal | None:
    return None if value is None else D(value).quantize(Decimal("0.01"), ROUND_HALF_UP)


def display_qty(value: Decimal, is_count_unit: bool) -> Decimal:
    """
    Requirement quantity for display. PCS-type items are always rounded UP
    (2050.5 eggs -> 2051) so the factory never buys too few. Weight/volume
    shows 3 decimals (gram / ml precision).
    """
    v = D(value)
    if is_count_unit:
        return v.quantize(Decimal("1"), ROUND_CEILING)
    return v.quantize(Decimal("0.001"), ROUND_HALF_UP)
