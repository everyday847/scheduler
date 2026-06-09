"""Slice 1 — the ADR-0005 contract test for the Stroke criterion.

The Stroke criterion is one Rule with two co-located manifestations:

  * evaluate(view, week, dow, fellow) -> bool : reads a concrete Schedule
    (via a ScheduleView) and says whether a stroke-night violation fires.
  * encode(sink, ...) : emits the pseudo-Boolean form to a ConstraintSink.

Both consume ONE shared geometry method, gating_terms(week, dow), so the day ->
gating-week mapping that historically drifted between the encoder and the
evaluator (the Saturday false-red bug) cannot diverge: there is a single
definition.

This test is the regression guard ADR-0005 specified and the prior bug would
have failed: it pins a concrete schedule, counts evaluate() violations, then
pins the SAME schedule into PB variables, runs encode(), solves, and asserts the
number of forced soft-violation indicators equals the evaluate() count. A
hard-strength variant asserts a pinned violation is UNSAT.

Stroke geometry (mirrors both prior implementations):
  Rule 1 (weekday Stroke): a night the evening before a Stroke workday is a
    violation. Mon-Thu nights (dow 0-3) gate on the SAME week's Stroke service;
    the Sunday night (dow 6) gates on the NEXT week's. Fri/Sat nights (dow 4,5)
    do not gate (the next morning is not a stroke workday).
  Rule 2 (weekend Stroke role): the Weekend Stroke holder is penalized on
    Saturday night (dow 5) only.
  Exemption: in a dual-stroke week (>=2 fellows on Stroke), both rules are
    exempt for that week (the second Stroke fellow covers the night).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from schedule_rules.criteria.stroke import StrokeCriterion
from schedule_rules.strength import HARD, SOFT
from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.constraint_sink import OpbConstraintSink
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner


ROUNDINGSAT_BINARY = (
    Path(__file__).resolve().parent.parent / "vendor" / "roundingsat" / "build" / "roundingsat"
)

# dow: Mon=0 Tue=1 Wed=2 Thu=3 Fri=4 Sat=5 Sun=6
_MON, _TUE, _WED, _THU, _FRI, _SAT, _SUN = range(7)
_WEEKEND_STROKE = "Weekend Stroke"


@pytest.fixture
def runner():
    if not ROUNDINGSAT_BINARY.exists():
        pytest.skip("RoundingSat binary not built")
    return RoundingSatRunner(ROUNDINGSAT_BINARY)


class DictScheduleView:
    """A concrete Schedule backed by plain dicts — the domain data evaluate()
    reads, with no solver coupling. (Not a behavior mock: it holds real
    assignments and answers questions about them.)

    weekday[w] : {fellow: shift}
    weekend[w] : {role: fellow}
    """

    def __init__(self, weekday: list[dict[str, str]], weekend: list[dict[str, str]]):
        self._weekday = weekday
        self._weekend = weekend

    @property
    def num_weeks(self) -> int:
        return len(self._weekday)

    def weekday_service(self, week: int, fellow: str) -> str:
        if 0 <= week < len(self._weekday):
            return self._weekday[week].get(fellow, "")
        return ""

    def weekend_role_holder(self, week: int, role: str) -> str | None:
        if 0 <= week < len(self._weekend):
            return self._weekend[week].get(role)
        return None

    def fellows_on_shift(self, week: int, shift: str) -> list[str]:
        if 0 <= week < len(self._weekday):
            return [f for f, s in self._weekday[week].items() if s == shift]
        return []


# A schedule exercising every branch:
#   w0: A on Stroke (single).  A holds Weekend Stroke.
#   w1: A and B both on Stroke (DUAL -> exempt).  A holds Weekend Stroke.
#   w2: A on Elective (no stroke).  B holds Weekend Stroke.
def _schedule() -> DictScheduleView:
    return DictScheduleView(
        weekday=[
            {"A": "Stroke", "B": "NCC1"},
            {"A": "Stroke", "B": "Stroke"},
            {"A": "Elective", "B": "Elective"},
        ],
        weekend=[
            {_WEEKEND_STROKE: "A", "Weekend NCC1": "B"},
            {_WEEKEND_STROKE: "A", "Weekend NCC1": "B"},
            {_WEEKEND_STROKE: "B", "Weekend NCC1": "A"},
        ],
    )


# Which (week, dow, fellow) nights we place, chosen to hit each branch.
_NIGHTS = [
    (0, _MON, "A"),   # w0 Mon night, A on Stroke this week -> Rule 1 fires
    (0, _FRI, "A"),   # w0 Fri night -> Rule 1 does NOT gate (Fri)
    (0, _SAT, "A"),   # w0 Sat night, A holds Weekend Stroke -> Rule 2 fires
    (0, _SAT, "B"),   # w0 Sat night, B does NOT hold Weekend Stroke -> no fire
    (1, _MON, "A"),   # w1 Mon night, but w1 is DUAL stroke -> exempt, no fire
    (2, _SUN, "A"),   # w2 Sun night gates on w3 (absent) -> no fire
]


def _evaluate_count(view: DictScheduleView, crit: StrokeCriterion) -> int:
    return sum(
        1 for (w, dow, f) in _NIGHTS if crit.evaluate(view, w, dow, f)
    )


class TestEvaluateGeometry:
    """The evaluate side alone, branch by branch (fast, no solver)."""

    def setup_method(self):
        self.crit = StrokeCriterion()
        self.view = _schedule()

    def test_weekday_monday_before_stroke_fires(self):
        assert self.crit.evaluate(self.view, 0, _MON, "A") is True

    def test_friday_does_not_gate(self):
        assert self.crit.evaluate(self.view, 0, _FRI, "A") is False

    def test_weekend_stroke_holder_saturday_fires(self):
        assert self.crit.evaluate(self.view, 0, _SAT, "A") is True

    def test_non_weekend_stroke_holder_saturday_does_not_fire(self):
        assert self.crit.evaluate(self.view, 0, _SAT, "B") is False

    def test_dual_stroke_week_is_exempt(self):
        assert self.crit.evaluate(self.view, 1, _MON, "A") is False


class TestEncodeEvaluateAgree:
    """The ADR-0005 contract: pin the concrete schedule into PB vars, encode,
    solve, and assert the forced soft-indicator count equals evaluate()'s."""

    def test_soft_violation_counts_match(self, runner):
        view = _schedule()
        crit = StrokeCriterion()
        expected = _evaluate_count(view, crit)

        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)

        # Allocate + pin vars to the concrete schedule.
        shift_var: dict[tuple[int, str, str], int] = {}
        role_var: dict[tuple[int, str], int] = {}
        dual_var: dict[int, int] = {}
        fellows = ["A", "B"]
        shifts = ["Stroke", "NCC1", "Elective"]
        for w in range(view.num_weeks):
            for f in fellows:
                for s in shifts:
                    v = opb.new_var()
                    shift_var[(w, f, s)] = v
                    on = view.weekday_service(w, f) == s
                    opb.add_unit(v if on else -v)
            for role in (_WEEKEND_STROKE, "Weekend NCC1"):
                v = opb.new_var()
                holder = view.weekend_role_holder(w, role)
                role_var[(w, role)] = v  # var means "holder is the fellow we ask about"
                # encoded per-fellow below; here we store holder identity separately
            dv = opb.new_var()
            dual_var[w] = dv
            is_dual = len(view.fellows_on_shift(w, "Stroke")) >= 2
            opb.add_unit(dv if is_dual else -dv)

        # Per-(week,role,fellow) weekend-role vars, pinned.
        wr_fellow_var: dict[tuple[int, str, str], int] = {}
        for w in range(view.num_weeks):
            for role in (_WEEKEND_STROKE, "Weekend NCC1"):
                for f in fellows:
                    v = opb.new_var()
                    wr_fellow_var[(w, role, f)] = v
                    opb.add_unit(v if view.weekend_role_holder(w, role) == f else -v)

        # Night vars, pinned to the placed nights.
        night_var: dict[tuple[int, int, str], int] = {}
        placed = {(w, dow, f) for (w, dow, f) in _NIGHTS}
        for (w, dow, f) in _NIGHTS:
            v = opb.new_var()
            night_var[(w, dow, f)] = v
            opb.add_unit(v)  # this night happens

        # Drive encode through the shared geometry.
        for (w, dow, f) in _NIGHTS:
            for term in crit.gating_terms(w, dow):
                if term.is_weekend_role:
                    tv = wr_fellow_var.get((term.week, term.target, f))
                else:
                    tv = shift_var.get((term.week, f, term.target))
                if tv is None:
                    continue  # gating week out of range
                exempt = dual_var.get(term.week)
                crit.encode(
                    sink,
                    night_var=night_var[(w, dow, f)],
                    term_var=tv,
                    exempt_var=exempt,
                    strength=SOFT,
                    weight=5,
                )

        result = runner.solve(opb)
        assert result.satisfiable
        forced_true = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced_true == expected

    def test_hard_strength_forbids_a_pinned_violation(self, runner):
        """With HARD strength, a pinned stroke violation (Mon night before a
        Stroke week) must be UNSAT."""
        view = _schedule()
        crit = StrokeCriterion()
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)

        stroke_v = opb.new_var()
        opb.add_unit(stroke_v)   # A is on Stroke in the gating week
        night_v = opb.new_var()
        opb.add_unit(night_v)    # A takes the Monday night
        # no dual exemption
        crit.encode(sink, night_var=night_v, term_var=stroke_v, exempt_var=None,
                    strength=HARD, weight=5)

        result = runner.solve(opb)
        assert result.satisfiable is False
