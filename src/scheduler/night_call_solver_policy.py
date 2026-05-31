from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from itertools import permutations
from pathlib import Path
import sys

try:
    from z3 import And, Bool, If, IntVal, Or, Solver, Sum, sat, set_param, set_option
    set_param('sat.threads', 95)
    set_param('smt.threads', 95)
    set_option("verbose", 10)
except ImportError:
    pass

from .call_schedule_common import (
    NIGHT_ROLES,
    ParsedCallScheduleCsv,
    WEEKEND_ROLES,
    is_anaesthesia_service,
    is_clinic_service,
    is_night_blocked,
    is_night_holiday_eligible,
    is_preferred_sunday_following_service,
    parse_call_schedule_csv,
)
from .night_call_solver import (
    NightScheduleSolution,
    NightSolverConfig,
    absolute_day_index,
    holiday_indices_for_config,
    summarize_night_solution,
    write_night_schedule_csv,
)


CRITERION_ANAESTHESIA = "anaesthesia"
CRITERION_CLINIC = "clinic"
CRITERION_STROKE = "stroke"
CRITERION_FRIDAY_WEEKEND_NCC1 = "friday_weekend_ncc1"
CRITERION_SUNDAY_FOLLOWING = "sunday_following"
ALL_POLICY_CRITERIA = frozenset(
    {
        CRITERION_ANAESTHESIA,
        CRITERION_CLINIC,
        CRITERION_STROKE,
        CRITERION_FRIDAY_WEEKEND_NCC1,
        CRITERION_SUNDAY_FOLLOWING,
    }
)


@dataclass(frozen=True)
class NightPolicyWeights:
    anaesthesia: int = 1
    clinic: int = 1
    stroke: int = 5
    friday_weekend_ncc1: int = 1
    sunday_following: int = 1

    def for_criterion(self, criterion: str) -> int:
        return getattr(self, criterion)


@dataclass(frozen=True)
class NightPolicyCounts:
    by_criterion: dict[str, int]
    weighted_total: int


@dataclass(frozen=True)
class NightPolicySpec:
    name: str
    hard_criteria: frozenset[str]


@dataclass(frozen=True)
class NightPolicySolveResult:
    tier: str
    solution: NightScheduleSolution
    counts: NightPolicyCounts
    hard_criteria: frozenset[str]
    optimized: bool


def staged_policy_specs() -> tuple[NightPolicySpec, ...]:
    return (
        NightPolicySpec("all-soft", frozenset()),
        NightPolicySpec("hard-sunday", frozenset({CRITERION_SUNDAY_FOLLOWING})),
        NightPolicySpec("hard-sunday-anaesthesia", frozenset({CRITERION_SUNDAY_FOLLOWING, CRITERION_ANAESTHESIA})),
        NightPolicySpec(
            "hard-sunday-anaesthesia-friday",
            frozenset({CRITERION_SUNDAY_FOLLOWING, CRITERION_ANAESTHESIA, CRITERION_FRIDAY_WEEKEND_NCC1}),
        ),
        NightPolicySpec(
            "hard-sunday-anaesthesia-friday-stroke",
            frozenset(
                {
                    CRITERION_SUNDAY_FOLLOWING,
                    CRITERION_ANAESTHESIA,
                    CRITERION_FRIDAY_WEEKEND_NCC1,
                    CRITERION_STROKE,
                }
            ),
        ),
    )


def solve_night_schedule_policy_incremental(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig = NightSolverConfig(),
    hard_criteria: set[str] | frozenset[str] = frozenset(),
    weights: NightPolicyWeights = NightPolicyWeights(),
    max_violation_limit: int | None = None,
    emit_summary: bool = True,
) -> NightPolicySolveResult:
    hard_criteria = _validate_hard_criteria(hard_criteria)
    solver, assignments, weighted_soft_count = _build_policy_solver(
        parsed,
        config=config,
        hard_criteria=hard_criteria,
        weights=weights,
    )

    upper_bound = _weighted_upper_bound(parsed, weights, hard_criteria) if max_violation_limit is None else max_violation_limit
    best_model = None
    best_limit = None
    low = 0
    high = upper_bound

    while low <= high:
        candidate = (low + high) // 2
        solver.push()
        solver.add(weighted_soft_count <= candidate)
        if solver.check() == sat:
            best_model = solver.model()
            best_limit = candidate
            high = candidate - 1
        else:
            low = candidate + 1
        solver.pop()

    if best_model is None or best_limit is None:
        raise ValueError(f"Night call schedule is unsatisfiable with <= {upper_bound} weighted soft violations")

    result = _result_from_model(
        parsed,
        assignments,
        best_model,
        tier=f"policy-incremental-soft<={best_limit}",
        hard_criteria=hard_criteria,
        weights=weights,
        optimized=True,
    )
    if emit_summary:
        print_policy_summary(parsed, result)
    return result


def solve_night_schedule_policy_at_limit(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig = NightSolverConfig(),
    hard_criteria: set[str] | frozenset[str] = frozenset(),
    weights: NightPolicyWeights = NightPolicyWeights(),
    max_violation_limit: int | None = None,
    emit_summary: bool = True,
) -> NightPolicySolveResult:
    hard_criteria = _validate_hard_criteria(hard_criteria)
    solver, assignments, weighted_soft_count = _build_policy_solver(
        parsed,
        config=config,
        hard_criteria=hard_criteria,
        weights=weights,
    )
    limit = _weighted_upper_bound(parsed, weights, hard_criteria) if max_violation_limit is None else max_violation_limit
    solver.add(weighted_soft_count <= limit)
    if solver.check() != sat:
        raise ValueError(f"Night call schedule is unsatisfiable with <= {limit} weighted soft violations")

    result = _result_from_model(
        parsed,
        assignments,
        solver.model(),
        tier=f"policy-unoptimized-soft<={limit}",
        hard_criteria=hard_criteria,
        weights=weights,
        optimized=False,
    )
    if emit_summary:
        print_policy_summary(parsed, result)
    return result


def run_staged_policy_schedules(
    parsed: ParsedCallScheduleCsv,
    output_prefix: str | Path,
    *,
    config: NightSolverConfig = NightSolverConfig(),
    weights: NightPolicyWeights = NightPolicyWeights(),
    max_violation_limit: int | None = None,
    optimize: bool = True,
    workers: int | None = None,
    progress: bool = False,
) -> list[tuple[NightPolicySpec, NightPolicySolveResult, Path]]:
    output_prefix = Path(output_prefix)
    specs = staged_policy_specs()
    results = []
    if progress:
        mode = "optimized binary search" if optimize else "unoptimized bounded solve"
        print(f"Launching {len(specs)} staged policy runs with {mode}.", flush=True)
        for spec in specs:
            print(f"  queued {spec.name}: hard={_format_hard_criteria(spec.hard_criteria)}", flush=True)
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _solve_policy_worker,
                parsed,
                config,
                spec,
                weights,
                max_violation_limit,
                optimize,
            ): spec
            for spec in specs
        }
        for future in as_completed(futures):
            spec = futures[future]
            result = future.result()
            output_path = _staged_output_path(output_prefix, spec.name, optimize)
            if progress:
                print(f"Completed {spec.name}: weighted={result.counts.weighted_total}; writing {output_path}", flush=True)
            write_night_schedule_csv(parsed, result.solution, output_path)
            if progress:
                print(f"Wrote {output_path}", flush=True)
            results.append((spec, result, output_path))

    return sorted(results, key=lambda item: specs.index(item[0]))


def criteria_counts_for_solution(
    parsed: ParsedCallScheduleCsv,
    solution: NightScheduleSolution,
    *,
    weights: NightPolicyWeights = NightPolicyWeights(),
) -> NightPolicyCounts:
    counts = {criterion: 0 for criterion in ALL_POLICY_CRITERIA}
    for week_index, week_assignments in enumerate(solution.assignments_by_week):
        for day_of_week, role in enumerate(NIGHT_ROLES):
            fellow_name = week_assignments[role]
            for criterion in _criteria_for_assignment(parsed, week_index, day_of_week, fellow_name):
                counts[criterion] += 1
    return NightPolicyCounts(
        by_criterion=counts,
        weighted_total=sum(count * weights.for_criterion(criterion) for criterion, count in counts.items()),
    )


def print_policy_summary(parsed: ParsedCallScheduleCsv, result: NightPolicySolveResult) -> None:
    print(f"Tier: {result.tier}")
    print(f"Hard criteria: {', '.join(sorted(result.hard_criteria)) or 'none'}")
    print(f"Weighted soft violations: {result.counts.weighted_total}")
    print("Policy criteria:")
    for criterion in sorted(ALL_POLICY_CRITERIA):
        print(f"  {criterion}: {result.counts.by_criterion[criterion]}")
    summary = summarize_night_solution(parsed, result.solution)
    print("Night summary:")
    for fellow in parsed.fellow_names:
        if summary.total_nights_by_fellow[fellow] or summary.friday_nights_by_fellow[fellow]:
            print(f"  {fellow}: total={summary.total_nights_by_fellow[fellow]} friday={summary.friday_nights_by_fellow[fellow]}")


def parse_night_call_csv(path: str | Path) -> ParsedCallScheduleCsv:
    parsed = parse_call_schedule_csv(path)
    missing = [role for role in WEEKEND_ROLES if role not in parsed.existing_schedule_columns]
    if missing:
        raise ValueError(f"Night solver requires weekend columns: {', '.join(missing)}")
    return parsed


def _solve_policy_worker(
    parsed: ParsedCallScheduleCsv,
    config: NightSolverConfig,
    spec: NightPolicySpec,
    weights: NightPolicyWeights,
    max_violation_limit: int | None,
    optimize: bool,
) -> NightPolicySolveResult:
    solve = solve_night_schedule_policy_incremental if optimize else solve_night_schedule_policy_at_limit
    return solve(
        parsed,
        config=config,
        hard_criteria=spec.hard_criteria,
        weights=weights,
        max_violation_limit=max_violation_limit,
        emit_summary=False,
    )


def _build_policy_solver(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig,
    hard_criteria: frozenset[str],
    weights: NightPolicyWeights,
):
    solver = Solver()
    day_count = len(parsed.week_rows) * len(NIGHT_ROLES)
    assignments = [
        [Bool(f"policy_night_day_{day_index}_fellow_{fellow_index}") for fellow_index in range(len(parsed.fellow_names))]
        for day_index in range(day_count)
    ]

    _add_base_bool_constraints(solver, parsed, assignments, config)
    weighted_terms = []
    for day_index, day_assignments in enumerate(assignments):
        week_index, day_of_week = divmod(day_index, len(NIGHT_ROLES))
        for fellow_index, fellow_name in enumerate(parsed.fellow_names):
            criteria = _criteria_for_assignment(parsed, week_index, day_of_week, fellow_name)
            for criterion in criteria:
                if criterion in hard_criteria:
                    solver.add(day_assignments[fellow_index] == False)
                else:
                    weighted_terms.append(weights.for_criterion(criterion) * _indicator(day_assignments[fellow_index]))

    return solver, assignments, Sum(weighted_terms) if weighted_terms else IntVal(0)


def _add_base_bool_constraints(
    solver: Solver,
    parsed: ParsedCallScheduleCsv,
    assignments: list[list],
    config: NightSolverConfig,
) -> None:
    holiday_indices = set(holiday_indices_for_config(config))
    day_count = len(assignments)

    for day_index, day_assignments in enumerate(assignments):
        solver.add(Sum([_indicator(is_assigned) for is_assigned in day_assignments]) == 1)
        week_index, _ = divmod(day_index, len(NIGHT_ROLES))
        week_row = parsed.week_rows[week_index]
        for fellow_index, fellow_name in enumerate(parsed.fellow_names):
            weekday_service = week_row.weekday_assignments[fellow_name]
            if fellow_name in config.ccm_fellows or is_night_blocked(weekday_service):
                solver.add(day_assignments[fellow_index] == False)
            if day_index in holiday_indices and not is_night_holiday_eligible(weekday_service):
                solver.add(day_assignments[fellow_index] == False)

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


def _criteria_for_assignment(
    parsed: ParsedCallScheduleCsv,
    week_index: int,
    day_of_week: int,
    fellow_name: str,
) -> tuple[str, ...]:
    week_row = parsed.week_rows[week_index]
    weekday_service = week_row.weekday_assignments[fellow_name]
    criteria = []
    if is_anaesthesia_service(weekday_service):
        criteria.append(CRITERION_ANAESTHESIA)
    if is_clinic_service(weekday_service):
        criteria.append(CRITERION_CLINIC)
    if "Stroke" in weekday_service:
        criteria.append(CRITERION_STROKE)
    if day_of_week == 4 and week_row.schedule_assignments.get("Weekend NCC1") == fellow_name:
        criteria.append(CRITERION_FRIDAY_WEEKEND_NCC1)
    if day_of_week == 6 and week_index + 1 < len(parsed.week_rows):
        following_service = parsed.week_rows[week_index + 1].weekday_assignments[fellow_name]
        if not is_preferred_sunday_following_service(following_service):
            criteria.append(CRITERION_SUNDAY_FOLLOWING)
    return tuple(criteria)


def _result_from_model(
    parsed: ParsedCallScheduleCsv,
    assignments: list[list],
    model,
    *,
    tier: str,
    hard_criteria: frozenset[str],
    weights: NightPolicyWeights,
    optimized: bool,
) -> NightPolicySolveResult:
    solution = _build_bool_matrix_solution(parsed, assignments, model)
    return NightPolicySolveResult(
        tier=tier,
        solution=solution,
        counts=criteria_counts_for_solution(parsed, solution, weights=weights),
        hard_criteria=hard_criteria,
        optimized=optimized,
    )


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


def _add_multiset_constraint(
    solver: Solver,
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


def _count_assignments(assignments: list[list], fellow_index: int):
    return Sum([_indicator(day_assignments[fellow_index]) for day_assignments in assignments])


def _count_friday_assignments(assignments: list[list], fellow_index: int):
    return Sum([_indicator(assignments[day_index][fellow_index]) for day_index in range(4, len(assignments), len(NIGHT_ROLES))])


def _weighted_upper_bound(
    parsed: ParsedCallScheduleCsv,
    weights: NightPolicyWeights,
    hard_criteria: frozenset[str],
) -> int:
    total = 0
    for week_index in range(len(parsed.week_rows)):
        for day_of_week in range(len(NIGHT_ROLES)):
            for fellow_name in parsed.fellow_names:
                for criterion in _criteria_for_assignment(parsed, week_index, day_of_week, fellow_name):
                    if criterion not in hard_criteria:
                        total += weights.for_criterion(criterion)
    return total


def _indicator(term):
    return If(term, 1, 0)


def _validate_hard_criteria(hard_criteria: set[str] | frozenset[str]) -> frozenset[str]:
    invalid = set(hard_criteria) - ALL_POLICY_CRITERIA
    if invalid:
        raise ValueError(f"Unknown policy criteria: {', '.join(sorted(invalid))}")
    return frozenset(hard_criteria)


def _staged_output_path(output_prefix: Path, spec_name: str, optimize: bool) -> Path:
    suffix = "optimized" if optimize else "unoptimized"
    if output_prefix.suffix:
        return output_prefix.with_name(f"{output_prefix.stem}.{spec_name}.{suffix}{output_prefix.suffix}")
    return output_prefix.parent / f"{output_prefix.name}.{spec_name}.{suffix}.csv"


def _parse_hard_criteria(value: str) -> frozenset[str]:
    if not value or value == "none":
        return frozenset()
    return _validate_hard_criteria(frozenset(part.strip() for part in value.split(",") if part.strip()))


def _format_hard_criteria(hard_criteria: frozenset[str]) -> str:
    return ", ".join(sorted(hard_criteria)) if hard_criteria else "none"


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Solve night call schedules with configurable hard/soft policy criteria.")
    parser.add_argument("input_csv")
    parser.add_argument("output")
    parser.add_argument("--staged", action="store_true", help="Run the five staged hardening policies in parallel.")
    parser.add_argument("--workers", type=int, default=None, help="Worker count for --staged.")
    parser.add_argument("--hard", default="none", help="Comma-separated hard criteria for a single run.")
    parser.add_argument("--max-soft", type=int, default=None, help="Maximum weighted soft score to search or accept.")
    parser.add_argument("--no-optimize", action="store_true", help="Skip binary search and accept any solution under --max-soft.")
    parser.add_argument("--stroke-weight", type=int, default=5)
    parser.add_argument("--clinic-weight", type=int, default=1)
    parser.add_argument("--anaesthesia-weight", type=int, default=1)
    parser.add_argument("--friday-weekend-ncc1-weight", type=int, default=1)
    parser.add_argument("--sunday-following-weight", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(sys.argv[1:] if argv is None else argv)
    parsed = parse_night_call_csv(args.input_csv)
    weights = NightPolicyWeights(
        anaesthesia=args.anaesthesia_weight,
        clinic=args.clinic_weight,
        stroke=args.stroke_weight,
        friday_weekend_ncc1=args.friday_weekend_ncc1_weight,
        sunday_following=args.sunday_following_weight,
    )
    mode = "unoptimized bounded solve" if args.no_optimize else "optimized binary search"
    print(f"Parsed {len(parsed.week_rows)} schedule weeks from {args.input_csv}.", flush=True)
    print(
        "Policy weights: "
        f"anaesthesia={weights.anaesthesia}, clinic={weights.clinic}, stroke={weights.stroke}, "
        f"friday_weekend_ncc1={weights.friday_weekend_ncc1}, sunday_following={weights.sunday_following}.",
        flush=True,
    )

    if args.staged:
        results = run_staged_policy_schedules(
            parsed,
            args.output,
            weights=weights,
            max_violation_limit=args.max_soft,
            optimize=not args.no_optimize,
            workers=args.workers,
            progress=True,
        )
        for spec, result, output_path in results:
            print(f"{spec.name}: weighted={result.counts.weighted_total} output={output_path}")
        return 0

    solve = solve_night_schedule_policy_at_limit if args.no_optimize else solve_night_schedule_policy_incremental
    hard_criteria = _parse_hard_criteria(args.hard)
    print(f"Launching single policy run with {mode}; hard={_format_hard_criteria(hard_criteria)}.", flush=True)
    result = solve(
        parsed,
        hard_criteria=hard_criteria,
        weights=weights,
        max_violation_limit=args.max_soft,
    )
    print(f"Completed single policy run: weighted={result.counts.weighted_total}.", flush=True)
    print(f"Writing {args.output}.", flush=True)
    write_night_schedule_csv(parsed, result.solution, args.output)
    print(f"Wrote {args.output}.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
