from __future__ import annotations

import csv
from dataclasses import dataclass, field
from io import BytesIO, StringIO
from typing import Any

import openpyxl


SKIP_COLUMNS = frozenset({
    "Week", "Day",
    "Weekend NCC1", "Weekend NCC2", "Weekend Stroke",
    "Night Mon", "Night Tue", "Night Wed", "Night Thu",
    "Night Fri", "Night Sat", "Night Sun",
})


@dataclass
class ImportResult:
    fellow_names: list[str]
    num_weeks: int
    shifts_found: set[str]
    assignments: dict[str, list[str]]


def parse_schedule_file(file_bytes: bytes, filename: str) -> ImportResult:
    """Parse a CSV or XLSX schedule file in our export format.

    Expected format: first row = headers (fellow names), subsequent rows =
    one row per week with shift assignments in each fellow's column.
    Columns matching known weekend/night role names or 'Week'/'Day' are skipped.
    """
    if filename.lower().endswith(".xlsx"):
        return _parse_xlsx(file_bytes)
    else:
        return _parse_csv(file_bytes)


def _parse_xlsx(file_bytes: bytes) -> ImportResult:
    wb = openpyxl.load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValueError("Empty spreadsheet")

    header = [str(cell or "").strip() for cell in rows[0]]
    fellow_cols = [(i, name) for i, name in enumerate(header) if name and name not in SKIP_COLUMNS]

    if not fellow_cols:
        raise ValueError("No fellow columns found in header row")

    fellow_names = [name for _, name in fellow_cols]
    assignments: dict[str, list[str]] = {name: [] for name in fellow_names}
    shifts_found: set[str] = set()

    for row in rows[1:]:
        padded = list(row) + [None] * max(0, len(header) - len(list(row)))
        for col_idx, name in fellow_cols:
            value = str(padded[col_idx] or "").strip()
            assignments[name].append(value)
            if value:
                shifts_found.add(value)

    num_weeks = len(rows) - 1
    wb.close()

    return ImportResult(
        fellow_names=fellow_names,
        num_weeks=num_weeks,
        shifts_found=shifts_found,
        assignments=assignments,
    )


def _parse_csv(file_bytes: bytes) -> ImportResult:
    text = file_bytes.decode("utf-8-sig")
    reader = csv.reader(StringIO(text))
    rows = list(reader)

    if not rows:
        raise ValueError("Empty CSV file")

    header = [cell.strip() for cell in rows[0]]
    fellow_cols = [(i, name) for i, name in enumerate(header) if name and name not in SKIP_COLUMNS]

    if not fellow_cols:
        raise ValueError("No fellow columns found in header row")

    fellow_names = [name for _, name in fellow_cols]
    assignments: dict[str, list[str]] = {name: [] for name in fellow_names}
    shifts_found: set[str] = set()

    for row in rows[1:]:
        padded = row + [""] * max(0, len(header) - len(row))
        for col_idx, name in fellow_cols:
            value = padded[col_idx].strip()
            assignments[name].append(value)
            if value:
                shifts_found.add(value)

    num_weeks = len(rows) - 1

    return ImportResult(
        fellow_names=fellow_names,
        num_weeks=num_weeks,
        shifts_found=shifts_found,
        assignments=assignments,
    )
