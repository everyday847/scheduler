"""Unit tests for the post-processing NCC1<->NCC2 weekend-role swap.

The swap aligns weekday NCC role with weekend NCC role when it strictly improves
alignment, UNLESS the incoming Weekend-NCC1 fellow is on that week's Friday night
(which would create a friday_weekend_ncc1 HARD violation).
"""

from __future__ import annotations

from parafrost_scheduler.experiment import swap_ncc_weekend_roles
from scheduler.weekend_call_types import WeekendScheduleSolution
from scheduler.night_call_types import NightScheduleSolution
from scheduler.call_schedule_common import ParsedCallScheduleCsv, WeekRow


def _parsed(weekday_by_week):
    """weekday_by_week: list of {fellow: weekday_shift} dicts, one per week."""
    week_rows = [
        WeekRow(weekday_assignments=dict(wd), schedule_assignments={}, raw_row=[])
        for wd in weekday_by_week
    ]
    fellows = list(weekday_by_week[0].keys())
    return ParsedCallScheduleCsv(
        fellow_names=fellows, existing_schedule_columns=(),
        week_rows=week_rows, trailing_rows=[],
    )


def _weekend(assign_by_week):
    return WeekendScheduleSolution(assignments_by_week=[dict(a) for a in assign_by_week])


def _night(fri_by_week):
    """fri_by_week: list of fellow-name-on-Night-Fri (or '') per week."""
    weeks = []
    for fri in fri_by_week:
        wk = {role: "" for role in
              ("Night Mon", "Night Tue", "Night Wed", "Night Thu",
               "Night Fri", "Night Sat", "Night Sun")}
        wk["Night Fri"] = fri
        weeks.append(wk)
    return NightScheduleSolution(assignments_by_week=weeks)


def test_swap_aligns_when_beneficial():
    parsed = _parsed([{"A": "NCC2", "B": "NCC1"}])  # A does weekday NCC2, B weekday NCC1
    weekend = _weekend([{"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "C"}])
    night = _night([""])  # nobody on Friday night
    out = swap_ncc_weekend_roles(parsed, weekend, night)
    a = out.assignments_by_week[0]
    assert a["Weekend NCC1"] == "B" and a["Weekend NCC2"] == "A", "should swap to align"
    assert a["Weekend Stroke"] == "C", "stroke untouched"


def test_swap_skipped_when_friday_hard_violation():
    parsed = _parsed([{"A": "NCC2", "B": "NCC1"}])
    weekend = _weekend([{"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "C"}])
    night = _night(["B"])  # B (incoming Weekend NCC1) is on Friday night -> would be hard violation
    out = swap_ncc_weekend_roles(parsed, weekend, night)
    a = out.assignments_by_week[0]
    assert a["Weekend NCC1"] == "A" and a["Weekend NCC2"] == "B", "must NOT swap (hard guard)"


def test_noop_when_already_aligned():
    parsed = _parsed([{"A": "NCC1", "B": "NCC2"}])
    weekend = _weekend([{"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "C"}])
    night = _night([""])
    out = swap_ncc_weekend_roles(parsed, weekend, night)
    a = out.assignments_by_week[0]
    assert a["Weekend NCC1"] == "A" and a["Weekend NCC2"] == "B", "aligned -> unchanged"


def test_noop_when_no_strict_improvement():
    # Neither holder is on weekday NCC1/NCC2 -> swapping changes nothing useful.
    parsed = _parsed([{"A": "Elec", "B": "Stroke"}])
    weekend = _weekend([{"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "C"}])
    night = _night([""])
    out = swap_ncc_weekend_roles(parsed, weekend, night)
    a = out.assignments_by_week[0]
    assert a["Weekend NCC1"] == "A" and a["Weekend NCC2"] == "B", "no improvement -> unchanged"


def test_input_not_mutated():
    parsed = _parsed([{"A": "NCC2", "B": "NCC1"}])
    weekend = _weekend([{"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "C"}])
    night = _night([""])
    swap_ncc_weekend_roles(parsed, weekend, night)
    assert weekend.assignments_by_week[0]["Weekend NCC1"] == "A", "original must be untouched"
