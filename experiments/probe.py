"""Feasibility probe for an experiment variant — the CORRECT way (per handoff).

Builds the full schedule OPB with objective=False and calls runner.solve(), which
stops at the FIRST model (SAT) or a proven UNSAT. This is a true feasibility check;
optimize() (what `schedule.py sat` uses) runs the whole budget and is wrong here.

  PYTHONPATH=src python experiments/probe.py --variant SBW --relax-locks --timeout 3000

Prints exactly one of: SAT / UNSAT / UNKNOWN(timeout), with elapsed seconds.
A returned satisfiable=False from solve() is a PROVEN UNSAT; a timeout raises
TimeoutExpired which we catch and report as UNKNOWN.
"""

from __future__ import annotations

import argparse
import dataclasses
import subprocess
import sys
import time
from pathlib import Path

from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

REPO = Path(__file__).resolve().parent.parent
ROUNDINGSAT = REPO / "vendor/roundingsat/build/roundingsat"
WORKBOOK = REPO / "workbook_partial_input7.xlsx"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True,
                    help="exp variant name (S/B/W/SB/SW/BW/SBW), or 'baseline'")
    ap.add_argument("--relax-locks", action="store_true")
    ap.add_argument("--timeout", type=float, default=3000.0)
    args = ap.parse_args()

    if args.variant == "baseline":
        annual = REPO / "config/annual/my-2026-2027-v3.yaml"
    else:
        annual = REPO / f"config/annual/exp/exp-{args.variant}.yaml"

    config, _ = assemble_config(WORKBOOK, annual_path=annual, verbose=True)
    if args.relax_locks:
        config = dataclasses.replace(config, relax_locked_ncc_trio=True)

    opb, _ = build_full_schedule_opb(config, objective=False)
    runner = RoundingSatRunner(ROUNDINGSAT)

    print(f"[probe] variant={args.variant} relax_locks={args.relax_locks} "
          f"timeout={args.timeout}s constraints={opb.num_constraints}", flush=True)
    start = time.perf_counter()
    try:
        res = runner.solve(opb, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(f"RESULT variant={args.variant} UNKNOWN(timeout) "
              f"elapsed={time.perf_counter()-start:.1f}s", flush=True)
        return 0
    elapsed = time.perf_counter() - start
    verdict = "SAT" if res.satisfiable else "UNSAT"
    print(f"RESULT variant={args.variant} {verdict} elapsed={elapsed:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
