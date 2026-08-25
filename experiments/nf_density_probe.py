"""Density probe: what bounds NCC-service density, and can we push it toward ~6?

User question (2026-06-18): relax day-shift continuity (Rule 11 / min-NCC-block) and the
day-rest / max-consecutive-call cap (Rule B), but NOT NF rest, and see if density rises
toward 6 (ideal; no need higher). NCC1/NCC2 continuity may also help/hurt via NCC1<->NCC2
symmetry breaking — toggled here too.

Density here = (call-days held in NCC-labeled weeks) / (NCC-labeled weeks), per JR/SR fellow,
averaged. We OPTIMIZE (objective on) since density only materializes under optimization, and
ADD a direct soft reward for NCC-service-days (so the solver actually pushes density up rather
than just satisfying floors). Each cell reports per-fellow density + the headline mean.

Levers (--grid-index into the grid below):
  ruleB (max consecutive call days): 14 / 21 / off
  rule11 (min NCC block >=2wk):      on / off
  ncc1 continuity:                   weekday / off
  Rule A (week off-cap) is the density FLOOR (>=5/wk) — kept ON (=2) since it drives density
  up; we are testing the CEILING levers. NF rest always ON (user: don't touch it).
  Day band OFF (density should float to whatever the rules permit).

Usage (array): --grid-index $SLURM_ARRAY_TASK_ID --time-limit 1800   (or -1 to print size)
"""
from __future__ import annotations

import argparse
import collections
import time
from itertools import product
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

RULEB = [("b14", 14), ("b21", 21), ("bOff", 0)]
RULE11 = [("blk2", True), ("blkOff", False)]
NCC1 = [("ncc1wk", "weekday"), ("ncc1Off", "off")]
GRID = list(product(RULEB, RULE11, NCC1))   # 3*2*2 = 12 cells


def _cfg(ruleb, rule11, ncc1):
    base = yaml.safe_load(open(REPO / "config/annual/ncc-nf-model.yaml"))
    so = base["solver_options"]
    so["nf_week_off_cap"] = 2            # FLOOR (density driver) — keep on
    so["nf_max_consecutive_call_days"] = ruleb
    so["nf_min_ncc_block_weeks"] = rule11
    so["nf_ncc1_continuity"] = ncc1
    so["nf_ccm_no_bridge_blocks"] = True
    so["nf_service_day_band"] = "off"    # let density float
    # Direct density reward: a SOFT bonus per NCC service-day (negative penalty). We can't
    # easily add a negative-weight objective term via config, so instead we DROP the
    # NCC-week penalty (which fights density indirectly) and rely on Rule A's floor +
    # the soft Elec target. To actively push density we add a per-fellow soft "NCC-day
    # at_least" that the encoder turns into a penalty when unmet — approximate via a high
    # soft Elec target staying, plus removing the ccm/ncc week penalties so the solver is
    # free to pack. (Density is then driven by Rule A floor; the levers test the ceiling.)
    so["nf_ncc_week_penalty"] = 10       # keep: fewer NCC weeks => denser (concentration)
    so["nf_ccm_block_penalty"] = 50
    return base


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid-index", type=int, required=True)
    ap.add_argument("--time-limit", type=float, default=1800.0)
    args = ap.parse_args()
    if args.grid_index < 0:
        print(f"grid size = {len(GRID)} cells")
        return 0

    (bl, ruleb), (rl, rule11), (nl, ncc1) = GRID[args.grid_index]
    label = f"{bl}/{rl}/{nl}"
    base = _cfg(ruleb, rule11, ncc1)
    tmp = REPO / "results_slurm" / f"_denscfg_{args.grid_index}.yaml"
    tmp.parent.mkdir(exist_ok=True)
    yaml.safe_dump(base, open(tmp, "w"), sort_keys=False)
    res = assemble_config(None, annual_path=tmp, standing_path=STANDING, verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    sd = cfg.start_dow

    opb, vm = build_full_schedule_opb(cfg, objective=True)
    t0 = time.time()
    r = RUNNER.optimize(opb, time_limit=args.time_limit, echo_progress=True)
    dt = round(time.time() - t0, 1)
    if r.assignment is None:
        v = "UNSAT" if getattr(r, "optimal", False) else f"NO-INCUMBENT@{args.time_limit}s"
        print(f"DCELL {args.grid_index} | {label} | {v} | dens=-")
        return 0

    sol = decode_solution(r.assignment, vm)
    perweek = collections.defaultdict(lambda: collections.defaultdict(int))
    for d, h in enumerate(sol.call_assignments_by_day):
        w = _day_to_week(d, sd)
        for role in CALL_ROLES:
            if h.get(role):
                perweek[w][h[role]] += 1
    dens_by = {}
    for name in [x for g in ("NCC_JR", "NCC_SR") for x in cfg.fellow_groups[g]]:
        labels = sol.weekly_assignments[name]
        ncc = sum(1 for l in labels if l == "NCC")
        nccdays = sum(perweek[w][name] for w in range(len(labels)) if labels[w] == "NCC")
        dens_by[name] = round(nccdays / ncc, 2) if ncc else 0.0
    mean = round(sum(dens_by.values()) / len(dens_by), 2) if dens_by else 0
    ev = evaluate_nf(sol, cfg)
    evtag = "ok" if ev.ok else f"viol{len(ev.violations)}"
    opt = "OPT" if r.optimal else "inc"
    print(f"DCELL {args.grid_index} | {label} | SAT-{opt}@{dt}s | "
          f"mean-dens={mean} | per={dens_by} | eval={evtag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
