"""Long-budget streaming optimization of workbook5, for Slurm execution.

Reuses run_v3_optimize_wb2's machinery (which already points at workbook5 with
hard NCC1/NCC2 + hard weekend-night) but overrides the time budget so the solver
keeps improving for hours. Each improving incumbent is written to
output_v3_wb5.csv / output_v3_wb5_workbook.xlsx immediately (anytime), so the
latest schedule is always on disk and the run is interruptible.
"""
from __future__ import annotations

import sys

import run_v3_optimize_wb2 as r
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner


def main():
    print("=" * 64)
    print(f"HEAVY OPTIMIZATION — {r.WORKBOOK.name}")
    print("  hard NCC1/NCC2 coverage; hard Saturday weekend-night matching;")
    print("  soft Sunday matching; NS full-week block; week-0 Stroke Elec pins")
    print("=" * 64, flush=True)
    config, annual = r.load_config()
    runner = RoundingSatRunner(r.ROUNDINGSAT)
    # Short previews for instant feedback, then one long run for best quality.
    best = r.optimize(config, runner, annual,
                      preview_seconds=(8.0, 25.0, 90.0), max_seconds=10800.0)
    if best is None:
        print("INFEASIBLE!")
        return 1
    print(f"\nFINAL best penalty: {best.soft_penalty}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
