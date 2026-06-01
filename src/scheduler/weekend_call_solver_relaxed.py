from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

from z3 import BoolVal, Distinct, If, Int, Or, Solver, Sum, sat

from .call_schedule_common import ParsedCallScheduleCsv, WEEKEND_ROLES, parse_call_schedule_csv
from .weekend_call_solver import (
    WeekendScheduleSolution,
    WeekendSolverConfig,
    solve_weekend_schedule_staged,
    summarize_weekend_solution,
    is_weekend_blocked,
    weekend_roles_for_fellow,
    write_weekend_schedule_csv,
)


CORE_ROLE_TO_SERVICE = {
    "Weekend NCC1": "NCC1",
    "Weekend NCC2": "NCC2",
    "Weekend Stroke": "Stroke",
}
CORE_SERVICES = frozenset(CORE_ROLE_TO_SERVICE.values())


@dataclass(frozen=True)
class RelaxedWeekendSolveResult:
    tier: str
    solution: WeekendScheduleSolution


def solve_weekend_schedule_relaxed(
    parsed: ParsedCallScheduleCsv,
    *,
    config: WeekendSolverConfig = WeekendSolverConfig(),
    relaxed_mismatch_limit: int = 5,
    emit_summary: bool = True,
) -> RelaxedWeekendSolveResult:
    strict_solution = _solve_with_mismatch_limit(parsed, config=config, mismatch_limit=0)
    if strict_solution is not None:
        result = RelaxedWeekendSolveResult(tier="strict-core-match", solution=strict_solution)
    else:
        relaxed_solution = _solve_with_mismatch_limit(parsed, config=config, mismatch_limit=relaxed_mismatch_limit)
        if relaxed_solution is not None:
            result = RelaxedWeekendSolveResult(tier=f"relaxed-core-match<={relaxed_mismatch_limit}", solution=relaxed_solution)
        else:
            result = RelaxedWeekendSolveResult(
                tier="staged-fallback",
                solution=solve_weekend_schedule_staged(parsed, config=config),
            )

    if emit_summary:
        _print_relaxed_summary(parsed, result)
    return result


def count_core_cross_role_mismatches(parsed: ParsedCallScheduleCsv, solution: WeekendScheduleSolution) -> int:
    mismatches = 0
    for week_index, week_row in enumerate(parsed.week_rows):
        assignments = solution.assignments_by_week[week_index]
        for role, fellow_name in assignments.items():
            weekday_service = week_row.weekday_assignments[fellow_name]
            if weekday_service in CORE_SERVICES and weekday_service != CORE_ROLE_TO_SERVICE[role]:
                mismatches += 1
    return mismatches


def parse_weekend_call_csv(path: str | Path) -> ParsedCallScheduleCsv:
    return parse_call_schedule_csv(path)


def _solve_with_mismatch_limit(
    parsed: ParsedCallScheduleCsv,
    *,
    config: WeekendSolverConfig,
    mismatch_limit: int,
) -> WeekendScheduleSolution | None:
    solver = Solver()
    variables = {
        (week_index, role): Int(f"relaxed_week_{week_index}_{role.replace(' ', '_').lower()}_{mismatch_limit}")
        for week_index in range(len(parsed.week_rows))
        for role in WEEKEND_ROLES
    }
    _add_weekend_constraints(solver, parsed, variables, config)
    mismatch_terms = []
    for week_index in range(len(parsed.week_rows)):
        for role in WEEKEND_ROLES:
            mismatch_terms.append(_core_cross_role_mismatch_indicator(parsed, variables[week_index, role], week_index, role))
    solver.add(Sum(mismatch_terms) <= mismatch_limit)
    if solver.check() != sat:
        return None
    model = solver.model()
    return WeekendScheduleSolution(
        assignments_by_week=[
            {
                role: parsed.fellow_names[model.evaluate(variables[week_index, role]).as_long()]
                for role in WEEKEND_ROLES
            }
            for week_index in range(len(parsed.week_rows))
        ]
    )


def _add_weekend_constraints(solver: Solver, parsed: ParsedCallScheduleCsv, variables: dict, config: WeekendSolverConfig) -> None:
    fellow_count = len(parsed.fellow_names)
    week_count = len(parsed.week_rows)
    for week_index, week_row in enumerate(parsed.week_rows):
        solver.add(Distinct(*[variables[week_index, role] for role in WEEKEND_ROLES]))
        for role in WEEKEND_ROLES:
            solver.add(variables[week_index, role] >= 0)
            solver.add(variables[week_index, role] < fellow_count)
        for fellow_index, fellow_name in enumerate(parsed.fellow_names):
            eligible_roles = weekend_roles_for_fellow(
                fellow_name,
                week_row.weekday_assignments[fellow_name],
                ccm_fellows=config.ccm_fellows,
                always_stroke_eligible=config.always_stroke_eligible,
                telestroke_stroke_eligible=config.telestroke_stroke_eligible,
                stroke_only_eligible=config.stroke_only_eligible,
                blocking_exact_services=config.blocking_exact_services,
                blocking_substring_services=config.blocking_substring_services,
            )
            for role in WEEKEND_ROLES:
                if role not in eligible_roles:
                    solver.add(variables[week_index, role] != fellow_index)

    for fellow_index in range(fellow_count):
        work_indicators = [
            Sum([_eq_indicator(variables[week_index, role], fellow_index) for role in WEEKEND_ROLES])
            for week_index in range(week_count)
        ]
        _add_spacing_constraints(solver, work_indicators, config)

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


def _core_cross_role_mismatch_indicator(parsed: ParsedCallScheduleCsv, variable, week_index: int, role: str):
    wrong_core_indices = [
        fellow_index
        for fellow_index, fellow_name in enumerate(parsed.fellow_names)
        if (
            parsed.week_rows[week_index].weekday_assignments[fellow_name] in CORE_SERVICES
            and parsed.week_rows[week_index].weekday_assignments[fellow_name] != CORE_ROLE_TO_SERVICE[role]
        )
    ]
    if not wrong_core_indices:
        return If(BoolVal(False), 1, 0)
    return If(Or(*[variable == fellow_index for fellow_index in wrong_core_indices]), 1, 0)


def _count_role_assignments(variables: dict, role: str, fellow_index: int, week_count: int):
    return Sum([_eq_indicator(variables[week_index, role], fellow_index) for week_index in range(week_count)])


def _eq_indicator(variable, fellow_index: int):
    return If(variable == fellow_index, 1, 0)


def _add_spacing_constraints(solver: Solver, work_indicators: list, config: WeekendSolverConfig) -> None:
    window = config.spacing_window_weekends
    for start in range(len(work_indicators) - (window - 1)):
        solver.add(Sum(work_indicators[start : start + window]) <= config.spacing_max_weekends)


def _print_relaxed_summary(parsed: ParsedCallScheduleCsv, result: RelaxedWeekendSolveResult) -> None:
    print(f"Tier: {result.tier}")
    print(f"Core cross-role mismatches: {count_core_cross_role_mismatches(parsed, result.solution)}")
    summary = summarize_weekend_solution(parsed, result.solution)
    print("Weekend summary:")
    print(f"  total same-service matches: {summary.total_matches}")
    print(f"  total mismatches: {summary.total_mismatches}")
    print(f"  weekday NCC1 mismatch count: {summary.ncc1_mismatches}")
    print(f"  weekday NCC2 mismatch count: {summary.ncc2_mismatches}")
    print(f"  weekday Stroke mismatch count: {summary.stroke_mismatches}")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("Usage: python -m scheduler.weekend_call_solver_relaxed <input_csv> <output_csv>", file=sys.stderr)
        return 2

    parsed = parse_weekend_call_csv(args[0])
    result = solve_weekend_schedule_relaxed(parsed)
    write_weekend_schedule_csv(parsed, result.solution, args[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
