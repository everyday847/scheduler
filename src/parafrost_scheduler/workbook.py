"""Excel workbook generator for the night call schedule.

Produces a two-sheet workbook:
  Sheet 1 "Fellow Schedule" — rows = weeks, groups of 3 columns per fellow
  Sheet 2 "Shift Coverage"  — rows = weeks, columns = shift roles
"""

from __future__ import annotations

import csv
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from scheduler.call_schedule_common import (
    NIGHT_ROLES,
    WEEKEND_ROLES,
    ParsedCallScheduleCsv,
    parse_call_schedule_csv,
)
from scheduler.night_call_types import NightScheduleSolution
from scheduler.weekend_call_types import WeekendScheduleSolution, BackupScheduleSolution
from scheduler.night_policy_types import (
    NightPolicyWeights,
    criteria_for_assignment,
    weekend_criteria_for_role,
)
from scheduler.call_schedule_common import ParsedCallScheduleCsv


def _weeks_with_dual_stroke(parsed: ParsedCallScheduleCsv) -> frozenset[int]:
    """Return week indices where 2+ fellows are on Stroke service (not Telestroke)."""
    dual = set()
    for week_index, row in enumerate(parsed.week_rows):
        stroke_count = sum(
            1 for svc in row.weekday_assignments.values()
            if "Stroke" in svc and "Telestroke" not in svc
        )
        if stroke_count >= 2:
            dual.add(week_index)
    return frozenset(dual)


# ---------------------------------------------------------------------------
# Colour palettes
# ---------------------------------------------------------------------------

SERVICE_COLORS: dict[str, str] = {
    "MICU": "B4C6E7",
    "MSICU": "B4C6E7",
    "Resifellow MSICU": "B4C6E7",
    "SICU": "FFE699",
    "Elective/SICU": "FFE699",
    "NCC": "C6E0B4",
    "NCC1": "C6E0B4",
    "NCC2": "C6E0B4",
    "Swing": "A9D08E",
    "Stroke": "F8CBAD",
    "Telestroke/Clinic": "DCF4D6",
    "Telestroke": "DCF4D6",
    "Anesthesia": "F8CBAD",
    "Anaesthesia": "F8CBAD",
    "Elec": "E7E6E6",
    "Clinic/Elective": "C4D677",
    "NIR": "DDB644",
    "Vacation": "D9E2F3",
    "NS SCVMC": "FFF3CC",
    "SCVMC": "FFF3CC",
}

FELLOW_CATEGORY_COLORS: dict[str, str] = {
    "ncc_senior": "C6E0B4",
    "ncc_junior": "A9D08E",
    "stroke_senior": "F8CBAD",
    "telestroke": "DCF4D6",
    "ccm": "B4C6E7",
}

FELLOW_CATEGORIES: dict[str, str] = {
    "Cindy Wong": "ncc_senior",
    "Alex Hanson": "ncc_senior",
    "Raya Aliakbar": "ncc_junior",
    "Joseph Conovaloff": "ncc_junior",
    "Aditya Srivatsan": "stroke_senior",
    "Cameron Schmidt": "stroke_senior",
    "Harneet Dhillon": "stroke_senior",
    "Helena Xeros": "stroke_senior",
    "Jinyuan Liu": "telestroke",
    "Sokena Zaidi": "telestroke",
}

_ELECTIVE_GRAY = "E7E6E6"
_RED_FONT = "FF0000"
_THIN = Side(style="thin")
_THICK = Side(style="medium")


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _make_fill(hex_color: str) -> PatternFill:
    return PatternFill(fill_type="solid", fgColor=hex_color)


def _bold_font(color: str = "000000") -> Font:
    return Font(bold=True, color=color)


def _normal_font(color: str = "000000") -> Font:
    return Font(color=color)


def _service_color(service: str) -> str | None:
    """Return a hex fill color for a weekday service string, or None."""
    if service in SERVICE_COLORS:
        return SERVICE_COLORS[service]
    # Substring match — try longest key first so "Resifellow MSICU" doesn't
    # accidentally match "MSICU" before being matched as a whole.
    for key in sorted(SERVICE_COLORS, key=len, reverse=True):
        if key in service:
            return SERVICE_COLORS[key]
    # "Elective/..." variants that didn't match anything above
    if "Elective" in service or "elective" in service:
        return _ELECTIVE_GRAY
    return None


# Call-grid per-fellow fills: three greens for NCC fellows (JR/SR), three blues for
# CCM fellows, assigned by position within each group so same-group fellows are
# easily told apart. Returned as hex (no leading '#').
_CALL_GREENS = ("A9D08E", "C6E0B4", "70AD47")   # mid, light, dark green
_CALL_BLUES = ("9DC3E6", "B4C6E7", "5B9BD5")    # mid, light, dark blue
_CALL_REDS = ("FF9999", "FFC7CE", "FF5050", "C00000")  # Stroke: light->dark red


def _call_fellow_fill_map(fellow_groups: dict[str, list[str]]) -> dict[str, str]:
    """Map each fellow name to a hex fill: NCC_JR/NCC_SR get greens, CCM gets blues,
    Stroke gets reds, cycling each group's shade palette by index within the group."""
    out: dict[str, str] = {}
    ncc = fellow_groups.get("NCC_JR", []) + fellow_groups.get("NCC_SR", [])
    for i, name in enumerate(ncc):
        out[name] = _CALL_GREENS[i % 3]
    for i, name in enumerate(fellow_groups.get("CCM", [])):
        out[name] = _CALL_BLUES[i % 3]
    for i, name in enumerate(fellow_groups.get("Stroke", [])):
        out[name] = _CALL_REDS[i % len(_CALL_REDS)]
    return out


def _fellow_category_color(fellow_name: str) -> str | None:
    """Return a hex fill colour for a fellow's shift-coverage cell."""
    if fellow_name in FELLOW_CATEGORIES:
        cat = FELLOW_CATEGORIES[fellow_name]
    elif "CCM" in fellow_name or "Pulmonary" in fellow_name:
        cat = "ccm"
    else:
        return None
    return FELLOW_CATEGORY_COLORS.get(cat)


def _right_border() -> Border:
    return Border(right=_THICK)


def _thin_all_border() -> Border:
    return Border(top=_THIN, bottom=_THIN, left=_THIN, right=_THIN)


# ---------------------------------------------------------------------------
# Violation detection
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sheet 1: Fellow Schedule
# ---------------------------------------------------------------------------

_DAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _weekend_role_for_fellow(
    weekend_assignments: dict[str, str],
    fellow_name: str,
) -> str:
    for role in WEEKEND_ROLES:
        if weekend_assignments.get(role) == fellow_name:
            # "Weekend NCC1" → "NCC1", "Weekend Stroke" → "Stroke"
            return role.replace("Weekend ", "")
    return ""


# Per-fellow column block: Weekday, Weekend, then one column per night Mon..Sun.
_FELLOW_STRIDE = 2 + 7
_NIGHT_HDR = ("M", "T", "W", "T", "F", "S", "S")  # narrow single-letter night cols


def _build_fellow_schedule_sheet(
    ws,
    parsed: ParsedCallScheduleCsv,
    night_solution: NightScheduleSolution,
    weekend_solution: WeekendScheduleSolution | None,
    hard_criteria: frozenset[str],
    night_gating=None,
    weekend_gating=None,
    weekend_night=None,
) -> None:
    fellows = parsed.fellow_names
    n_fellows = len(fellows)
    dual_stroke_weeks = _weeks_with_dual_stroke(parsed)

    # ---- Column widths ----
    ws.column_dimensions["A"].width = 6
    for fi in range(n_fellows):
        base_col = 2 + fi * _FELLOW_STRIDE  # 1-based: B=2
        ws.column_dimensions[get_column_letter(base_col)].width = 18     # Weekday
        ws.column_dimensions[get_column_letter(base_col + 1)].width = 8  # Weekend
        for d in range(7):                                               # Mon..Sun
            ws.column_dimensions[get_column_letter(base_col + 2 + d)].width = 3

    # ---- Row 1: merged fellow name headers ----
    ws.cell(row=1, column=1, value="Week").font = _bold_font()
    ws.cell(row=1, column=1).alignment = Alignment(horizontal="center")
    for fi, fellow in enumerate(fellows):
        start_col = 2 + fi * _FELLOW_STRIDE
        end_col = start_col + _FELLOW_STRIDE - 1
        ws.merge_cells(start_row=1, start_column=start_col, end_row=1, end_column=end_col)
        cell = ws.cell(row=1, column=start_col, value=fellow)
        cell.font = _bold_font()
        cell.alignment = Alignment(horizontal="center")
        for col in range(start_col, end_col + 1):
            right_side = _THICK if col == end_col else Side(style=None)
            ws.cell(row=1, column=col).border = Border(left=Side(style=None), right=right_side)

    # ---- Row 2: sub-headers (Weekday, Weekend, M T W T F S S) ----
    ws.cell(row=2, column=1, value="Week").font = _bold_font()
    ws.cell(row=2, column=1).alignment = Alignment(horizontal="center")
    for fi in range(n_fellows):
        base_col = 2 + fi * _FELLOW_STRIDE
        for offset, label in enumerate(("Weekday", "Weekend", *_NIGHT_HDR)):
            cell = ws.cell(row=2, column=base_col + offset, value=label)
            cell.font = _bold_font()
            cell.alignment = Alignment(horizontal="center")
        ws.cell(row=2, column=base_col + _FELLOW_STRIDE - 1).border = Border(right=_THICK)

    # ---- Data rows ----
    for week_index, week_row in enumerate(parsed.week_rows):
        row = week_index + 3
        ws.cell(row=row, column=1, value=week_index + 1)
        ws.cell(row=row, column=1).alignment = Alignment(horizontal="center")

        night_assignments = night_solution.assignments_by_week[week_index]
        weekend_assignments = (
            weekend_solution.assignments_by_week[week_index]
            if weekend_solution is not None
            else week_row.schedule_assignments
        )

        for fi, fellow in enumerate(fellows):
            base_col = 2 + fi * _FELLOW_STRIDE

            weekday_service = week_row.weekday_assignments.get(fellow, "")
            weekend_role = _weekend_role_for_fellow(weekend_assignments, fellow)

            # Weekday cell
            wd_cell = ws.cell(row=row, column=base_col, value=weekday_service)
            color = _service_color(weekday_service)
            if color:
                wd_cell.fill = _make_fill(color)

            # Weekend cell (red font when the weekend itself is at fault).
            weekend_cell = ws.cell(row=row, column=base_col + 1, value=weekend_role)
            if weekend_role:
                full_role = "Weekend " + weekend_role
                if weekend_criteria_for_role(
                    parsed, week_index, full_role, fellow,
                    weekend_solution=weekend_solution, weekend_gating=weekend_gating,
                ):
                    weekend_cell.font = _normal_font(_RED_FONT)

            # One cell per night Mon..Sun: marked "X" when the fellow holds that
            # night, reddened when THAT specific night triggers a soft criterion.
            for dow, role in enumerate(NIGHT_ROLES):
                cell = ws.cell(row=row, column=base_col + 2 + dow)
                cell.alignment = Alignment(horizontal="center")
                if night_assignments.get(role) != fellow:
                    continue
                cell.value = "X"
                criteria = criteria_for_assignment(
                    parsed, week_index, dow, fellow,
                    weekend_solution=weekend_solution,
                    dual_stroke_weeks=dual_stroke_weeks,
                    night_gating=night_gating, weekend_night=weekend_night,
                )
                if any(c not in hard_criteria for c in criteria):
                    cell.font = _normal_font(_RED_FONT)

            # Thick right border on last column of the fellow block.
            ws.cell(row=row, column=base_col + _FELLOW_STRIDE - 1).border = Border(right=_THICK)

    # ---- Freeze panes ----
    ws.freeze_panes = ws.cell(row=3, column=2)


# ---------------------------------------------------------------------------
# Sheet 2: Shift Coverage
# ---------------------------------------------------------------------------

_SHIFT_HEADERS = (
    "Week",
    "Wknd NCC1", "Wknd NCC2", "Wknd Stroke",
    "Night Mon", "Night Tue", "Night Wed", "Night Thu",
    "Night Fri", "Night Sat", "Night Sun",
)


def _has_shift_soft_violation(
    parsed: ParsedCallScheduleCsv,
    night_solution: NightScheduleSolution,
    weekend_solution: WeekendScheduleSolution | None,
    week_index: int,
    fellow_name: str,
    col_role: str,  # e.g. "Weekend NCC1" or "Night Fri"
    hard_criteria: frozenset[str],
    night_gating=None,
    weekend_night=None,
) -> bool:
    """Check whether this specific shift cell (fellow, week, role) violates a
    soft criterion. Delegates to the canonical criteria_for_assignment so it
    agrees with the per-night fellow-sheet coloring and the reported counts."""
    if col_role.startswith("Night "):
        day_abbr = col_role[len("Night "):]
        day_of_week = _DAY_ABBR.index(day_abbr)
        criteria = criteria_for_assignment(
            parsed, week_index, day_of_week, fellow_name,
            weekend_solution=weekend_solution,
            dual_stroke_weeks=_weeks_with_dual_stroke(parsed),
            night_gating=night_gating, weekend_night=weekend_night,
        )
        return any(c not in hard_criteria for c in criteria)

    # Weekend roles don't have direct soft violations (those are in the night solver)
    return False


def _build_shift_coverage_sheet(
    ws,
    parsed: ParsedCallScheduleCsv,
    night_solution: NightScheduleSolution,
    weekend_solution: WeekendScheduleSolution | None,
    hard_criteria: frozenset[str],
    night_gating=None,
    weekend_night=None,
) -> None:
    # Column widths
    ws.column_dimensions["A"].width = 6
    for col_idx in range(2, len(_SHIFT_HEADERS) + 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = 18

    # Row 1: headers
    for col_idx, header in enumerate(_SHIFT_HEADERS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = _bold_font()
        cell.alignment = Alignment(horizontal="center")

    # Map from display column header → actual role key
    # "Wknd NCC1" → "Weekend NCC1", "Night Mon" → "Night Mon"
    def _col_role(header: str) -> str:
        if header.startswith("Wknd "):
            return "Weekend " + header[len("Wknd "):]
        return header

    # Data rows
    for week_index, week_row in enumerate(parsed.week_rows):
        row = week_index + 2
        ws.cell(row=row, column=1, value=week_index + 1)
        ws.cell(row=row, column=1).alignment = Alignment(horizontal="center")

        night_assignments = night_solution.assignments_by_week[week_index]
        if weekend_solution is not None:
            weekend_assignments = weekend_solution.assignments_by_week[week_index]
        else:
            weekend_assignments = {k: v for k, v in week_row.schedule_assignments.items() if k in WEEKEND_ROLES}

        for col_idx, header in enumerate(_SHIFT_HEADERS[1:], start=2):
            role = _col_role(header)
            if role in WEEKEND_ROLES:
                fellow = weekend_assignments.get(role, "")
            else:
                fellow = night_assignments.get(role, "")

            cell = ws.cell(row=row, column=col_idx, value=fellow)
            if fellow:
                bg = _fellow_category_color(fellow)
                if bg:
                    cell.fill = _make_fill(bg)
                violation = _has_shift_soft_violation(
                    parsed, night_solution, weekend_solution,
                    week_index, fellow, role, hard_criteria,
                    night_gating=night_gating, weekend_night=weekend_night,
                )
                if violation:
                    cell.font = _normal_font(_RED_FONT)

    # Freeze panes
    ws.freeze_panes = ws.cell(row=2, column=2)


# ---------------------------------------------------------------------------
# Sheets 3 & 4: Backup
# ---------------------------------------------------------------------------

_BACKUP_FELLOW_GROUPS = ("NCC_JR", "NCC_SR", "STROKE")
_BACKUP_ROLE_NAMES = ("Backup", "Weekend Backup")


def _backup_eligible_fellow_names(
    parsed: ParsedCallScheduleCsv,
    fellow_groups: dict[str, list[str]] | None,
) -> list[str]:
    """Fellows shown on the per-fellow Backup sheet: NCC_JR/NCC_SR/STROKE only,
    in the parsed fellow order. Falls back to all fellows if groups unknown."""
    if not fellow_groups:
        return list(parsed.fellow_names)
    eligible = set()
    for grp in _BACKUP_FELLOW_GROUPS:
        eligible.update(fellow_groups.get(grp, []))
    return [f for f in parsed.fellow_names if f in eligible]


def _build_backup_fellow_sheet(
    ws,
    parsed: ParsedCallScheduleCsv,
    backup_solution: BackupScheduleSolution,
    fellow_groups: dict[str, list[str]] | None,
) -> None:
    """Per-fellow Backup view: NCC_JR/NCC_SR/STROKE fellows, two columns each
    ("Backup", "Wknd Backup") marking the weeks they hold each role."""
    fellows = _backup_eligible_fellow_names(parsed, fellow_groups)
    ws.column_dimensions["A"].width = 6
    for fi in range(len(fellows)):
        base_col = 2 + fi * 2
        ws.column_dimensions[get_column_letter(base_col)].width = 8
        ws.column_dimensions[get_column_letter(base_col + 1)].width = 12

    ws.cell(row=1, column=1, value="Week").font = _bold_font()
    for fi, fellow in enumerate(fellows):
        start_col = 2 + fi * 2
        ws.merge_cells(start_row=1, start_column=start_col, end_row=1, end_column=start_col + 1)
        c = ws.cell(row=1, column=start_col, value=fellow)
        c.font = _bold_font()
        c.alignment = Alignment(horizontal="center")
        ws.cell(row=1, column=start_col + 1).border = Border(right=_THICK)

    ws.cell(row=2, column=1, value="Week").font = _bold_font()
    for fi in range(len(fellows)):
        base_col = 2 + fi * 2
        for offset, label in enumerate(("Backup", "Wknd Bkup")):
            cell = ws.cell(row=2, column=base_col + offset, value=label)
            cell.font = _bold_font()
            cell.alignment = Alignment(horizontal="center")
        ws.cell(row=2, column=base_col + 1).border = Border(right=_THICK)

    for week_index in range(len(parsed.week_rows)):
        row = week_index + 3
        ws.cell(row=row, column=1, value=week_index + 1).alignment = Alignment(horizontal="center")
        assignments = backup_solution.assignments_by_week[week_index]
        for fi, fellow in enumerate(fellows):
            base_col = 2 + fi * 2
            ws.cell(row=row, column=base_col,
                    value="X" if assignments.get("Backup") == fellow else "")
            ws.cell(row=row, column=base_col + 1,
                    value="X" if assignments.get("Weekend Backup") == fellow else "")
            ws.cell(row=row, column=base_col + 1).border = Border(right=_THICK)
    ws.freeze_panes = ws.cell(row=3, column=2)


def _build_backup_coverage_sheet(
    ws,
    parsed: ParsedCallScheduleCsv,
    backup_solution: BackupScheduleSolution,
) -> None:
    """Per-service Backup view: rows = weeks, columns = Backup, Weekend Backup."""
    headers = ("Week", *_BACKUP_ROLE_NAMES)
    ws.column_dimensions["A"].width = 6
    for col_idx in range(2, len(headers) + 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = 18
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = _bold_font()
        cell.alignment = Alignment(horizontal="center")

    for week_index in range(len(parsed.week_rows)):
        row = week_index + 2
        ws.cell(row=row, column=1, value=week_index + 1).alignment = Alignment(horizontal="center")
        assignments = backup_solution.assignments_by_week[week_index]
        for col_idx, role in enumerate(_BACKUP_ROLE_NAMES, start=2):
            fellow = assignments.get(role, "")
            cell = ws.cell(row=row, column=col_idx, value=fellow)
            if fellow:
                bg = _fellow_category_color(fellow)
                if bg:
                    cell.fill = _make_fill(bg)
    ws.freeze_panes = ws.cell(row=2, column=2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_schedule_workbook(
    parsed: ParsedCallScheduleCsv,
    night_solution: NightScheduleSolution,
    weekend_solution: WeekendScheduleSolution | None,
    output_path: Path,
    *,
    hard_criteria: frozenset[str] = frozenset(),
    weights: NightPolicyWeights = NightPolicyWeights(),
    backup_solution: BackupScheduleSolution | None = None,
    fellow_groups: dict[str, list[str]] | None = None,
    night_gating=None,
    weekend_gating=None,
    weekend_night=None,
) -> None:
    """Write a color-coded Excel workbook for the combined night + weekend schedule.

    When ``backup_solution`` is provided, two extra sheets are added: a per-fellow
    "Backup Schedule" (NCC_JR/NCC_SR/STROKE only) and a per-service
    "Backup Coverage".

    ``night_gating`` / ``weekend_gating`` / ``weekend_night`` are the configured
    gating-criterion registries threaded to the cell-colorer so coloring uses the
    same config-driven criteria the solver encoded (no drift).
    """
    wb = openpyxl.Workbook()

    # Sheet 1
    ws1 = wb.active
    ws1.title = "Fellow Schedule"
    _build_fellow_schedule_sheet(ws1, parsed, night_solution, weekend_solution, hard_criteria,
                                 night_gating=night_gating, weekend_gating=weekend_gating,
                                 weekend_night=weekend_night)

    # Sheet 2
    ws2 = wb.create_sheet(title="Shift Coverage")
    _build_shift_coverage_sheet(ws2, parsed, night_solution, weekend_solution, hard_criteria,
                                night_gating=night_gating, weekend_night=weekend_night)

    # Sheets 3 & 4: Backup (optional)
    if backup_solution is not None:
        ws3 = wb.create_sheet(title="Backup Schedule")
        _build_backup_fellow_sheet(ws3, parsed, backup_solution, fellow_groups)
        ws4 = wb.create_sheet(title="Backup Coverage")
        _build_backup_coverage_sheet(ws4, parsed, backup_solution)

    wb.save(output_path)


# ---------------------------------------------------------------------------
# NF-model workbook (separate from wb7 write_schedule_workbook)
# ---------------------------------------------------------------------------

def _build_nf_weekly_sheet(ws, weekly_assignments, fellow_order, num_weeks):
    """Tab 1: rows=weeks, columns=fellows; each cell = the fellow's weekly label
    (NCC/MICU/Elec/Vac/Anaesthesia/...), colored by _service_color. One cell per
    fellow per week (no weekend/night sub-columns — the NF model's legacy layers
    are empty)."""
    ws.cell(row=1, column=1, value="Week")
    for fi, fellow in enumerate(fellow_order):
        ws.cell(row=1, column=2 + fi, value=fellow)
    for w in range(num_weeks):
        ws.cell(row=2 + w, column=1, value=w)
        for fi, fellow in enumerate(fellow_order):
            val = weekly_assignments[fellow][w]
            cell = ws.cell(row=2 + w, column=2 + fi, value=val)
            color = _service_color(val)
            if color:
                cell.fill = PatternFill(start_color=color, end_color=color, fill_type="solid")


def _build_call_detail_sheet(ws, call_assignments_by_day, horizon_start, start_dow,
                             fellow_fill=None):
    """Tab 2: a week-by-week call grid. Each week is a 3-row block (one row each for
    NCC1, NCC2, NF); the 7 columns are the days of that week (Mon..Sun). Each cell
    holds the fellow on that call role that day, colored by that fellow's category
    (three greens for NCC fellows, three blues for CCM) via *fellow_fill*. Each
    week's 3-row block is wrapped in a thick (medium) border for visibility.

    A leading "Week" column labels each block with its week index and start date;
    a "Role" column labels the 3 rows. Days before the horizon start (the partial
    first week) or past the horizon end are left blank.
    """
    from datetime import timedelta
    dow_names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    roles = ("NCC1", "NCC2", "NF")
    fellow_fill = fellow_fill or {}
    last_col = 2 + 7  # Week, Role, + 7 day columns

    # Header row: Week | Role | Mon..Sun
    ws.cell(row=1, column=1, value="Week")
    ws.cell(row=1, column=2, value="Role")
    for c, dn in enumerate(dow_names):
        ws.cell(row=1, column=3 + c, value=dn)

    num_days = len(call_assignments_by_day)
    if num_days == 0:
        return
    # Week index of each absolute day d is (start_dow + d) // 7; the first week may
    # be partial (starts mid-week). Group days by week index.
    last_week = (start_dow + num_days - 1) // 7
    for w in range(last_week + 1):
        block_top = 2 + w * 3
        block_bot = block_top + 2
        # Week label cell (merged across the 3 role rows for readability).
        # Date of the Monday (dow 0) of this week, derived from horizon_start.
        # horizon_start corresponds to absolute day 0 whose dow == start_dow.
        monday_offset = w * 7 - start_dow
        monday_date = horizon_start + timedelta(days=monday_offset)
        ws.cell(row=block_top, column=1,
                value=f"wk{w} ({monday_date.isoformat()})")
        ws.merge_cells(start_row=block_top, start_column=1,
                       end_row=block_bot, end_column=1)
        for ri, role in enumerate(roles):
            r = block_top + ri
            ws.cell(row=r, column=2, value=role)
            for dow in range(7):
                d = w * 7 - start_dow + dow
                if d < 0 or d >= num_days:
                    continue
                who = call_assignments_by_day[d].get(role, "")
                cell = ws.cell(row=r, column=3 + dow, value=who)
                color = fellow_fill.get(who)
                if color:
                    cell.fill = PatternFill(start_color=color, end_color=color,
                                            fill_type="solid")
        # Thick border around the whole 3x(label+role+7) week block.
        for r in range(block_top, block_bot + 1):
            for c in range(1, last_col + 1):
                cell = ws.cell(row=r, column=c)
                cur = cell.border
                cell.border = Border(
                    left=_THICK if c == 1 else cur.left,
                    right=_THICK if c == last_col else cur.right,
                    top=_THICK if r == block_top else cur.top,
                    bottom=_THICK if r == block_bot else cur.bottom,
                )


def write_nf_workbook(sol, config, output_path) -> None:
    """Write the NF-model workbook: weekly per-fellow tab + day-granular Call Detail
    (+ Backup if present). Distinct from write_schedule_workbook (wb7)."""
    wb = openpyxl.Workbook()
    fellow_order = list(sol.weekly_assignments.keys())
    num_weeks = len(next(iter(sol.weekly_assignments.values())))
    ws1 = wb.active
    ws1.title = "Fellow Schedule"
    _build_nf_weekly_sheet(ws1, sol.weekly_assignments, fellow_order, num_weeks)
    ws2 = wb.create_sheet(title="Call Detail")
    fellow_fill = _call_fellow_fill_map(config.fellow_groups)
    _build_call_detail_sheet(ws2, sol.call_assignments_by_day,
                             config.night_config.horizon_start_date, config.start_dow,
                             fellow_fill=fellow_fill)
    wb.save(output_path)


# ---------------------------------------------------------------------------
# Helper: reconstruct solution objects from saved CSV files
# ---------------------------------------------------------------------------

def parse_solutions_from_csvs(
    night_csv: str | Path,
    weekend_csv: str | Path,
) -> tuple[NightScheduleSolution, WeekendScheduleSolution, ParsedCallScheduleCsv]:
    """Read output CSVs and reconstruct solution + parsed objects.

    The night CSV has columns: [fellow...] [existing_schedule_cols...] [Night Mon ... Night Sun]
    The weekend CSV has columns: [fellow...] [existing_schedule_cols...] [Weekend NCC1 NCC2 Stroke]

    Returns (night_solution, weekend_solution, parsed) where ``parsed`` is built
    from the night CSV (which contains the original weekday assignments).
    """
    night_csv = Path(night_csv)
    weekend_csv = Path(weekend_csv)

    # ---- Parse night CSV ----
    with night_csv.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))

    header = rows[0]
    night_role_set = set(NIGHT_ROLES)
    weekend_role_set = set(WEEKEND_ROLES)

    # Split header into (fellow_names | existing_schedule_cols | night_roles)
    night_role_indices = [i for i, col in enumerate(header) if col in night_role_set]
    weekend_role_indices = [i for i, col in enumerate(header) if col in weekend_role_set]
    fellow_and_sched_indices = [
        i for i in range(len(header))
        if i not in set(night_role_indices) and i not in set(weekend_role_indices)
    ]
    # fellow names are those not in any known schedule column
    fellow_indices = [i for i in fellow_and_sched_indices if header[i] not in weekend_role_set]

    fellow_names = [header[i] for i in fellow_indices]
    existing_schedule_columns_from_header = [header[i] for i in weekend_role_indices]

    # Build ParsedCallScheduleCsv from the night CSV data rows
    from scheduler.call_schedule_common import WeekRow, BLOCK_MARKER
    week_rows: list[WeekRow] = []
    trailing_rows: list[list[str]] = []

    for row in rows[1:]:
        padded = row + [""] * max(0, len(header) - len(row))
        if BLOCK_MARKER in padded:
            trailing_rows.append(padded)
            continue
        weekday_assignments = {header[i]: padded[i] for i in fellow_indices}
        schedule_assignments = {header[i]: padded[i] for i in weekend_role_indices}
        week_rows.append(
            WeekRow(
                weekday_assignments=weekday_assignments,
                schedule_assignments=schedule_assignments,
                raw_row=[padded[i] for i in range(len(fellow_names) + len(existing_schedule_columns_from_header))],
            )
        )

    parsed = ParsedCallScheduleCsv(
        fellow_names=fellow_names,
        existing_schedule_columns=tuple(existing_schedule_columns_from_header),
        week_rows=week_rows,
        trailing_rows=trailing_rows,
    )

    # ---- Reconstruct NightScheduleSolution ----
    assignments_by_week: list[dict[str, str]] = []
    for row in rows[1:]:
        padded = row + [""] * max(0, len(header) - len(row))
        if BLOCK_MARKER in padded:
            continue
        night_assignments = {header[i]: padded[i] for i in night_role_indices}
        assignments_by_week.append(night_assignments)

    night_solution = NightScheduleSolution(assignments_by_week=assignments_by_week)

    # ---- Parse weekend CSV for WeekendScheduleSolution ----
    with weekend_csv.open(newline="", encoding="utf-8-sig") as fh:
        wrows = list(csv.reader(fh))

    wheader = wrows[0]
    w_role_indices = [i for i, col in enumerate(wheader) if col in weekend_role_set]

    weekend_assignments_by_week: list[dict[str, str]] = []
    for row in wrows[1:]:
        padded = row + [""] * max(0, len(wheader) - len(row))
        if BLOCK_MARKER in padded:
            continue
        wa = {wheader[i]: padded[i] for i in w_role_indices}
        weekend_assignments_by_week.append(wa)

    weekend_solution = WeekendScheduleSolution(assignments_by_week=weekend_assignments_by_week)

    return night_solution, weekend_solution, parsed
