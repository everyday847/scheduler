"""Slice 3 — contract tests for the cell-localizable WEEKEND criteria.

Two weekend soft criteria where a single weekend cell is "at fault" (so they can
be displayed by coloring that cell), each now a co-located Rule:

  WeekendRoleMismatch : a fellow holding a Weekend role (NCC1/NCC2/Stroke) whose
    weekday service that week is NOT the matching shift. (weight = weekend_mismatch_weight)
  PrevacationWeekend  : a fellow holding ANY Weekend role in the week immediately
    before a week they are on Vac. (weight = 1)

The aggregate/spacing weekend criteria (buffered-consecutive, weekend-total band)
have no single guilty cell and are deliberately NOT ported here.

Each agreement test pins a concrete schedule into PB vars, encodes via the rule,
solves, and asserts forced soft-indicator count == evaluate() count. evaluate()
is net-new (no prior weekend evaluator existed).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from schedule_rules.criteria.weekend_mismatch import WeekendRoleMismatchCriterion
from schedule_rules.criteria.prevacation_weekend import PrevacationWeekendCriterion
from schedule_rules.strength import SOFT
from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.constraint_sink import OpbConstraintSink
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner


ROUNDINGSAT_BINARY = (
    Path(__file__).resolve().parent.parent / "vendor" / "roundingsat" / "build" / "roundingsat"
)
_NCC1, _NCC2, _STROKE = "Weekend NCC1", "Weekend NCC2", "Weekend Stroke"


@pytest.fixture
def runner():
    if not ROUNDINGSAT_BINARY.exists():
        pytest.skip("RoundingSat binary not built")
    return RoundingSatRunner(ROUNDINGSAT_BINARY)


class DictScheduleView:
    def __init__(self, weekday, weekend):
        self._weekday = weekday
        self._weekend = weekend

    @property
    def num_weeks(self) -> int:
        return len(self._weekday)

    def weekday_service(self, week: int, fellow: str) -> str:
        if 0 <= week < len(self._weekday):
            return self._weekday[week].get(fellow, "")
        return ""

    def weekend_role_holder(self, week: int, role: str):
        if 0 <= week < len(self._weekend):
            return self._weekend[week].get(role)
        return None

    def fellows_on_shift(self, week, shift):
        if 0 <= week < len(self._weekday):
            return [f for f, s in self._weekday[week].items() if s == shift]
        return []


# ---------------------------------------------------------------------------
# WeekendRoleMismatch evaluate
# ---------------------------------------------------------------------------
class TestWeekendMismatchEvaluate:
    def setup_method(self):
        self.crit = WeekendRoleMismatchCriterion()

    def test_match_does_not_fire(self):
        view = DictScheduleView([{"A": "NCC1"}], [{_NCC1: "A"}])
        assert self.crit.evaluate(view, 0, _NCC1, "A") is False

    def test_mismatch_fires(self):
        # A is on weekday Stroke but holds Weekend NCC1 -> mismatch.
        view = DictScheduleView([{"A": "Stroke"}], [{_NCC1: "A"}])
        assert self.crit.evaluate(view, 0, _NCC1, "A") is True

    def test_stroke_role_matches_stroke_service(self):
        view = DictScheduleView([{"A": "Stroke"}], [{_STROKE: "A"}])
        assert self.crit.evaluate(view, 0, _STROKE, "A") is False

    def test_stroke_role_without_stroke_service_fires(self):
        view = DictScheduleView([{"A": "Elec"}], [{_STROKE: "A"}])
        assert self.crit.evaluate(view, 0, _STROKE, "A") is True


# ---------------------------------------------------------------------------
# PrevacationWeekend evaluate
# ---------------------------------------------------------------------------
class TestPrevacationEvaluate:
    def setup_method(self):
        self.crit = PrevacationWeekendCriterion()

    def test_weekend_before_vac_fires(self):
        # A on Vac in week 1; holds Weekend NCC1 in week 0 -> fires for week 0.
        view = DictScheduleView([{"A": "NCC1"}, {"A": "Vac"}], [{_NCC1: "A"}, {}])
        assert self.crit.evaluate(view, 0, _NCC1, "A") is True

    def test_weekend_not_before_vac_does_not_fire(self):
        view = DictScheduleView([{"A": "NCC1"}, {"A": "NCC2"}], [{_NCC1: "A"}, {}])
        assert self.crit.evaluate(view, 0, _NCC1, "A") is False

    def test_last_week_weekend_does_not_fire(self):
        view = DictScheduleView([{"A": "NCC1"}], [{_NCC1: "A"}])
        assert self.crit.evaluate(view, 0, _NCC1, "A") is False


# ---------------------------------------------------------------------------
# encode/evaluate agreement (solver-backed)
# ---------------------------------------------------------------------------
class TestWeekendMismatchAgreement:
    def test_mismatch_counts_match(self, runner):
        crit = WeekendRoleMismatchCriterion()
        view = DictScheduleView(
            [{"A": "Stroke", "B": "NCC1"}],
            [{_NCC1: "A", _NCC2: "B"}],  # A mismatches (Stroke vs NCC1); B mismatches (NCC1 vs NCC2)
        )
        placements = [(0, _NCC1, "A"), (0, _NCC2, "B")]
        expected = sum(1 for (w, r, f) in placements if crit.evaluate(view, w, r, f))

        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)
        for (w, r, f) in placements:
            wr_var = opb.new_var(); opb.add_unit(wr_var)  # holds the role
            matching_shift = {_NCC1: "NCC1", _NCC2: "NCC2", _STROKE: "Stroke"}[r]
            wd_var = opb.new_var()
            on_match = view.weekday_service(w, f) == matching_shift
            opb.add_unit(wd_var if on_match else -wd_var)
            crit.encode(sink, role_var=wr_var, weekday_match_var=wd_var,
                        strength=SOFT, weight=20)

        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced == expected == 2


class TestPrevacationAgreement:
    def test_prevacation_counts_match(self, runner):
        crit = PrevacationWeekendCriterion()
        view = DictScheduleView([{"A": "NCC1"}, {"A": "Vac"}], [{_NCC1: "A"}, {}])
        expected = 1 if crit.evaluate(view, 0, _NCC1, "A") else 0

        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)
        role_var = opb.new_var(); opb.add_unit(role_var)   # holds Weekend NCC1 week 0
        vac_var = opb.new_var(); opb.add_unit(vac_var)     # on Vac week 1
        crit.encode(sink, role_var=role_var, vac_var=vac_var, strength=SOFT, weight=1)

        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced == expected == 1
