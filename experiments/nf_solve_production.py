"""Solve the PRODUCTION NF config directly (all rules from config — no inline injection),
optimize with a wall-clock limit, write the workbook, and run the evaluator audit.

This is the canonical "solve the production schedule" entry point. Unlike nf_optimize.py
(which injects rules inline via CLI flags), this trusts config/annual/ncc-nf-model.yaml as
the single source of truth — every NF rule is now config-driven (commit 0df702e). The
objective is the model's own soft_violations (Elec soft target + concentration penalties +
CCM block target), so optimize() pushes toward the elective budget directly.

Usage (Slurm):
  sbatch -A prescient1 -p defq -n1 --time=03:00:00 --wrap \
    "cd <repo> && PYTHONPATH=src .venv/bin/python -u experiments/nf_solve_production.py \
       --time-limit 7200 --prefix output_nf_production"
"""
from __future__ import annotations

import argparse
import collections
import time
from pathlib import Path

from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb, decode_solution, _day_to_week, CALL_ROLES)
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.workbook import write_nf_workbook
from parafrost_scheduler.nf_evaluate import evaluate_nf

REPO = Path(__file__).resolve().parent.parent
RUNNER = RoundingSatRunner(REPO / "vendor/roundingsat/build/roundingsat")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--annual", default=str(REPO / "config/annual/ncc-nf-model.yaml"))
    ap.add_argument("--standing", default=str(REPO / "config/standing/ncc-nf-model.yaml"))
    ap.add_argument("--time-limit", type=float, default=7200.0)
    ap.add_argument("--prefix", default="output_nf_production")
    args = ap.parse_args()

    res = assemble_config(None, annual_path=Path(args.annual),
                          standing_path=Path(args.standing), verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    sd = cfg.start_dow

    def _report_and_write(assignment, vm, tag, out_path):
        """Decode, print per-fellow table, run evaluate_nf, write the workbook.
        Returns the NfEvalResult (so caller can gate on .ok)."""
        sol = decode_solution(assignment, vm)
        perweek = collections.defaultdict(lambda: collections.defaultdict(int))
        for d, h in enumerate(sol.call_assignments_by_day):
            w = _day_to_week(d, sd)
            for role in CALL_ROLES:
                if h.get(role):
                    perweek[w][h[role]] += 1
        order = ([x for g in ("NCC_JR", "NCC_SR") for x in cfg.fellow_groups[g]]
                 + list(cfg.fellow_groups.get("CCM", [])))
        print(f"\n=== {tag} ===")
        for name in order:
            labels = sol.weekly_assignments[name]
            ncc = sum(1 for l in labels if l == "NCC")
            elec = sum(1 for l in labels if l == "Elec")
            blank = sum(1 for l in labels if l == "")
            nccdays = sum(perweek[w][name] for w in range(len(labels)) if labels[w] == "NCC")
            svc = sum(perweek[w][name] for w in range(len(labels)))
            dens = f"{nccdays/ncc:.2f}" if ncc else "-"
            print(f"    {name:16s} NCC={ncc:2d} Elec={elec:2d} blank={blank:2d} "
                  f"dens={dens:>5s} svc-days={svc:3d}")
        ev = evaluate_nf(sol, cfg)
        print(ev.summary())
        if not ev.ok:
            print(f"!!! {tag}: {len(ev.violations)} EVAL VIOLATION(S) — does NOT satisfy rules.")
        write_nf_workbook(sol, cfg, out_path)
        print(f"wrote {out_path}")
        return ev

    # --- (1) initial SAT solution (objective=False, fast first-feasible) ---
    opb0, vm0 = build_full_schedule_opb(cfg, objective=False)
    print(f"[initial] solving for first feasible (objective=False)...")
    t = time.time()
    r0 = RUNNER.solve(opb0, timeout=min(args.time_limit, 3600))
    print(f"[initial] {'SAT' if r0.satisfiable else 'UNSAT'} in {round(time.time()-t,1)}s")
    ev0 = None
    if r0.satisfiable:
        ev0 = _report_and_write(r0.assignment, vm0, "INITIAL (first SAT)",
                                Path(args.prefix + "_initial.xlsx"))
    else:
        print("[initial] UNSAT — config infeasible; skipping optimize.")
        return 1

    # --- (2) final optimized solution ---
    opb, vm = build_full_schedule_opb(cfg, objective=True)
    if not opb.has_objective:
        print("WARNING: no objective set — config has no soft constraints?")
    print(f"\n[final] optimizing: time_limit={args.time_limit}s, "
          f"soft_terms={len(vm.soft_violations)}")
    t = time.time()
    r = RUNNER.optimize(opb, time_limit=args.time_limit, echo_progress=True)
    dt = round(time.time() - t, 1)
    if r.assignment is None:
        print(f"[final] NO INCUMBENT after {dt}s (optimal={getattr(r, 'optimal', None)}) "
              f"— keeping the initial SAT workbook.")
        return 0 if (ev0 and ev0.ok) else 2
    print(f"[final] optimize {dt}s optimal={r.optimal}")
    ev = _report_and_write(r.assignment, vm, "FINAL (optimized)",
                           Path(args.prefix + "_final.xlsx"))
    return 0 if ev.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
