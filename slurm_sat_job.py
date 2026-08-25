"""One SAT-experiment cell, for Slurm array execution.

Runs a single SAT check (workbook feasibility, a rule-hardening check, or a
mismatch hard-bound check) and writes one JSON result. Designed so a Slurm array
can run many of these in parallel on dedicated `defq` nodes — RoundingSat is
single-threaded, so each cell is one 1-CPU job.

Reuses the in-process harness (`harden_experiment`) verbatim; this is just a
thin CLI wrapper that maps one experiment cell to one JSON file.

Modes:
  --mode workbook --workbook PATH
      Build the config from a workbook (with the live hardened YAML) and SAT-check it.
  --mode harden --rule NAME [--rule NAME ...]
      Harden the named rule(s) and SAT-check.
  --mode kbound --k K
      Add a hard at_most_k over the weight-10 Sat/Sun weekend-night mismatch
      indicators and SAT-check (smallest feasible K = tightest enforceable bound).

Common options:
  --workbook PATH    workbook to import (default: whatever run_v3_optimize_wb2 points at)
  --activate NAME,…  inactive rules to activate (as soft) first
  --no-lock          clear locked_assignments (false-UNSAT A/B)
  --sat-limit / --unsat-limit   two-tier budgets (seconds)
  --out PATH         JSON output path (default results_slurm/<label>.json)
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

import harden_experiment as h
import run_v3_optimize_wb2 as wb
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb,
    soft_penalty_breakdown,
)

MISMATCH_WEIGHT = 10  # weight-10 soft indicators are exactly the Sat/Sun weekend-night mismatches


def _kbound_check(config, runner, k, *, sat_limit, unsat_limit):
    """SAT-check with a hard at_most_k over the weight-10 mismatch indicators."""
    opb, vm = build_full_schedule_opb(config, objective=True)
    mismatch_vars = [v for v, w in vm.soft_violations if w == MISMATCH_WEIGHT]
    opb.at_most_k(mismatch_vars, k)
    t0 = time.time()
    res = runner.optimize(opb, time_limit=sat_limit)
    if not (res.satisfiable and res.assignment) and not res.proven_unsat:
        res = runner.optimize(opb, time_limit=unsat_limit)
    secs = time.time() - t0
    if res.satisfiable and res.assignment is not None:
        wk, cn, tot = soft_penalty_breakdown(res.assignment, vm)
        actual = sum(1 for v in mismatch_vars if res.assignment.get(v, False))
        return {"state": "SAT", "seconds": secs, "total": tot, "weekly": wk,
                "call": cn, "optimal": res.optimal, "mismatch_count": actual,
                "mismatch_indicators": len(mismatch_vars)}
    if res.proven_unsat:
        return {"state": "UNSAT", "seconds": secs}
    return {"state": "UNKNOWN", "seconds": secs}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=["workbook", "harden", "kbound"], required=True)
    ap.add_argument("--workbook", type=str, default=None)
    ap.add_argument("--rule", action="append", default=[], help="rule name to harden (repeatable)")
    ap.add_argument("--k", type=int, default=None)
    ap.add_argument("--activate", type=str, default=None)
    ap.add_argument("--no-lock", action="store_true")
    ap.add_argument("--sat-limit", type=float, default=120.0)
    ap.add_argument("--unsat-limit", type=float, default=600.0)
    ap.add_argument("--label", type=str, default=None, help="result label / filename stem")
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    if args.workbook:
        wb.WORKBOOK = Path(args.workbook)

    activated = frozenset(s.strip() for s in (args.activate or "").split(",") if s.strip())
    runner = RoundingSatRunner(h.ROUNDINGSAT)

    label = args.label or f"{args.mode}_{(args.rule and '_'.join(args.rule)) or args.k or Path(args.workbook or 'wb').stem}"
    label = label.replace("/", "_").replace(" ", "_")

    if args.mode == "kbound":
        if args.k is None:
            raise SystemExit("--mode kbound requires --k")
        config = h.build_config_with_overrides(activated=activated, no_lock=args.no_lock, verbose=False)
        result = _kbound_check(config, runner, args.k,
                               sat_limit=args.sat_limit, unsat_limit=args.unsat_limit)
        result["k"] = args.k
    else:
        hardened = frozenset(args.rule) if args.mode == "harden" else frozenset()
        config = h.build_config_with_overrides(
            hardened=hardened, activated=activated, no_lock=args.no_lock, verbose=False)
        r = h.sat_check(config, runner, sat_limit=args.sat_limit, unsat_limit=args.unsat_limit)
        result = asdict(r)

    result.update({"mode": args.mode, "workbook": args.workbook, "rules": args.rule,
                   "activated": sorted(activated), "no_lock": args.no_lock, "label": label})

    out = Path(args.out) if args.out else Path("results_slurm") / f"{label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    print(f"[{label}] {result.get('state')} "
          f"{result.get('seconds', 0):.1f}s -> {out}", flush=True)


if __name__ == "__main__":
    main()
