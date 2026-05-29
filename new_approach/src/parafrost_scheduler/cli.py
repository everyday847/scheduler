"""CLI entry point for the ParaFROST-based night call scheduler.

Mirrors the interface of scheduler.night_call_solver_policy but uses the
SAT-based ParaFROST solver instead of Z3.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scheduler.night_call_solver import NightSolverConfig, write_night_schedule_csv
from scheduler.night_call_solver_policy import (
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


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Solve night call schedules using ParaFROST (SAT-based), "
            "with configurable hard/soft policy criteria."
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
    parser.add_argument(
        "--parafrost-path",
        type=Path,
        default=_DEFAULT_PARAFROST_PATH,
        help=f"Path to the ParaFROST binary (default: {_DEFAULT_PARAFROST_PATH}).",
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

    runner = ParaFrostRunner(args.parafrost_path)
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
    solve = (
        solve_night_schedule_parafrost_at_limit
        if args.no_optimize
        else solve_night_schedule_parafrost_incremental
    )
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


if __name__ == "__main__":
    raise SystemExit(main())
