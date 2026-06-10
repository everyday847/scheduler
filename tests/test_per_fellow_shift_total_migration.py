"""S1 migration equivalence: `per_fellow_shift_total` (old call_rule) ==
`shift_total` (typed SemanticConstraint with a single-fellow selector).

The legacy `per_fellow_shift_total` branch in `_encode_call_rules` counts ONE
fellow's shift vars across all weeks and emits a count-band via
`_add_cardinality_constraint`. The `shift_total` archetype does the SAME thing
through `WindowedCountBandCriterion`, resolving the fellow from a
`FellowSelector` instead of a raw `fellow:` string.

This test PROVES the migration is byte-neutral at the OPB-triple level: for
every relation (at_least / at_most / exactly) and strength (hard / soft),
building the full schedule with the rule expressed the OLD way (Config A) and
the NEW way (Config B) yields an IDENTICAL (num_vars, num_constraints,
len(objective)) triple.

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
    call_rules: list[dict] | None = None,
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
        start_dow=0, num_days=70, call_rules=call_rules or [])


def _old_way(relation: str, count: int, strength: str) -> ScheduleSolverConfig:
    """Config A: rule lives in call_rules as `per_fellow_shift_total`."""
    return _config(call_rules=[{
        "type": "per_fellow_shift_total",
        "name": "test",
        "fellow": FELLOW,
        "shifts": ["Stroke"],
        "relation": relation,
        "count": count,
        "strength": strength,
        "active": True,
    }])


def _new_way(relation: str, count: int, strength: str) -> ScheduleSolverConfig:
    """Config B: rule lives in constraints as a single-fellow `shift_total`."""
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
def test_old_and_new_yield_identical_triple(relation, count, strength):
    a = _triple(_old_way(relation, count, strength))
    b = _triple(_new_way(relation, count, strength))
    assert a == b, (
        f"OPB triple diverged for relation={relation} strength={strength}: "
        f"old(A)={a} new(B)={b}"
    )


def test_migration_actually_changes_the_triple_vs_no_rule():
    """Guard: the rule MUST move the triple off the empty baseline, otherwise
    an all-zero (no-op) encoding would make the equivalence test vacuous."""
    baseline = _triple(_config())
    with_rule = _triple(_new_way("at_least", 2, "hard"))
    assert with_rule != baseline
