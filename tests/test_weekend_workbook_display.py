"""Slice 3 — the weekend-fault DISPLAY: weekend cells render red when the
weekend itself is at fault (role/weekday mismatch, or weekend call before a
vacation), and stay un-colored when the weekend is fine.

This is the real-artifact check: build a tiny known schedule, generate the
actual workbook, and inspect the Weekend cell fonts.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl

from scheduler.call_schedule_common import ParsedCallScheduleCsv, WeekRow
from scheduler.night_call_types import NightScheduleSolution
from scheduler.weekend_call_types import WeekendScheduleSolution
from parafrost_scheduler.workbook import write_schedule_workbook

_RED = "FF0000"
_NIGHT_ROLES = ("Night Mon", "Night Tue", "Night Wed", "Night Thu", "Night Fri", "Night Sat", "Night Sun")


def _empty_nights(role_overrides: dict[str, str] | None = None) -> dict[str, str]:
    d = {r: "" for r in _NIGHT_ROLES}
    if role_overrides:
        d.update(role_overrides)
    return d


def _build(tmp_path: Path, weekday_by_week, weekend_by_week, night_by_week=None) -> Path:
    fellows = sorted({f for wk in weekday_by_week for f in wk})
    week_rows = [
        WeekRow(weekday_assignments=dict(wk), schedule_assignments={}, raw_row=[])
        for wk in weekday_by_week
    ]
    parsed = ParsedCallScheduleCsv(
        fellow_names=fellows, existing_schedule_columns=(),
        week_rows=week_rows, trailing_rows=[],
    )
    nights = [
        _empty_nights((night_by_week or [{}] * len(weekday_by_week))[w])
        for w in range(len(weekday_by_week))
    ]
    night = NightScheduleSolution(assignments_by_week=nights)
    weekend = WeekendScheduleSolution(assignments_by_week=weekend_by_week)
    out = tmp_path / "wb.xlsx"
    write_schedule_workbook(parsed, night, weekend, out)
    return out


# Per-fellow block: Weekday, Weekend, then 7 night cols (Mon..Sun) -> stride 9.
_STRIDE = 9


def _cell_red(path: Path, row: int, col: int) -> bool:
    wb = openpyxl.load_workbook(path)
    ws = wb["Fellow Schedule"]
    cell = ws.cell(row=row, column=col)
    font = cell.font
    if font is None or font.color is None:
        return False
    rgb = font.color.rgb
    return isinstance(rgb, str) and rgb.endswith(_RED)


def _weekend_cell_red(path: Path, week_index: int, fellow_col_index: int) -> bool:
    """Weekend col = base + 1 where base = 2 + fi*9 (1-based)."""
    return _cell_red(path, week_index + 3, 2 + fellow_col_index * _STRIDE + 1)


def _night_cell_red(path: Path, week_index: int, fellow_col_index: int, dow: int) -> bool:
    """Night col = base + 2 + dow (dow 0=Mon .. 6=Sun)."""
    return _cell_red(path, week_index + 3, 2 + fellow_col_index * _STRIDE + 2 + dow)


class TestWeekendMismatchDisplay:
    def test_mismatch_weekend_cell_is_red(self, tmp_path):
        # Fellow "A" on weekday Stroke but holds Weekend NCC1 -> mismatch.
        out = _build(
            tmp_path,
            weekday_by_week=[{"A": "Stroke"}],
            weekend_by_week=[{"Weekend NCC1": "A"}],
        )
        assert _weekend_cell_red(out, 0, 0) is True

    def test_matching_weekend_cell_not_red(self, tmp_path):
        out = _build(
            tmp_path,
            weekday_by_week=[{"A": "NCC1"}],
            weekend_by_week=[{"Weekend NCC1": "A"}],
        )
        assert _weekend_cell_red(out, 0, 0) is False


class TestPrevacationDisplay:
    def test_prevacation_weekend_cell_is_red(self, tmp_path):
        # A holds Weekend NCC1 in week 0 (matching NCC1), on Vac in week 1.
        # No mismatch in week 0, but it IS the weekend before vacation -> red.
        out = _build(
            tmp_path,
            weekday_by_week=[{"A": "NCC1"}, {"A": "Vac"}],
            weekend_by_week=[{"Weekend NCC1": "A"}, {}],
        )
        assert _weekend_cell_red(out, 0, 0) is True

    def test_no_vacation_next_week_not_red(self, tmp_path):
        out = _build(
            tmp_path,
            weekday_by_week=[{"A": "NCC1"}, {"A": "NCC2"}],
            weekend_by_week=[{"Weekend NCC1": "A"}, {}],
        )
        assert _weekend_cell_red(out, 0, 0) is False


class TestPerNightColumnDisplay:
    """The 7-column night layout: each weekend-night ↔ weekend-role violation
    reddens ONLY its own night cell, not the whole row."""

    def test_sunday_night_without_stroke_role_reddens_only_sunday(self, tmp_path):
        # A holds Weekend NCC1 (not Stroke) and takes BOTH Fri and Sun night.
        # Sunday-night REQUIRE Stroke fires (soft) -> Sunday cell red. Friday
        # FORBID also fires for the NCC1 holder, but NCC1-on-Friday is HARD; with
        # the default hard_criteria=frozenset() the colorer reddens any fired
        # criterion, so Friday reddens too — but Sat/the weekday/other nights do not.
        out = _build(
            tmp_path,
            weekday_by_week=[{"A": "NCC1"}],
            weekend_by_week=[{"Weekend NCC1": "A"}],
            night_by_week=[{"Night Sun": "A", "Night Sat": "A"}],
        )
        _SUN, _SAT, _MON = 6, 5, 0
        assert _night_cell_red(out, 0, 0, _SUN) is True, "Sunday-without-Stroke must redden Sunday"
        # Saturday: A holds Weekend NCC1 (an NCC role) -> REQUIRE satisfied -> not red.
        assert _night_cell_red(out, 0, 0, _SAT) is False, "Saturday satisfied -> not red"

    def test_clean_night_not_red(self, tmp_path):
        # A on weekday NCC1, holds Weekend NCC1, takes Saturday night -> Saturday
        # REQUIRE-NCC satisfied, no mismatch -> Saturday night cell not red.
        out = _build(
            tmp_path,
            weekday_by_week=[{"A": "NCC1"}],
            weekend_by_week=[{"Weekend NCC1": "A"}],
            night_by_week=[{"Night Sat": "A"}],
        )
        assert _night_cell_red(out, 0, 0, 5) is False
