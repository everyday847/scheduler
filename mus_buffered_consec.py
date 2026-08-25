"""Deletion-based MUS for the buffered-hard no-consecutive-weekends UNSAT on wb6.

Seed: the wb6 config with buffered-HARD consecutive weekends, which is UNSAT.
We identify a minimal unsatisfiable subset (MUS) over a curated set of relaxable
HARD constraint families. A family is "relaxed" by softening the matching YAML
rules (strength -> soft) or flipping an encoder toggle. We:

  1. Confirm the seed (nothing relaxed) is UNSAT.
  2. Confirm relaxing ALL families is SAT (sanity: the core lives in this set).
  3. Deletion pass: for each family, relax it ON TOP of the running "relaxed"
     set; if the problem stays UNSAT when the family is KEPT hard but becomes SAT
     when relaxed, the family is in the MUS. Concretely we walk the standard
     deletion-based MUS: keep a family hard only if relaxing it (with everything
     not-yet-fixed already relaxed) is necessary for UNSAT.

The result is a small set of families whose simultaneous hardness is what makes
buffered-hard infeasible — the real culprit, instead of a hand-wavy count.

Each SAT check is a separate RoundingSat call; run on Slurm (single 1-CPU job).
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import yaml

import run_v3_optimize_wb6 as r
from scheduler.solver_bridge import build_solver_config_from_request
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb

ROUNDINGSAT = r.ROUNDINGSAT
STANDING = r.STANDING

# Relaxable HARD families. YAML families list rule NAMES to soften; ENCODER
# families set an env var / config override. Curated to the constraints
# plausibly interacting with weekend spacing + light-week placement.
YAML_FAMILIES = {
    "stroke_tele_total": ["Stroke: Telestroke Total"],
    "stroke_clinic_total": ["Stroke: Clinic/Elective Total"],
    "stroke_service_total": ["Stroke: Stroke Service Total"],
    "stroke_vac_total": ["Stroke: Vacation Total"],
    "stroke_fixed_rotations": ["Stroke: SCVMC Rehab Total", "Stroke: NIR Total",
                                "Stroke: ISC Total", "STROKE Elective/APBN",
                                "Stroke NIR First Half", "Stroke NIR Second Half",
                                "Stroke SCVMC Second Half"],
    "stroke_nccswing_total": ["Stroke: NCC+Swing Total"],
    "ncc2_coverage": ["NCC2 Coverage"],
    "stroke_service_coverage": ["Stroke Service Coverage"],
    "telestroke_coverage": ["Telestroke Coverage"],
    "max_consecutive": ["Core ICU Max Consecutive", "NCC Team Max Consecutive",
                         "Swing Max Consecutive", "Short Stroke/Telestroke/Clinic/Elective"],
    "block_rotations": ["MICU 4-Week Blocks", "SICU 4-Week Blocks",
                         "Anaesthesia 4-Week Blocks", "NS 2-Week Blocks", "CCM NCC Block"],
    "week0_pins": ["Aditya wk0 Elec", "Cameron wk0 Elec", "Harneet wk0 Elec",
                   "Helena wk0 Elec", "Helena wk1 Stroke", "Helena wk1 not alone on Stroke"],
}
# Encoder families: (env_overrides, config_overrides) applied at build time.
ENCODER_FAMILIES = {
    "weekend_total_band": {"config": {"weekend_total_tolerance": 99}},
    "weekend_coverage": {"env": {"SCHED_DIAG_WEEKEND_COVERAGE": "relax"}},
    "night_spacing": {"env": {"SCHED_DIAG_DISABLE_NIGHT_SPACING": "1"}},
    "saturday_hard": {"config": {"weekend_night_saturday_hard": False}},
}
ALL_FAMILIES = list(YAML_FAMILIES) + list(ENCODER_FAMILIES)


def _build(relaxed: set[str], *, verbose=False):
    """Build the wb6 OPB+varmap with buffered-HARD consecutive, with the named
    families relaxed (YAML softened / encoder toggled)."""
    # Reset env each build.
    for fam in ENCODER_FAMILIES:
        for k in ENCODER_FAMILIES[fam].get("env", {}):
            os.environ.pop(k, None)
    os.environ["SCHED_DIAG_CONSECUTIVE"] = "hard"  # the seed under test

    annual = r.assemble_config_dicts(verbose=verbose)
    # Soften YAML families that are relaxed.
    relax_names = set()
    for fam in relaxed:
        if fam in YAML_FAMILIES:
            relax_names.update(YAML_FAMILIES[fam])
    if relax_names:
        for lst_key in ("standing_rules", "rules"):
            for rule in annual.get(lst_key, []):
                if rule.get("name") in relax_names:
                    rule["strength"] = "soft"
                    rule["active"] = rule.get("active", True)
    # Apply encoder env overrides for relaxed encoder families.
    config_over = {}
    for fam in relaxed:
        if fam in ENCODER_FAMILIES:
            for k, v in ENCODER_FAMILIES[fam].get("env", {}).items():
                os.environ[k] = v
            config_over.update(ENCODER_FAMILIES[fam].get("config", {}))

    config = build_solver_config_from_request(annual, standing_path=STANDING)
    if config_over:
        import dataclasses
        config = dataclasses.replace(config, **config_over)
    opb, vm = build_full_schedule_opb(config, objective=True)
    return opb, vm


def _is_sat(relaxed: set[str], runner, *, sat_limit, unsat_limit) -> str:
    opb, vm = _build(relaxed)
    res = runner.optimize(opb, time_limit=sat_limit)
    if res.satisfiable and res.assignment is not None:
        return "SAT"
    if res.proven_unsat:
        return "UNSAT"
    res = runner.optimize(opb, time_limit=unsat_limit)
    if res.satisfiable and res.assignment is not None:
        return "SAT"
    if res.proven_unsat:
        return "UNSAT"
    return "UNKNOWN"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sat-limit", type=float, default=120.0)
    ap.add_argument("--unsat-limit", type=float, default=300.0)
    ap.add_argument("--out", default="results_slurm/mus_buffered_consec.json")
    args = ap.parse_args()

    runner = RoundingSatRunner(ROUNDINGSAT)
    log = {"families": ALL_FAMILIES, "steps": []}

    def check(relaxed, label):
        t0 = time.time()
        state = _is_sat(set(relaxed), runner, sat_limit=args.sat_limit, unsat_limit=args.unsat_limit)
        secs = time.time() - t0
        print(f"[{label}] relaxed={sorted(relaxed)} -> {state} ({secs:.1f}s)", flush=True)
        log["steps"].append({"label": label, "relaxed": sorted(relaxed), "state": state, "seconds": secs})
        return state

    # 1. Seed: nothing relaxed -> expect UNSAT.
    seed = check(set(), "seed")
    # 2. All relaxed -> expect SAT.
    allrelaxed = check(set(ALL_FAMILIES), "all-relaxed")

    mus_core = []
    if seed == "UNSAT" and allrelaxed == "SAT":
        # Deletion-based MUS: start with ALL families HARD (= none relaxed = UNSAT).
        # For each family, tentatively relax it; if still UNSAT, KEEP it relaxed
        # (not needed for the conflict). If it becomes SAT, that family is part of
        # the MUS — revert (keep hard) and record it.
        relaxed = set()
        for fam in ALL_FAMILIES:
            trial = relaxed | {fam}
            state = check(trial, f"try-relax:{fam}")
            if state == "SAT":
                # fam is load-bearing for UNSAT under the current relaxed set.
                mus_core.append(fam)
                # keep fam HARD; do not add to relaxed.
            else:
                # fam not needed -> relax it permanently to shrink the problem.
                relaxed = trial
        log["mus_core"] = mus_core
        print(f"\nMUS core (families whose hardness is jointly required for UNSAT): {mus_core}", flush=True)
    else:
        log["mus_core"] = None
        print(f"\nCannot run MUS: seed={seed}, all-relaxed={allrelaxed} "
              f"(need UNSAT/SAT).", flush=True)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(log, indent=2))
    print(f"Wrote {args.out}", flush=True)


if __name__ == "__main__":
    main()
