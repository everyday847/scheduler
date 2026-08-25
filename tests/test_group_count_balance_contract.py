"""Contract test for the group_count_balance archetype (ask #4).

"The per-fellow counts of shift-set S within window W, across fellows in a group,
differ pairwise by at most max_difference." A soft violation fires per fellow
PAIR whose on-service counts differ by more than max_difference. This is the
first archetype that balances counts ACROSS fellows (windowed_balance balances
ONE fellow across two windows — a different shape).

Agreement test: pin a concrete schedule into PB vars, encode, solve, and assert
the forced soft-indicator count == evaluate()'s violating-pair count. Plus a
hard-strength UNSAT case.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from schedule_rules.criteria.group_count_balance import GroupCountBalanceCriterion
from schedule_rules.strength import HARD, SOFT
from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.constraint_sink import OpbConstraintSink
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner


ROUNDINGSAT_BINARY = (
    Path(__file__).resolve().parent.parent / "vendor" / "roundingsat" / "build" / "roundingsat"
)
_ON_SERVICE = ("Stroke", "NCC1", "NCC2", "Swing")


@pytest.fixture
def runner():
    if not ROUNDINGSAT_BINARY.exists():
        pytest.skip("RoundingSat binary not built")
    return RoundingSatRunner(ROUNDINGSAT_BINARY)


class DictScheduleView:
    def __init__(self, weekday):
        self._weekday = weekday  # weekday[w] = {fellow: shift}

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


# A 5-week window, 3 fellows. On-service counts: A=3, B=3, C=5.
# max_difference=1 -> pairs (A,B)=0 ok, (A,C)=2 violate, (B,C)=2 violate => 2.
def _view():
    return DictScheduleView([
        {"A": "Stroke", "B": "Stroke", "C": "Stroke"},
        {"A": "NCC1",   "B": "NCC1",   "C": "NCC1"},
        {"A": "NCC2",   "B": "NCC2",   "C": "NCC2"},
        {"A": "Elec",   "B": "Elec",   "C": "NCC1"},
        {"A": "Elec",   "B": "Elec",   "C": "Swing"},
    ])


_FELLOWS = ["A", "B", "C"]
_WINDOW = (0, 5)


class TestEvaluate:
    def setup_method(self):
        self.crit = GroupCountBalanceCriterion()
        self.view = _view()

    def test_counts_within_bound_no_violation(self):
        pairs = self.crit.evaluate(self.view, fellows=_FELLOWS, shifts=_ON_SERVICE,
                                   window=_WINDOW, max_difference=2)
        assert pairs == []   # max spread is 2, bound is 2 -> ok

    def test_violating_pairs_reported(self):
        pairs = self.crit.evaluate(self.view, fellows=_FELLOWS, shifts=_ON_SERVICE,
                                   window=_WINDOW, max_difference=1)
        assert set(pairs) == {("A", "C"), ("B", "C")}

    def test_window_restricts_counting(self):
        # window [0,3): all three have 3 on-service -> no violation at any bound>=0
        pairs = self.crit.evaluate(self.view, fellows=_FELLOWS, shifts=_ON_SERVICE,
                                   window=(0, 3), max_difference=0)
        assert pairs == []


class TestEncodeEvaluateAgree:
    def _build(self, max_difference, strength):
        view = _view()
        crit = GroupCountBalanceCriterion()
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)

        # Pin (fellow, week, shift) vars to the view; build per-fellow on-service
        # var lists over the window.
        fellow_var_lists = []
        for f in _FELLOWS:
            vlist = []
            for w in range(_WINDOW[0], _WINDOW[1]):
                for s in _ON_SERVICE:
                    v = opb.new_var()
                    on = view.weekday_service(w, f) == s
                    opb.add_unit(v if on else -v)
                    vlist.append(v)
            fellow_var_lists.append(vlist)

        crit.encode(sink, fellow_var_lists=fellow_var_lists,
                    max_difference=max_difference, strength=strength, weight=100)
        return crit, view, opb, soft

    def test_soft_violation_count_matches_evaluate(self, runner):
        crit, view, opb, soft = self._build(max_difference=1, strength=SOFT)
        expected = len(crit.evaluate(view, fellows=_FELLOWS, shifts=_ON_SERVICE,
                                     window=_WINDOW, max_difference=1))
        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced == expected == 2

    def test_within_bound_no_soft_violations(self, runner):
        crit, view, opb, soft = self._build(max_difference=2, strength=SOFT)
        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced == 0

    def test_hard_strength_forbids_imbalance(self, runner):
        _crit, _view, opb, _soft = self._build(max_difference=1, strength=HARD)
        # The pinned schedule has a spread of 2 > 1, so hard balance is UNSAT.
        assert runner.solve(opb).satisfiable is False
