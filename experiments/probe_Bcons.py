"""Probe: SCVMC as a CONSECUTIVE 2-week block WITHOUT even-week alignment.

The config `block_rotation` (variant B) forces SCVMC weeks onto FIXED even-aligned
2-week blocks ([0,1],[2,3],...), which combined with the existing "exactly 2 SCVMC
weeks" rule appears to be very hard. This probe instead encodes pure CONSECUTIVITY:
no ISOLATED SCVMC week. For each STROKE fellow and week w:

    SCVMC[w] => SCVMC[w-1] OR SCVMC[w+1]
    i.e.  ~SCVMC[w] + SCVMC[w-1] + SCVMC[w+1] >= 1   (boundary terms dropped)

With the existing exactly-2-total SCVMC rule, this forces the two weeks adjacent
ANYWHERE (no alignment), which is the intended "two-week block" semantics.

Built objective=False + solve() (first model / proven UNSAT), --relax-locks.
  PYTHONPATH=src python experiments/probe_Bcons.py --timeout 5400
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
SCVMC = "SCVMC Rehab"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--relax-locks", action="store_true", default=True)
    ap.add_argument("--timeout", type=float, default=5400.0)
    args = ap.parse_args()

    config, _ = assemble_config(WORKBOOK, verbose=True)
    if args.relax_locks:
        config = dataclasses.replace(config, relax_locked_ncc_trio=True)

    opb, vm = build_full_schedule_opb(config, objective=False)

    si = vm.shifts.index(SCVMC)
    stroke = config.fellow_groups["STROKE"]
    nweeks = vm.num_weeks
    added = 0
    for fname in stroke:
        f = vm.fellow_names.index(fname)
        for w in range(nweeks):
            cur = vm.xs[f][w][si]
            if cur == 0:
                continue
            # ~cur + prev + next >= 1  (no isolated SCVMC week)
            terms = [(-cur, 1)]
            if w - 1 >= 0 and vm.xs[f][w - 1][si] != 0:
                terms.append((vm.xs[f][w - 1][si], 1))
            if w + 1 < nweeks and vm.xs[f][w + 1][si] != 0:
                terms.append((vm.xs[f][w + 1][si], 1))
            opb.weighted_sum_at_least(terms, 1)
            added += 1

    runner = RoundingSatRunner(ROUNDINGSAT)
    print(f"[probe] variant=Bcons (consecutive, no alignment) relax_locks={args.relax_locks} "
          f"timeout={args.timeout}s constraints={opb.num_constraints} added={added}", flush=True)
    start = time.perf_counter()
    try:
        res = runner.solve(opb, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(f"RESULT variant=Bcons UNKNOWN(timeout) elapsed={time.perf_counter()-start:.1f}s", flush=True)
        return 0
    elapsed = time.perf_counter() - start
    print(f"RESULT variant=Bcons {'SAT' if res.satisfiable else 'UNSAT'} elapsed={elapsed:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
