from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
import csv
from itertools import permutations
from pathlib import Path
import sys

from .call_schedule_common import (
    DEFAULT_CCM_FELLOWS,
    NIGHT_ROLES,
    ParsedCallScheduleCsv,
    is_anaesthesia_service,
    is_clinic_service,
    is_night_blocked as common_is_night_blocked,
    is_night_holiday_eligible as common_is_night_holiday_eligible,
    is_preferred_sunday_following_service as common_is_preferred_sunday_following_service,
)


HORIZON_START_DATE = date(2026, 6, 29)
DEFAULT_HOLIDAY_DATES = (
    date(2026, 7, 3),
    date(2026, 9, 7),
    date(2026, 11, 26),
    date(2026, 11, 27),
    date(2026, 12, 24),
    date(2026, 12, 25),
    date(2027, 1, 1),
    date(2027, 1, 18),
    date(2027, 2, 15),
    date(2027, 5, 31),
)


@dataclass(frozen=True)
class CountMultiset:
    names: tuple[str, ...]
    values: tuple[int, ...]


@dataclass(frozen=True)
class NightSolverConfig:
    total_nights: dict[str, int] = None
    friday_nights: dict[str, int] = None
    total_night_multisets: tuple[CountMultiset, ...] = (
        CountMultiset(("Jinyuan Liu", "Sokena Zaidi"), (15, 16)),
    )
    friday_night_multisets: tuple[CountMultiset, ...] = (
        CountMultiset(("Cindy Wong", "Alex Hanson"), (3, 4)),
    )
    ccm_fellows: frozenset[str] = DEFAULT_CCM_FELLOWS
    holiday_dates: tuple[date, ...] = DEFAULT_HOLIDAY_DATES
    horizon_start_date: date = HORIZON_START_DATE

    spacing_max_nights: int = 1
    spacing_window_days: int = 3
    blocking_exact_services: tuple[str, ...] = ("Vacation", "NS SCVMC", "AAN", "Elective/NCS 2026", "")
    blocking_substring_services: tuple[str, ...] = ("SICU", "MSICU")
    holiday_allowed_services: tuple[str, ...] = ("NCC1", "NCC2", "Stroke")
    penalty_weights: dict[str, int] = field(default_factory=lambda: {
        "anaesthesia": 1, "clinic": 1, "stroke": 5,
        "friday_weekend_ncc1": 1, "sunday_following": 1,
    })
    sunday_preferred_services: tuple[str, ...] = (
        "Elec", "Telestroke/Clinic", "Clinic/Elective",
        "SCVMC Rehab", "NIR", "ISC", "Vac",
    )

    def __post_init__(self) -> None:
        default_total_nights = {
            "Cindy Wong": 20,
            "Alex Hanson": 20,
            "Raya Aliakbar": 30,
            "Joseph Conovaloff": 30,
            "Aditya Srivatsan": 60,
            "Cameron Schmidt": 60,
            "Harneet Dhillon": 60,
            "Helena Xeros": 60,
        }
        default_friday_nights = {
            "Raya Aliakbar": 4,
            "Joseph Conovaloff": 4,
            "Aditya Srivatsan": 8,
            "Cameron Schmidt": 8,
            "Harneet Dhillon": 8,
            "Helena Xeros": 8,
            "Jinyuan Liu": 3,
            "Sokena Zaidi": 3,
        }
        object.__setattr__(self, "total_nights", dict(default_total_nights if self.total_nights is None else self.total_nights))
        object.__setattr__(self, "friday_nights", dict(default_friday_nights if self.friday_nights is None else self.friday_nights))


@dataclass(frozen=True)
class NightScheduleSolution:
    assignments_by_week: list[dict[str, str]]


@dataclass(frozen=True)
class NightSummary:
    total_nights_by_fellow: dict[str, int]
    friday_nights_by_fellow: dict[str, int]
    anaesthesia_nights: int
    clinic_nights: int
    friday_weekend_ncc1_violations: int
    sunday_following_service_violations: int


def absolute_day_index(week_index: int, day_of_week: int) -> int:
    return week_index * 7 + day_of_week


def date_to_week_and_day(day: date, *, horizon_start_date: date = HORIZON_START_DATE) -> tuple[int, int]:
    delta = (day - horizon_start_date).days
    return divmod(delta, 7)


def holiday_indices_for_config(config: NightSolverConfig) -> tuple[int, ...]:
    return tuple((holiday_date - config.horizon_start_date).days for holiday_date in config.holiday_dates)


def is_night_blocked(weekday_assignment: str, *, config: NightSolverConfig | None = None) -> bool:
    if config is not None:
        return common_is_night_blocked(weekday_assignment, exact_services=config.blocking_exact_services, substring_services=config.blocking_substring_services)
    return common_is_night_blocked(weekday_assignment)


def is_night_holiday_eligible(weekday_assignment: str, *, config: NightSolverConfig | None = None) -> bool:
    if config is not None:
        return common_is_night_holiday_eligible(weekday_assignment, allowed_services=config.holiday_allowed_services)
    return common_is_night_holiday_eligible(weekday_assignment)


def is_preferred_sunday_following_service(weekday_assignment: str, *, config: NightSolverConfig | None = None) -> bool:
    if config is not None:
        return common_is_preferred_sunday_following_service(weekday_assignment, preferred=config.sunday_preferred_services)
    return common_is_preferred_sunday_following_service(weekday_assignment)


def summarize_night_solution(parsed: ParsedCallScheduleCsv, solution: NightScheduleSolution, *, config: NightSolverConfig | None = None) -> NightSummary:
    total_nights_by_fellow = {fellow: 0 for fellow in parsed.fellow_names}
    friday_nights_by_fellow = {fellow: 0 for fellow in parsed.fellow_names}
    anaesthesia_nights = 0
    clinic_nights = 0
    friday_weekend_ncc1_violations = 0
    sunday_following_service_violations = 0

    for week_index, week_assignments in enumerate(solution.assignments_by_week):
        for day_of_week, role in enumerate(NIGHT_ROLES):
            fellow = week_assignments[role]
            total_nights_by_fellow[fellow] += 1
            weekday_service = parsed.week_rows[week_index].weekday_assignments[fellow]
            if day_of_week == 4:
                friday_nights_by_fellow[fellow] += 1
                if parsed.week_rows[week_index].schedule_assignments.get("Weekend NCC1") == fellow:
                    friday_weekend_ncc1_violations += 1
            if is_anaesthesia_service(weekday_service):
                anaesthesia_nights += 1
            if is_clinic_service(weekday_service):
                clinic_nights += 1
            if day_of_week == 6 and week_index + 1 < len(parsed.week_rows):
                following_service = parsed.week_rows[week_index + 1].weekday_assignments[fellow]
                if not is_preferred_sunday_following_service(following_service, config=config):
                    sunday_following_service_violations += 1

    return NightSummary(
        total_nights_by_fellow=total_nights_by_fellow,
        friday_nights_by_fellow=friday_nights_by_fellow,
        anaesthesia_nights=anaesthesia_nights,
        clinic_nights=clinic_nights,
        friday_weekend_ncc1_violations=friday_weekend_ncc1_violations,
        sunday_following_service_violations=sunday_following_service_violations,
    )


def write_night_schedule_csv(
    parsed: ParsedCallScheduleCsv,
    solution: NightScheduleSolution,
    output_path: str | Path,
) -> None:
    output = Path(output_path)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([*parsed.fellow_names, *parsed.existing_schedule_columns, *NIGHT_ROLES])
        for week_row, night_assignment in zip(parsed.week_rows, solution.assignments_by_week):
            writer.writerow([*week_row.raw_row, *(night_assignment[role] for role in NIGHT_ROLES)])
        for trailing_row in parsed.trailing_rows:
            writer.writerow([*trailing_row, *([""] * len(NIGHT_ROLES))])
