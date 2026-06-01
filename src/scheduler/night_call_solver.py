from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
import csv
from itertools import permutations
from pathlib import Path
import sys

try:
    from z3 import If, Int, IntVal, Optimize, Or, Sum, sat
except ImportError:
    pass

from .call_schedule_common import (
    DEFAULT_CCM_FELLOWS,
    NIGHT_ROLES,
    ParsedCallScheduleCsv,
    WEEKEND_ROLES,
    is_anaesthesia_service,
    is_clinic_service,
    is_night_blocked as common_is_night_blocked,
    is_night_holiday_eligible as common_is_night_holiday_eligible,
    is_preferred_sunday_following_service as common_is_preferred_sunday_following_service,
    parse_call_schedule_csv,
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


def parse_night_call_csv(path: str | Path) -> ParsedCallScheduleCsv:
    parsed = parse_call_schedule_csv(path)
    missing = [role for role in WEEKEND_ROLES if role not in parsed.existing_schedule_columns]
    if missing:
        raise ValueError(f"Night solver requires weekend columns: {', '.join(missing)}")
    return parsed


def solve_night_schedule(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig = NightSolverConfig(),
    emit_summary: bool = True,
) -> NightScheduleSolution:
    solver = Optimize()
    day_count = len(parsed.week_rows) * len(NIGHT_ROLES)
    day_vars = [Int(f"night_day_{day_index}") for day_index in range(day_count)]
    _add_night_constraints(solver, parsed, day_vars, config)
    _add_night_objectives(solver, parsed, day_vars, config)
    if solver.check() != sat:
        raise ValueError("Night call schedule is unsatisfiable")
    model = solver.model()
    solution = _build_night_solution(parsed, day_vars, model)
    if emit_summary:
        _print_night_summary(parsed, solution, config=config)
    return solution


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


def _add_night_constraints(solver: Optimize, parsed: ParsedCallScheduleCsv, day_vars: list[Int], config: NightSolverConfig) -> None:
    holiday_indices = set(holiday_indices_for_config(config))
    day_count = len(day_vars)
    fellow_count = len(parsed.fellow_names)
    for day_index in range(day_count):
        week_index, day_of_week = divmod(day_index, 7)
        solver.add(day_vars[day_index] >= 0)
        solver.add(day_vars[day_index] < fellow_count)
        week_row = parsed.week_rows[week_index]
        for fellow_index, fellow_name in enumerate(parsed.fellow_names):
            weekday_service = week_row.weekday_assignments[fellow_name]
            if fellow_name in config.ccm_fellows or is_night_blocked(weekday_service, config=config):
                solver.add(day_vars[day_index] != fellow_index)
            if day_index in holiday_indices and not is_night_holiday_eligible(weekday_service, config=config):
                solver.add(day_vars[day_index] != fellow_index)

    window = config.spacing_window_days
    for fellow_index in range(fellow_count):
        for start in range(day_count - (window - 1)):
            solver.add(Sum([_eq_indicator(day_vars[start + offset], fellow_index) for offset in range(window)]) <= config.spacing_max_nights)

    for fellow_name, total in config.total_nights.items():
        solver.add(_count_assignments(day_vars, parsed.fellow_names.index(fellow_name)) == total)
    for fellow_name, total in config.friday_nights.items():
        solver.add(_count_friday_assignments(day_vars, parsed.fellow_names.index(fellow_name)) == total)

    for multiset_constraint in config.total_night_multisets:
        _add_multiset_constraint(solver, day_vars, parsed.fellow_names, multiset_constraint, friday_only=False)
    for multiset_constraint in config.friday_night_multisets:
        _add_multiset_constraint(solver, day_vars, parsed.fellow_names, multiset_constraint, friday_only=True)


def _add_night_objectives(solver: Optimize, parsed: ParsedCallScheduleCsv, day_vars: list[Int], config: NightSolverConfig) -> None:
    penalties = _night_soft_violation_terms(parsed, day_vars, config=config)
    categories = [
        (penalties[0], config.penalty_weights["anaesthesia"]),
        (penalties[1], config.penalty_weights["clinic"]),
        (penalties[2], config.penalty_weights["friday_weekend_ncc1"]),
        (penalties[3], config.penalty_weights["sunday_following"]),
    ]
    for terms, weight in categories:
        if terms:
            solver.minimize(Sum([weight * t for t in terms]))


def _add_multiset_constraint(
    solver: Optimize,
    day_vars: list[Int],
    fellow_names: list[str],
    multiset_constraint: CountMultiset,
    *,
    friday_only: bool,
) -> None:
    unique_permutations = {tuple(ordering) for ordering in permutations(multiset_constraint.values)}
    disjuncts = []
    for ordering in unique_permutations:
        conjuncts = []
        for fellow_name, total in zip(multiset_constraint.names, ordering):
            fellow_index = fellow_names.index(fellow_name)
            count = _count_friday_assignments(day_vars, fellow_index) if friday_only else _count_assignments(day_vars, fellow_index)
            conjuncts.append(count == total)
        disjuncts.append(Sum([If(conjunct, 1, 0) for conjunct in conjuncts]) == len(conjuncts))
    solver.add(Or(*disjuncts))


def _count_assignments(day_vars: list[Int], fellow_index: int):
    return Sum([_eq_indicator(variable, fellow_index) for variable in day_vars])


def _count_friday_assignments(day_vars: list[Int], fellow_index: int):
    return Sum([_eq_indicator(day_vars[day_index], fellow_index) for day_index in range(4, len(day_vars), 7)])


def _eq_indicator(variable, fellow_index: int):
    return If(variable == fellow_index, 1, 0)


def _sum_or_zero(terms: list):
    return Sum(terms) if terms else IntVal(0)


def count_night_soft_violations(parsed: ParsedCallScheduleCsv, day_vars: list[Int], *, config: NightSolverConfig | None = None) -> object:
    return Sum([_sum_or_zero(terms) for terms in _night_soft_violation_terms(parsed, day_vars, config=config)])


def _night_soft_violation_terms(parsed: ParsedCallScheduleCsv, day_vars: list[Int], *, config: NightSolverConfig | None = None) -> list[list]:
    anaesthesia_penalties = []
    clinic_penalties = []
    friday_weekend_penalties = []
    sunday_following_penalties = []
    include_sunday = True
    if config is not None:
        include_sunday = config.penalty_weights.get("sunday_following", 0) > 0
    for day_index, variable in enumerate(day_vars):
        week_index, day_of_week = divmod(day_index, 7)
        week_row = parsed.week_rows[week_index]
        for fellow_index, fellow_name in enumerate(parsed.fellow_names):
            weekday_service = week_row.weekday_assignments[fellow_name]
            eq = _eq_indicator(variable, fellow_index)
            if is_anaesthesia_service(weekday_service):
                anaesthesia_penalties.append(eq)
            if is_clinic_service(weekday_service):
                clinic_penalties.append(eq)
            if day_of_week == 4 and week_row.schedule_assignments.get("Weekend NCC1") == fellow_name:
                friday_weekend_penalties.append(eq)
            if include_sunday and day_of_week == 6 and week_index + 1 < len(parsed.week_rows):
                following_service = parsed.week_rows[week_index + 1].weekday_assignments[fellow_name]
                if not is_preferred_sunday_following_service(following_service, config=config):
                    sunday_following_penalties.append(eq)

    return [anaesthesia_penalties, clinic_penalties, friday_weekend_penalties, sunday_following_penalties]


def _build_night_solution(parsed: ParsedCallScheduleCsv, day_vars: list[Int], model) -> NightScheduleSolution:
    assignments_by_week = []
    for week_index in range(len(parsed.week_rows)):
        assignments_by_week.append(
            {
                NIGHT_ROLES[day_of_week]: parsed.fellow_names[model.evaluate(day_vars[absolute_day_index(week_index, day_of_week)]).as_long()]
                for day_of_week in range(7)
            }
        )
    return NightScheduleSolution(assignments_by_week=assignments_by_week)


def _print_night_summary(parsed: ParsedCallScheduleCsv, solution: NightScheduleSolution, *, config: NightSolverConfig | None = None) -> None:
    summary = summarize_night_solution(parsed, solution, config=config)
    print("Night summary:")
    for fellow in parsed.fellow_names:
        if summary.total_nights_by_fellow[fellow] or summary.friday_nights_by_fellow[fellow]:
            print(f"  {fellow}: total={summary.total_nights_by_fellow[fellow]} friday={summary.friday_nights_by_fellow[fellow]}")
    print(f"  anaesthesia nights: {summary.anaesthesia_nights}")
    print(f"  clinic nights: {summary.clinic_nights}")
    print(f"  friday/weekend-ncc1 violations: {summary.friday_weekend_ncc1_violations}")
    print(f"  sunday/next-week-start violations: {summary.sunday_following_service_violations}")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("Usage: python -m scheduler.night_call_solver <input_csv> <output_csv>", file=sys.stderr)
        return 2

    parsed = parse_night_call_csv(args[0])
    solution = solve_night_schedule(parsed)
    write_night_schedule_csv(parsed, solution, args[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
