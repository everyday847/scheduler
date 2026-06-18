"""Map the NCC-week-cap vs service-day-band frontier for the NF model.

Two complementary questions (per fellow group):
  (a) How SMALL can the hard NCC-week cap go with the day band FIXED?
  (b) How LARGE can the day band stay while the NCC-week cap is pushed to 14 / 12?

All driven through CONFIG so this exercises the real production encoder path (Rules
A/B/C/11 are now encoder constraints gated by config.nf_* flags — no inline injection):
  - NCC-week cap: a hard `shift_total at_most` rule per group (count = --jr-week-cap / --sr-week-cap).
  - Service-day band: config.nf_service_day_band = {"NCC_JR":[lo,hi],"NCC_SR":[lo,hi]}.
  - Elec un-forbidden via a hard Elec at_least floor (so a cap that frees weeks can use Elec).
  - --rules ABC11 toggles the corresponding config flags (week_off_cap=2, max_call=14,
    ccm_no_bridge, min_ncc_block, ncc1 weekday).

Reports SAT/UNSAT/time + per-fellow NCC-weeks / Elec / density / svc-days, and runs the
evaluator on any SAT solution.

Usage (Slurm):
  sbatch -A prescient1 -p defq -n1 --time=02:00:00 --wrap \
   "cd <repo> && PYTHONPATH=src .venv/bin/python -u experiments/nf_cap_band_frontier.py \
      --jr-week-cap 14 --sr-week-cap 24 --jr-band 75,85 --sr-band 125,135 \
      --rules ABC11 --elec-floor 6 --timeout 5400"
"""
from __future__ import annotations

import argparse
import collections
import subprocess
import time
from pathlib import Path

import yaml

from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb, decode_solution, _day_to_week, CALL_ROLES)
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.nf_evaluate import evaluate_nf

REPO = Path(__file__).resolve().parent.parent
RUNNER = RoundingSatRunner(REPO / "vendor/roundingsat/build/roundingsat")
STANDING = REPO / "config/standing/ncc-nf-model.yaml"


def _band(s):
    lo, hi = s.split(",")
    return [int(lo), int(hi)]


def _annual(elec_floor, jr_week_cap, sr_week_cap) -> Path:
    base = yaml.safe_load(open(REPO / "config/annual/ncc-nf-model.yaml"))
    for g in ("NCC_JR", "NCC_SR"):
        base["rules"].append({"type": "shift_total", "name": f"{g} Elec",
                              "groups": [g], "shifts": ["Elec"], "relation": "at_least",
                              "count": elec_floor, "strength": "hard"})
    if jr_week_cap:
        base["rules"].append({"type": "shift_total", "name": "JR NCC week cap",
                              "groups": ["NCC_JR"], "shifts": ["NCC"], "relation": "at_most",
                              "count": jr_week_cap, "strength": "hard"})
    if sr_week_cap:
        base["rules"].append({"type": "shift_total", "name": "SR NCC week cap",
                              "groups": ["NCC_SR"], "shifts": ["NCC"], "relation": "at_most",
                              "count": sr_week_cap, "strength": "hard"})
    out = REPO / "results_slurm" / f"_frontier_e{elec_floor}_jr{jr_week_cap}_sr{sr_week_cap}.yaml"
    out.parent.mkdir(exist_ok=True)
    yaml.safe_dump(base, open(out, "w"), sort_keys=False)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jr-week-cap", type=int, default=0)
    ap.add_argument("--sr-week-cap", type=int, default=0)
    ap.add_argument("--jr-band", type=_band, default=[75, 85])
    ap.add_argument("--sr-band", type=_band, default=[125, 135])
    ap.add_argument("--rules", default="")          # e.g. "ABC11"
    ap.add_argument("--elec-floor", type=int, default=6)
    ap.add_argument("--timeout", type=float, default=5400.0)
    args = ap.parse_args()

    cfg = assemble_config(None,
                          annual_path=_annual(args.elec_floor, args.jr_week_cap, args.sr_week_cap),
                          standing_path=STANDING, verbose=False)
    cfg = cfg[0] if isinstance(cfg, tuple) else cfg
    sd = cfg.start_dow

    # day-band override
    object.__setattr__(cfg, "nf_service_day_band",
                       {"NCC_JR": args.jr_band, "NCC_SR": args.sr_band})
    # rule flags
    R = args.rules.upper()
    if "A" in R:
        object.__setattr__(cfg, "nf_week_off_cap", 2)
    if "B" in R:
        object.__setattr__(cfg, "nf_max_consecutive_call_days", 14)
    if "C" in R:
        object.__setattr__(cfg, "nf_ccm_no_bridge_blocks", True)
    if "1" in R:                                    # "11" -> min_ncc_block
        object.__setattr__(cfg, "nf_min_ncc_block_weeks", True)
        object.__setattr__(cfg, "nf_ncc1_continuity", "weekday")

    opb, vm = build_full_schedule_opb(cfg, objective=False)
    tag = (f"jrcap={args.jr_week_cap or '-'} srcap={args.sr_week_cap or '-'} "
           f"jrband={args.jr_band} srband={args.sr_band} rules={R or '-'} "
           f"elec>={args.elec_floor}")
    t = time.time()
    try:
        r = RUNNER.solve(opb, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(f"[{tag}] TIMEOUT>{args.timeout}s")
        return 0
    dt = round(time.time() - t, 1)
    if not r.satisfiable:
        print(f"[{tag}] UNSAT {dt}s")
        return 0
    sol = decode_solution(r.assignment, vm)
    perweek = collections.defaultdict(lambda: collections.defaultdict(int))
    for d, h in enumerate(sol.call_assignments_by_day):
        w = _day_to_week(d, sd)
        for role in CALL_ROLES:
            if h.get(role):
                perweek[w][h[role]] += 1
    print(f"[{tag}] SAT {dt}s")
    order = [x for g in ("NCC_JR", "NCC_SR") for x in cfg.fellow_groups[g]]
    for name in order:
        labels = sol.weekly_assignments[name]
        ncc = sum(1 for l in labels if l == "NCC")
        elec = sum(1 for l in labels if l == "Elec")
        nccdays = sum(perweek[w][name] for w in range(len(labels)) if labels[w] == "NCC")
        svc = sum(perweek[w][name] for w in range(len(labels)))
        dens = f"{nccdays/ncc:.2f}" if ncc else "-"
        print(f"    {name:6s} NCC={ncc:2d} Elec={elec:2d} dens={dens:>5s} svc={svc:3d}")
    ev = evaluate_nf(sol, cfg)
    print("    " + ev.summary().replace("\n", "\n    "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
