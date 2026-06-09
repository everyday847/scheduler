#!/usr/bin/env python
"""Regression criterion-count fingerprint (READ-ONLY harness, no source edits).

Builds the default-config full-schedule OPB, runs ONE short bounded RoundingSat
optimize to get the FIRST feasible incumbent, decodes it, and prints the
per-criterion night counts + weighted soft-violation total.

Usage:
    cd /cv/scratch/u/watkina6/scheduler
    PYTHONPATH=src python docs/superpowers/plans/regression_criterion_fingerprint.py \
        --workbook new_approach/workbook_partial_input6.xlsx --seconds 120
"""
from __future__ import annotations

import argparse
from pathlib import Path

from parafrost_scheduler.experiment import assemble_config, solution_to_parsed
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb,
    decode_solution,
    soft_penalty_breakdown,
)
from scheduler.night_policy_types import (
    ALL_POLICY_CRITERIA,
    criteria_counts_for_solution,
)

ROUNDINGSAT = Path("vendor/roundingsat/build/roundingsat")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workbook", default="new_approach/workbook_partial_input6.xlsx")
    ap.add_argument("--seconds", type=float, default=120.0)
    args = ap.parse_args()

    config, _annual = assemble_config(Path(args.workbook), verbose=True)
    print(f"weekend_consecutive_hard={config.weekend_consecutive_hard} "
          f"night_hard_criteria={sorted(config.night_hard_criteria)}", flush=True)

    runner = RoundingSatRunner(ROUNDINGSAT)
    opb, var_map = build_full_schedule_opb(config, objective=True)
    res = runner.optimize(opb, time_limit=args.seconds, opt_mode="hybrid",
                          echo_progress=False)
    if not (res.satisfiable and res.assignment is not None):
        print("UNSAT" if res.proven_unsat else "UNKNOWN (no feasible point in budget)")
        return 1

    sol = decode_solution(res.assignment, var_map)
    wk, cn, tot = soft_penalty_breakdown(res.assignment, var_map)
    print(f"FEASIBLE incumbent: soft_total={tot} weekly={wk} call={cn} "
          f"optimal={res.optimal}", flush=True)

    parsed = solution_to_parsed(sol, list(sol.weekly_assignments.keys()))
    counts = criteria_counts_for_solution(parsed, sol.night_solution, config=None)
    print("Policy criteria (night):")
    for criterion in sorted(ALL_POLICY_CRITERIA):
        print(f"  {criterion}: {counts.by_criterion[criterion]}")
    print(f"Weighted soft violations (night-policy): {counts.weighted_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
