"""Name the binding weeks: minimum night/weekend coverage deficit on the trusted
encoding, and exactly which nights/roles cannot be covered.

Coverage is encoded SOFT (slack u=1 = uncovered, weight=COVERAGE_WEIGHT). We then
add a HARD cap `sum(coverage_slacks) <= K` and binary-search the smallest feasible
K = the minimum number of slots the encoder genuinely cannot cover. Decoding the
K*-solution lists the specific uncovered nights/weeks.

Run each layer in isolation (the two deficits are independent, per
diagnose_night_coverage.py).
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
COVERAGE_WEIGHT = 100000

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

    specific = annual.get("specific_assignments", {})
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

    standing = yaml.safe_load(STANDING.read_text())
    for r in standing["rules"]:
        if r.get("name") == "NCC Team Cap":
            r["strength"] = "soft"
    annual["standing_rules"] = standing["rules"]

    return build_solver_config_from_request(annual, standing_path=STANDING)


def coverage_slacks(var_map):
    return [v for v, w in var_map.soft_violations if w == COVERAGE_WEIGHT]


def build_capped(config, env, k):
    """Build OPB (with env toggles) bounded so all softs are free, then add a
    HARD cap sum(coverage_slacks) <= k. Returns (result, var_map, elapsed)."""
    saved = {}
    for key, val in env.items():
        saved[key] = os.environ.get(key)
        os.environ[key] = val
    try:
        opb, var_map = build_full_schedule_opb(config, soft_bound=None)
        upper = sum(w for _, w in var_map.soft_violations)
        opb_b, var_map = build_full_schedule_opb(config, soft_bound=upper)
        opb_b.at_most_k(coverage_slacks(var_map), k)
        t0 = time.time()
        res = runner_global.solve(opb_b, timeout=120.0)
        return res, var_map, time.time() - t0, opb_b
    finally:
        for key, val in saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val


def min_deficit(config, env, layer_name):
    """Binary search smallest K with sum(coverage_slacks) <= K feasible."""
    res, var_map, dt, opb = build_capped(config, env, 0)
    n_slots = len(coverage_slacks(var_map))
    print(f"  [{layer_name}] {n_slots} coverage slots, "
          f"{opb.num_vars}v {opb.num_constraints}c")
    if res.satisfiable:
        print(f"  [{layer_name}] K=0 SAT ({dt:.1f}s) — no deficit")
        return 0, var_map, res.assignment
    print(f"  [{layer_name}] K=0 UNSAT ({dt:.1f}s) — deficit exists, searching")

    lo, hi = 1, n_slots
    best = None
    while lo < hi:
        mid = (lo + hi) // 2
        res, vm, dt, _ = build_capped(config, env, mid)
        tag = "SAT" if res.satisfiable else "UNSAT"
        print(f"  [{layer_name}] K={mid}: {tag} ({dt:.1f}s)")
        if res.satisfiable:
            best = (mid, vm, res.assignment)
            hi = mid
        else:
            lo = mid + 1
    if best is None or best[0] != lo:
        res, vm, dt, _ = build_capped(config, env, lo)
        best = (lo, vm, res.assignment)
    return best[0], best[1], best[2]


def report_nights(config):
    print("\n" + "=" * 64)
    print("NIGHT COVERAGE DEFICIT (weekends disabled)")
    print("=" * 64)
    env = {"SCHED_DIAG_DISABLE_WEEKENDS": "1", "SCHED_DIAG_NIGHT_COVERAGE": "soft"}
    k, var_map, assignment = min_deficit(config, env, "night")
    print(f"\n  Minimum uncovered nights: {k}")
    if k > 0:
        sol = decode_solution(assignment, var_map)
        from scheduler.call_schedule_common import NIGHT_ROLES
        from parafrost_scheduler.schedule_solver import _week_day
        uncovered = []
        for w, week in enumerate(sol.night_solution.assignments_by_week):
            for dow_target, role in enumerate(NIGHT_ROLES):
                d = _week_day(w, dow_target, var_map.start_dow)
                if d < 0 or d >= var_map.num_days:
                    continue  # phantom slot outside the horizon, not a real night
                if week.get(role, "") == "":
                    uncovered.append((w, role))
        print(f"  Uncovered night slots ({len(uncovered)}):")
        from collections import Counter
        wk_counts = Counter(w for w, _ in uncovered)
        for w in sorted(wk_counts):
            roles = [r for ww, r in uncovered if ww == w]
            print(f"    week {w:2d}: {wk_counts[w]} uncovered  ({', '.join(roles)})")


def report_weekends(config):
    print("\n" + "=" * 64)
    print("WEEKEND COVERAGE DEFICIT (nights disabled)")
    print("=" * 64)
    env = {"SCHED_DIAG_DISABLE_NIGHTS": "1", "SCHED_DIAG_WEEKEND_COVERAGE": "soft"}
    k, var_map, assignment = min_deficit(config, env, "weekend")
    print(f"\n  Minimum uncovered weekend roles: {k}")
    if k > 0:
        sol = decode_solution(assignment, var_map)
        uncovered = []
        for w, week in enumerate(sol.weekend_solution.assignments_by_week):
            for role, who in week.items():
                if who == "":
                    uncovered.append((w, role))
        print(f"  Uncovered weekend role slots ({len(uncovered)}):")
        from collections import Counter
        wk_counts = Counter(w for w, _ in uncovered)
        for w in sorted(wk_counts):
            roles = [r for ww, r in uncovered if ww == w]
            print(f"    week {w:2d}: {wk_counts[w]} uncovered  ({', '.join(roles)})")


runner_global = RoundingSatRunner(ROUNDINGSAT)


def main():
    config = load_config()
    report_nights(config)
    report_weekends(config)
    print("\n" + "=" * 64)


if __name__ == "__main__":
    sys.exit(main())
