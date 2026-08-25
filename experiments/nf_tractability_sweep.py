"""Tractability sweep over the NF parameter space, as a Slurm JOB ARRAY.

Each array task computes its cell from --grid-index against a grid defined here, builds the
config (all rules config-driven — no inline injection), and runs optimize() with a short
time limit. The metric is TIME-TO-FIRST-INCUMBENT (the real tractability signal: plain
solve() stalls on the full stack but optimize() finds incumbents), plus the final verdict.

Two experiments woven into one grid:
  - TIGHTNESS ramp (Elec floor, NCC-week caps, day band) → fast SAT or fast UNSAT.
  - RULE ABLATION (drop A / B / C / 11 one at a time) → which rule is the tractability killer
    (a cheap stand-in for "make it soft" — OFF is the limit of soft).
  - CCM SYMMETRY BREAK on/off (pin CCM Generic 1 to the first NCC block).

Verdict line (grep-able):
  CELL <i> | <params> | <SAT-incumbent@Ts / UNSAT@Ts / NO-INCUMBENT@limit> | firstinc=<Ts> | eval=<ok/violN>

Usage (job array):
  sbatch --array=0-143 ... --wrap "... --grid-index $SLURM_ARRAY_TASK_ID --time-limit 900"
  (see experiments/nf_tractability_sweep.sh for the exact launcher)
Or print the grid size / a cell:  --grid-index -1  (prints count and exits).
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

# --- the grid ---------------------------------------------------------------
# Each dimension is a list of (label, value). The cartesian product is the grid.
RULESETS = [
    ("ABC11", dict(a=2, b=14, c=True, m=True)),   # full stack
    ("dropA", dict(a=0, b=14, c=True, m=True)),   # ablate Rule A (off-cap)
    ("dropB", dict(a=2, b=0,  c=True, m=True)),   # ablate Rule B (max-call)
    ("dropC", dict(a=2, b=14, c=False, m=True)),  # ablate Rule C (ccm no-bridge)
    ("drop11", dict(a=2, b=14, c=True, m=False)), # ablate Rule 11 (min-ncc-block)
    ("none", dict(a=0, b=0, c=False, m=False)),   # base model only
]
CCM_SYM = [("symOff", False), ("symOn", True)]
ELEC = [("e2", 2), ("e4", 4), ("e6", 6)]
CAPS = [
    ("nocap", (0, 0)),
    ("capJR16SR28", (16, 28)),
    ("capJR14SR24", (14, 24)),
    ("capJR12SR22", (12, 22)),
]
# day band held at default 75/85,125/135 for this sweep (band is a separate axis to
# explore later; keeping it fixed bounds the grid to 6*2*3*4 = 144 cells).

GRID = list(product(RULESETS, CCM_SYM, ELEC, CAPS))


def _annual(rules, ccm_sym, elec_floor, caps) -> Path:
    base = yaml.safe_load(open(REPO / "config/annual/ncc-nf-model.yaml"))
    # Strip the production solver_options NF rule flags; the probe sets them explicitly
    # so a cell is fully determined by its grid coords (config file is just the skeleton).
    so = base.setdefault("solver_options", {})
    for k in ("nf_week_off_cap", "nf_max_consecutive_call_days", "nf_ccm_no_bridge_blocks",
              "nf_min_ncc_block_weeks"):
        so.pop(k, None)
    so["nf_week_off_cap"] = rules["a"]
    so["nf_max_consecutive_call_days"] = rules["b"]
    so["nf_ccm_no_bridge_blocks"] = rules["c"]
    so["nf_min_ncc_block_weeks"] = rules["m"]
    so["nf_ncc1_continuity"] = "weekday"
    # Elec floor: replace any existing floor rules with this cell's level.
    base["rules"] = [r for r in base["rules"]
                     if not (r.get("name", "").endswith("Elec floor"))]
    for g in ("NCC_JR", "NCC_SR"):
        base["rules"].append({"type": "shift_total", "name": f"{g} Elec floor",
                              "groups": [g], "shifts": ["Elec"], "relation": "at_least",
                              "count": elec_floor, "strength": "hard"})
    # NCC-week caps (0 => omit). Activate the dormant caps at this cell's level.
    base["rules"] = [r for r in base["rules"] if "NCC week cap" not in r.get("name", "")]
    jr_cap, sr_cap = caps
    if jr_cap:
        base["rules"].append({"type": "shift_total", "name": "JR NCC week cap",
                              "groups": ["NCC_JR"], "shifts": ["NCC"], "relation": "at_most",
                              "count": jr_cap, "strength": "hard"})
    if sr_cap:
        base["rules"].append({"type": "shift_total", "name": "SR NCC week cap",
                              "groups": ["NCC_SR"], "shifts": ["NCC"], "relation": "at_most",
                              "count": sr_cap, "strength": "hard"})
    # CCM symmetry break: pin CCM Generic 1 to NCC in week 0 → with all-or-none blocks it
    # owns the first block, distinguishing it from the (otherwise identical) CCM Generic 2.
    if ccm_sym:
        base["rules"].append({"type": "specific_assignment",
                              "name": "CCM1 block0 symmetry break",
                              "fellow": "CCM Generic 1", "week": 0, "shift": "NCC",
                              "strength": "hard"})
    return base


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid-index", type=int, required=True)
    ap.add_argument("--time-limit", type=float, default=900.0)
    args = ap.parse_args()

    if args.grid_index < 0:
        print(f"grid size = {len(GRID)} cells")
        return 0
    if args.grid_index >= len(GRID):
        print(f"grid-index {args.grid_index} out of range (grid size {len(GRID)})")
        return 1

    (rs_label, rules), (sym_label, ccm_sym), (e_label, elec), (cap_label, caps) = GRID[args.grid_index]
    label = f"{rs_label}/{sym_label}/{e_label}/{cap_label}"

    base = _annual(rules, ccm_sym, elec, caps)
    tmp = REPO / "results_slurm" / f"_sweepcfg_{args.grid_index}.yaml"
    tmp.parent.mkdir(exist_ok=True)
    yaml.safe_dump(base, open(tmp, "w"), sort_keys=False)
    res = assemble_config(None, annual_path=tmp, standing_path=STANDING, verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    sd = cfg.start_dow

    opb, vm = build_full_schedule_opb(cfg, objective=True)
    t0 = time.time()
    r = RUNNER.optimize(opb, time_limit=args.time_limit, echo_progress=True)
    dt = round(time.time() - t0, 1)

    # verdict
    if r.assignment is None:
        if getattr(r, "optimal", False):
            verdict = f"UNSAT@{dt}s"
        else:
            verdict = f"NO-INCUMBENT@{args.time_limit}s"
        print(f"CELL {args.grid_index} | {label} | {verdict} | firstinc=- | eval=-")
        return 0

    sol = decode_solution(r.assignment, vm)
    ev = evaluate_nf(sol, cfg)
    evtag = "ok" if ev.ok else f"viol{len(ev.violations)}"
    # per-fellow elec summary (compact)
    elec_by = {}
    for name in [x for g in ("NCC_JR", "NCC_SR") for x in cfg.fellow_groups[g]]:
        labels = sol.weekly_assignments[name]
        elec_by[name] = sum(1 for l in labels if l == "Elec")
    opt = "OPT" if r.optimal else "inc"
    print(f"CELL {args.grid_index} | {label} | SAT-{opt}@{dt}s | "
          f"elec={elec_by} | eval={evtag}")
    if not ev.ok:
        print(f"  EVAL VIOLATIONS: {ev.by_rule()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
