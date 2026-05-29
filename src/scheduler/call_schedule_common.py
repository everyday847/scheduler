from __future__ import annotations

from dataclasses import dataclass
import csv
from pathlib import Path


BLOCK_MARKER = "Block 1"
WEEKEND_ROLES = ("Weekend NCC1", "Weekend NCC2", "Weekend Stroke")
NIGHT_ROLES = ("Night Mon", "Night Tue", "Night Wed", "Night Thu", "Night Fri", "Night Sat", "Night Sun")
KNOWN_SCHEDULE_COLUMNS = frozenset({*WEEKEND_ROLES, *NIGHT_ROLES})

BLOCKING_EXACT_VALUES = frozenset({"Vacation", "Elective/NCS 2026", ""})
WEEKEND_BLOCKING_SUBSTRINGS = ("SICU", "MSICU")
NIGHT_BLOCKING_EXACT_VALUES = frozenset({"Vacation", "NS SCVMC", "AAN", "Elective/NCS 2026", ""})
NIGHT_BLOCKING_SUBSTRINGS = ("SICU", "MSICU")

DEFAULT_CCM_FELLOWS = frozenset(
    {
        "Pulmonary/Cardio CCM Fellow (Core NCC) 1",
        "Pulmonary/Cardio CCM Fellow (Core NCC) 2",
        "CCM  Fellow (Elective) 1",
    }
)
DEFAULT_ALWAYS_STROKE_ELIGIBLE = frozenset(
    {"Aditya Srivatsan", "Cameron Schmidt", "Harneet Dhillon", "Helena Xeros"}
)
DEFAULT_TELESTROKE_STROKE_ELIGIBLE = frozenset({"Jinyuan Liu", "Sokena Zaidi"})
DEFAULT_STROKE_ONLY_ELIGIBLE = frozenset({"Raya Aliakbar", "Joseph Conovaloff"})


@dataclass(frozen=True)
class WeekRow:
    weekday_assignments: dict[str, str]
    schedule_assignments: dict[str, str]
    raw_row: list[str]


@dataclass(frozen=True)
class ParsedCallScheduleCsv:
    fellow_names: list[str]
    existing_schedule_columns: tuple[str, ...]
    week_rows: list[WeekRow]
    trailing_rows: list[list[str]]


def parse_call_schedule_csv(path: str | Path) -> ParsedCallScheduleCsv:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))

    header = rows[0]
    fellow_names = [column for column in header if column not in KNOWN_SCHEDULE_COLUMNS]
    existing_schedule_columns = tuple(column for column in header if column in KNOWN_SCHEDULE_COLUMNS)
    expected_columns = len(fellow_names) + len(existing_schedule_columns)

    week_rows: list[WeekRow] = []
    trailing_rows: list[list[str]] = []
    for row in rows[1:]:
        padded_row = row + [""] * (expected_columns - len(row))
        if BLOCK_MARKER in padded_row:
            trailing_rows.append(padded_row)
            continue
        weekday_values = padded_row[: len(fellow_names)]
        schedule_values = padded_row[len(fellow_names) : expected_columns]
        week_rows.append(
            WeekRow(
                weekday_assignments=dict(zip(fellow_names, weekday_values, strict=True)),
                schedule_assignments=dict(zip(existing_schedule_columns, schedule_values, strict=True)),
                raw_row=padded_row[:expected_columns],
            )
        )

    return ParsedCallScheduleCsv(
        fellow_names=fellow_names,
        existing_schedule_columns=existing_schedule_columns,
        week_rows=week_rows,
        trailing_rows=trailing_rows,
    )


def is_weekend_blocked(weekday_assignment: str, *, is_ccm_fellow: bool) -> bool:
    if weekday_assignment == "" and is_ccm_fellow:
        return True
    if weekday_assignment in BLOCKING_EXACT_VALUES - {""}:
        return True
    return any(token in weekday_assignment for token in WEEKEND_BLOCKING_SUBSTRINGS)


def weekend_roles_for_fellow(
    fellow_name: str,
    weekday_assignment: str,
    *,
    ccm_fellows: frozenset[str] = DEFAULT_CCM_FELLOWS,
    always_stroke_eligible: frozenset[str] = DEFAULT_ALWAYS_STROKE_ELIGIBLE,
    telestroke_stroke_eligible: frozenset[str] = DEFAULT_TELESTROKE_STROKE_ELIGIBLE,
    stroke_only_eligible: frozenset[str] = DEFAULT_STROKE_ONLY_ELIGIBLE,
) -> set[str]:
    if is_weekend_blocked(weekday_assignment, is_ccm_fellow=fellow_name in ccm_fellows):
        return set()

    roles = {"Weekend NCC1", "Weekend NCC2"}
    if fellow_name in always_stroke_eligible:
        roles.add("Weekend Stroke")
    elif fellow_name in telestroke_stroke_eligible and weekday_assignment in {"Stroke", "Telestroke", "Telestroke/Clinic"}:
        roles.add("Weekend Stroke")
    elif fellow_name in stroke_only_eligible and weekday_assignment == "Stroke":
        roles.add("Weekend Stroke")
    return roles


def is_night_blocked(weekday_assignment: str) -> bool:
    if weekday_assignment in NIGHT_BLOCKING_EXACT_VALUES:
        return True
    return any(token in weekday_assignment for token in NIGHT_BLOCKING_SUBSTRINGS)


def is_anaesthesia_service(weekday_assignment: str) -> bool:
    return "Anesthesia" in weekday_assignment or "Anaesthesia" in weekday_assignment


def is_clinic_service(weekday_assignment: str) -> bool:
    return "Clinic" in weekday_assignment


def is_night_holiday_eligible(weekday_assignment: str) -> bool:
    return weekday_assignment in {"NCC1", "NCC2", "Stroke"}


def is_preferred_sunday_following_service(weekday_assignment: str) -> bool:
    if is_anaesthesia_service(weekday_assignment) or is_clinic_service(weekday_assignment):
        return False
    if weekday_assignment in {"Vacation", "NS SCVMC", "NIR", "Telestroke/Clinic"}:
        return False
    return not any(token in weekday_assignment for token in NIGHT_BLOCKING_SUBSTRINGS)
