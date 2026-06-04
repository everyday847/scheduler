"""CLI entry point for the night call scheduler.

Supports three solver backends:
- ``--solver parafrost`` (default): CNF/DIMACS encoding solved with ParaFROST.
- ``--solver roundingsat``: OPB/pseudo-Boolean encoding solved with RoundingSat.
- ``--solver joint``: Joint OPB encoding that solves weekend + night assignments
  simultaneously via RoundingSat.  Requires ``--weekend-csv`` pointing at an
  input CSV with NO pre-existing weekend columns (just weekday service data).

The OPB path uses native pseudo-Boolean constraints — cardinality and weighted
sum constraints are single lines instead of thousands of CNF clauses.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scheduler.night_call_types import NightSolverConfig, write_night_schedule_csv
from scheduler.night_policy_types import (
    ALL_POLICY_CRITERIA,
    NightPolicyWeights,
    NightPolicySpec,
    parse_night_call_csv,
    print_policy_summary,
    staged_policy_specs,
)

from parafrost_scheduler.parafrost_runner import ParaFrostRunner
from parafrost_scheduler.night_solver import (
    solve_night_schedule_parafrost_at_limit,
    solve_night_schedule_parafrost_incremental,
)

_DEFAULT_PARAFROST_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "vendor"
    / "ParaFROST"
    / "build"
    / "cpu"
    / "bin"
    / "parafrost"
)

_DEFAULT_ROUNDINGSAT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "vendor"
    / "roundingsat"
    / "build"
    / "roundingsat"
)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Solve night call schedules with configurable hard/soft policy criteria.\n"
            "Supports two solver backends: ParaFROST (SAT/CNF) and RoundingSat (PB/OPB)."
        )
    )
    parser.add_argument("input_csv", help="Path to the input schedule CSV.")
    parser.add_argument("output", help="Path for the output CSV (or prefix when --staged).")
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Run the five staged hardening policies sequentially.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="(Unused; accepted for API compatibility with the Z3 CLI.)",
    )
    parser.add_argument(
        "--hard",
        default="none",
        help="Comma-separated hard criteria for a single run.",
    )
    parser.add_argument(
        "--max-soft",
        type=int,
        default=None,
        help="Maximum weighted soft score to search or accept.",
    )
    parser.add_argument(
        "--no-optimize",
        action="store_true",
        help="Skip binary search and accept any solution under --max-soft.",
    )
    parser.add_argument("--stroke-weight", type=int, default=5)
    parser.add_argument("--clinic-weight", type=int, default=1)
    parser.add_argument("--anaesthesia-weight", type=int, default=1)
    parser.add_argument("--friday-weekend-ncc1-weight", type=int, default=1)
    parser.add_argument("--sunday-following-weight", type=int, default=1)
    # Solver selection
    parser.add_argument(
        "--solver",
        choices=["parafrost", "roundingsat", "joint", "schedule"],
        default="parafrost",
        help=(
            "Solver backend to use. 'parafrost' uses CNF/DIMACS with the ParaFROST SAT "
            "solver (default). 'roundingsat' uses OPB pseudo-Boolean format with the "
            "RoundingSat PB solver. 'joint' solves weekend+night simultaneously via "
            "RoundingSat (requires --weekend-csv). 'schedule' jointly solves weekly shifts "
            "+ weekends + nights from YAML config (requires --annual-config and "
            "--standing-config)."
        ),
    )
    parser.add_argument(
        "--weekend-csv",
        type=Path,
        default=None,
        help=(
            "Path to input CSV with weekday service assignments but NO weekend columns. "
            "Required when --solver joint is used."
        ),
    )
    parser.add_argument(
        "--annual-config",
        type=Path,
        default=None,
        help="Path to annual YAML config (fellow groups, shifts). Required for --solver schedule.",
    )
    parser.add_argument(
        "--standing-config",
        type=Path,
        default=None,
        help="Path to standing rules YAML config. Required for --solver schedule.",
    )
    parser.add_argument(
        "--parafrost-path",
        type=Path,
        default=_DEFAULT_PARAFROST_PATH,
        help=f"Path to the ParaFROST binary (default: {_DEFAULT_PARAFROST_PATH}).",
    )
    parser.add_argument(
        "--parafrost-args",
        type=str,
        default="",
        help="Comma-separated extra arguments for ParaFROST (e.g. '-no-sigma,-quiet').",
    )
    parser.add_argument(
        "--roundingsat-path",
        type=Path,
        default=_DEFAULT_ROUNDINGSAT_PATH,
        help=f"Path to the RoundingSat binary (default: {_DEFAULT_ROUNDINGSAT_PATH}).",
    )
    return parser


def _parse_hard_criteria(value: str) -> frozenset[str]:
    if not value or value.strip().lower() == "none":
        return frozenset()
    parts = frozenset(part.strip() for part in value.split(",") if part.strip())
    invalid = parts - ALL_POLICY_CRITERIA
    if invalid:
        raise ValueError(f"Unknown policy criteria: {', '.join(sorted(invalid))}")
    return parts


def _format_hard_criteria(hard_criteria: frozenset[str]) -> str:
    return ", ".join(sorted(hard_criteria)) if hard_criteria else "none"


def _staged_output_path(output_prefix: Path, spec_name: str, optimize: bool) -> Path:
    suffix = "optimized" if optimize else "unoptimized"
    if output_prefix.suffix:
        return output_prefix.with_name(
            f"{output_prefix.stem}.{spec_name}.{suffix}{output_prefix.suffix}"
        )
    return output_prefix.parent / f"{output_prefix.name}.{spec_name}.{suffix}.csv"


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(sys.argv[1:] if argv is None else argv)

    # -----------------------------------------------------------------------
    # Full schedule solver: jointly solves weekly + weekend + night
    # -----------------------------------------------------------------------
    if args.solver == "schedule":
        return _run_schedule_solver(args)

    # -----------------------------------------------------------------------
    # Joint solver: dedicated path that solves weekend+night simultaneously
    # -----------------------------------------------------------------------
    if args.solver == "joint":
        return _run_joint_solver(args)

    # Build the appropriate runner and solver functions
    if args.solver == "roundingsat":
        from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
        from parafrost_scheduler.night_solver_pb import (
            solve_night_schedule_roundingsat_at_limit,
            solve_night_schedule_roundingsat_incremental,
        )
        runner = RoundingSatRunner(args.roundingsat_path)
        solve = (
            solve_night_schedule_roundingsat_at_limit
            if args.no_optimize
            else solve_night_schedule_roundingsat_incremental
        )
        print(f"Using RoundingSat PB solver: {args.roundingsat_path}", flush=True)
    else:
        extra_args = [a for a in args.parafrost_args.split(",") if a] if args.parafrost_args else []
        runner = ParaFrostRunner(args.parafrost_path, extra_args=extra_args)
        solve = (
            solve_night_schedule_parafrost_at_limit
            if args.no_optimize
            else solve_night_schedule_parafrost_incremental
        )
        print(f"Using ParaFROST SAT solver: {args.parafrost_path}", flush=True)

    parsed = parse_night_call_csv(args.input_csv)
    print(f"Parsed {len(parsed.week_rows)} schedule weeks from {args.input_csv}.", flush=True)

    weights = NightPolicyWeights(
        anaesthesia=args.anaesthesia_weight,
        clinic=args.clinic_weight,
        stroke=args.stroke_weight,
        friday_weekend_ncc1=args.friday_weekend_ncc1_weight,
        sunday_following=args.sunday_following_weight,
    )
    print(
        "Policy weights: "
        f"anaesthesia={weights.anaesthesia}, clinic={weights.clinic}, "
        f"stroke={weights.stroke}, "
        f"friday_weekend_ncc1={weights.friday_weekend_ncc1}, "
        f"sunday_following={weights.sunday_following}.",
        flush=True,
    )

    config = NightSolverConfig()
    mode = "unoptimized bounded solve" if args.no_optimize else "optimized binary search"

    if args.staged:
        specs = staged_policy_specs()
        output_prefix = Path(args.output)
        print(f"Launching {len(specs)} staged policy runs with {mode}.", flush=True)
        for spec in specs:
            print(
                f"  running {spec.name}: hard={_format_hard_criteria(spec.hard_criteria)}",
                flush=True,
            )
            result = solve(
                parsed,
                config=config,
                hard_criteria=spec.hard_criteria,
                weights=weights,
                max_violation_limit=args.max_soft,
                runner=runner,
                emit_summary=False,
            )
            output_path = _staged_output_path(output_prefix, spec.name, not args.no_optimize)
            write_night_schedule_csv(parsed, result.solution, output_path)
            print(
                f"Completed {spec.name}: weighted={result.counts.weighted_total}; "
                f"wrote {output_path}",
                flush=True,
            )
        return 0

    hard_criteria = _parse_hard_criteria(args.hard)
    print(
        f"Launching single policy run with {mode}; "
        f"hard={_format_hard_criteria(hard_criteria)}.",
        flush=True,
    )
    result = solve(
        parsed,
        config=config,
        hard_criteria=hard_criteria,
        weights=weights,
        max_violation_limit=args.max_soft,
        runner=runner,
    )
    print(f"Completed single policy run: weighted={result.counts.weighted_total}.", flush=True)
    print(f"Writing {args.output}.", flush=True)
    write_night_schedule_csv(parsed, result.solution, args.output)
    print(f"Wrote {args.output}.", flush=True)
    return 0


def _run_schedule_solver(args) -> int:
    """Run the full joint schedule solver (weekly + weekend + night)."""
    from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
    from parafrost_scheduler.schedule_solver import (
        load_schedule_config,
        solve_full_schedule,
    )
    from parafrost_scheduler.workbook import generate_workbook

    if args.annual_config is None or args.standing_config is None:
        print(
            "Error: --solver schedule requires --annual-config and --standing-config",
            file=sys.stderr,
        )
        return 2

    runner = RoundingSatRunner(args.roundingsat_path)
    print(f"Using RoundingSat PB solver (schedule mode): {args.roundingsat_path}", flush=True)

    weights = NightPolicyWeights(
        anaesthesia=args.anaesthesia_weight,
        clinic=args.clinic_weight,
        stroke=args.stroke_weight,
        friday_weekend_ncc1=args.friday_weekend_ncc1_weight,
        sunday_following=args.sunday_following_weight,
    )
    hard_criteria = _parse_hard_criteria(args.hard)

    config = load_schedule_config(
        args.annual_config,
        args.standing_config,
        night_weights=weights,
        night_hard_criteria=hard_criteria,
    )

    print(
        f"Loaded config: {sum(len(v) for v in config.fellow_groups.values())} fellows, "
        f"{len(config.shifts)} shifts, {len(config.constraints)} rules, "
        f"{config.num_weeks} weeks.",
        flush=True,
    )

    solution = solve_full_schedule(
        config,
        runner,
        max_soft=args.max_soft,
        emit_progress=True,
    )

    if solution is None:
        print("No feasible solution found.", file=sys.stderr)
        return 1

    # Write output CSV: weekly assignments
    output_path = Path(args.output)
    import csv
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        fellow_names = list(solution.weekly_assignments.keys())
        writer.writerow(
            fellow_names
            + list(solution.weekend_solution.assignments_by_week[0].keys())
            + list(solution.night_solution.assignments_by_week[0].keys())
        )
        for w in range(config.num_weeks):
            row = [solution.weekly_assignments[name][w] for name in fellow_names]
            row += [solution.weekend_solution.assignments_by_week[w].get(r, "") for r in
                    solution.weekend_solution.assignments_by_week[0].keys()]
            row += [solution.night_solution.assignments_by_week[w].get(r, "") for r in
                    solution.night_solution.assignments_by_week[0].keys()]
            writer.writerow(row)

    print(f"Wrote schedule to {output_path}", flush=True)
    print(f"Soft penalty: {solution.soft_penalty}", flush=True)

    # Print summary
    _print_schedule_summary(solution, config)
    return 0


def _print_schedule_summary(solution, config):
    """Print a summary of the schedule solution."""
    fellow_names = list(solution.weekly_assignments.keys())

    # Weekly shift counts
    print("\nWeekly shift counts per fellow:")
    for name in fellow_names:
        assignments = solution.weekly_assignments[name]
        counts = {}
        for shift in assignments:
            if shift:
                counts[shift] = counts.get(shift, 0) + 1
        if counts:
            parts = ", ".join(f"{s}={c}" for s, c in sorted(counts.items()))
            print(f"  {name}: {parts}")

    # Night counts
    print("\nNight counts per fellow:")
    night_counts = {}
    friday_counts = {}
    for w, week_nights in enumerate(solution.night_solution.assignments_by_week):
        for day_of_week, (role, fellow) in enumerate(week_nights.items()):
            if fellow:
                night_counts[fellow] = night_counts.get(fellow, 0) + 1
                if day_of_week == 4:
                    friday_counts[fellow] = friday_counts.get(fellow, 0) + 1
    for name in sorted(night_counts.keys()):
        print(f"  {name}: total={night_counts[name]} friday={friday_counts.get(name, 0)}")

    # Weekend counts
    print("\nWeekend counts per fellow:")
    wknd_counts: dict[str, dict[str, int]] = {}
    for week_assignments in solution.weekend_solution.assignments_by_week:
        for role, fellow in week_assignments.items():
            if fellow:
                wknd_counts.setdefault(fellow, {})
                wknd_counts[fellow][role] = wknd_counts[fellow].get(role, 0) + 1
    for name in sorted(wknd_counts.keys()):
        parts = ", ".join(f"{r}={c}" for r, c in sorted(wknd_counts[name].items()))
        print(f"  {name}: {parts}")


def _run_joint_solver(args) -> int:
    """Run the joint weekend+night solver via RoundingSat."""
    from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
    from parafrost_scheduler.joint_solver import (
        solve_joint_schedule,
        solve_joint_schedule_incremental,
    )
    from scheduler.call_schedule_common import parse_call_schedule_csv
    from scheduler.night_call_types import write_night_schedule_csv
    from scheduler.weekend_call_types import WeekendSolverConfig, write_weekend_schedule_csv

    # --weekend-csv is required for the joint solver
    if args.weekend_csv is None:
        print(
            "Error: --solver joint requires --weekend-csv <path_to_input_csv>",
            file=sys.stderr,
        )
        return 2

    runner = RoundingSatRunner(args.roundingsat_path)
    print(f"Using RoundingSat PB solver (joint mode): {args.roundingsat_path}", flush=True)

    parsed = parse_call_schedule_csv(args.weekend_csv)
    print(
        f"Parsed {len(parsed.week_rows)} schedule weeks from {args.weekend_csv}.",
        flush=True,
    )

    weights = NightPolicyWeights(
        anaesthesia=args.anaesthesia_weight,
        clinic=args.clinic_weight,
        stroke=args.stroke_weight,
        friday_weekend_ncc1=args.friday_weekend_ncc1_weight,
        sunday_following=args.sunday_following_weight,
    )
    print(
        "Policy weights: "
        f"anaesthesia={weights.anaesthesia}, clinic={weights.clinic}, "
        f"stroke={weights.stroke}, "
        f"friday_weekend_ncc1={weights.friday_weekend_ncc1}, "
        f"sunday_following={weights.sunday_following}.",
        flush=True,
    )

    hard_criteria = _parse_hard_criteria(args.hard)
    night_config = NightSolverConfig()
    weekend_config = WeekendSolverConfig()
    mode = "unoptimized bounded solve" if args.no_optimize else "optimized binary search"
    print(
        f"Launching joint solver with {mode}; "
        f"hard={_format_hard_criteria(hard_criteria)}.",
        flush=True,
    )

    solve_joint = solve_joint_schedule if args.no_optimize else solve_joint_schedule_incremental
    result = solve_joint(
        parsed,
        night_config=night_config,
        weekend_config=weekend_config,
        hard_criteria=hard_criteria,
        weights=weights,
        max_violation_limit=args.max_soft,
        runner=runner,
    )

    print(
        f"Completed joint solver: weighted={result.night_policy_result.counts.weighted_total}.",
        flush=True,
    )

    # Write night schedule to output path
    output_path = Path(args.output)
    print(f"Writing night schedule to {output_path}.", flush=True)
    write_night_schedule_csv(parsed, result.night_solution, output_path)
    print(f"Wrote {output_path}.", flush=True)

    # Write weekend schedule alongside the night output
    weekend_output = output_path.with_name(output_path.stem + ".weekend" + output_path.suffix)
    print(f"Writing weekend schedule to {weekend_output}.", flush=True)
    write_weekend_schedule_csv(parsed, result.weekend_solution, weekend_output)
    print(f"Wrote {weekend_output}.", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
