"""Localize the WB2 night/weekend UNSAT: is it a genuine per-night/role coverage
shortage against the shrunken eligible pool, or a remaining encoding artifact?

Uses env-gated diagnostic toggles in schedule_solver.py:
  SCHED_DIAG_DISABLE_NIGHTS=1     -> zero all night vars (isolate weekend layer)
  SCHED_DIAG_DISABLE_WEEKENDS=1   -> zero all weekend vars (isolate night layer)
  SCHED_DIAG_NIGHT_COVERAGE=relax -> per-night exactly_one -> at_most_one
  SCHED_DIAG_WEEKEND_COVERAGE=relax -> per-role exactly_one -> at_most_one

Loads WB2 config CORRECTLY (reads `specific_assignments`, the current key, so the
STROKE/NH pins are actually applied — the run script reads the stale
`locked_assignments` key and silently drops them).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import yaml

from scheduler.solver_bridge import build_solver_config_from_request
from scheduler.schedule_import import parse_schedule_file
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.schedule_solver import build_full_schedule_opb, decode_solution

ANNUAL = Path("config/annual/my-2026-2027-v3.yaml")
STANDING = Path("config/standing/stanford-fellowship-v3.yaml")
WORKBOOK = Path("workbook_partial_input2.xlsx")
ROUNDINGSAT = Path("new_approach/vendor/roundingsat/build/roundingsat")

SHIFT_MAP = {
    'MSICU': 'MICU', 'Anesthesia': 'Anaesthesia', 'Vacation': 'Vac',
    'Elective': 'Elec', 'Elective/SICU': 'SICU', 'Elective/NCS 2026': 'Elec',
    'Elec/APBN': 'Elec', 'NS SCVMC': 'NS',
}


def load_config():
    annual = yaml.safe_load(ANNUAL.read_text())
    locked = {}
    wb_groups = set(
        annual["fellow_groups"]["NCC_JR"]
        + annual["fellow_groups"]["NCC_SR"]
        + annual["fellow_groups"]["CCM"]
    )
    if WORKBOOK.exists():
        wb = parse_schedule_file(WORKBOOK.read_bytes(), WORKBOOK.name)
        for name, shifts in wb.assignments.items():
            if name not in wb_groups:
                continue
            locked[name] = [SHIFT_MAP.get(s, s) if s else "" for s in shifts]
    annual["locked_assignments"] = locked

    # CORRECT key: specific_assignments (STROKE/NH pins). The run script reads
    # the stale `locked_assignments` key here and drops these silently.
    specific = annual.get("specific_assignments", {})
    pin_count = 0
    for name in annual["fellow_groups"]["STROKE"] + annual["fellow_groups"]["NH"]:
        if name not in specific or name in locked:
            continue
        for w, shift in enumerate(specific[name]):
            if shift:
                annual.setdefault("rules", []).append({
                    "type": "specific_assignment", "fellow": name,
                    "week": w, "shift": shift, "strength": "hard",
                    "active": True, "name": f"{name} w{w} {shift}", "groups": [],
                })
                pin_count += 1

    # Soften NCC Team Cap — known encoding interaction (documented).
    standing = yaml.safe_load(STANDING.read_text())
    for r in standing["rules"]:
        if r.get("name") == "NCC Team Cap":
            r["strength"] = "soft"
    annual["standing_rules"] = standing["rules"]

    config = build_solver_config_from_request(annual, standing_path=STANDING)
    return config, locked, pin_count


def check(config, runner, label, env=None, timeout=120.0):
    saved = {}
    env = env or {}
    for k, v in env.items():
        saved[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        opb, var_map = build_full_schedule_opb(config, soft_bound=None)
        upper = sum(w for _, w in var_map.soft_violations)
        opb_f, _ = build_full_schedule_opb(config, soft_bound=upper)
        t0 = time.time()
        result = runner.solve(opb_f, timeout=timeout)
        elapsed = time.time() - t0
        if result.satisfiable:
            print(f"  [{label}] SAT ({elapsed:.1f}s)  "
                  f"[{opb.num_vars}v {opb.num_constraints}c]")
            return True
        print(f"  [{label}] UNSAT ({elapsed:.1f}s)  "
              f"[{opb.num_vars}v {opb.num_constraints}c]")
        return False
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def main():
    runner = RoundingSatRunner(ROUNDINGSAT)
    config, locked, pin_count = load_config()
    print("=" * 64)
    print(f"NIGHT/WEEKEND COVERAGE DIAGNOSIS — {WORKBOOK.name}")
    print(f"  locked workbook fellows: {sorted(locked)}")
    print(f"  STROKE/NH specific-assignment pins applied: {pin_count}")
    print("=" * 64)

    print("\n--- Baseline (everything on) ---")
    check(config, runner, "full")

    print("\n--- Isolate NIGHT layer (weekends disabled) ---")
    check(config, runner, "nights only", {"SCHED_DIAG_DISABLE_WEEKENDS": "1"})
    check(config, runner, "nights only + coverage relaxed",
          {"SCHED_DIAG_DISABLE_WEEKENDS": "1", "SCHED_DIAG_NIGHT_COVERAGE": "relax"})

    print("\n--- Isolate WEEKEND layer (nights disabled) ---")
    check(config, runner, "weekends only", {"SCHED_DIAG_DISABLE_NIGHTS": "1"})
    check(config, runner, "weekends only + coverage relaxed",
          {"SCHED_DIAG_DISABLE_NIGHTS": "1", "SCHED_DIAG_WEEKEND_COVERAGE": "relax"})

    print("\n--- Both layers, both coverages relaxed ---")
    check(config, runner, "full + both coverages relaxed",
          {"SCHED_DIAG_NIGHT_COVERAGE": "relax", "SCHED_DIAG_WEEKEND_COVERAGE": "relax"})

    print("\n" + "=" * 64)


if __name__ == "__main__":
    sys.exit(main())
