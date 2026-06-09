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


def _build(tmp_path: Path, weekday_by_week, weekend_by_week) -> Path:
    fellows = sorted({f for wk in weekday_by_week for f in wk})
    week_rows = [
        WeekRow(weekday_assignments=dict(wk), schedule_assignments={}, raw_row=[])
        for wk in weekday_by_week
    ]
    parsed = ParsedCallScheduleCsv(
        fellow_names=fellows, existing_schedule_columns=(),
        week_rows=week_rows, trailing_rows=[],
    )
    night = NightScheduleSolution(assignments_by_week=[_empty_nights() for _ in weekday_by_week])
    weekend = WeekendScheduleSolution(assignments_by_week=weekend_by_week)
    out = tmp_path / "wb.xlsx"
    write_schedule_workbook(parsed, night, weekend, out)
    return out


def _weekend_cell_red(path: Path, week_index: int, fellow_col_index: int) -> bool:
    """fellow_col_index: 0-based fellow index. Weekend col = 2 + fi*3 + 1 (1-based)."""
    wb = openpyxl.load_workbook(path)
    ws = wb["Fellow Schedule"]
    row = week_index + 3
    col = 2 + fellow_col_index * 3 + 1
    cell = ws.cell(row=row, column=col)
    font = cell.font
    if font is None or font.color is None:
        return False
    rgb = font.color.rgb
    return isinstance(rgb, str) and rgb.endswith(_RED)


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
