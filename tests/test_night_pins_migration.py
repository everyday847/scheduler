"""Post-cutover guards for the four night-pin kinds.

These four call-rule types — specific_night_assignment / blocked_night /
friday_call_assignment / group_night_requirement — were dissolved out of the
raw `call_rules` channel into the typed `config.constraints` pipeline (encoded
by the `_NIGHT_HANDLERS` walk onto the xn night layer). The old `call_rules`
side has been removed (`_encode_call_rules` now raises for any migrated type),
so the original byte-for-byte OLD-vs-NEW equivalence tests no longer apply.
Their equivalence was proven during the migration and is now locked in by the
live OPB-triple regression gate plus the archetype's own contract test
(tests/test_night_literal_pin_contract.py covers encode/evaluate correctness).

What remains here are lighter new-path-only guards: route each kind through the
typed pipeline and assert the build with the rule emits MORE constraints than an
otherwise-identical build without it (so the migration didn't silently become a
no-op). The group_night_requirement scenarios additionally pin the complement
semantics: a requirement whose allowed groups cover EVERY fellow forbids nobody
(no added constraints), while one with an excluded group forbids the outsiders.
"""

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


HORIZON = date(2026, 7, 6)  # Monday = horizon day 0
START_DOW = 0
NUM_DAYS = 21
FELLOW_GROUPS = {"NCC_SR": ["Alice", "Bob", "Carol"]}


def _make_config(constraints, fellow_groups=None):
    if fellow_groups is None:
        fellow_groups = FELLOW_GROUPS
    all_fellows = frozenset(f for fs in fellow_groups.values() for f in fs)
    night_config = NightSolverConfig(
        total_nights={},
        friday_nights={},
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(),
        horizon_start_date=HORIZON,
    )
    weekend_config = WeekendSolverConfig(
        ncc_totals={},
        stroke_totals={},
        stroke_cohort=(),
        stroke_cohort_total=None,
        ccm_fellows=frozenset(),
        always_stroke_eligible=all_fellows,
        telestroke_stroke_eligible=frozenset(),
        stroke_only_eligible=frozenset(),
    )
    return ScheduleSolverConfig(
        fellow_groups=fellow_groups,
        shifts=["NCC1"],
        constraints=constraints,
        night_config=night_config,
        weekend_config=weekend_config,
        night_hard_criteria=frozenset(),
        start_dow=START_DOW,
        num_days=NUM_DAYS,
        call_rules=[],
    )


def _annual(kind, **kw):
    return SemanticConstraint(
        kind=kind,
        lifecycle=ConstraintLifecycle.ANNUAL_RULE,
        strength=ConstraintStrength.HARD,
        **kw,
    )


def _num_constraints(constraints, fellow_groups=None):
    opb, _ = build_full_schedule_opb(
        _make_config(constraints, fellow_groups=fellow_groups), objective=True)
    return opb.num_constraints


# July 6 2026 = Monday = horizon day 0; start_dow=0.
class TestNightPinNewPathFires:
    def test_specific_night_assignment(self):
        """PIN Alice on July 10th forces her night var true → one added line."""
        constraint = _annual(
            "specific_night_assignment",
            fellows=FellowSelector.by_names("Alice"),
            params={"dates": ["2026-07-10"], "name": "Alice covers July 10th"},
        )
        assert _num_constraints([constraint]) > _num_constraints([])

    def test_blocked_night(self):
        """FORBID Bob on July 12th excludes his night var → one added line."""
        constraint = _annual(
            "blocked_night",
            fellows=FellowSelector.by_names("Bob"),
            params={"dates": ["2026-07-12"], "name": "Bob off July 12th"},
        )
        assert _num_constraints([constraint]) > _num_constraints([])

    def test_friday_call_assignment(self):
        """PIN Carol's Friday (dow=4) night in week 1 → one added line."""
        constraint = _annual(
            "friday_call_assignment",
            fellows=FellowSelector.by_names("Carol"),
            params={"weeks": [1], "dow": 4, "name": "Carol Friday wk1"},
        )
        assert _num_constraints([constraint]) > _num_constraints([])

    def test_group_night_requirement_no_excluded_is_noop(self):
        """When the allowed groups cover EVERY fellow, the complement (forbidden)
        set is empty, so the requirement forbids nobody and adds no constraints.
        This pins the complement semantics — not a no-op bug."""
        constraint = _annual(
            "group_night_requirement",
            fellows=FellowSelector.by_groups("NCC_SR"),
            params={
                "groups": ["NCC_SR"],
                "dates": ["2026-07-08", "2026-07-15"],
                "name": "Only NCC_SR on these nights",
            },
        )
        # NCC_SR covers all of Alice/Bob/Carol → complement empty → no new lines.
        assert _num_constraints([constraint]) == _num_constraints([])

    def test_group_night_requirement_with_excluded_group(self):
        """With Dave outside NCC_SR, the requirement forbids the outsiders' (Carol,
        Dave) nights on the given date → strictly more constraints than baseline."""
        groups = {"NCC_SR": ["Alice", "Bob"], "OTHER": ["Carol", "Dave"]}
        constraint = _annual(
            "group_night_requirement",
            fellows=FellowSelector.by_groups("NCC_SR"),
            params={"groups": ["NCC_SR"], "dates": ["2026-07-08"], "name": "Only NCC_SR"},
        )
        assert (_num_constraints([constraint], fellow_groups=groups)
                > _num_constraints([], fellow_groups=groups))
