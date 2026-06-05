"""Two ad-hoc SAT checks on wb6, post-fixes (soft sunday_following):

  --exp tol2     buffered-HARD consecutive weekends with weekend_total_tolerance=2
                 (the MUS lever: buffered-hard was UNSAT only because of the
                 hard +/-1 weekend-total band).
  --exp ccm_eow  every-other-weekend HARD just for CCM fellows: no CCM fellow
                 works weekend call in two back-to-back weeks (a plain at_most-1
                 over each adjacent weekend pair, NO buffer exemption).

Each writes a JSON result. Run on Slurm (single 1-CPU job; RoundingSat is
single-threaded).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import time
from pathlib import Path

import run_v3_optimize_wb6 as r
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.schedule_solver import (
    build_full_schedule_opb, soft_penalty_breakdown,
)


def _ccm_every_other_weekend(opb, vm, config):
    """Add hard at_most-1 over each adjacent weekend pair for CCM fellows."""
    ccm = set(config.fellow_groups.get("CCM", []))
    names = vm.fellow_names
    n_added = 0
    for f, name in enumerate(names):
        if name not in ccm:
            continue
        # work[w] indicator = OR of the fellow's weekend role vars that week.
        work = {}
        for w in range(vm.num_weeks):
            roles = [vm.wr[w][rl][f] for rl in range(3) if f in vm.wr[w][rl]]
            if not roles:
                continue
            if len(roles) == 1:
                work[w] = roles[0]
            else:
                aux = opb.new_var()
                for rv in roles:
                    opb.weighted_sum_at_least([(aux, 1), (-rv, 1)], 1)
                opb.weighted_sum_at_least([(v, 1) for v in roles] + [(-aux, 1)], 1)
                work[w] = aux
        weeks = sorted(work)
        for i in range(len(weeks) - 1):
            w1, w2 = weeks[i], weeks[i + 1]
            if w2 - w1 == 1:
                opb.at_most_k([work[w1], work[w2]], 1)
                n_added += 1
    return n_added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", choices=["tol2", "ccm_eow"], required=True)
    ap.add_argument("--sat-limit", type=float, default=400.0)
    ap.add_argument("--unsat-limit", type=float, default=1500.0)
    args = ap.parse_args()

    config, annual = r.load_config()
    note = {}

    if args.exp == "tol2":
        os.environ["SCHED_DIAG_CONSECUTIVE"] = "hard"  # buffered-hard consecutive
        config = dataclasses.replace(config, weekend_total_tolerance=2)
        note["weekend_total_tolerance"] = 2
        note["consecutive"] = "buffered-hard"
        opb, vm = build_full_schedule_opb(config, objective=True)
    elif args.exp == "ccm_eow":
        # baseline config (soft consecutive), then bolt on CCM every-other-weekend.
        opb, vm = build_full_schedule_opb(config, objective=True)
        added = _ccm_every_other_weekend(opb, vm, config)
        note["ccm_pairs_constrained"] = added

    runner = RoundingSatRunner(r.ROUNDINGSAT)
    t0 = time.time()
    res = runner.optimize(opb, time_limit=args.sat_limit)
    if not (res.satisfiable and res.assignment) and not res.proven_unsat:
        res = runner.optimize(opb, time_limit=args.unsat_limit)
    secs = time.time() - t0

    out = {"exp": args.exp, "seconds": secs, **note}
    if res.satisfiable and res.assignment is not None:
        wk, cn, tot = soft_penalty_breakdown(res.assignment, vm)
        out.update(state="SAT", total=tot, weekly=wk, call=cn, optimal=res.optimal)
    elif res.proven_unsat:
        out.update(state="UNSAT")
    else:
        out.update(state="UNKNOWN")

    path = Path("results_slurm") / f"wb6_exp_{args.exp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2))
    print(f"[{args.exp}] {out['state']} {secs:.1f}s total={out.get('total','')} -> {path}", flush=True)


if __name__ == "__main__":
    main()
