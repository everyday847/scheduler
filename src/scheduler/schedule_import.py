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
        wb.close()
        raise ValueError("Empty spreadsheet")

    # Detect workbook format: row 2 contains "Weekday"
    if len(rows) >= 2:
        row2 = [str(cell or "").strip() for cell in rows[1]]
        if "Weekday" in row2:
            result = _parse_workbook_format(rows)
            wb.close()
            return result

    # Simple format: one column per fellow
    header = [str(cell or "").strip() for cell in rows[0]]
    fellow_cols = [(i, name) for i, name in enumerate(header) if name and name not in SKIP_COLUMNS]

    if not fellow_cols:
        wb.close()
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


def _parse_workbook_format(rows: list[tuple]) -> ImportResult:
    """Parse the 3-column-per-fellow workbook format.

    Layout:
      Row 1: "Week", <Fellow A merged across 3 cols>, <Fellow B merged across 3 cols>, ...
      Row 2: "Week", "Weekday", "Weekend", "Nights", "Weekday", "Weekend", "Nights", ...
      Row 3+: week number, weekday value, weekend value, nights value, ...

    We extract only the Weekday column for each fellow.
    """
    row1 = [str(cell or "").strip() for cell in rows[0]]
    row2 = [str(cell or "").strip() for cell in rows[1]]

    # Find weekday columns (where row2 == "Weekday")
    weekday_cols = [i for i, val in enumerate(row2) if val == "Weekday"]

    # For each weekday column, find the fellow name in row 1.
    # Due to merged cells in read_only mode, the name appears only in the
    # top-left cell of the merge; other cells are None/empty.
    # The "Weekday" column is the first of the 3-column group, so the name
    # should be at that same column index. If not, look backwards.
    fellow_cols: list[tuple[int, str]] = []
    for col_idx in weekday_cols:
        name = row1[col_idx] if col_idx < len(row1) else ""
        # If empty (merged cell artifact), look backwards up to 3 columns
        if not name:
            for back in range(1, 4):
                if col_idx - back >= 0 and row1[col_idx - back]:
                    name = row1[col_idx - back]
                    break
        if name and name != "Week":
            fellow_cols.append((col_idx, name))

    if not fellow_cols:
        raise ValueError("No fellow columns found in workbook format")

    fellow_names = [name for _, name in fellow_cols]
    assignments: dict[str, list[str]] = {name: [] for name in fellow_names}
    shifts_found: set[str] = set()

    for row in rows[2:]:  # Skip 2 header rows
        padded = list(row) + [None] * max(0, len(row1) - len(list(row)))
        for col_idx, name in fellow_cols:
            value = str(padded[col_idx] or "").strip()
            assignments[name].append(value)
            if value:
                shifts_found.add(value)

    num_weeks = len(rows) - 2  # Subtract 2 header rows

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
