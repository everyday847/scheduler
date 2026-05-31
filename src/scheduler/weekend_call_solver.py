from __future__ import annotations

from dataclasses import dataclass
import csv
from pathlib import Path
import sys

try:
    from z3 import BoolVal, Distinct, If, Int, Optimize, Or, Solver, Sum, sat, set_option
    set_option(verbose=10)
except ImportError:
    pass


from .call_schedule_common import (
    DEFAULT_ALWAYS_STROKE_ELIGIBLE,
    DEFAULT_CCM_FELLOWS,
    DEFAULT_STROKE_ONLY_ELIGIBLE,
    DEFAULT_TELESTROKE_STROKE_ELIGIBLE,
    ParsedCallScheduleCsv,
    WEEKEND_ROLES,
    parse_call_schedule_csv,
    weekend_roles_for_fellow,
)


DEFAULT_EXACT_NCC_TOTALS = {
    "Cindy Wong": 12,
    "Alex Hanson": 12,
    "Raya Aliakbar": 15,
    "Joseph Conovaloff": 15,
    "Aditya Srivatsan": 7,
    "Cameron Schmidt": 7,
    "Harneet Dhillon": 7,
    "Helena Xeros": 7,
    "Jinyuan Liu": 1,
    "Sokena Zaidi": 1,
}
DEFAULT_EXACT_STROKE_TOTALS = {
    "Cindy Wong": 0,
    "Alex Hanson": 0,
    "Raya Aliakbar": 2,
    "Joseph Conovaloff": 2,
    "Jinyuan Liu": 2,
    "Sokena Zaidi": 2,
}
DEFAULT_STROKE_COHORT = ("Aditya Srivatsan", "Cameron Schmidt", "Harneet Dhillon", "Helena Xeros")


@dataclass(frozen=True)
class WeekendSolverConfig:
    ncc_totals: dict[str, int] = None
    stroke_totals: dict[str, int] = None
    stroke_cohort: tuple[str, ...] = DEFAULT_STROKE_COHORT
    stroke_cohort_total: int | None = 45
    stroke_cohort_min: int = 11
    stroke_cohort_max: int = 12
    ccm_fellows: frozenset[str] = DEFAULT_CCM_FELLOWS
    ccm_ncc_total: int | None = 22
    always_stroke_eligible: frozenset[str] = DEFAULT_ALWAYS_STROKE_ELIGIBLE
    telestroke_stroke_eligible: frozenset[str] = DEFAULT_TELESTROKE_STROKE_ELIGIBLE
    stroke_only_eligible: frozenset[str] = DEFAULT_STROKE_ONLY_ELIGIBLE

    def __post_init__(self) -> None:
        object.__setattr__(self, "ncc_totals", dict(DEFAULT_EXACT_NCC_TOTALS if self.ncc_totals is None else self.ncc_totals))
        object.__setattr__(self, "stroke_totals", dict(DEFAULT_EXACT_STROKE_TOTALS if self.stroke_totals is None else self.stroke_totals))


@dataclass(frozen=True)
class WeekendScheduleSolution:
    assignments_by_week: list[dict[str, str]]


@dataclass(frozen=True)
class WeekendSummary:
    total_matches: int
    total_mismatches: int
    ncc1_mismatches: int
    ncc2_mismatches: int
    stroke_mismatches: int


def parse_weekend_call_csv(path: str | Path) -> ParsedCallScheduleCsv:
    return parse_call_schedule_csv(path)


def solve_weekend_schedule(
    parsed: ParsedCallScheduleCsv,
    *,
    config: WeekendSolverConfig = WeekendSolverConfig(),
    emit_summary: bool = True,
) -> WeekendScheduleSolution:
    feasible_solver = Solver()
    feasible_variables = {
        (week_index, role): Int(f"week_{week_index}_{role.replace(' ', '_').lower()}_feasible")
        for week_index in range(len(parsed.week_rows))
        for role in WEEKEND_ROLES
    }
    _add_weekend_constraints(feasible_solver, parsed, feasible_variables, config)
    if feasible_solver.check() != sat:
        raise ValueError("Weekend call schedule is unsatisfiable")
    feasible_model = feasible_solver.model()
    feasible_solution = _build_weekend_solution(parsed, feasible_variables, feasible_model)
    feasible_match_count = count_weekend_role_matches(parsed, feasible_solution)

    solver = Optimize()
    variables = {
        (week_index, role): Int(f"week_{week_index}_{role.replace(' ', '_').lower()}")
        for week_index in range(len(parsed.week_rows))
        for role in WEEKEND_ROLES
    }
    _add_weekend_constraints(solver, parsed, variables, config)
    match_expression = _count_exact_matches(parsed, variables)
    solver.add(match_expression >= feasible_match_count)
    for week_index in range(len(parsed.week_rows)):
        for role in WEEKEND_ROLES:
            solver.add_soft(_matching_role_constraint(parsed, variables[week_index, role], week_index, role))
    if solver.check() != sat:
        raise ValueError("Weekend call schedule is unsatisfiable")
    model = solver.model()
    solution = _build_weekend_solution(parsed, variables, model)
    if emit_summary:
        _print_weekend_summary(parsed, solution)
    return solution


def solve_weekend_schedule_staged(
    parsed: ParsedCallScheduleCsv,
    *,
    config: WeekendSolverConfig = WeekendSolverConfig(),
) -> WeekendScheduleSolution:
    best_solution: WeekendScheduleSolution | None = None
    best_score: int | None = None

    stroke_choices = config.stroke_cohort if config.stroke_cohort else (None,)
    for extra_stroke_fellow in stroke_choices:
        stroke_assignments = _solve_stroke_stage(parsed, config, extra_stroke_fellow)
        if stroke_assignments is None:
            continue
        ncc_assignments = _solve_ncc_stage(parsed, config, stroke_assignments)
        if ncc_assignments is None:
            continue

        assignments_by_week: list[dict[str, str]] = []
        for week_index in range(len(parsed.week_rows)):
            assignments_by_week.append(
                {
                    "Weekend NCC1": parsed.fellow_names[ncc_assignments[0][week_index]],
                    "Weekend NCC2": parsed.fellow_names[ncc_assignments[1][week_index]],
                    "Weekend Stroke": parsed.fellow_names[stroke_assignments[week_index]],
                }
            )

        solution = WeekendScheduleSolution(assignments_by_week=assignments_by_week)
        score = count_weekend_role_matches(parsed, solution)
        if best_score is None or score > best_score:
            best_score = score
            best_solution = solution

    if best_solution is None:
        raise ValueError("Weekend call schedule is unsatisfiable")
    return best_solution


def summarize_weekend_solution(parsed: ParsedCallScheduleCsv, solution: WeekendScheduleSolution) -> WeekendSummary:
    ncc1_mismatches = 0
    ncc2_mismatches = 0
    stroke_mismatches = 0
    matches = 0
    for week_index, week_row in enumerate(parsed.week_rows):
        assignments = solution.assignments_by_week[week_index]
        if week_row.weekday_assignments[assignments["Weekend NCC1"]] == "NCC1":
            matches += 1
        else:
            ncc1_mismatches += 1
        if week_row.weekday_assignments[assignments["Weekend NCC2"]] == "NCC2":
            matches += 1
        else:
            ncc2_mismatches += 1
        if week_row.weekday_assignments[assignments["Weekend Stroke"]] == "Stroke":
            matches += 1
        else:
            stroke_mismatches += 1

    return WeekendSummary(
        total_matches=matches,
        total_mismatches=ncc1_mismatches + ncc2_mismatches + stroke_mismatches,
        ncc1_mismatches=ncc1_mismatches,
        ncc2_mismatches=ncc2_mismatches,
        stroke_mismatches=stroke_mismatches,
    )


def count_weekend_role_matches(parsed: ParsedCallScheduleCsv, solution: WeekendScheduleSolution) -> int:
    return summarize_weekend_solution(parsed, solution).total_matches


def write_weekend_schedule_csv(
    parsed: ParsedCallScheduleCsv,
    solution: WeekendScheduleSolution,
    output_path: str | Path,
) -> None:
    output = Path(output_path)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([*parsed.fellow_names, *parsed.existing_schedule_columns, *WEEKEND_ROLES])
        for week_row, weekend_assignment in zip(parsed.week_rows, solution.assignments_by_week, strict=True):
            writer.writerow([*week_row.raw_row, *(weekend_assignment[role] for role in WEEKEND_ROLES)])
        for trailing_row in parsed.trailing_rows:
            writer.writerow([*trailing_row, "", "", ""])


def _print_weekend_summary(parsed: ParsedCallScheduleCsv, solution: WeekendScheduleSolution) -> None:
    summary = summarize_weekend_solution(parsed, solution)
    print("Weekend summary:")
    print(f"  total same-service matches: {summary.total_matches}")
    print(f"  total mismatches: {summary.total_mismatches}")
    print(f"  weekday NCC1 mismatch count: {summary.ncc1_mismatches}")
    print(f"  weekday NCC2 mismatch count: {summary.ncc2_mismatches}")
    print(f"  weekday Stroke mismatch count: {summary.stroke_mismatches}")


def _add_weekend_constraints(solver: Solver | Optimize, parsed: ParsedCallScheduleCsv, variables: dict, config: WeekendSolverConfig) -> None:
    fellow_count = len(parsed.fellow_names)
    week_count = len(parsed.week_rows)
    for week_index, week_row in enumerate(parsed.week_rows):
        week_vars = [variables[week_index, role] for role in WEEKEND_ROLES]
        for role in WEEKEND_ROLES:
            solver.add(variables[week_index, role] >= 0)
            solver.add(variables[week_index, role] < fellow_count)
        solver.add(Distinct(*week_vars))
        for fellow_index, fellow_name in enumerate(parsed.fellow_names):
            eligible_roles = weekend_roles_for_fellow(
                fellow_name,
                week_row.weekday_assignments[fellow_name],
                ccm_fellows=config.ccm_fellows,
                always_stroke_eligible=config.always_stroke_eligible,
                telestroke_stroke_eligible=config.telestroke_stroke_eligible,
                stroke_only_eligible=config.stroke_only_eligible,
            )
            for role in WEEKEND_ROLES:
                if role not in eligible_roles:
                    solver.add(variables[week_index, role] != fellow_index)

    for fellow_index in range(fellow_count):
        work_indicators = [
            Sum([_eq_indicator(variables[week_index, role], fellow_index) for role in WEEKEND_ROLES])
            for week_index in range(week_count)
        ]
        _add_spacing_constraints(solver, work_indicators)

    for fellow_name, total in config.ncc_totals.items():
        fellow_index = parsed.fellow_names.index(fellow_name)
        solver.add(
            _count_role_assignments(variables, "Weekend NCC1", fellow_index, week_count)
            + _count_role_assignments(variables, "Weekend NCC2", fellow_index, week_count)
            == total
        )

    for fellow_name, total in config.stroke_totals.items():
        fellow_index = parsed.fellow_names.index(fellow_name)
        solver.add(_count_role_assignments(variables, "Weekend Stroke", fellow_index, week_count) == total)

    if config.stroke_cohort:
        stroke_counts = []
        for fellow_name in config.stroke_cohort:
            fellow_index = parsed.fellow_names.index(fellow_name)
            count = _count_role_assignments(variables, "Weekend Stroke", fellow_index, week_count)
            solver.add(count >= config.stroke_cohort_min)
            solver.add(count <= config.stroke_cohort_max)
            stroke_counts.append(count)
        if config.stroke_cohort_total is not None:
            solver.add(Sum(stroke_counts) == config.stroke_cohort_total)

    if config.ccm_ncc_total is not None and config.ccm_fellows:
        solver.add(
            Sum(
                [
                    _count_role_assignments(variables, "Weekend NCC1", parsed.fellow_names.index(fellow_name), week_count)
                    + _count_role_assignments(variables, "Weekend NCC2", parsed.fellow_names.index(fellow_name), week_count)
                    for fellow_name in config.ccm_fellows
                    if fellow_name in parsed.fellow_names
                ]
            )
            == config.ccm_ncc_total
        )


def _solve_stroke_stage(
    parsed: ParsedCallScheduleCsv,
    config: WeekendSolverConfig,
    extra_stroke_fellow: str | None,
) -> list[int] | None:
    week_count = len(parsed.week_rows)
    variables = [Int(f"stroke_stage_{week_index}") for week_index in range(week_count)]
    solver = Solver()
    for week_index, week_row in enumerate(parsed.week_rows):
        solver.add(variables[week_index] >= 0)
        solver.add(variables[week_index] < len(parsed.fellow_names))
        for fellow_index, fellow_name in enumerate(parsed.fellow_names):
            eligible = weekend_roles_for_fellow(
                fellow_name,
                week_row.weekday_assignments[fellow_name],
                ccm_fellows=config.ccm_fellows,
                always_stroke_eligible=config.always_stroke_eligible,
                telestroke_stroke_eligible=config.telestroke_stroke_eligible,
                stroke_only_eligible=config.stroke_only_eligible,
            )
            if "Weekend Stroke" not in eligible:
                solver.add(variables[week_index] != fellow_index)

    for fellow_index in range(len(parsed.fellow_names)):
        _add_spacing_constraints(solver, [_eq_indicator(variables[week_index], fellow_index) for week_index in range(week_count)])

    for fellow_name, total in config.stroke_totals.items():
        solver.add(_count_assignments(variables, parsed.fellow_names.index(fellow_name)) == total)

    if config.stroke_cohort and extra_stroke_fellow is not None:
        for fellow_name in config.stroke_cohort:
            fellow_index = parsed.fellow_names.index(fellow_name)
            solver.add(_count_assignments(variables, fellow_index) == (12 if fellow_name == extra_stroke_fellow else 11))

    if solver.check() != sat:
        return None
    model = solver.model()
    return [model.evaluate(variable).as_long() for variable in variables]


def _solve_ncc_stage(
    parsed: ParsedCallScheduleCsv,
    config: WeekendSolverConfig,
    stroke_assignments: list[int],
) -> tuple[list[int], list[int]] | None:
    week_count = len(parsed.week_rows)
    ncc1_vars = [Int(f"ncc1_stage_{week_index}") for week_index in range(week_count)]
    ncc2_vars = [Int(f"ncc2_stage_{week_index}") for week_index in range(week_count)]
    solver = Solver()
    for week_index, week_row in enumerate(parsed.week_rows):
        solver.add(ncc1_vars[week_index] >= 0)
        solver.add(ncc1_vars[week_index] < len(parsed.fellow_names))
        solver.add(ncc2_vars[week_index] >= 0)
        solver.add(ncc2_vars[week_index] < len(parsed.fellow_names))
        solver.add(Distinct(ncc1_vars[week_index], ncc2_vars[week_index]))
        solver.add(ncc1_vars[week_index] != stroke_assignments[week_index])
        solver.add(ncc2_vars[week_index] != stroke_assignments[week_index])
        for fellow_index, fellow_name in enumerate(parsed.fellow_names):
            eligible = weekend_roles_for_fellow(
                fellow_name,
                week_row.weekday_assignments[fellow_name],
                ccm_fellows=config.ccm_fellows,
                always_stroke_eligible=config.always_stroke_eligible,
                telestroke_stroke_eligible=config.telestroke_stroke_eligible,
                stroke_only_eligible=config.stroke_only_eligible,
            )
            if "Weekend NCC1" not in eligible:
                solver.add(ncc1_vars[week_index] != fellow_index)
            if "Weekend NCC2" not in eligible:
                solver.add(ncc2_vars[week_index] != fellow_index)

    for fellow_index in range(len(parsed.fellow_names)):
        work_indicators = [
            _eq_indicator(ncc1_vars[week_index], fellow_index)
            + _eq_indicator(ncc2_vars[week_index], fellow_index)
            + If(stroke_assignments[week_index] == fellow_index, 1, 0)
            for week_index in range(week_count)
        ]
        _add_spacing_constraints(solver, work_indicators)

    for fellow_name, total in config.ncc_totals.items():
        fellow_index = parsed.fellow_names.index(fellow_name)
        solver.add(_count_assignments(ncc1_vars, fellow_index) + _count_assignments(ncc2_vars, fellow_index) == total)

    if solver.check() != sat:
        return None
    model = solver.model()
    return ([model.evaluate(variable).as_long() for variable in ncc1_vars], [model.evaluate(variable).as_long() for variable in ncc2_vars])


def _count_exact_matches(parsed: ParsedCallScheduleCsv, variables: dict) -> int:
    return Sum(
        [
            If(parsed.week_rows[week_index].weekday_assignments[parsed.fellow_names[fellow_index]] == expected_service, 1, 0)
            * _eq_indicator(variables[week_index, role], fellow_index)
            for week_index in range(len(parsed.week_rows))
            for role, expected_service in (
                ("Weekend NCC1", "NCC1"),
                ("Weekend NCC2", "NCC2"),
                ("Weekend Stroke", "Stroke"),
            )
            for fellow_index in range(len(parsed.fellow_names))
        ]
    )


def _matching_role_constraint(parsed: ParsedCallScheduleCsv, variable, week_index: int, role: str):
    expected_service = {"Weekend NCC1": "NCC1", "Weekend NCC2": "NCC2", "Weekend Stroke": "Stroke"}[role]
    matching_indices = [
        fellow_index
        for fellow_index, fellow_name in enumerate(parsed.fellow_names)
        if parsed.week_rows[week_index].weekday_assignments[fellow_name] == expected_service
    ]
    if not matching_indices:
        return BoolVal(False)
    return Or(*[variable == fellow_index for fellow_index in matching_indices])


def _build_weekend_solution(parsed: ParsedCallScheduleCsv, variables: dict, model) -> WeekendScheduleSolution:
    assignments_by_week = []
    for week_index in range(len(parsed.week_rows)):
        assignments_by_week.append(
            {
                role: parsed.fellow_names[model.evaluate(variables[week_index, role]).as_long()]
                for role in WEEKEND_ROLES
            }
        )
    return WeekendScheduleSolution(assignments_by_week=assignments_by_week)


def _count_role_assignments(variables: dict, role: str, fellow_index: int, week_count: int):
    return Sum([_eq_indicator(variables[week_index, role], fellow_index) for week_index in range(week_count)])


def _add_spacing_constraints(solver: Solver | Optimize, work_indicators: list) -> None:
    for week_index in range(len(work_indicators) - 1):
        solver.add(work_indicators[week_index] + work_indicators[week_index + 1] <= 1)
    for start in range(len(work_indicators) - 3):
        solver.add(Sum(work_indicators[start : start + 4]) <= 2)


def _count_assignments(vars_by_week: list[Int], fellow_index: int):
    return Sum([_eq_indicator(variable, fellow_index) for variable in vars_by_week])


def _eq_indicator(variable, fellow_index: int):
    return If(variable == fellow_index, 1, 0)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("Usage: python -m scheduler.weekend_call_solver <input_csv> <output_csv>", file=sys.stderr)
        return 2

    parsed = parse_weekend_call_csv(args[0])
    solution = solve_weekend_schedule(parsed)
    write_weekend_schedule_csv(parsed, solution, args[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
