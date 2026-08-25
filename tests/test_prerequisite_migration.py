"""Post-cutover guard for the weekend prerequisite migration.

The two prerequisite kinds (weekend_stroke_prerequisite, weekend_ncc_prerequisite)
were dissolved out of the raw `call_rules` channel into the typed
`config.constraints` pipeline (the `_WEEKEND_HANDLERS` registry walk via the
co-located WeekendRolePrerequisiteCriterion). The old `_encode_weekend_prerequisites`
function has been removed; its byte-for-byte equivalence to the new path was
proven during the migration and is now locked in by the live OPB-triple
regression gate (49620/152329/20319) plus the archetype's own contract test
(tests/test_weekend_prerequisite_contract.py).

This file keeps the one guard that does not depend on the deleted function: that
a prereq constraint routed through the typed pipeline actually emits constraints
(so the migration didn't silently turn into a no-op)."""

from __future__ import annotations

from datetime import date

from parafrost_scheduler.schedule_types import ScheduleSolverConfig
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb
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


def _make_config(constraints) -> ScheduleSolverConfig:
    """Minimal weekend-eligible config: all three fellows stroke-eligible (so
    wr[w][role] vars exist), 3-week horizon with a weekend each week."""
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
        call_rules=[])


def _zero_alice(zero_shifts) -> SemanticConstraint:
    """Forbid Alice's weekday *zero_shifts* in all weeks → her prior service is
    always empty → exercises the empty-prior (forbid) branch."""
    return SemanticConstraint(
        kind="service_profile",
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=ConstraintStrength.HARD,
        fellows=FellowSelector.by_names("Alice"),
        params={"zero_shifts": list(zero_shifts)})


def test_typed_prereq_actually_emits_constraints():
    """A prereq routed through the typed pipeline must DO something: the build
    with the rule emits more constraints than the build without it (forbidding
    Alice's weekend-Stroke role in weeks with no prior Stroke service, plus the
    conditional lines for Bob/Carol)."""
    sp = _zero_alice(["Stroke"])
    with_rule = _make_config([sp, SemanticConstraint(
        kind="weekend_stroke_prerequisite",
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=ConstraintStrength.HARD,
        params={"exempt_fellows": [], "exempt_groups": []})])
    without = _make_config([sp])

    opb_with, _ = build_full_schedule_opb(with_rule, objective=True)
    opb_without, _ = build_full_schedule_opb(without, objective=True)
    assert opb_with.num_constraints > opb_without.num_constraints
