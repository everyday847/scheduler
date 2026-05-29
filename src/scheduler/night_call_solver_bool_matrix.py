from __future__ import annotations

from itertools import permutations
from pathlib import Path
import sys

from z3 import And, Bool, If, IntVal, Optimize, Or, Sum, sat

from .call_schedule_common import (
    NIGHT_ROLES,
    ParsedCallScheduleCsv,
    is_anaesthesia_service,
    is_clinic_service,
    is_night_blocked,
    is_night_holiday_eligible,
    is_preferred_sunday_following_service,
)
from .night_call_solver import (
    NightScheduleSolution,
    NightSolverConfig,
    absolute_day_index,
    holiday_indices_for_config,
    write_night_schedule_csv,
)
from .night_call_solver_relaxed import (
    RelaxedNightSolveResult,
    _print_relaxed_summary,
    parse_night_call_csv,
)


def solve_night_schedule_bool_matrix(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig = NightSolverConfig(),
    emit_summary: bool = True,
) -> RelaxedNightSolveResult:
    solver = Optimize()
    day_count = len(parsed.week_rows) * 7
    assignments = [
        [Bool(f"bool_night_day_{day_index}_fellow_{fellow_index}") for fellow_index in range(len(parsed.fellow_names))]
        for day_index in range(day_count)
    ]

    _add_bool_matrix_constraints(solver, parsed, assignments, config)
    soft_violation_count = _bool_soft_violation_count(parsed, assignments)
    solver.minimize(soft_violation_count)

    if solver.check() != sat:
        raise ValueError("Night call schedule is unsatisfiable")

    solution = _build_bool_matrix_solution(parsed, assignments, solver.model())
    result = RelaxedNightSolveResult(
        tier="bool-matrix-optimize",
        solution=solution,
        soft_violations=solver.model().evaluate(soft_violation_count).as_long(),
    )
    if emit_summary:
        _print_relaxed_summary(parsed, result)
    return result


def _add_bool_matrix_constraints(
    solver: Optimize,
    parsed: ParsedCallScheduleCsv,
    assignments: list[list],
    config: NightSolverConfig,
) -> None:
    holiday_indices = set(holiday_indices_for_config(config))
    day_count = len(assignments)

    for day_index, day_assignments in enumerate(assignments):
        solver.add(Sum([_indicator(is_assigned) for is_assigned in day_assignments]) == 1)
        week_index, day_of_week = divmod(day_index, 7)
        week_row = parsed.week_rows[week_index]
        for fellow_index, fellow_name in enumerate(parsed.fellow_names):
            weekday_service = week_row.weekday_assignments[fellow_name]
            if fellow_name in config.ccm_fellows or is_night_blocked(weekday_service):
                solver.add(assignments[day_index][fellow_index] == False)
            if day_index in holiday_indices and not is_night_holiday_eligible(weekday_service):
                solver.add(assignments[day_index][fellow_index] == False)
            if is_anaesthesia_service(weekday_service):
                solver.add(assignments[day_index][fellow_index] == False)
            if day_of_week == 4 and week_row.schedule_assignments.get("Weekend NCC1") == fellow_name:
                solver.add(assignments[day_index][fellow_index] == False)

    for week_index in range(len(parsed.week_rows) - 1):
        sunday_index = absolute_day_index(week_index, 6)
        next_week = parsed.week_rows[week_index + 1]
        for fellow_index, fellow_name in enumerate(parsed.fellow_names):
            following_service = next_week.weekday_assignments[fellow_name]
            if not is_preferred_sunday_following_service(following_service):
                solver.add(assignments[sunday_index][fellow_index] == False)

    for fellow_index in range(len(parsed.fellow_names)):
        for start in range(day_count - 2):
            solver.add(Sum([_indicator(assignments[start + offset][fellow_index]) for offset in range(3)]) <= 1)

    for fellow_name, total in config.total_nights.items():
        solver.add(_count_assignments(assignments, parsed.fellow_names.index(fellow_name)) == total)
    for fellow_name, total in config.friday_nights.items():
        solver.add(_count_friday_assignments(assignments, parsed.fellow_names.index(fellow_name)) == total)

    for multiset_constraint in config.total_night_multisets:
        _add_multiset_constraint(solver, assignments, parsed.fellow_names, multiset_constraint, friday_only=False)
    for multiset_constraint in config.friday_night_multisets:
        _add_multiset_constraint(solver, assignments, parsed.fellow_names, multiset_constraint, friday_only=True)


def _add_multiset_constraint(
    solver: Optimize,
    assignments: list[list],
    fellow_names: list[str],
    multiset_constraint,
    *,
    friday_only: bool,
) -> None:
    disjuncts = []
    for ordering in {tuple(ordering) for ordering in permutations(multiset_constraint.values)}:
        conjuncts = []
        for fellow_name, total in zip(multiset_constraint.names, ordering, strict=True):
            fellow_index = fellow_names.index(fellow_name)
            count = _count_friday_assignments(assignments, fellow_index) if friday_only else _count_assignments(assignments, fellow_index)
            conjuncts.append(count == total)
        disjuncts.append(And(*conjuncts))
    solver.add(Or(*disjuncts))


def _bool_soft_violation_count(parsed: ParsedCallScheduleCsv, assignments: list[list]):
    terms = []
    for day_index, day_assignments in enumerate(assignments):
        week_index, day_of_week = divmod(day_index, 7)
        week_row = parsed.week_rows[week_index]
        for fellow_index, fellow_name in enumerate(parsed.fellow_names):
            weekday_service = week_row.weekday_assignments[fellow_name]
            if is_clinic_service(weekday_service):
                terms.append(_indicator(day_assignments[fellow_index]))
            if weekday_service == "Stroke":
                terms.append(_indicator(day_assignments[fellow_index]))
                terms.append(_indicator(day_assignments[fellow_index]))
                terms.append(_indicator(day_assignments[fellow_index]))
                terms.append(_indicator(day_assignments[fellow_index]))
                terms.append(_indicator(day_assignments[fellow_index]))
    return Sum(terms) if terms else IntVal(0)


def _count_assignments(assignments: list[list], fellow_index: int):
    return Sum([_indicator(day_assignments[fellow_index]) for day_assignments in assignments])


def _count_friday_assignments(assignments: list[list], fellow_index: int):
    return Sum([_indicator(assignments[day_index][fellow_index]) for day_index in range(4, len(assignments), 7)])


def _indicator(term):
    return If(term, 1, 0)


def _build_bool_matrix_solution(parsed: ParsedCallScheduleCsv, assignments: list[list], model) -> NightScheduleSolution:
    assignments_by_week = []
    for week_index in range(len(parsed.week_rows)):
        week_assignments = {}
        for day_of_week, role in enumerate(NIGHT_ROLES):
            day_index = absolute_day_index(week_index, day_of_week)
            assigned_index = next(
                fellow_index
                for fellow_index, is_assigned in enumerate(assignments[day_index])
                if model.evaluate(is_assigned)
            )
            week_assignments[role] = parsed.fellow_names[assigned_index]
        assignments_by_week.append(week_assignments)
    return NightScheduleSolution(assignments_by_week=assignments_by_week)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("Usage: python -m scheduler.night_call_solver_bool_matrix <input_csv> <output_csv>", file=sys.stderr)
        return 2

    parsed = parse_night_call_csv(Path(args[0]))
    result = solve_night_schedule_bool_matrix(parsed)
    write_night_schedule_csv(parsed, result.solution, args[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
