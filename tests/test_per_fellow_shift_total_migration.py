"""Post-cutover guard for the S1 `per_fellow_shift_total` migration.

The legacy `per_fellow_shift_total` branch in `_encode_call_rules` counted ONE
fellow's shift vars across all weeks and emitted a count-band. It was dissolved
into the typed `shift_total` archetype (a SemanticConstraint with a single-fellow
selector, encoded by WindowedCountBandCriterion). The old `call_rules` side has
been removed (`_encode_call_rules` now raises for any migrated type), so the
original OLD-vs-NEW byte-for-byte equivalence test no longer applies. Its
equivalence was proven during the migration and is now locked in by the live
OPB-triple regression gate plus the archetype's own contract test.

What remains here is the new-path-only guard: the rule routed through the typed
pipeline MUST move the OPB triple off the empty baseline (otherwise an all-zero,
no-op encoding would mean the migration silently dropped the rule). It is
parametrized across every relation x strength so each branch is exercised.

Run:
    PYTHONPATH=src pytest tests/test_per_fellow_shift_total_migration.py -v
"""

from __future__ import annotations

from datetime import date

import pytest

from parafrost_scheduler.schedule_types import ScheduleSolverConfig
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb
from scheduler.night_call_types import NightSolverConfig
from scheduler.semantic_constraints import (
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
    SemanticConstraint,
    ShiftSet,
)
from scheduler.weekend_call_types import WeekendSolverConfig


FELLOW = "Bob"
SHIFTS = ["NCC1", "NCC2", "Stroke", "Elec"]


def _config(
    *,
    constraints: list[SemanticConstraint] | None = None,
) -> ScheduleSolverConfig:
    """Minimal full-schedule config; mirrors test_unknown_call_rule_fails_fast."""
    return ScheduleSolverConfig(
        fellow_groups={"NCC_SR": [FELLOW]},
        shifts=list(SHIFTS),
        constraints=constraints or [],
        night_config=NightSolverConfig(
            total_nights={}, friday_nights={}, total_night_multisets=(),
            friday_night_multisets=(), ccm_fellows=frozenset(), holiday_dates=(),
            horizon_start_date=date(2026, 7, 6)),
        weekend_config=WeekendSolverConfig(
            ncc_totals={}, stroke_totals={}, stroke_cohort=(), stroke_cohort_total=None,
            ccm_fellows=frozenset(), always_stroke_eligible=frozenset(),
            telestroke_stroke_eligible=frozenset(), stroke_only_eligible=frozenset()),
        night_hard_criteria=frozenset(),
        start_dow=0, num_days=70, call_rules=[])


def _new_way(relation: str, count: int, strength: str) -> ScheduleSolverConfig:
    """Config: rule lives in constraints as a single-fellow `shift_total`."""
    semantic_strength = (
        ConstraintStrength.SOFT if strength == "soft" else ConstraintStrength.HARD
    )
    constraint = SemanticConstraint(
        kind="shift_total",
        lifecycle=ConstraintLifecycle.ANNUAL_RULE,
        strength=semantic_strength,
        fellows=FellowSelector.by_names(FELLOW),
        shifts=ShiftSet("test", ("Stroke",)),
        params={"relation": relation, "count": count, "name": "test"},
    )
    return _config(constraints=[constraint])


def _triple(config: ScheduleSolverConfig) -> tuple[int, int, int]:
    opb, _ = build_full_schedule_opb(config, objective=True)
    return (opb.num_vars, opb.num_constraints, len(opb._objective) if opb._objective else 0)


# Cover all three relations x both strengths. `count=2` keeps at_least feasible
# (2 <= number of weekly vars) so the hard at_least line is actually emitted.
_CASES = [
    (relation, count, strength)
    for relation, count in (("at_least", 2), ("at_most", 2), ("exactly", 2))
    for strength in ("hard", "soft")
]


@pytest.mark.parametrize("relation,count,strength", _CASES)
def test_typed_shift_total_changes_the_triple_vs_no_rule(relation, count, strength):
    """The typed `shift_total` rule MUST move the OPB triple off the empty
    baseline; an all-zero (no-op) encoding would mean the migration silently
    dropped the rule."""
    baseline = _triple(_config())
    with_rule = _triple(_new_way(relation, count, strength))
    assert with_rule != baseline, (
        f"typed shift_total was a no-op for relation={relation} strength={strength}: "
        f"baseline={baseline} with_rule={with_rule}"
    )
