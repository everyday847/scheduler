"""Tests for the stroke night-policy criterion in the canonical evaluator.

The stroke criterion must mirror the ENCODER
(schedule_solver._encode_night_policy_criteria), which has TWO distinct rules:

  1. Weekday Stroke service penalizes only the nights BEFORE a stroke workday —
     Sun/Mon/Tue/Wed/Thu nights (the morning after is a stroke workday), reading
     the Stroke service of the week containing that next morning. Fri/Sat nights
     are NOT penalized (the next morning is not a stroke workday).
  2. The Weekend STROKE role holder is penalized for Sat/Sun night.

Crucially, a fellow on weekday Stroke who holds Weekend NCC1 (not Weekend
Stroke) is NOT penalized for Saturday night — that was a false-red bug where the
evaluator fired stroke for ANY night in a weekday-Stroke week.

Dual-stroke weeks (two Stroke fellows on service) are exempt from both rules.
"""

from __future__ import annotations

from scheduler.call_schedule_common import (
    NIGHT_ROLES,
    ParsedCallScheduleCsv,
    WeekRow,
)
from scheduler.night_call_types import NightScheduleSolution
from scheduler.weekend_call_types import WeekendScheduleSolution
from scheduler.night_policy_types import (
    CRITERION_STROKE,
    criteria_for_assignment,
)


def _parsed(week_services: list[dict[str, str]]) -> ParsedCallScheduleCsv:
    fellows = sorted({f for wk in week_services for f in wk})
    week_rows = [
        WeekRow(weekday_assignments=dict(wk), schedule_assignments={}, raw_row=[])
        for wk in week_services
    ]
    return ParsedCallScheduleCsv(
        fellow_names=fellows, existing_schedule_columns=(),
        week_rows=week_rows, trailing_rows=[],
    )


def _weekend(weeks: list[dict[str, str]]) -> WeekendScheduleSolution:
    """weeks[i] maps role -> fellow (e.g. {'Weekend Stroke': 'Aditya'})."""
    return WeekendScheduleSolution(assignments_by_week=weeks)


# Mon=0 Tue=1 Wed=2 Thu=3 Fri=4 Sat=5 Sun=6
_MON, _TUE, _WED, _THU, _FRI, _SAT, _SUN = range(7)


class TestWeekdayStrokeNightsBeforeWorkday:
    """Rule 1: weekday Stroke penalizes Sun/Mon/Tue/Wed/Thu nights, not Fri/Sat."""

    def test_monday_night_penalized(self):
        parsed = _parsed([{"S": "Stroke"}])
        assert CRITERION_STROKE in criteria_for_assignment(parsed, 0, _MON, "S")

    def test_thursday_night_penalized(self):
        parsed = _parsed([{"S": "Stroke"}])
        assert CRITERION_STROKE in criteria_for_assignment(parsed, 0, _THU, "S")

    def test_friday_night_not_penalized(self):
        parsed = _parsed([{"S": "Stroke"}])
        assert CRITERION_STROKE not in criteria_for_assignment(parsed, 0, _FRI, "S")

    def test_saturday_night_not_penalized_for_weekday_stroke(self):
        """The reported false-red: weekday Stroke must NOT penalize Saturday."""
        parsed = _parsed([{"S": "Stroke"}])
        assert CRITERION_STROKE not in criteria_for_assignment(parsed, 0, _SAT, "S")

    def test_sunday_night_penalized_when_next_week_is_stroke(self):
        # Sunday night precedes Monday stroke workday of the NEXT week.
        parsed = _parsed([{"S": "NCC1"}, {"S": "Stroke"}])
        assert CRITERION_STROKE in criteria_for_assignment(parsed, 0, _SUN, "S")

    def test_sunday_night_not_penalized_when_next_week_not_stroke(self):
        parsed = _parsed([{"S": "Stroke"}, {"S": "NCC1"}])
        assert CRITERION_STROKE not in criteria_for_assignment(parsed, 0, _SUN, "S")


class TestWeekendStrokeRole:
    """Rule 2: only the Weekend STROKE role holder is penalized for Sat/Sun."""

    def test_weekend_stroke_holder_saturday_penalized(self):
        parsed = _parsed([{"A": "Stroke", "J": "Stroke"}])  # both on service
        # dual-stroke would exempt; pass empty dual set + single-stroke to isolate
        parsed = _parsed([{"A": "Telestroke/Clinic", "J": "Stroke"}])
        wknd = _weekend([{"Weekend Stroke": "A", "Weekend NCC1": "J"}])
        # A holds Weekend Stroke -> Saturday penalized.
        assert CRITERION_STROKE in criteria_for_assignment(
            parsed, 0, _SAT, "A", weekend_solution=wknd)

    def test_weekend_ncc1_holder_saturday_not_penalized(self):
        """Jinyuan's case: weekday Stroke + Weekend NCC1 + Saturday night must
        NOT be flagged stroke (he is not the Weekend Stroke holder)."""
        parsed = _parsed([{"J": "Stroke", "A": "Telestroke/Clinic"}])
        wknd = _weekend([{"Weekend NCC1": "J", "Weekend Stroke": "A"}])
        assert CRITERION_STROKE not in criteria_for_assignment(
            parsed, 0, _SAT, "J", weekend_solution=wknd)


class TestDualStrokeExemption:
    def test_dual_stroke_week_exempts_weekday(self):
        parsed = _parsed([{"S": "Stroke"}])
        assert CRITERION_STROKE not in criteria_for_assignment(
            parsed, 0, _MON, "S", dual_stroke_weeks=frozenset({0}))
