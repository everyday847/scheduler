"""S4 byte-equivalence: the WeekendRolePrerequisite archetype reproduces the old
`_encode_weekend_prerequisites` emission EXACTLY.

Two prerequisite kinds (weekend_stroke_prerequisite, weekend_ncc_prerequisite)
move from the raw `call_rules` channel (old function, still wired) into the typed
`config.constraints` pipeline (new `_WEEKEND_HANDLERS` registry entry). This pins
that the relocation is byte-neutral.

HARD: the old function is wired into build_full_schedule_opb (line ~1942) with NO
soft_violations argument, so the LIVE call_rules path is ALWAYS hard. We compare a
full `build_full_schedule_opb`:
    Config A — prereq as a call_rules entry (old path), vs
    Config B — prereq as a SemanticConstraint(strength=HARD) in config.constraints
               (new registry-walk path).
The OPB triple AND the constraint multiset must be identical (HARD allocates no
fresh vars, so the lines coincide regardless of emission ORDER).

SOFT: the call_rules path has NO soft mode (the live wiring never passes
soft_violations), so there is no Config-A soft variant to compare through the full
build. Instead we drive the OLD function directly with soft_violations (its only
soft entry point) and the NEW handler on an IDENTICAL opb/xs/wr layout, and assert
the emitted constraint lines AND soft entries are byte-identical (same var
indices). This pins the soft branch's relocation.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

import pytest

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.schedule_types import ScheduleSolverConfig
from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb,
    _build_fellow_mapping,
    _encode_weekend_prerequisites,
    _encode_weekend_prerequisite_rule,
)
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig
from scheduler.semantic_constraints import (
    SemanticConstraint,
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
)


FELLOWS = ["Alice", "Bob", "Carol"]
SHIFTS = ["Stroke", "NCC1", "NCC2"]


def _make_config(call_rules, constraints) -> ScheduleSolverConfig:
    """Minimal weekend-eligible config: all three fellows are stroke-eligible (so
    wr[w][role] vars exist) and have weekday Stroke/NCC1/NCC2 available (so prior
    service is meaningful), with a 3-week horizon that has weekends each week."""
    night_config = NightSolverConfig(
        total_nights={}, friday_nights={},
        total_night_multisets=(), friday_night_multisets=(),
        ccm_fellows=frozenset(), holiday_dates=(),
        horizon_start_date=date(2026, 7, 6))
    weekend_config = WeekendSolverConfig(
        ncc_totals={}, stroke_totals={}, stroke_cohort=(), stroke_cohort_total=None,
        ccm_fellows=frozenset(),
        always_stroke_eligible=frozenset(FELLOWS),
        telestroke_stroke_eligible=frozenset(), stroke_only_eligible=frozenset())
    return ScheduleSolverConfig(
        fellow_groups={"NCC_SR": list(FELLOWS)},
        shifts=list(SHIFTS),
        constraints=constraints,
        night_config=night_config,
        weekend_config=weekend_config,
        start_dow=0,
        num_days=21,
        call_rules=call_rules)


def _zero_alice(zero_shifts) -> SemanticConstraint:
    """Forbid Alice's weekday *zero_shifts* in ALL weeks → her prior service is
    always empty → exercises the empty-prior (forbid / slack) branch. Bob and
    Carol keep theirs → exercise the non-empty (conditional) branch. Both branches
    appear in one config, so the migration is meaningfully tested."""
    return SemanticConstraint(
        kind="service_profile",
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=ConstraintStrength.HARD,
        fellows=FellowSelector.by_names("Alice"),
        params={"zero_shifts": list(zero_shifts)})


def _triple(opb):
    return (opb.num_vars, opb.num_constraints,
            len(opb._objective) if opb._objective else 0)


# ---------------------------------------------------------------------------
# HARD: full-build A (call_rules) == B (constraints)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind,zero", [
    ("weekend_stroke_prerequisite", ["Stroke"]),
    ("weekend_ncc_prerequisite", ["NCC1", "NCC2"]),
])
def test_hard_full_build_byte_equivalent(kind, zero):
    sp = _zero_alice(zero)

    config_a = _make_config(
        call_rules=[{"type": kind, "active": True,
                     "exempt_fellows": [], "exempt_groups": []}],
        constraints=[sp])
    config_b = _make_config(
        call_rules=[],
        constraints=[sp, SemanticConstraint(
            kind=kind,
            lifecycle=ConstraintLifecycle.STANDING_RULE,
            strength=ConstraintStrength.HARD,
            params={"exempt_fellows": [], "exempt_groups": []})])

    opb_a, _ = build_full_schedule_opb(config_a, objective=True)
    opb_b, _ = build_full_schedule_opb(config_b, objective=True)

    assert _triple(opb_a) == _triple(opb_b)
    # HARD allocates no fresh vars → the lines coincide as a multiset regardless
    # of where each path emits them in the OPB.
    assert Counter(opb_a._constraints) == Counter(opb_b._constraints)
    assert opb_a._objective == opb_b._objective


def test_hard_build_actually_fires():
    """Guard: the prereq must DO something (not a trivial A==B because nothing
    emitted). Compare against a build with the prereq absent entirely."""
    sp = _zero_alice(["Stroke"])
    with_rule = _make_config(
        call_rules=[{"type": "weekend_stroke_prerequisite", "active": True}],
        constraints=[sp])
    without = _make_config(call_rules=[], constraints=[sp])
    opb_with, _ = build_full_schedule_opb(with_rule, objective=True)
    opb_without, _ = build_full_schedule_opb(without, objective=True)
    # The rule adds constraints (forbid Alice's week-0..2 Stroke role + the
    # conditional lines for Bob/Carol), so the builds must differ.
    assert opb_with.num_constraints > opb_without.num_constraints


# ---------------------------------------------------------------------------
# SOFT: direct-call old vs new on an identical layout (the call_rules path has no
# soft mode, so this is the faithful soft reference).
# ---------------------------------------------------------------------------
def _layout(opb, num_weeks, num_fellows, num_shifts, zero_for_fellow0):
    """Allocate xs[f][w][s] then wr[w][role][f] in the SAME order the full builder
    uses, zeroing fellow 0's *zero_for_fellow0* shift indices so the empty-prior
    branch is exercised. Returns (xs, wr)."""
    xs = []
    for f in range(num_fellows):
        xs.append([])
        for w in range(num_weeks):
            xs[f].append([opb.new_var() for _ in range(num_shifts)])
    for w in range(num_weeks):
        for si in zero_for_fellow0:
            xs[0][w][si] = 0
    wr = []
    for w in range(num_weeks):
        week = [{f: opb.new_var() for f in range(num_fellows)} for _ in range(3)]
        wr.append(week)
    return xs, wr


@pytest.mark.parametrize("kind,zero_idx", [
    ("weekend_stroke_prerequisite", [0]),       # Stroke
    ("weekend_ncc_prerequisite", [1, 2]),        # NCC1, NCC2
])
def test_soft_direct_call_byte_equivalent(kind, zero_idx):
    config = _make_config(
        call_rules=[{"type": kind, "active": True}], constraints=[])
    shift_idx = {s: i for i, s in enumerate(SHIFTS)}
    num_weeks = config.num_weeks

    # OLD soft path (its only entry point: pass soft_violations explicitly).
    opb_old = OpbBuilder()
    xs_o, wr_o = _layout(opb_old, num_weeks, len(FELLOWS), len(SHIFTS), zero_idx)
    sv_old: list[tuple[int, int]] = []
    _encode_weekend_prerequisites(
        opb_old, wr_o, xs_o, config, FELLOWS, shift_idx, soft_violations=sv_old)

    # NEW soft path: the archetype handler, on an IDENTICAL var layout.
    opb_new = OpbBuilder()
    xs_n, wr_n = _layout(opb_new, num_weeks, len(FELLOWS), len(SHIFTS), zero_idx)
    sv_new: list[tuple[int, int]] = []
    fm = _build_fellow_mapping(config.fellow_groups)
    con = SemanticConstraint(
        kind=kind, lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=ConstraintStrength.SOFT, params={})
    _encode_weekend_prerequisite_rule(
        opb_new, wr_n, con, xs=xs_n, config=config, fellow_mapping=fm,
        fellow_names=FELLOWS, shift_idx=shift_idx, soft_violations=sv_new)

    # Byte-identical: same lines (same var indices), same soft entries, same count.
    assert opb_old._constraints == opb_new._constraints
    assert sv_old == sv_new
    assert sv_old, "soft path must register at least one slack (Alice week 0+)"
    assert opb_old.num_vars == opb_new.num_vars


@pytest.mark.parametrize("kind,zero_idx", [
    ("weekend_stroke_prerequisite", [0]),
    ("weekend_ncc_prerequisite", [1, 2]),
])
def test_hard_direct_call_byte_equivalent(kind, zero_idx):
    """The HARD direct-call also coincides line-for-line (and pins that the new
    handler's hard branch matches the old function's default-None branch)."""
    config = _make_config(
        call_rules=[{"type": kind, "active": True}], constraints=[])
    shift_idx = {s: i for i, s in enumerate(SHIFTS)}
    num_weeks = config.num_weeks

    opb_old = OpbBuilder()
    xs_o, wr_o = _layout(opb_old, num_weeks, len(FELLOWS), len(SHIFTS), zero_idx)
    _encode_weekend_prerequisites(opb_old, wr_o, xs_o, config, FELLOWS, shift_idx)

    opb_new = OpbBuilder()
    xs_n, wr_n = _layout(opb_new, num_weeks, len(FELLOWS), len(SHIFTS), zero_idx)
    sv_new: list[tuple[int, int]] = []
    fm = _build_fellow_mapping(config.fellow_groups)
    con = SemanticConstraint(
        kind=kind, lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=ConstraintStrength.HARD, params={})
    _encode_weekend_prerequisite_rule(
        opb_new, wr_n, con, xs=xs_n, config=config, fellow_mapping=fm,
        fellow_names=FELLOWS, shift_idx=shift_idx, soft_violations=sv_new)

    assert opb_old._constraints == opb_new._constraints
    assert sv_new == []
    assert opb_old.num_vars == opb_new.num_vars
