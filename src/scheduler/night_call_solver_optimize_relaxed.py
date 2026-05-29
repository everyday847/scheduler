from __future__ import annotations

from pathlib import Path
import sys

from z3 import Int, Optimize, sat

from .call_schedule_common import ParsedCallScheduleCsv
from .night_call_solver import (
    NightSolverConfig,
    _build_night_solution,
    write_night_schedule_csv,
)
from .night_call_solver_relaxed import (
    RelaxedNightSolveResult,
    _add_relaxed_night_constraints,
    _count_relaxed_soft_violations,
    _count_solution_soft_violations,
    _print_relaxed_summary,
    parse_night_call_csv,
)


def solve_night_schedule_optimize_relaxed(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig = NightSolverConfig(),
    emit_summary: bool = True,
) -> RelaxedNightSolveResult:
    solver = Optimize()
    solver.set(priority="lex")
    day_count = len(parsed.week_rows) * 7
    day_vars = [Int(f"opt_relaxed_night_day_{day_index}") for day_index in range(day_count)]

    _add_relaxed_night_constraints(solver, parsed, day_vars, config)
    solver.minimize(_count_relaxed_soft_violations(parsed, day_vars))

    if solver.check() != sat:
        raise ValueError("Night call schedule is unsatisfiable")

    solution = _build_night_solution(parsed, day_vars, solver.model())
    result = RelaxedNightSolveResult(
        tier="optimize-soft-lex",
        solution=solution,
        soft_violations=_count_solution_soft_violations(parsed, solution),
    )
    if emit_summary:
        _print_relaxed_summary(parsed, result)
    return result


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("Usage: python -m scheduler.night_call_solver_optimize_relaxed <input_csv> <output_csv>", file=sys.stderr)
        return 2

    parsed = parse_night_call_csv(Path(args[0]))
    result = solve_night_schedule_optimize_relaxed(parsed)
    write_night_schedule_csv(parsed, result.solution, args[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
