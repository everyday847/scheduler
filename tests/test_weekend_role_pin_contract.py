"""Contract test for the WeekendRolePin archetype (S3 migration).

ONE archetype covering two annual weekend-pin types:

  specific_weekend_assignment (PIN)   : a fellow MUST hold one weekend role
    (NCC1/NCC2/Stroke) across the given weeks.
  blocked_weekend (FORBID)            : a fellow must hold NONE of the three
    weekend roles across the given weeks.

Both are HARD unit pins (no soft variant). The agreement tests pin a concrete
schedule into PB vars, encode via the archetype, solve with vendored RoundingSat,
and assert the pin forces the role var TRUE / the forbid forces all role vars
FALSE. evaluate() is exercised against a DictScheduleView with weekend_role_holder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from schedule_rules.criteria.weekend_role_pin import WeekendRolePin, PIN, FORBID
from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.constraint_sink import OpbConstraintSink
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner


ROUNDINGSAT_BINARY = (
    Path(__file__).resolve().parent.parent / "vendor" / "roundingsat" / "build" / "roundingsat"
)
_NCC1, _NCC2, _STROKE = "Weekend NCC1", "Weekend NCC2", "Weekend Stroke"
_ALL_ROLES = (_NCC1, _NCC2, _STROKE)


@pytest.fixture
def runner():
    if not ROUNDINGSAT_BINARY.exists():
        pytest.skip("RoundingSat binary not built")
    return RoundingSatRunner(ROUNDINGSAT_BINARY)


class DictScheduleView:
    def __init__(self, weekend):
        # weekend[w] = {role_name: fellow}
        self._weekend = weekend

    @property
    def num_weeks(self) -> int:
        return len(self._weekend)

    def weekend_role_holder(self, week: int, role: str):
        if 0 <= week < len(self._weekend):
            return self._weekend[week].get(role)
        return None

    def weekday_service(self, week, fellow):
        return ""

    def fellows_on_shift(self, week, shift):
        return []

    def night_holder(self, day):
        return None


# ---------------------------------------------------------------------------
# evaluate — PIN (specific_weekend_assignment)
# ---------------------------------------------------------------------------
class TestPinEvaluate:
    def setup_method(self):
        self.rule = WeekendRolePin()

    def test_pin_satisfied_no_violation(self):
        view = DictScheduleView([{_NCC1: "A"}, {_NCC1: "A"}])
        flagged = self.rule.evaluate(
            view, fellow="A", weeks=[0, 1], role_name=_NCC1,
            all_role_names=_ALL_ROLES, action=PIN)
        assert flagged == []

    def test_pin_unheld_week_flagged(self):
        # A holds NCC1 in week 0 but not week 1 -> week 1 violates.
        view = DictScheduleView([{_NCC1: "A"}, {_NCC1: "B"}])
        flagged = self.rule.evaluate(
            view, fellow="A", weeks=[0, 1], role_name=_NCC1,
            all_role_names=_ALL_ROLES, action=PIN)
        assert [w for (w, _d) in flagged] == [1]

    def test_pin_out_of_range_week_skipped(self):
        view = DictScheduleView([{_NCC1: "A"}])
        flagged = self.rule.evaluate(
            view, fellow="A", weeks=[0, 5], role_name=_NCC1,
            all_role_names=_ALL_ROLES, action=PIN)
        assert flagged == []


# ---------------------------------------------------------------------------
# evaluate — FORBID (blocked_weekend)
# ---------------------------------------------------------------------------
class TestForbidEvaluate:
    def setup_method(self):
        self.rule = WeekendRolePin()

    def test_forbid_clear_no_violation(self):
        # A holds none of the three roles those weeks -> ok.
        view = DictScheduleView([{_NCC1: "B"}, {_STROKE: "C"}])
        flagged = self.rule.evaluate(
            view, fellow="A", weeks=[0, 1], role_name=None,
            all_role_names=_ALL_ROLES, action=FORBID)
        assert flagged == []

    def test_forbid_any_role_held_flagged(self):
        # A holds Weekend Stroke in week 1 -> week 1 violates.
        view = DictScheduleView([{_NCC1: "B"}, {_STROKE: "A"}])
        flagged = self.rule.evaluate(
            view, fellow="A", weeks=[0, 1], role_name=None,
            all_role_names=_ALL_ROLES, action=FORBID)
        assert [w for (w, _d) in flagged] == [1]


# ---------------------------------------------------------------------------
# encode/evaluate agreement (solver-backed)
# ---------------------------------------------------------------------------
class TestPinEncodeForcesTrue:
    def test_pin_forces_role_var_true(self, runner):
        rule = WeekendRolePin()
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)
        # Two role vars for the pinned fellow across two weeks.
        role_vars = [opb.new_var(), opb.new_var()]
        rule.encode(sink, role_vars=role_vars, action=PIN)
        result = runner.solve(opb)
        assert result.satisfiable
        # The pin forced each role var TRUE.
        assert all(result.assignment.get(v, False) for v in role_vars)
        assert soft == []  # HARD pin adds no soft channel


class TestForbidEncodeForcesFalse:
    def test_forbid_forces_all_role_vars_false(self, runner):
        rule = WeekendRolePin()
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)
        # Three role vars (NCC1/NCC2/Stroke) for one week.
        role_vars = [opb.new_var(), opb.new_var(), opb.new_var()]
        rule.encode(sink, role_vars=role_vars, action=FORBID)
        result = runner.solve(opb)
        assert result.satisfiable
        # The forbid forced each role var FALSE.
        assert all(not result.assignment.get(v, False) for v in role_vars)
        assert soft == []


class TestUnknownActionRejected:
    def test_encode_rejects_unknown_action(self):
        with pytest.raises(ValueError):
            WeekendRolePin().encode(_NullSink(), role_vars=[1], action="bogus")


class _NullSink:
    def add_unit(self, lit):
        pass
