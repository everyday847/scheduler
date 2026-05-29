from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

from z3 import If, Int, IntVal, Optimize, Or, Solver, Sum, sat, set_option

set_option(verbose=10)

from .call_schedule_common import ParsedCallScheduleCsv, WEEKEND_ROLES, parse_call_schedule_csv
from .night_call_solver import (
    NightScheduleSolution,
    NightSolverConfig,
    _add_night_constraints,
    summarize_night_solution,
    write_night_schedule_csv,
    _build_night_solution,
    _eq_indicator,
    _sum_or_zero,
)
from .call_schedule_common import (
    is_anaesthesia_service,
    is_clinic_service,
    is_preferred_sunday_following_service,
)


@dataclass(frozen=True)
class RelaxedNightSolveResult:
    tier: str
    solution: NightScheduleSolution
    soft_violations: int


def solve_night_schedule_relaxed(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig = NightSolverConfig(),
    relaxed_violation_limit: int = 10,
    emit_summary: bool = True,
) -> RelaxedNightSolveResult:
    strict_solution = _solve_with_violation_limit(parsed, config=config, violation_limit=0)
    if strict_solution is not None:
        result = RelaxedNightSolveResult(
            tier="strict-zero-soft-violations",
            solution=strict_solution.solution,
            soft_violations=strict_solution.soft_violations,
        )
    else:
        relaxed_solution = _solve_with_violation_limit(parsed, config=config, violation_limit=relaxed_violation_limit)
        if relaxed_solution is not None:
            result = RelaxedNightSolveResult(
                tier=f"relaxed-soft<={relaxed_violation_limit}",
                solution=relaxed_solution.solution,
                soft_violations=relaxed_solution.soft_violations,
            )
        else:
            fallback_solution = _solve_optimized_fallback(parsed, config=config)
            fallback_violations = _count_solution_soft_violations(parsed, fallback_solution)
            result = RelaxedNightSolveResult(
                tier="optimized-fallback",
                solution=fallback_solution,
                soft_violations=fallback_violations,
            )

    if emit_summary:
        _print_relaxed_summary(parsed, result)
    return result


def parse_night_call_csv(path: str | Path) -> ParsedCallScheduleCsv:
    parsed = parse_call_schedule_csv(path)
    missing = [role for role in WEEKEND_ROLES if role not in parsed.existing_schedule_columns]
    if missing:
        raise ValueError(f"Night solver requires weekend columns: {', '.join(missing)}")
    return parsed


def _solve_with_violation_limit(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig,
    violation_limit: int,
) -> RelaxedNightSolveResult | None:
    solver = Solver()
    day_count = len(parsed.week_rows) * 7
    day_vars = [Int(f"relaxed_night_day_{day_index}_{violation_limit}") for day_index in range(day_count)]
    _add_relaxed_night_constraints(solver, parsed, day_vars, config)
    soft_violation_count = _count_relaxed_soft_violations(parsed, day_vars)
    solver.add(soft_violation_count <= violation_limit)
    if solver.check() != sat:
        return None
    model = solver.model()
    solution = _build_night_solution(parsed, day_vars, model)
    return RelaxedNightSolveResult(
        tier=f"soft<={violation_limit}",
        solution=solution,
        soft_violations=_count_solution_soft_violations(parsed, solution),
    )


def _count_solution_soft_violations(
    parsed: ParsedCallScheduleCsv,
    solution: NightScheduleSolution,
) -> int:
    total = 0
    for week_index, week_assignments in enumerate(solution.assignments_by_week):
        for day_of_week, role in enumerate(("Night Mon", "Night Tue", "Night Wed", "Night Thu", "Night Fri", "Night Sat", "Night Sun")):
            fellow_name = week_assignments[role]
            weekday_service = parsed.week_rows[week_index].weekday_assignments[fellow_name]
            if "Anesthesia" in weekday_service or "Anaesthesia" in weekday_service:
                total += 1
            if "Stroke" in weekday_service:
                total += 1
            if day_of_week == 4 and parsed.week_rows[week_index].schedule_assignments.get("Weekend NCC1") == fellow_name:
                total += 1
    return total


def _count_relaxed_soft_violations(parsed: ParsedCallScheduleCsv, day_vars: list[Int]):
    anaesthesia_penalties = []
    clinic_penalties = []
    friday_weekend_penalties = []
    for day_index, variable in enumerate(day_vars):
        week_index, day_of_week = divmod(day_index, 7)
        week_row = parsed.week_rows[week_index]
        for fellow_index, fellow_name in enumerate(parsed.fellow_names):
            weekday_service = week_row.weekday_assignments[fellow_name]
            eq = _eq_indicator(variable, fellow_index)
            # if is_anaesthesia_service(weekday_service):
            #     anaesthesia_penalties.append(eq)
            if is_clinic_service(weekday_service):
                clinic_penalties.append(eq)
            if "Stroke" in weekday_service:
                clinic_penalties.append(eq)
            if day_of_week == 4 and week_row.schedule_assignments.get("Weekend NCC1") == fellow_name:
                friday_weekend_penalties.append(eq)
    return Sum([_sum_or_zero(terms) for terms in [anaesthesia_penalties, clinic_penalties, friday_weekend_penalties]])


def _add_relaxed_night_constraints(solver: Solver, parsed: ParsedCallScheduleCsv, day_vars: list[Int], config: NightSolverConfig) -> None:
    _add_night_constraints(solver, parsed, day_vars, config)
    _add_sunday_following_hard_constraints(solver, parsed, day_vars)
    _add_anaesthesia_hard_constraints(solver, parsed, day_vars)
    _add_friday_hard_constraints(solver, parsed, day_vars)


def _add_sunday_following_hard_constraints(solver: Solver, parsed: ParsedCallScheduleCsv, day_vars: list[Int]) -> None:
    for week_index in range(len(parsed.week_rows) - 1):
        sunday_var = day_vars[(week_index * 7) + 6]
        next_week = parsed.week_rows[week_index + 1]
        for fellow_index, fellow_name in enumerate(parsed.fellow_names):
            following_service = next_week.weekday_assignments[fellow_name]
            if not is_preferred_sunday_following_service(following_service):
                solver.add(sunday_var != fellow_index)

def _add_anaesthesia_hard_constraints(solver: Solver, parsed: ParsedCallScheduleCsv, day_vars: list[Int]) -> None:
    for day_index, variable in enumerate(day_vars):
        week_index, _ = divmod(day_index, 7)
        week_row = parsed.week_rows[week_index]
        for fellow_index, fellow_name in enumerate(parsed.fellow_names):
            weekday_service = week_row.weekday_assignments[fellow_name]
            if is_anaesthesia_service(weekday_service):
                solver.add(variable != fellow_index)

def _add_friday_hard_constraints(solver: Solver, parsed: ParsedCallScheduleCsv, day_vars: list[Int]) -> None:
    for day_index, variable in enumerate(day_vars):
        week_index, day_of_week = divmod(day_index, 7)
        week_row = parsed.week_rows[week_index]
        for fellow_index, fellow_name in enumerate(parsed.fellow_names):
            if day_of_week == 4 and week_row.schedule_assignments.get("Weekend NCC1") == fellow_name:
                solver.add(variable != fellow_index)
                


def _solve_optimized_fallback(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig,
) -> NightScheduleSolution:
    solver = Optimize()
    day_count = len(parsed.week_rows) * 7
    day_vars = [Int(f"relaxed_night_opt_day_{day_index}") for day_index in range(day_count)]
    _add_relaxed_night_constraints(solver, parsed, day_vars, config)
    _add_relaxed_soft_objectives(solver, parsed, day_vars)
    if solver.check() != sat:
        raise ValueError("Night call schedule is unsatisfiable")
    model = solver.model()
    return _build_night_solution(parsed, day_vars, model)


def _add_relaxed_soft_objectives(solver: Optimize, parsed: ParsedCallScheduleCsv, day_vars: list[Int]) -> None:
    anaesthesia_penalties = []
    clinic_penalties = []
    friday_weekend_penalties = []
    for day_index, variable in enumerate(day_vars):
        week_index, day_of_week = divmod(day_index, 7)
        week_row = parsed.week_rows[week_index]
        for fellow_index, fellow_name in enumerate(parsed.fellow_names):
            weekday_service = week_row.weekday_assignments[fellow_name]
            eq = _eq_indicator(variable, fellow_index)
            # if is_anaesthesia_service(weekday_service):
            #     anaesthesia_penalties.append(eq)
            if is_clinic_service(weekday_service):
                clinic_penalties.append(eq)
            # if day_of_week == 4 and week_row.schedule_assignments.get("Weekend NCC1") == fellow_name:
            #     friday_weekend_penalties.append(eq)

    solver.minimize(_sum_or_zero(anaesthesia_penalties))
    solver.minimize(_sum_or_zero(clinic_penalties))
    solver.minimize(_sum_or_zero(friday_weekend_penalties))


def _print_relaxed_summary(parsed: ParsedCallScheduleCsv, result: RelaxedNightSolveResult) -> None:
    print(f"Tier: {result.tier}")
    print(f"Soft violations: {result.soft_violations}")
    summary = summarize_night_solution(parsed, result.solution)
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
        print("Usage: python -m scheduler.night_call_solver_relaxed <input_csv> <output_csv>", file=sys.stderr)
        return 2

    parsed = parse_night_call_csv(args[0])
    result = solve_night_schedule_relaxed(parsed, relaxed_violation_limit=90)
    write_night_schedule_csv(parsed, result.solution, args[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
