"""Contract test for the windowed_count_band archetype (ADR-0005).

The shape behind both shift_total and staffing_per_week: a relation (at_least /
at_most / exactly) on the COUNT of a shift-set in a window. encode reproduces the
solver's _add_cardinality_constraint — HARD ⇒ a native cardinality line; SOFT ⇒ a
DEVIATION-SCALED penalty (one penalized unary slack per unit of deviation, so the
forced-slack count == |count - target|, not a flat 1).

Agreement test: pin a count, encode, solve, and assert the forced-slack count ==
the deviation magnitude evaluate() implies (|actual - target| in the violated
direction). Plus a hard-strength UNSAT case where the pinned count breaks the
relation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from schedule_rules.weekly.windowed_count_band import WindowedCountBandCriterion
from schedule_rules.strength import HARD, SOFT
from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.constraint_sink import OpbConstraintSink
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner


ROUNDINGSAT_BINARY = (
    Path(__file__).resolve().parent.parent / "vendor" / "roundingsat" / "build" / "roundingsat"
)
_TARGET_SHIFTS = ("NCC1", "NCC2")


@pytest.fixture
def runner():
    if not ROUNDINGSAT_BINARY.exists():
        pytest.skip("RoundingSat binary not built")
    return RoundingSatRunner(ROUNDINGSAT_BINARY)


class DictScheduleView:
    def __init__(self, weekday):
        self._weekday = weekday

    @property
    def num_weeks(self) -> int:
        return len(self._weekday)

    def weekday_service(self, week, fellow):
        if 0 <= week < len(self._weekday):
            return self._weekday[week].get(fellow, "")
        return ""

    def all_services(self, week, fellow):
        s = self.weekday_service(week, fellow)
        return [s] if s else []

    def night_holder(self, day):
        return None


# One fellow A: NCC over weeks 0,1,2 (count 3), Elec on 3,4. Window [0,5) => 3.
def _view():
    return DictScheduleView([
        {"A": "NCC1"},
        {"A": "NCC2"},
        {"A": "NCC1"},
        {"A": "Elec"},
        {"A": "Elec"},
    ])


_GROUPS = [("A", ["A"])]
_WINDOW = range(0, 5)


class TestEvaluate:
    def setup_method(self):
        self.crit = WindowedCountBandCriterion()
        self.view = _view()

    def test_at_least_satisfied(self):
        v = self.crit.evaluate(self.view, groups=_GROUPS, shifts=_TARGET_SHIFTS,
                               window=_WINDOW, relation="at_least", count=3)
        assert v == []

    def test_at_least_violated_reports_actual(self):
        v = self.crit.evaluate(self.view, groups=_GROUPS, shifts=_TARGET_SHIFTS,
                               window=_WINDOW, relation="at_least", count=5)
        assert v == [("A", 3)]

    def test_at_most_violated(self):
        v = self.crit.evaluate(self.view, groups=_GROUPS, shifts=_TARGET_SHIFTS,
                               window=_WINDOW, relation="at_most", count=1)
        assert v == [("A", 3)]

    def test_exactly_violated(self):
        v = self.crit.evaluate(self.view, groups=_GROUPS, shifts=_TARGET_SHIFTS,
                               window=_WINDOW, relation="exactly", count=2)
        assert v == [("A", 3)]

    def test_window_restricts(self):
        v = self.crit.evaluate(self.view, groups=_GROUPS, shifts=_TARGET_SHIFTS,
                               window=range(0, 2), relation="exactly", count=2)
        assert v == []  # only weeks 0,1 -> count 2


class TestEncodeEvaluateAgree:
    def _build(self, relation, count, strength):
        """Pin fellow A's 5-week NCC/Elec column into PB vars and encode the
        band over the NCC vars."""
        view = _view()
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)

        vars = []
        for w in _WINDOW:
            for s in _TARGET_SHIFTS:
                v = opb.new_var()
                on = view.weekday_service(w, "A") == s
                opb.add_unit(v if on else -v)
                vars.append(v)

        crit = WindowedCountBandCriterion()
        crit.encode(sink, vars=vars, relation=relation, target=count,
                    strength=strength, weight=100)
        return crit, view, opb, soft

    def _actual(self, crit, view):
        # The fellow's true count in the window (3).
        return crit.count(view, "A", _TARGET_SHIFTS, _WINDOW)

    def test_soft_at_least_shortfall_slacks(self, runner):
        # target 5, actual 3 -> deviation 2 forced slacks.
        crit, view, opb, soft = self._build("at_least", 5, SOFT)
        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced == 5 - self._actual(crit, view) == 2

    def test_soft_at_most_excess_slacks(self, runner):
        # target 1, actual 3 -> excess 2 forced slacks.
        crit, view, opb, soft = self._build("at_most", 1, SOFT)
        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced == self._actual(crit, view) - 1 == 2

    def test_soft_exactly_deviation_slacks(self, runner):
        # target 2, actual 3 -> |3-2| = 1 forced slack.
        crit, view, opb, soft = self._build("exactly", 2, SOFT)
        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced == abs(self._actual(crit, view) - 2) == 1

    def test_soft_satisfied_no_slacks(self, runner):
        crit, view, opb, soft = self._build("at_least", 3, SOFT)
        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced == 0

    def test_hard_at_least_unsat(self, runner):
        # actual 3 < required 5 under HARD -> UNSAT.
        _crit, _view, opb, _soft = self._build("at_least", 5, HARD)
        assert runner.solve(opb).satisfiable is False

    def test_hard_exactly_unsat(self, runner):
        # actual 3 != exactly 2 under HARD -> UNSAT.
        _crit, _view, opb, _soft = self._build("exactly", 2, HARD)
        assert runner.solve(opb).satisfiable is False

    def test_hard_exactly_sat(self, runner):
        # actual 3 == exactly 3 under HARD -> SAT, no soft.
        _crit, _view, opb, soft = self._build("exactly", 3, HARD)
        assert runner.solve(opb).satisfiable is True
        assert soft == []
