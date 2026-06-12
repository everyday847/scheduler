"""Contract tests for the WeekendNightCriterion archetype (ADR-0005).

Three instances of one Rule Shape — the relationship between a fellow's weekend
ROLE and their weekend-NIGHT call:
  Friday   (dow 4, FORBID): night-holder should hold NO weekend role. Per-role
    strength: NCC1 hard, NCC2/Stroke soft.
  Saturday (dow 5, REQUIRE): night-holder should hold an NCC weekend role.
  Sunday   (dow 6, REQUIRE + eligibility-forbid): night-holder should hold
    Weekend Stroke; a non-stroke-eligible fellow is barred from Sunday night.

Each agreement test pins a concrete schedule into PB vars, encodes, solves, and
asserts the forced soft-indicator count equals evaluate()'s count; a hard variant
asserts a pinned violation is UNSAT.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from schedule_rules.criteria.weekend_night import (
    WeekendNightCriterion, RoleStrength, EligibilityForbid, REQUIRE, FORBID)
from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.constraint_sink import OpbConstraintSink
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner


ROUNDINGSAT_BINARY = (
    Path(__file__).resolve().parent.parent / "vendor" / "roundingsat" / "build" / "roundingsat"
)
_MON, _TUE, _WED, _THU, _FRI, _SAT, _SUN = range(7)
_NCC1, _NCC2, _STROKE = "Weekend NCC1", "Weekend NCC2", "Weekend Stroke"


@pytest.fixture
def runner():
    if not ROUNDINGSAT_BINARY.exists():
        pytest.skip("RoundingSat binary not built")
    return RoundingSatRunner(ROUNDINGSAT_BINARY)


class DictScheduleView:
    def __init__(self, weekend: list[dict[str, str]]):
        self._weekend = weekend

    @property
    def num_weeks(self) -> int:
        return len(self._weekend)

    def weekday_service(self, week, fellow):
        return ""

    def weekend_role_holder(self, week, role):
        if 0 <= week < len(self._weekend):
            return self._weekend[week].get(role)
        return None

    def fellows_on_shift(self, week, shift):
        return []

    def night_holder(self, day):
        return None


def _friday() -> WeekendNightCriterion:
    return WeekendNightCriterion("weekend_night_friday", dow=_FRI, polarity=FORBID,
        role_strengths=(RoleStrength(_NCC1, hard=True),
                        RoleStrength(_NCC2, hard=False, weight=40),
                        RoleStrength(_STROKE, hard=False, weight=40)))


def _saturday() -> WeekendNightCriterion:
    return WeekendNightCriterion("weekend_night_saturday", dow=_SAT, polarity=REQUIRE,
        role_strengths=(RoleStrength(_NCC1, hard=False, weight=10),
                        RoleStrength(_NCC2, hard=False, weight=10)))


def _sunday() -> WeekendNightCriterion:
    return WeekendNightCriterion("weekend_night_sunday", dow=_SUN, polarity=REQUIRE,
        role_strengths=(RoleStrength(_STROKE, hard=False, weight=10),),
        eligibility=EligibilityForbid(_STROKE, hard=False, weight=10))


# ---------------------------------------------------------------------------
# evaluate geometry
# ---------------------------------------------------------------------------
class TestFridayEvaluate:
    def setup_method(self):
        self.crit = _friday()

    def test_holds_weekend_role_fires(self):
        view = DictScheduleView([{_NCC2: "A"}])
        assert self.crit.evaluate(view, 0, _FRI, "A") is True

    def test_no_weekend_role_does_not_fire(self):
        view = DictScheduleView([{_NCC2: "B"}])
        assert self.crit.evaluate(view, 0, _FRI, "A") is False

    def test_non_friday_does_not_fire(self):
        view = DictScheduleView([{_NCC1: "A"}])
        assert self.crit.evaluate(view, 0, _SAT, "A") is False


class TestSaturdayEvaluate:
    def setup_method(self):
        self.crit = _saturday()

    def test_holds_ncc_does_not_fire(self):
        view = DictScheduleView([{_NCC1: "A"}])
        assert self.crit.evaluate(view, 0, _SAT, "A") is False

    def test_no_ncc_fires(self):
        view = DictScheduleView([{_STROKE: "A"}])  # holds Stroke, not an NCC role
        assert self.crit.evaluate(view, 0, _SAT, "A") is True

    def test_non_saturday_does_not_fire(self):
        view = DictScheduleView([{}])
        assert self.crit.evaluate(view, 0, _FRI, "A") is False


class TestSundayEvaluate:
    def setup_method(self):
        self.crit = _sunday()

    def test_holds_stroke_does_not_fire(self):
        view = DictScheduleView([{_STROKE: "A"}])
        assert self.crit.evaluate(view, 0, _SUN, "A") is False

    def test_no_stroke_fires(self):
        view = DictScheduleView([{_NCC1: "A"}])
        assert self.crit.evaluate(view, 0, _SUN, "A") is True


# ---------------------------------------------------------------------------
# encode/evaluate agreement (solver-backed)
# ---------------------------------------------------------------------------
class TestFridayAgreement:
    def test_soft_counts_match(self, runner):
        crit = _friday()
        # A holds NCC2 (soft@40), B holds nothing.
        view = DictScheduleView([{_NCC2: "A"}])
        placements = [(0, "A"), (0, "B")]
        expected = sum(1 for (w, f) in placements if crit.evaluate(view, w, _FRI, f))

        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)
        for (w, f) in placements:
            night = opb.new_var(); opb.add_unit(night)
            role_vars = {}
            for role in (_NCC1, _NCC2, _STROKE):
                v = opb.new_var()
                opb.add_unit(v if view.weekend_role_holder(w, role) == f else -v)
                role_vars[role] = v
            crit.encode(sink, night_var=night, role_vars=role_vars)
        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced == expected == 1

    def test_hard_ncc1_forbids(self, runner):
        crit = _friday()
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)
        night = opb.new_var(); opb.add_unit(night)
        ncc1 = opb.new_var(); opb.add_unit(ncc1)  # holds Weekend NCC1
        crit.encode(sink, night_var=night, role_vars={_NCC1: ncc1})
        assert runner.solve(opb).satisfiable is False


class TestSaturdayAgreement:
    def test_require_soft_counts_match(self, runner):
        crit = _saturday()
        # A holds NCC1 (satisfied, no viol); B holds Stroke only (viol).
        view = DictScheduleView([{_NCC1: "A", _STROKE: "B"}])
        placements = [(0, "A"), (0, "B")]
        expected = sum(1 for (w, f) in placements if crit.evaluate(view, w, _SAT, f))

        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)
        for (w, f) in placements:
            night = opb.new_var(); opb.add_unit(night)
            role_vars = {}
            for role in (_NCC1, _NCC2):  # the required NCC roles the fellow can hold
                v = opb.new_var()
                opb.add_unit(v if view.weekend_role_holder(w, role) == f else -v)
                role_vars[role] = v
            crit.encode(sink, night_var=night, role_vars=role_vars)
        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced == expected == 1


class TestSundayAgreement:
    def test_require_stroke_soft(self, runner):
        crit = _sunday()
        view = DictScheduleView([{_STROKE: "A"}])  # A holds Stroke -> satisfied
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)
        night = opb.new_var(); opb.add_unit(night)
        stroke = opb.new_var(); opb.add_unit(stroke)  # A holds Weekend Stroke
        crit.encode(sink, night_var=night, role_vars={_STROKE: stroke})
        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced == 0  # holds Stroke, no violation

    def test_require_stroke_violation_penalized(self, runner):
        crit = _sunday()
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)
        night = opb.new_var(); opb.add_unit(night)
        stroke = opb.new_var(); opb.add_unit(-stroke)  # A does NOT hold Stroke
        crit.encode(sink, night_var=night, role_vars={_STROKE: stroke})
        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, w) in soft if result.assignment.get(ind, False))
        assert forced == 1  # night AND not-stroke -> soft@10

    def test_eligibility_forbid_soft(self, runner):
        """A fellow with no Weekend-Stroke var (not eligible) on Sunday night ->
        the eligibility penalty fires (soft here)."""
        crit = _sunday()
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)
        night = opb.new_var(); opb.add_unit(night)
        crit.encode(sink, night_var=night, role_vars={})  # no Stroke var -> not eligible
        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, w) in soft if result.assignment.get(ind, False))
        assert forced == 1

    def test_eligibility_forbid_hard_unsat(self, runner):
        crit = WeekendNightCriterion(
            "weekend_night_sunday", dow=_SUN, polarity=REQUIRE,
            role_strengths=(RoleStrength(_STROKE, hard=False, weight=10),),
            eligibility=EligibilityForbid(_STROKE, hard=True))
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)
        night = opb.new_var(); opb.add_unit(night)  # takes Sunday night
        crit.encode(sink, night_var=night, role_vars={})  # not eligible -> forbidden
        assert runner.solve(opb).satisfiable is False
