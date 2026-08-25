"""EXPERIMENT: can NCC1 always be a full 7-day-week assignment (one fellow holds
NCC1 every day of a week), leaving only NCC2 to fragment because NF runs don't align
to week boundaries?

Encoding: for each week and fellow, force NCC1 constant across the days of that week
(NCC1[d][f] == NCC1[d+1][f] for consecutive days in the same week). Combined with the
hard coverage (exactly 1 NCC1 holder per day), this makes NCC1 a clean weekly block.

Reports SAT/UNSAT/time and, if SAT, verifies NCC1 is week-constant and shows the
NCC1-by-week assignment + how fragmented NCC2 ends up.

Usage: PYTHONPATH=src .venv/bin/python experiments/nf_ncc1_fullweek.py [--timeout 180]
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
from parafrost_scheduler.schedule_types import day_of_week
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

REPO = Path(__file__).resolve().parent.parent
RUNNER = RoundingSatRunner(REPO / "vendor/roundingsat/build/roundingsat")
STANDING = REPO / "config/standing/ncc-nf-model.yaml"


def _annual_with_elec() -> Path:
    base = yaml.safe_load(open(REPO / "config/annual/ncc-nf-model.yaml"))
    for g in ("NCC_JR", "NCC_SR"):
        base["rules"].append({"type": "shift_total", "name": f"{g} Elec budget",
                              "groups": [g], "shifts": ["Elec"], "relation": "at_least",
                              "count": 2, "strength": "hard"})
    out = Path("/tmp/nf-ncc1-annual.yaml")
    yaml.safe_dump(base, open(out, "w"), sort_keys=False)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args()

    cfg = assemble_config(None, annual_path=_annual_with_elec(),
                          standing_path=STANDING, verbose=False)
    cfg = cfg[0] if isinstance(cfg, tuple) else cfg
    sd = cfg.start_dow

    opb, vm = build_full_schedule_opb(cfg, objective=False)
    diw = collections.defaultdict(list)
    for d in range(vm.num_days):
        diw[_day_to_week(d, sd)].append(d)

    # Force NCC1 constant within each week, per fellow.
    for f in range(len(vm.fellow_names)):
        for w, days in diw.items():
            days = sorted(days)
            for a, b in zip(days, days[1:]):
                va, vb = vm.call[a][f]["NCC1"], vm.call[b][f]["NCC1"]
                # va == vb : (va => vb) and (vb => va)
                opb.weighted_sum_at_least([(-va, 1), (vb, 1)], 1)
                opb.weighted_sum_at_least([(-vb, 1), (va, 1)], 1)

    t = time.time()
    try:
        r = RUNNER.solve(opb, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(f"NCC1 full-week: TIMEOUT>{args.timeout}s")
        return 1
    dt = round(time.time() - t, 1)
    if not r.satisfiable:
        print(f"NCC1 full-week: UNSAT {dt}s  -> NOT possible to lock NCC1 to full weeks")
        return 0
    print(f"NCC1 full-week: SAT {dt}s  -> POSSIBLE")
    sol = decode_solution(r.assignment, vm)

    # Verify + report NCC1-by-week and NCC2 fragmentation.
    holder = {role: [h.get(role, "") for h in sol.call_assignments_by_day]
              for role in CALL_ROLES}
    print("\nNCC1 holder by week (should be one fellow per week, all days):")
    for w, days in sorted(diw.items()):
        names = {holder["NCC1"][d] for d in days if holder["NCC1"][d]}
        days_on = sum(1 for d in days if holder["NCC1"][d])
        tag = "OK" if len(names) <= 1 else "SPLIT!"
        if w < 12 or len(names) > 1:
            print(f"  wk{w:2d}: {sorted(names)} ({days_on} days) {tag}")

    # NCC2 run-length distribution (expected: fragmented)
    print("\nNCC2 consecutive-day run lengths per JR/SR fellow:")
    jr = set(cfg.fellow_groups["NCC_JR"]); sr = set(cfg.fellow_groups["NCC_SR"])
    for name in [x for g in ("NCC_JR", "NCC_SR") for x in cfg.fellow_groups[g]]:
        seq = holder["NCC2"]
        runs = []; cur = 0
        for d in range(vm.num_days):
            if seq[d] == name:
                cur += 1
            else:
                if cur: runs.append(cur)
                cur = 0
        if cur: runs.append(cur)
        hist = collections.Counter(runs)
        print(f"  {name:14s} {len(runs)} runs " +
              ",".join(f"{ln}d×{hist[ln]}" for ln in sorted(hist)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
