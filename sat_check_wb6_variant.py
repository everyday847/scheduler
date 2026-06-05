"""SAT-feasibility check for ONE heavy_opt_wb6 variant. Writes a JSON result.

Usage:
  PYTHONPATH=src:new_approach/src python sat_check_wb6_variant.py --variant hard_stroke
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import run_v3_optimize_wb6 as r
import heavy_opt_wb6 as h
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.schedule_solver import build_full_schedule_opb, soft_penalty_breakdown


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True)
    ap.add_argument("--sat-limit", type=float, default=400.0)
    ap.add_argument("--unsat-limit", type=float, default=1500.0)
    args = ap.parse_args()

    config, annual = r.load_config()
    config = h._apply_variant(config, args.variant)
    runner = RoundingSatRunner(r.ROUNDINGSAT)

    opb, vm = build_full_schedule_opb(config, objective=True)
    t0 = time.time()
    res = runner.optimize(opb, time_limit=args.sat_limit)
    if not (res.satisfiable and res.assignment) and not res.proven_unsat:
        res = runner.optimize(opb, time_limit=args.unsat_limit)
    secs = time.time() - t0

    out = {"variant": args.variant, "seconds": secs}
    if res.satisfiable and res.assignment is not None:
        wk, cn, tot = soft_penalty_breakdown(res.assignment, vm)
        out.update(state="SAT", total=tot, weekly=wk, call=cn, optimal=res.optimal)
    elif res.proven_unsat:
        out.update(state="UNSAT")
    else:
        out.update(state="UNKNOWN")

    path = Path("results_slurm") / f"wb6_variant_{args.variant}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2))
    print(f"[{args.variant}] {out['state']} {secs:.1f}s "
          f"{out.get('total','')} -> {path}", flush=True)


if __name__ == "__main__":
    main()
