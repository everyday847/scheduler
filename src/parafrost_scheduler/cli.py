"""CLI entry point for the full schedule solver (weekly + weekend + night).

Uses the `--solver schedule` path to jointly solve shifts from YAML config
via the RoundingSat PB solver.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


_DEFAULT_ROUNDINGSAT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "vendor"
    / "roundingsat"
    / "build"
    / "roundingsat"
)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Solve full call schedules from YAML config files."
    )
    parser.add_argument(
        "--annual-config",
        type=Path,
        required=True,
        help="Path to annual YAML config (fellow groups, shifts).",
    )
    parser.add_argument(
        "--standing-config",
        type=Path,
        required=True,
        help="Path to standing rules YAML config.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="schedule.csv",
        help="Path for the output CSV.",
    )
    parser.add_argument(
        "--roundingsat-path",
        type=Path,
        default=_DEFAULT_ROUNDINGSAT_PATH,
        help=f"Path to the RoundingSat binary (default: {_DEFAULT_ROUNDINGSAT_PATH}).",
    )
    parser.add_argument(
        "--max-soft",
        type=int,
        default=None,
        help="Maximum weighted soft score to search or accept.",
    )
    parser.add_argument(
        "--stroke-weight", type=int, default=5,
        help="Weight for stroke-service night assignment.",
    )
    parser.add_argument(
        "--clinic-weight", type=int, default=1,
        help="Weight for clinic-service night assignment.",
    )
    parser.add_argument(
        "--anaesthesia-weight", type=int, default=1,
        help="Weight for anaesthesia-service night assignment.",
    )
    parser.add_argument(
        "--friday-weekend-ncc1-weight", type=int, default=1,
        help="Weight for NCC1 following a Friday weekend.",
    )
    parser.add_argument(
        "--sunday-following-weight", type=int, default=1,
        help="Weight for night on Sunday following weekend NCC1.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(sys.argv[1:] if argv is None else argv)

    from scheduler.night_policy_types import NightPolicyWeights
    from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
    from parafrost_scheduler.schedule_optimizer import solve_full_schedule
    from parafrost_scheduler.schedule_loader import load_schedule_config
    from parafrost_scheduler.workbook import generate_workbook

    runner = RoundingSatRunner(args.roundingsat_path)
    print(f"Using RoundingSat PB solver: {args.roundingsat_path}", flush=True)

    weights = NightPolicyWeights(
        anaesthesia=args.anaesthesia_weight,
        clinic=args.clinic_weight,
        stroke=args.stroke_weight,
        friday_weekend_ncc1=args.friday_weekend_ncc1_weight,
        sunday_following=args.sunday_following_weight,
    )

    config = load_schedule_config(
        args.annual_config,
        args.standing_config,
        night_weights=weights,
        night_hard_criteria=frozenset(),
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

    # Write output CSV
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

    _print_schedule_summary(solution, config)
    return 0


def _print_schedule_summary(solution, config):
    """Print a summary of the schedule solution."""
    fellow_names = list(solution.weekly_assignments.keys())

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


if __name__ == "__main__":
    raise SystemExit(main())
