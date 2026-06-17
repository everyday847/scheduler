"""Sweep hard Elec floor (2..8) x optional per-NCC-week density floor for the NF model.

Elec floor is set via config rules (so derive_forbidden_shifts also stops forbidding
Elec). The density floor (NCC week => >= D call-days) is injected directly into the OPB
(no YAML rule type yet), mirroring experiments/nf_elec_frontier.py.

For each (elec_floor, density) cell: solve objective=False, report SAT/UNSAT/TIMEOUT,
wall time, and per-fellow Elec-weeks / NCC-weeks / mean-call-days-per-NCC-week / service-days.

Usage:
  PYTHONPATH=src .venv/bin/python experiments/nf_elec_density_sweep.py \
      --elec 2 3 5 8 --dens 0 5 --timeout 90
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
BASE_ANNUAL = REPO / "config/annual/ncc-nf-model.yaml"
STANDING = REPO / "config/standing/ncc-nf-model.yaml"


def _cfg_with_elec(elec_floor: int) -> Path:
    """Write a temp annual config with hard JR/SR Elec at_least floors added."""
    base = yaml.safe_load(open(BASE_ANNUAL))
    base["rules"].append({"type": "shift_total", "name": "JR Elec floor",
                          "groups": ["NCC_JR"], "shifts": ["Elec"],
                          "relation": "at_least", "count": elec_floor, "strength": "hard"})
    base["rules"].append({"type": "shift_total", "name": "SR Elec floor",
                          "groups": ["NCC_SR"], "shifts": ["Elec"],
                          "relation": "at_least", "count": elec_floor, "strength": "hard"})
    out = Path(f"/tmp/nf-elec-{elec_floor}.yaml")
    yaml.safe_dump(base, open(out, "w"), sort_keys=False)
    return out


def _add_density_floor(opb, vm, cfg, dens: int):
    """NCC week => >= dens call-days, for JR/SR fellows (injected into OPB)."""
    if dens <= 0:
        return
    nccsi = vm.shifts.index("NCC")
    sd = vm.start_dow
    diw = collections.defaultdict(list)
    for d in range(vm.num_days):
        diw[_day_to_week(d, sd)].append(d)
    jr = set(cfg.fellow_groups["NCC_JR"])
    sr = set(cfg.fellow_groups["NCC_SR"])
    for f, name in enumerate(vm.fellow_names):
        if name not in jr and name not in sr:
            continue
        for w, days in diw.items():
            wk = vm.xs[f][w][nccsi]
            if wk == 0:
                continue
            cdays = [vm.call[d][f][r] for d in days for r in CALL_ROLES]
            # sum(cdays) - dens*wk >= 0  →  wk=1 forces >=dens; wk=0 is free.
            # NOTE: must be (wk, -dens) [positive literal, negative weight], NOT
            # (-wk, dens) [negated literal] which yields dens*(1-wk) and is VACUOUS.
            opb.weighted_sum_at_least([(v, 1) for v in cdays] + [(wk, -dens)], 0)


def trial(elec_floor: int, dens: int, timeout: float):
    annual = _cfg_with_elec(elec_floor)
    res = assemble_config(None, annual_path=annual, standing_path=STANDING, verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    _add_density_floor(opb, vm, cfg, dens)
    tag = f"Elec>={elec_floor}, dens>={dens or '-'}"
    t = time.time()
    try:
        r = RUNNER.solve(opb, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"[{tag}] TIMEOUT>{timeout}s")
        return
    dt = round(time.time() - t, 1)
    if not r.satisfiable:
        print(f"[{tag}] UNSAT {dt}s")
        return
    sol = decode_solution(r.assignment, vm)
    # per-fellow call days
    perweek = collections.defaultdict(lambda: collections.defaultdict(int))
    callcount = collections.Counter()
    for d, h in enumerate(sol.call_assignments_by_day):
        w = _day_to_week(d, vm.start_dow)
        for role in CALL_ROLES:
            if h.get(role):
                perweek[w][h[role]] += 1
                callcount[h[role]] += 1
    print(f"[{tag}] SAT {dt}s")
    order = [x for g in ("NCC_JR", "NCC_SR") for x in cfg.fellow_groups[g]]
    for name in order:
        labels = sol.weekly_assignments[name]
        ncc = sum(1 for l in labels if l == "NCC")
        elec = sum(1 for l in labels if l == "Elec")
        nccdays = sum(perweek[w][name] for w in range(len(labels)) if labels[w] == "NCC")
        dn = nccdays / ncc if ncc else 0
        print(f"    {name:14s} Elec={elec:2d} NCC-wk={ncc:2d} dens={dn:4.2f} "
              f"svc-days={callcount[name]:3d}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--elec", type=int, nargs="+", default=[2, 3, 5, 8])
    ap.add_argument("--dens", type=int, nargs="+", default=[0, 5])
    ap.add_argument("--timeout", type=float, default=90.0)
    args = ap.parse_args()
    for dens in args.dens:
        for ef in args.elec:
            trial(ef, dens, args.timeout)


if __name__ == "__main__":
    main()
