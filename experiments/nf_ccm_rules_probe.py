"""Feasibility probe for the revised CCM / rest rules (objective=False).

Rules (selectable via --rules, e.g. ABC):
  A: per-week off-day cap. A week has AT MOST 2 days off — EXCEPT the one case where the
     week contains a full NF run whose ENTIRE mandatory rest (1 before + 2 after) falls in
     that same week, in which case 3 off is allowed (all 3 are mandatory). You never get
     "NF rest PLUS up to 2 discretionary". Formally: off_in_week <= 2 + extra, where extra
     in {0,1} and extra==1 requires >=3 mandatory-NF-rest off-days that week (3*extra <= R).
     "off" via _build_off_indicator (Elec/Vac/MICU weeks are working, not off → unaffected).
  B: max 14 consecutive CALL days (call = OR of NCC1/NCC2/NF that day; a background-rotation
     or off day breaks the streak). All call fellows. (Hard; soft<=12 is a later refinement.)
  C: CCM NF runs may not bridge a 4-week block boundary (offset-1 grid): forbid nf[b-1]&nf[b]
     at each block-start day b.

Usage (Slurm):
  sbatch -A prescient1 -p defq -n1 --time=01:00:00 --wrap \
    "cd <repo> && PYTHONPATH=src .venv/bin/python -u experiments/nf_ccm_rules_probe.py --rules ABC --timeout 1800"
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
    build_full_schedule_opb, _day_to_week, CALL_ROLES, _build_off_indicator)
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

REPO = Path(__file__).resolve().parent.parent
RUNNER = RoundingSatRunner(REPO / "vendor/roundingsat/build/roundingsat")
STANDING = REPO / "config/standing/ncc-nf-model.yaml"


def _annual_with_elec(floor: int) -> Path:
    base = yaml.safe_load(open(REPO / "config/annual/ncc-nf-model.yaml"))
    for g in ("NCC_JR", "NCC_SR"):
        base["rules"].append({"type": "shift_total", "name": f"{g} Elec",
                              "groups": [g], "shifts": ["Elec"], "relation": "at_least",
                              "count": floor, "strength": "hard"})
    out = REPO / "results_slurm" / f"_ccm_probe_annual_e{floor}.yaml"
    out.parent.mkdir(exist_ok=True)
    yaml.safe_dump(base, open(out, "w"), sort_keys=False)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", default="ABC")
    ap.add_argument("--elec-floor", type=int, default=6)
    ap.add_argument("--timeout", type=float, default=1800.0)
    args = ap.parse_args()
    want = set(args.rules.upper())

    cfg = assemble_config(None, annual_path=_annual_with_elec(args.elec_floor),
                          standing_path=STANDING, verbose=False)
    cfg = cfg[0] if isinstance(cfg, tuple) else cfg
    sd = cfg.start_dow

    opb, vm = build_full_schedule_opb(cfg, objective=False)
    si = {s: i for i, s in enumerate(vm.shifts)}
    n = vm.num_days
    ccm = set(cfg.fellow_groups["CCM"])
    diw = collections.defaultdict(list)
    for d in range(n):
        diw[_day_to_week(d, sd)].append(d)

    def AND_upper(lits):
        """Aux var v with v => each lit (so v can be 1 only if all lits are 1)."""
        v = opb.new_var()
        for lit in lits:
            opb.weighted_sum_at_least([(-v, 1), (lit, 1)], 1)
        return v

    for f, name in enumerate(vm.fellow_names):
        nf = [vm.call[d][f]["NF"] for d in range(n)]
        off = [_build_off_indicator(opb, vm.call, vm.xs, si, cfg, f, d, sd) for d in range(n)]

        if "A" in want:
            # mandatory-NF-rest indicator per day: off[d] AND it is one of the 3 forced
            # rest days of an in-horizon NF run touching this day.
            #   before:  off[d] & nf[d+1]                (d is the 1 day before a run start)
            #   after-1: off[d] & nf[d-1]                (d is 1st day after a run end)
            #   after-2: off[d] & ~nf[d-1] & nf[d-2]     (d is 2nd day after a run end)
            rest = [None] * n
            for d in range(n):
                terms = []
                if d + 1 < n:
                    terms.append(AND_upper([off[d], nf[d + 1]]))
                if d - 1 >= 0:
                    terms.append(AND_upper([off[d], nf[d - 1]]))
                if d - 2 >= 0:
                    nnf = opb.new_var()
                    opb.weighted_sum_at_least([(-nnf, 1), (-nf[d - 1], 1)], 1)  # nnf => ~nf[d-1]
                    terms.append(AND_upper([off[d], nnf, nf[d - 2]]))
                rv = opb.new_var()
                if terms:
                    opb.weighted_sum_at_least([(t, 1) for t in terms] + [(-rv, 1)], 0)  # rv <= sum
                else:
                    opb.add_unit(-rv)
                rest[d] = rv
            for w, days in diw.items():
                offs = [off[d] for d in days]
                rests = [rest[d] for d in days]
                extra = opb.new_var()                       # may relax cap to 3, only if forced
                # 3*extra <= sum(rests)  → extra can be 1 only with >=3 mandatory rest days.
                # MUST be (extra, -3) [neg weight on POSITIVE literal]; (-extra, 3) emits
                # +3*~extra = +3*(1-extra) which is VACUOUS (extra unconstrained). [bug fixed]
                opb.weighted_sum_at_least([(r, 1) for r in rests] + [(extra, -3)], 0)
                # sum(off) <= 2 + extra
                opb.weighted_sum_at_most([(o, 1) for o in offs] + [(extra, -1)], 2)

        if "B" in want:
            cday = []
            for d in range(n):
                roles = [vm.call[d][f][r] for r in CALL_ROLES]
                c = opb.new_var()
                for rv in roles:
                    opb.weighted_sum_at_least([(-rv, 1), (c, 1)], 1)        # role => c
                opb.weighted_sum_at_least([(-c, 1)] + [(rv, 1) for rv in roles], 1)  # c => some role
                cday.append(c)
            for d in range(n - 15 + 1):
                opb.weighted_sum_at_least([(-cday[d + o], 1) for o in range(15)], 1)

        if "C" in want and name in ccm:
            for wk in range(5, vm.num_weeks, 4):
                bd = diw.get(wk, [])
                if bd and min(bd) - 1 >= 0:
                    b = min(bd)
                    opb.at_most_k([nf[b - 1], nf[b]], 1)

    t = time.time()
    try:
        r = RUNNER.solve(opb, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(f"rules={''.join(sorted(want))} elec>={args.elec_floor}: TIMEOUT>{args.timeout}s")
        return 0
    dt = round(time.time() - t, 1)
    print(f"rules={''.join(sorted(want))} elec>={args.elec_floor}: "
          f"{'SAT' if r.satisfiable else 'UNSAT'} {dt}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
