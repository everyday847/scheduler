"""Contract test for the full_assignment archetype (ADR-0005).

"Each fellow in scope holds exactly one shift per week." encode fires one soft
indicator per (fellow, week) the fellow is UNASSIGNED; evaluate flags both
unassigned (0) and double-booked (>1). On the assignment grid the solver caps
sum(active) <= 1, so the only fireable soft state is "unassigned" — the geometry
where encode and evaluate coincide.

Agreement test: pin a concrete schedule into PB vars (forcing some cells empty),
encode, solve, and assert the forced soft-indicator count == the count of
unassigned cells evaluate() reports. Plus a hard-strength UNSAT case.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from schedule_rules.weekly.full_assignment import FullAssignmentCriterion
from schedule_rules.strength import HARD, SOFT
from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.constraint_sink import OpbConstraintSink
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner


ROUNDINGSAT_BINARY = (
    Path(__file__).resolve().parent.parent / "vendor" / "roundingsat" / "build" / "roundingsat"
)
_SHIFTS = ("Stroke", "NCC1", "Elec")


@pytest.fixture
def runner():
    if not ROUNDINGSAT_BINARY.exists():
        pytest.skip("RoundingSat binary not built")
    return RoundingSatRunner(ROUNDINGSAT_BINARY)


class DictScheduleView:
    def __init__(self, weekday):
        self._weekday = weekday  # weekday[w] = {fellow: shift or [shifts]}

    @property
    def num_weeks(self) -> int:
        return len(self._weekday)

    def weekday_service(self, week, fellow):
        if 0 <= week < len(self._weekday):
            v = self._weekday[week].get(fellow, "")
            return v if isinstance(v, str) else ""
        return ""

    def all_services(self, week, fellow):
        if 0 <= week < len(self._weekday):
            v = self._weekday[week].get(fellow, "")
            if isinstance(v, list):
                return [s for s in v if s]
            return [v] if v else []
        return []

    def night_holder(self, day):
        return None


_FELLOWS = ["A", "B"]


# A=fully assigned both weeks; B=unassigned week 0, assigned week 1, double-booked
# week 2. Unassigned cells = 1 (B,wk0). Double-booked = 1 (B,wk2). Empties = 1.
def _view():
    return DictScheduleView([
        {"A": "Stroke", "B": ""},
        {"A": "NCC1",   "B": "Elec"},
        {"A": "Elec",   "B": ["Stroke", "NCC1"]},
    ])


_WINDOW = range(0, 3)


class TestEvaluate:
    def setup_method(self):
        self.crit = FullAssignmentCriterion()
        self.view = _view()

    def test_flags_unassigned_and_double_booked(self):
        cells = self.crit.evaluate(self.view, fellows=_FELLOWS, window=_WINDOW)
        # (B,0,0) unassigned and (B,2,2) double-booked
        assert set(cells) == {("B", 0, 0), ("B", 2, 2)}

    def test_fully_assigned_no_violation(self):
        cells = self.crit.evaluate(self.view, fellows=["A"], window=_WINDOW)
        assert cells == []


class TestEncodeEvaluateAgree:
    def _build(self, strength, *, leave_empty):
        """Pin a 2-fellow x 2-week grid. *leave_empty* = set of (fellow, week)
        cells forced to hold NO shift (the unassigned case)."""
        view_rows = [
            {"A": "Stroke", "B": "NCC1"},
            {"A": "NCC1",   "B": "Elec"},
        ]
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)

        fellows = ["A", "B"]
        week_var_lists = []
        n_empty = 0
        for f in fellows:
            for w in range(2):
                active = []
                empty = (f, w) in leave_empty
                if empty:
                    n_empty += 1
                for s in _SHIFTS:
                    v = opb.new_var()
                    on = (not empty) and view_rows[w][f] == s
                    # Each grid cell holds AT MOST one shift (the Solver Invariant).
                    opb.add_unit(v if on else -v)
                    active.append(v)
                week_var_lists.append(active)

        crit = FullAssignmentCriterion()
        crit.encode(sink, week_var_lists=week_var_lists, strength=strength, weight=100)
        return n_empty, opb, soft

    def test_soft_unassigned_count_matches(self, runner):
        n_empty, opb, soft = self._build(SOFT, leave_empty={("B", 0), ("A", 1)})
        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced == n_empty == 2

    def test_soft_all_assigned_no_violation(self, runner):
        _n, opb, soft = self._build(SOFT, leave_empty=set())
        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced == 0

    def test_hard_strength_forbids_unassigned(self, runner):
        # A cell pinned empty under HARD at_least-one is UNSAT.
        _n, opb, _soft = self._build(HARD, leave_empty={("B", 0)})
        assert runner.solve(opb).satisfiable is False
