"""Contract test for the specific_assignment archetype (ADR-0005).

"A named shift must be filled in a named week by SOMEONE from the selected
fellows." encode emits the coverage disjunction over one (shift, week); the soft
indicator fires iff no selected fellow holds it. evaluate is per-fellow; the two
coincide on the single-fellow geometry (one fellow ⇒ "someone covers it" ==
"that fellow covers it").

Agreement test: pin a concrete (shift, week) column, encode, solve, and assert
the forced soft-indicator (0 or 1) matches whether evaluate finds the cell
uncovered. Plus a hard-strength UNSAT case.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from schedule_rules.weekly.specific_assignment import SpecificAssignmentCriterion
from schedule_rules.strength import HARD, SOFT
from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.constraint_sink import OpbConstraintSink
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner


ROUNDINGSAT_BINARY = (
    Path(__file__).resolve().parent.parent / "vendor" / "roundingsat" / "build" / "roundingsat"
)
_TARGET = "ISC"


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


_FELLOWS = ["A", "B"]


class TestEvaluate:
    def test_covered_no_violation(self):
        view = DictScheduleView([{"A": "ISC", "B": "ISC"}])
        pairs = SpecificAssignmentCriterion().evaluate(
            view, fellows=_FELLOWS, target=_TARGET, window=range(0, 1))
        assert pairs == []

    def test_uncovered_flags_each_fellow(self):
        view = DictScheduleView([{"A": "Elec", "B": "Elec"}])
        pairs = SpecificAssignmentCriterion().evaluate(
            view, fellows=_FELLOWS, target=_TARGET, window=range(0, 1))
        assert set(pairs) == {("A", 0), ("B", 0)}


class TestEncodeEvaluateAgree:
    def _build(self, strength, *, covered):
        """One target-shift column over 2 fellows. *covered* True ⇒ fellow A holds
        the shift; False ⇒ nobody does (the violation case)."""
        view = DictScheduleView([{"A": "ISC" if covered else "Elec", "B": "Elec"}])
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)

        vars_for_week = []
        for f in _FELLOWS:
            v = opb.new_var()
            on = view.weekday_service(0, f) == _TARGET
            opb.add_unit(v if on else -v)
            vars_for_week.append(v)

        crit = SpecificAssignmentCriterion()
        crit.encode(sink, vars_for_week=vars_for_week, strength=strength, weight=100)
        return crit, view, opb, soft

    def test_soft_uncovered_fires_indicator(self, runner):
        _crit, _view, opb, soft = self._build(SOFT, covered=False)
        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced == 1  # single coverage indicator fires

    def test_soft_covered_no_violation(self, runner):
        _crit, _view, opb, soft = self._build(SOFT, covered=True)
        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced == 0

    def test_hard_uncovered_is_unsat(self, runner):
        _crit, _view, opb, _soft = self._build(HARD, covered=False)
        # Nobody holds the target and HARD requires someone -> UNSAT.
        assert runner.solve(opb).satisfiable is False
