"""Tests for the clinic night-policy criterion in the canonical evaluator.

The clinic criterion distinguishes the two clinic services:

  * ``Telestroke/Clinic`` fellows may take ANY weekday night (previous Sunday
    through that week's Thursday) — they NEVER trigger the clinic criterion.
  * ``Clinic/Elective`` fellows are softly discouraged from the previous-Sunday,
    Tuesday, and Wednesday nights (the nights before their Mon/Wed/Thu clinic
    days), but may take Monday and Thursday nights — so the clinic criterion
    fires ONLY on Sun/Tue/Wed nights, and only for Clinic/Elective.

These tests pin ``criteria_for_assignment`` (the single source of truth shared
by the violation counter and the workbook colorer) against that spec.
"""

from __future__ import annotations

from scheduler.call_schedule_common import (
    NIGHT_ROLES,
    ParsedCallScheduleCsv,
    WeekRow,
)
from scheduler.night_policy_types import (
    CRITERION_CLINIC,
    criteria_for_assignment,
)


def _parsed(week_services: list[dict[str, str]]) -> ParsedCallScheduleCsv:
    """Build a ParsedCallScheduleCsv from a per-week {fellow: service} map."""
    fellows = sorted({f for wk in week_services for f in wk})
    week_rows = [
        WeekRow(
            weekday_assignments=dict(wk),
            schedule_assignments={},
            raw_row=[],
        )
        for wk in week_services
    ]
    return ParsedCallScheduleCsv(
        fellow_names=fellows,
        existing_schedule_columns=(),
        week_rows=week_rows,
        trailing_rows=[],
    )


# day_of_week: Mon=0 Tue=1 Wed=2 Thu=3 Fri=4 Sat=5 Sun=6
_TUE, _WED, _SUN = 1, 2, 6
_MON, _THU, _FRI = 0, 3, 4


class TestClinicElectivePenalizedDays:
    """Clinic/Elective fellow: clinic criterion fires on Sun/Tue/Wed nights."""

    def test_tuesday_night_penalized(self):
        parsed = _parsed([{"Carol": "Clinic/Elective"}])
        assert CRITERION_CLINIC in criteria_for_assignment(parsed, 0, _TUE, "Carol")

    def test_wednesday_night_penalized(self):
        parsed = _parsed([{"Carol": "Clinic/Elective"}])
        assert CRITERION_CLINIC in criteria_for_assignment(parsed, 0, _WED, "Carol")

    def test_sunday_night_penalized_when_next_week_is_clinic(self):
        # Sunday night of week 0 precedes Monday clinic of week 1: read week 1.
        parsed = _parsed([
            {"Carol": "NCC1"},
            {"Carol": "Clinic/Elective"},
        ])
        assert CRITERION_CLINIC in criteria_for_assignment(parsed, 0, _SUN, "Carol")


class TestClinicElectiveAllowedDays:
    """Clinic/Elective fellow: clinic criterion does NOT fire on Mon/Thu/Fri."""

    def test_monday_night_allowed(self):
        parsed = _parsed([{"Carol": "Clinic/Elective"}])
        assert CRITERION_CLINIC not in criteria_for_assignment(parsed, 0, _MON, "Carol")

    def test_thursday_night_allowed(self):
        parsed = _parsed([{"Carol": "Clinic/Elective"}])
        assert CRITERION_CLINIC not in criteria_for_assignment(parsed, 0, _THU, "Carol")

    def test_friday_night_allowed(self):
        parsed = _parsed([{"Carol": "Clinic/Elective"}])
        assert CRITERION_CLINIC not in criteria_for_assignment(parsed, 0, _FRI, "Carol")

    def test_sunday_night_allowed_when_next_week_not_clinic(self):
        parsed = _parsed([
            {"Carol": "Clinic/Elective"},
            {"Carol": "NCC1"},
        ])
        assert CRITERION_CLINIC not in criteria_for_assignment(parsed, 0, _SUN, "Carol")


class TestTelestrokeClinicNeverPenalized:
    """Telestroke/Clinic fellow: clinic criterion never fires (any weekday night)."""

    def test_tuesday_night_not_penalized(self):
        parsed = _parsed([{"Jin": "Telestroke/Clinic"}])
        assert CRITERION_CLINIC not in criteria_for_assignment(parsed, 0, _TUE, "Jin")

    def test_wednesday_night_not_penalized(self):
        parsed = _parsed([{"Jin": "Telestroke/Clinic"}])
        assert CRITERION_CLINIC not in criteria_for_assignment(parsed, 0, _WED, "Jin")

    def test_monday_night_not_penalized(self):
        parsed = _parsed([{"Jin": "Telestroke/Clinic"}])
        assert CRITERION_CLINIC not in criteria_for_assignment(parsed, 0, _MON, "Jin")

    def test_thursday_night_not_penalized(self):
        parsed = _parsed([{"Jin": "Telestroke/Clinic"}])
        assert CRITERION_CLINIC not in criteria_for_assignment(parsed, 0, _THU, "Jin")

    def test_sunday_night_not_penalized_even_before_telestroke_week(self):
        parsed = _parsed([
            {"Jin": "NCC1"},
            {"Jin": "Telestroke/Clinic"},
        ])
        assert CRITERION_CLINIC not in criteria_for_assignment(parsed, 0, _SUN, "Jin")
