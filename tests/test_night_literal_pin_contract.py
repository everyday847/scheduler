"""Contract test for the night_literal_pin archetype (S2).

Pin (force true) or forbid (force false) a set of night literals at resolved
day coordinates. FOUR night-layer kinds collapse onto this shape:
specific_night_assignment / friday_call_assignment (PIN), blocked_night /
group_night_requirement (FORBID).

Agreement test: pin a concrete night schedule into a DictScheduleView, assert
evaluate flags the right violations; then encode against a real OpbBuilder +
OpbConstraintSink and solve with the vendored RoundingSat, asserting a pinned
var is forced true and a forbidden var is excluded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from schedule_rules.night.night_literal_pin import NightLiteralPin, PIN, FORBID
from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.constraint_sink import OpbConstraintSink
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner


ROUNDINGSAT_BINARY = (
    Path(__file__).resolve().parent.parent / "vendor" / "roundingsat" / "build" / "roundingsat"
)


@pytest.fixture
def runner():
    if not ROUNDINGSAT_BINARY.exists():
        pytest.skip("RoundingSat binary not built")
    return RoundingSatRunner(ROUNDINGSAT_BINARY)


class DictScheduleView:
    """A view backed by an explicit day -> fellow night map."""

    def __init__(self, num_weeks, nights):
        self._num_weeks = num_weeks
        self._nights = nights  # nights[day] = fellow name or None

    @property
    def num_weeks(self) -> int:
        return self._num_weeks

    def weekday_service(self, week, fellow):
        return ""

    def all_services(self, week, fellow):
        return []

    def night_holder(self, day):
        return self._nights.get(day)


# Concrete night schedule: day0->Alice, day1->Bob, day2->Carol, day3 unassigned.
def _view():
    return DictScheduleView(num_weeks=4, nights={0: "Alice", 1: "Bob", 2: "Carol"})


class TestEvaluate:
    def setup_method(self):
        self.pin = NightLiteralPin()
        self.view = _view()

    def test_pin_satisfied_when_fellow_holds_night(self):
        bad = self.pin.evaluate(self.view, days=[0], action=PIN, fellow="Alice")
        assert bad == []

    def test_pin_violated_when_other_fellow_holds_night(self):
        bad = self.pin.evaluate(self.view, days=[0, 1], action=PIN, fellow="Alice")
        # day0 ok (Alice), day1 violated (Bob holds it).
        assert bad == [(1, "Bob")]

    def test_pin_violated_when_night_unassigned(self):
        bad = self.pin.evaluate(self.view, days=[3], action=PIN, fellow="Alice")
        assert bad == [(3, None)]

    def test_forbid_satisfied_when_fellow_absent(self):
        bad = self.pin.evaluate(self.view, days=[1, 2], action=FORBID, fellow="Alice")
        assert bad == []

    def test_forbid_violated_when_fellow_holds_night(self):
        bad = self.pin.evaluate(self.view, days=[0, 1], action=FORBID, fellow="Bob")
        assert bad == [(1, "Bob")]

    def test_group_requirement_flags_nonmembers(self):
        # Only Alice/Bob are allowed; Carol on day2 is a non-member violation.
        bad = self.pin.evaluate(
            self.view, days=[0, 1, 2, 3], action=FORBID,
            allowed_fellows=frozenset({"Alice", "Bob"}))
        assert bad == [(2, "Carol")]

    def test_group_requirement_unassigned_night_ok(self):
        bad = self.pin.evaluate(
            self.view, days=[3], action=FORBID,
            allowed_fellows=frozenset({"Alice"}))
        assert bad == []  # None holder is fine

    def test_unknown_action_raises(self):
        with pytest.raises(ValueError):
            self.pin.evaluate(self.view, days=[0], action="bogus", fellow="Alice")


class TestEncodeForcesAndExcludes:
    def test_pin_forces_var_true(self, runner):
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)
        pinned = opb.new_var()
        free = opb.new_var()
        NightLiteralPin().encode(sink, night_vars=[pinned], action=PIN)
        result = runner.solve(opb)
        assert result.satisfiable
        assert result.assignment.get(pinned) is True

    def test_forbid_excludes_var(self, runner):
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)
        forbidden = opb.new_var()
        NightLiteralPin().encode(sink, night_vars=[forbidden], action=FORBID)
        result = runner.solve(opb)
        assert result.satisfiable
        assert result.assignment.get(forbidden, False) is False

    def test_pin_and_forbid_together(self, runner):
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)
        a, b = opb.new_var(), opb.new_var()
        NightLiteralPin().encode(sink, night_vars=[a], action=PIN)
        NightLiteralPin().encode(sink, night_vars=[b], action=FORBID)
        result = runner.solve(opb)
        assert result.satisfiable
        assert result.assignment.get(a) is True
        assert result.assignment.get(b, False) is False

    def test_unknown_action_raises(self):
        opb = OpbBuilder()
        sink = OpbConstraintSink(opb, [])
        v = opb.new_var()
        with pytest.raises(ValueError):
            NightLiteralPin().encode(sink, night_vars=[v], action="bogus")

    def test_empty_var_list_is_noop(self):
        opb = OpbBuilder()
        sink = OpbConstraintSink(opb, [])
        before = opb.num_constraints
        NightLiteralPin().encode(sink, night_vars=[], action=PIN)
        assert opb.num_constraints == before
