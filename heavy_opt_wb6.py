"""Parametrized long-budget streaming optimization of workbook6, for Slurm.

One script, several VARIANTS that express different violation prioritizations by
flipping which constraints are hard. Select a variant with the VARIANT env var
(or --variant). Each variant writes its own output_v3_wb6_<variant>.* files so
several can run in parallel on separate defq nodes.

Variants (all share the new defaults: full NCC2 coverage, week-0-exempt
Telestroke, fixed clinic criterion, soft buffer-exempt consecutive weekends):
  baseline       — shipped defaults: hard anaesthesia/friday_weekend_ncc1/
                   sunday_following; soft stroke, clinic, consecutive weekends,
                   Sunday weekend-stroke linking.
  hard_stroke    — baseline + the stroke night criterion made HARD (prioritize
                   zero stroke-night violations).
  hard_sunday    — baseline + Sunday-night-must-be-weekend-Stroke made HARD
                   (prioritize Sunday weekend-stroke coverage).
  hard_consec    — baseline + buffered-hard consecutive weekends. NOTE: this was
                   proven UNSAT on wb6 (2026-06-05); kept only so the comparison
                   harness records the INFEASIBLE result rather than silently
                   omitting it.
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from pathlib import Path

import run_v3_optimize_wb6 as r
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from scheduler.night_policy_types import CRITERION_STROKE


def _apply_variant(config, variant: str):
    """Return a config copy adjusted for the named variant."""
    if variant == "baseline":
        return config
    if variant == "hard_stroke":
        return dataclasses.replace(
            config, night_hard_criteria=config.night_hard_criteria | {CRITERION_STROKE}
        )
    if variant == "hard_sunday":
        return dataclasses.replace(config, weekend_night_sunday_hard=True)
    if variant == "hard_consec":
        return dataclasses.replace(config, weekend_consecutive_hard=True)
    raise SystemExit(f"unknown variant: {variant!r}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variant", default=os.environ.get("VARIANT", "baseline"))
    ap.add_argument("--max-seconds", type=float, default=21600.0)  # 6 hours
    args = ap.parse_args()
    variant = args.variant

    # Per-variant output paths so parallel runs don't clobber each other.
    r.OUTPUT_CSV = Path(f"output_v3_wb6_{variant}.csv")
    r.OUTPUT_WORKBOOK = Path(f"output_v3_wb6_{variant}_workbook.xlsx")

    print("=" * 64)
    print(f"HEAVY OPTIMIZATION — {r.WORKBOOK.name} — variant={variant}")
    print(f"  budget={args.max_seconds:.0f}s  -> {r.OUTPUT_CSV}")
    print("=" * 64, flush=True)

    config, annual = r.load_config()
    config = _apply_variant(config, variant)
    print(f"  weekend_consecutive_hard = {config.weekend_consecutive_hard}")
    print(f"  weekend_night_sunday_hard = {config.weekend_night_sunday_hard}")
    print(f"  night_hard_criteria = {sorted(config.night_hard_criteria)}", flush=True)

    runner = RoundingSatRunner(r.ROUNDINGSAT)
    best = r.optimize(config, runner, annual,
                      preview_seconds=(8.0, 25.0, 90.0), max_seconds=args.max_seconds)
    if best is None:
        print("INFEASIBLE!")
        return 1
    print(f"\nFINAL best penalty: {best.soft_penalty}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
