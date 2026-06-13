"""Dispatch-level strength test — no_isolated_week handler.

no_isolated_week forbids an ISOLATED week on the target shift set: a fellow on the
shift in week w must also be on it in w-1 or w+1. Paired with an exactly-N total it
forces the N weeks CONTIGUOUS anywhere (no fixed even-week alignment, unlike
all_or_none_block). Added for the SCVMC 2-week-block experiment after the
even-aligned block_rotation encoding proved very hard while consecutivity-only was
SAT.

This pins strength through the REAL ``build_full_schedule_opb`` dispatch (per
test-strength-dispatch-gap): hard -> UNSAT when a week is isolated; soft -> SAT and
the per-week penalty is registered.

Violation geometry: pin F0 ON "MICU" in week 2 and OFF in weeks 1 and 3 -> week 2 is
an isolated MICU week. (MICU is an arbitrary single shift in the test palette;
at-most-one-shift-per-week makes pinning another shift in wk1/wk3 force MICU off
there, but we pin MICU directly off for clarity.)
"""

from __future__ import annotations

from _dispatch_helpers import (
    make_dispatch_config, build, pin_shift, solve_sat, has_soft_weight,
    assert_baseline_sat)
from scheduler.semantic_constraints import (
    SemanticConstraint, ConstraintLifecycle, ConstraintStrength, FellowSelector,
    ShiftSet)


_SHIFTS = ("Stroke", "NCC1", "NCC2", "Vac", "Elec", "MICU")
_SENTINEL_WEIGHT = 777
# Need weeks 1,2,3 to pin an isolated week 2 with both neighbours present, so use a
# 5-week (35-day) horizon rather than the 3-week default.
_NUM_DAYS = 35


def _scvmc_like_contiguous(strength: ConstraintStrength) -> SemanticConstraint:
    """no_isolated_week on MICU for the NCC_SR group (the default fellows' group)."""
    return SemanticConstraint(
        kind="no_isolated_week",
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=strength,
        fellows=FellowSelector.by_groups("NCC_SR"),
        shifts=ShiftSet.single("MICU"),
        params={"name": "MICU contiguous"},
    )


def _pin_isolated(opb, vm) -> None:
    """F0 holds MICU in week 2 but NOT in weeks 1 or 3 -> isolated MICU week."""
    pin_shift(opb, vm, "F0", 2, "MICU", value=True)
    pin_shift(opb, vm, "F0", 1, "MICU", value=False)
    pin_shift(opb, vm, "F0", 3, "MICU", value=False)


def test_baseline_sat_without_rule():
    """The pins alone (rule removed) are SAT, so a hard-variant UNSAT below is the
    rule, not the floor."""
    assert_baseline_sat(_pin_isolated, shifts=_SHIFTS, num_days=_NUM_DAYS, weekly_soft_weight=_SENTINEL_WEIGHT)


def test_hard_no_isolated_week_is_unsat():
    """HARD: an isolated MICU week is forbidden -> the pinned isolation is UNSAT."""
    cfg = make_dispatch_config(
        constraints=[_scvmc_like_contiguous(ConstraintStrength.HARD)],
        shifts=_SHIFTS, num_days=_NUM_DAYS, weekly_soft_weight=_SENTINEL_WEIGHT)
    opb, vm = build(cfg)
    _pin_isolated(opb, vm)
    assert not solve_sat(opb)


def test_soft_no_isolated_week_is_sat_and_penalizes():
    """SOFT: the isolated week is allowed but penalized; SAT + the sentinel weight
    proves the dispatch emitted the soft rule (SAT alone would mask a no-op)."""
    cfg = make_dispatch_config(
        constraints=[_scvmc_like_contiguous(ConstraintStrength.SOFT)],
        shifts=_SHIFTS, num_days=_NUM_DAYS, weekly_soft_weight=_SENTINEL_WEIGHT)
    opb, vm = build(cfg)
    _pin_isolated(opb, vm)
    assert solve_sat(opb)
    assert has_soft_weight(vm, _SENTINEL_WEIGHT)
