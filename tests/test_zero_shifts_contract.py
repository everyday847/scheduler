"""Contract test for the zero_shifts archetype (ADR-0005).

"Selected fellows may never hold the named shifts." encode forbids
unconditionally — each (fellow, week, forbidden-shift) var is forced false (there
is no soft channel; a forbid is structurally hard). evaluate flags any held
forbidden shift on a concrete schedule.

Agreement test: a schedule that DROPS a fellow onto a forbidden shift is exactly
the schedule encode makes UNSAT and evaluate flags. We assert both: evaluate
reports the held forbidden shifts, and the encode that forbids those same vars is
UNSAT when they are pinned true; a clean schedule encodes SAT with no flags.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from schedule_rules.weekly.zero_shifts import ZeroShiftsCriterion
from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.constraint_sink import OpbConstraintSink
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner


ROUNDINGSAT_BINARY = (
    Path(__file__).resolve().parent.parent / "vendor" / "roundingsat" / "build" / "roundingsat"
)
_FORBIDDEN = {"NCC1", "NCC2"}
_SHIFTS = ("Stroke", "NCC1", "NCC2", "Elec")


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


_FELLOWS = ["A"]


class TestEvaluate:
    def test_clean_no_violation(self):
        view = DictScheduleView([{"A": "Stroke"}, {"A": "Elec"}])
        hits = ZeroShiftsCriterion().evaluate(
            view, fellows=_FELLOWS, forbidden=_FORBIDDEN, window=range(0, 2))
        assert hits == []

    def test_held_forbidden_flagged(self):
        view = DictScheduleView([{"A": "NCC1"}, {"A": "NCC2"}])
        hits = ZeroShiftsCriterion().evaluate(
            view, fellows=_FELLOWS, forbidden=_FORBIDDEN, window=range(0, 2))
        assert set(hits) == {("A", 0, "NCC1"), ("A", 1, "NCC2")}

    def test_window_restricts(self):
        view = DictScheduleView([{"A": "NCC1"}, {"A": "NCC2"}])
        hits = ZeroShiftsCriterion().evaluate(
            view, fellows=_FELLOWS, forbidden=_FORBIDDEN, window=range(0, 1))
        assert set(hits) == {("A", 0, "NCC1")}


class TestEncodeEvaluateAgree:
    def _build(self, view):
        """Pin the schedule, build the forbidden-shift var list (every
        (fellow, week, forbidden-shift) cell), and encode the forbid."""
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)

        forbidden_vars = []
        for f in _FELLOWS:
            for w in range(view.num_weeks):
                for s in _SHIFTS:
                    v = opb.new_var()
                    on = view.weekday_service(w, f) == s
                    opb.add_unit(v if on else -v)
                    if s in _FORBIDDEN:
                        forbidden_vars.append(v)

        ZeroShiftsCriterion().encode(sink, forbidden_vars=forbidden_vars)
        return opb, soft

    def test_held_forbidden_makes_unsat(self, runner):
        # The fellow is pinned on NCC1 (forbidden); the forbid contradicts -> UNSAT.
        view = DictScheduleView([{"A": "NCC1"}])
        opb, soft = self._build(view)
        # evaluate agrees the schedule violates.
        assert ZeroShiftsCriterion().evaluate(
            view, fellows=_FELLOWS, forbidden=_FORBIDDEN, window=range(0, 1))
        assert runner.solve(opb).satisfiable is False
        assert soft == []  # no soft channel

    def test_clean_schedule_sat(self, runner):
        view = DictScheduleView([{"A": "Stroke"}, {"A": "Elec"}])
        opb, soft = self._build(view)
        assert ZeroShiftsCriterion().evaluate(
            view, fellows=_FELLOWS, forbidden=_FORBIDDEN, window=range(0, 2)) == []
        assert runner.solve(opb).satisfiable is True
        assert soft == []
