"""Solve schedule with workbook import + new constraints, then optimize."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import yaml

from scheduler.solver_bridge import build_solver_config_from_request
from scheduler.schedule_import import parse_schedule_file
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.schedule_types import FullScheduleSolution
from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb,
    decode_solution,
)

ANNUAL = Path("config/annual/my-2026-2027-v3.yaml")
STANDING = Path("config/standing/stanford-fellowship-v3.yaml")
WORKBOOK = Path("workbook_partial_input.xlsx")
ROUNDINGSAT = Path("new_approach/vendor/roundingsat/build/roundingsat")

SHIFT_MAP = {
    'MSICU': 'MICU', 'Anesthesia': 'Anaesthesia', 'Vacation': 'Vac',
    'Elective': 'Elec', 'Elective/SICU': 'SICU', 'Elective/NCS 2026': 'Elec',
    'Elec/APBN': 'Elec', 'NS SCVMC': 'NS',
}


def load_config_with_workbook():
    annual = yaml.safe_load(ANNUAL.read_text())

    locked = {}
    wb_groups = set(annual.get("fellow_groups", {}).get("NCC_JR", [])
                    + annual.get("fellow_groups", {}).get("NCC_SR", [])
                    + annual.get("fellow_groups", {}).get("CCM", []))
    if WORKBOOK.exists():
        wb_bytes = WORKBOOK.read_bytes()
        result = parse_schedule_file(wb_bytes, WORKBOOK.name)
        print(f"Imported workbook: {len(result.fellow_names)} fellows, "
              f"{result.num_weeks} weeks, shifts: {result.shifts_found}")
        for name, shifts in result.assignments.items():
            if name not in wb_groups:
                continue
            mapped = []
            for s in shifts:
                mapped.append(SHIFT_MAP.get(s, s) if s else "")
            locked[name] = mapped
        print(f"Locked {len(locked)} fellows from workbook (NCC + CCM)")
    annual["locked_assignments"] = locked

    # STROKE/NH specific-week assignments become specific_assignment rules
    specific_locks = annual.get("locked_assignments", {})
    for group in ("STROKE", "NH"):
        for name in annual.get("fellow_groups", {}).get(group, []):
            if name not in specific_locks or name in locked:
                continue
            for w, shift in enumerate(specific_locks[name]):
                if shift:
                    annual.setdefault("rules", []).append({
                        "type": "specific_assignment",
                        "name": f"{name} w{w} {shift}",
                        "fellow": name,
                        "week": w,
                        "shift": shift,
                        "strength": "hard",
                        "active": True,
                        "groups": [],
                    })
    config = build_solver_config_from_request(annual, standing_path=STANDING)
    return config


def solve_at_bound(config, runner, bound, timeout=30.0):
    opb, var_map = build_full_schedule_opb(config, soft_bound=bound)
    try:
        result = runner.solve(opb, timeout=timeout)
    except Exception as e:
        return None, None, str(e)
    if result.satisfiable:
        _, decode_map = build_full_schedule_opb(config, soft_bound=bound)
        sol = decode_solution(result.assignment, decode_map)
        return sol, result.assignment, None
    return None, None, "UNSAT"


def main():
    config = load_config_with_workbook()
    runner = RoundingSatRunner(ROUNDINGSAT)

    print(f"\nConfig: {sum(len(v) for v in config.fellow_groups.values())} fellows, "
          f"{len(config.shifts)} shifts, {len(config.constraints)} rules, "
          f"{config.num_weeks} weeks")
    print(f"Call rules: {len(config.call_rules)}")

    # Build formula to get upper bound
    opb_check, var_map = build_full_schedule_opb(config, soft_bound=None)
    upper_bound = sum(w for _, w in var_map.soft_violations)
    print(f"Soft violation upper bound: {upper_bound}")
    print(f"Formula: {opb_check.num_vars} vars, {opb_check.num_constraints} constraints, "
          f"{len(var_map.soft_violations)} soft indicators\n")

    # Phase 1: feasibility check
    print("=" * 60)
    print("PHASE 1: Feasibility check")
    print("=" * 60)
    t0 = time.time()
    sol, best_assignment, err = solve_at_bound(config, runner, upper_bound, timeout=120.0)
    elapsed = time.time() - t0
    if sol is None:
        print(f"INFEASIBLE at upper bound {upper_bound}: {err}")
        return 1
    print(f"FEASIBLE at bound={upper_bound}, penalty={sol.soft_penalty} ({elapsed:.1f}s)")
    best_bound = upper_bound
    best_sol = sol
    write_solution(best_sol, config, Path("output_new_constraints.csv"))

    # Optimization: probe at bound, jump to actual_penalty-1 on SAT
    print(f"\n{'=' * 60}")
    print("OPTIMIZATION (penalty-jump strategy)")
    print("=" * 60)

    current = best_sol.soft_penalty - 1
    timeout = 30.0
    while current >= 0:
        t0 = time.time()
        sol, assignment, err = solve_at_bound(config, runner, current, timeout=timeout)
        elapsed = time.time() - t0
        if sol is not None:
            best_sol = sol
            best_bound = current
            print(f"  SAT at bound={current} → penalty={sol.soft_penalty} ({elapsed:.1f}s)")
            write_solution(best_sol, config, Path("output_new_constraints.csv"))
            if sol.soft_penalty < current:
                current = sol.soft_penalty - 1
            else:
                current -= 1
                timeout = 120.0
        else:
            print(f"  UNSAT/timeout at {current} ({err}, {elapsed:.1f}s)")
            if "timed out" in (err or ""):
                timeout = min(timeout * 2, 300.0)
                current -= 1
            else:
                break

    print(f"\n{'=' * 60}")
    print(f"OPTIMAL soft penalty: {best_bound}")
    print(f"Actual penalty in solution: {best_sol.soft_penalty}")
    print("=" * 60)

    write_solution(best_sol, config, Path("output_new_constraints.csv"))
    return 0


def write_solution(sol, config, output_path):
    import csv
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        fellow_names = list(sol.weekly_assignments.keys())
        wk_keys = list(sol.weekend_solution.assignments_by_week[0].keys())
        nk_keys = list(sol.night_solution.assignments_by_week[0].keys())
        writer.writerow(fellow_names + wk_keys + nk_keys)
        for w in range(config.num_weeks):
            row = [sol.weekly_assignments[name][w] for name in fellow_names]
            row += [sol.weekend_solution.assignments_by_week[w].get(r, "") for r in wk_keys]
            row += [sol.night_solution.assignments_by_week[w].get(r, "") for r in nk_keys]
            writer.writerow(row)
    print(f"Wrote schedule to {output_path} (penalty={sol.soft_penalty})", flush=True)


if __name__ == "__main__":
    sys.exit(main())
