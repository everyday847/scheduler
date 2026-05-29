from __future__ import annotations

from pathlib import Path
import sys

from z3 import Bool, Solver, sat

from .call_schedule_common import ParsedCallScheduleCsv
from .night_call_solver import NightSolverConfig, write_night_schedule_csv
from .night_call_solver_bool_matrix import (
    _add_bool_matrix_constraints,
    _bool_soft_violation_count,
    _build_bool_matrix_solution,
)
from .night_call_solver_relaxed import (
    RelaxedNightSolveResult,
    _print_relaxed_summary,
    parse_night_call_csv,
)


def solve_night_schedule_bool_matrix_incremental(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig = NightSolverConfig(),
    max_violation_limit: int | None = None,
    emit_summary: bool = True,
) -> RelaxedNightSolveResult:
    solver = Solver()
    day_count = len(parsed.week_rows) * 7
    assignments = [
        [Bool(f"bool_incremental_night_day_{day_index}_fellow_{fellow_index}") for fellow_index in range(len(parsed.fellow_names))]
        for day_index in range(day_count)
    ]

    _add_bool_matrix_constraints(solver, parsed, assignments, config)
    soft_violation_count = _bool_soft_violation_count(parsed, assignments)

    upper_bound = day_count if max_violation_limit is None else max_violation_limit
    best_model = None
    best_limit = None
    low = 0
    high = upper_bound

    while low <= high:
        candidate = (low + high) // 2
        solver.push()
        solver.add(soft_violation_count <= candidate)
        if solver.check() == sat:
            best_model = solver.model()
            best_limit = candidate
            high = candidate - 1
        else:
            low = candidate + 1
        solver.pop()

    if best_model is None or best_limit is None:
        raise ValueError(f"Night call schedule is unsatisfiable with <= {upper_bound} soft violations")

    solution = _build_bool_matrix_solution(parsed, assignments, best_model)
    result = RelaxedNightSolveResult(
        tier=f"bool-matrix-incremental-soft<={best_limit}",
        solution=solution,
        soft_violations=best_limit,
    )
    if emit_summary:
        _print_relaxed_summary(parsed, result)
    return result


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) not in {2, 3}:
        print(
            "Usage: python -m scheduler.night_call_solver_bool_matrix_incremental <input_csv> <output_csv> [max_soft_violations]",
            file=sys.stderr,
        )
        return 2

    parsed = parse_night_call_csv(Path(args[0]))
    max_violation_limit = int(args[2]) if len(args) == 3 else None
    result = solve_night_schedule_bool_matrix_incremental(parsed, max_violation_limit=max_violation_limit)
    write_night_schedule_csv(parsed, result.solution, args[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
