"""v3 schedule optimization with workbook_partial_input2.xlsx.

Version: 2026-06-03b
Same as run_v3_optimize.py but uses workbook2 and keeps NCC Team Cap hard.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import yaml

from scheduler.solver_bridge import build_solver_config_from_request
from scheduler.schedule_import import parse_schedule_file
from scheduler.call_schedule_common import NIGHT_ROLES, WEEKEND_ROLES, ParsedCallScheduleCsv, WeekRow
from scheduler.night_call_types import NightScheduleSolution
from scheduler.weekend_call_types import WeekendScheduleSolution
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.schedule_solver import (
    build_full_schedule_opb, decode_solution, soft_penalty_breakdown,
    optimize_stream, FullScheduleSolution,
)
from parafrost_scheduler.workbook import write_schedule_workbook

ANNUAL = Path("config/annual/my-2026-2027-v3.yaml")
STANDING = Path("config/standing/stanford-fellowship-v3.yaml")
WORKBOOK = Path("workbook_partial_input2.xlsx")
ROUNDINGSAT = Path("new_approach/vendor/roundingsat/build/roundingsat")
OUTPUT_WORKBOOK = Path("output_v3_wb2_workbook.xlsx")
OUTPUT_CSV = Path("output_v3_wb2.csv")

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
        print(f"Imported workbook: {WORKBOOK.name}, {len(wb.fellow_names)} fellows")
        for name, shifts in wb.assignments.items():
            if name not in wb_groups:
                continue
            locked[name] = [SHIFT_MAP.get(s, s) if s else "" for s in shifts]
        print(f"Locked {len(locked)} fellows from workbook (NCC + CCM)")

    annual["locked_assignments"] = locked

    specific = yaml.safe_load(ANNUAL.read_text()).get("specific_assignments", {})
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

    # Soften NCC Team Cap — same encoding interaction bug as wb1
    standing = yaml.safe_load(STANDING.read_text())
    for r in standing["rules"]:
        if r.get("name") == "NCC Team Cap":
            r["strength"] = "soft"
    annual["standing_rules"] = standing["rules"]

    config = build_solver_config_from_request(annual, standing_path=STANDING)
    return config, annual


def solution_to_parsed(sol, fellow_order):
    num_weeks = len(next(iter(sol.weekly_assignments.values())))
    week_rows = []
    for w in range(num_weeks):
        weekday_assignments = {name: sol.weekly_assignments[name][w] for name in fellow_order}
        weekend_data = sol.weekend_solution.assignments_by_week[w]
        schedule_assignments = {role: weekend_data.get(role, "") for role in WEEKEND_ROLES}
        week_rows.append(WeekRow(weekday_assignments=weekday_assignments,
                                 schedule_assignments=schedule_assignments, raw_row=[]))
    return ParsedCallScheduleCsv(fellow_names=fellow_order,
                                 existing_schedule_columns=tuple(WEEKEND_ROLES),
                                 week_rows=week_rows, trailing_rows=[])


def write_csv(sol, config, output_path):
    import csv
    fellow_names = list(sol.weekly_assignments.keys())
    num_weeks = len(next(iter(sol.weekly_assignments.values())))
    wk_keys = list(sol.weekend_solution.assignments_by_week[0].keys())
    nk_keys = list(sol.night_solution.assignments_by_week[0].keys())
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(fellow_names + wk_keys + nk_keys)
        for w in range(num_weeks):
            row = [sol.weekly_assignments[name][w] for name in fellow_names]
            row += [sol.weekend_solution.assignments_by_week[w].get(r, "") for r in wk_keys]
            row += [sol.night_solution.assignments_by_week[w].get(r, "") for r in nk_keys]
            writer.writerow(row)


def optimize(config, runner, annual, *, preview_seconds=(8.0, 25.0), max_seconds=180.0):
    """Native-optimization streaming. Each improving incumbent is written to the
    CSV + workbook immediately (anytime: the latest schedule is always on disk),
    with the weekly vs weekend/night soft-penalty breakdown printed live."""
    print("\n--- Native optimization (streaming incumbents) ---")
    print("  (total = weekly + weekend/night soft penalty)")
    best = None
    for step in optimize_stream(config, runner,
                                preview_seconds=preview_seconds, max_seconds=max_seconds):
        best = step.solution
        tag = "  [OPTIMAL]" if step.optimal else ""
        print(f"  [{step.elapsed:6.1f}s] total={step.total_penalty:6d}  "
              f"weekly={step.weekly_penalty:5d}  weekend/night={step.call_penalty:5d}{tag}",
              flush=True)
        write_outputs(step.solution, config, annual)
    if best is None:
        print("INFEASIBLE!")
        return None
    print(f"\nBest penalty: {best.soft_penalty}")
    return best


def write_outputs(sol, config, annual):
    """Write the CSV and workbook for a (possibly intermediate) solution."""
    write_csv(sol, config, OUTPUT_CSV)
    fellow_order = list(sol.weekly_assignments.keys())
    parsed = solution_to_parsed(sol, fellow_order)
    from scheduler.night_policy_types import (
        CRITERION_ANAESTHESIA, CRITERION_FRIDAY_WEEKEND_NCC1, CRITERION_SUNDAY_FOLLOWING,
    )
    hard_criteria = frozenset({CRITERION_ANAESTHESIA, CRITERION_FRIDAY_WEEKEND_NCC1,
                               CRITERION_SUNDAY_FOLLOWING})
    write_schedule_workbook(parsed, sol.night_solution, sol.weekend_solution,
                            OUTPUT_WORKBOOK, hard_criteria=hard_criteria)


def main():
    print("=" * 60)
    print(f"V3 Schedule Optimization — {WORKBOOK.name}")
    print("=" * 60)

    config, annual = load_config()
    runner = RoundingSatRunner(ROUNDINGSAT)

    best = optimize(config, runner, annual)
    if best is None:
        return 1

    # write_outputs() already wrote the latest CSV + workbook on each improvement.
    print(f"\nWrote CSV: {OUTPUT_CSV}")
    print(f"Wrote workbook: {OUTPUT_WORKBOOK}")

    vac_requests = annual.get("fellow_week_pairs", {})
    vac_ok = sum(1 for n, ws in vac_requests.items() for w in ws
                 if w < len(best.weekly_assignments.get(n, [])) and best.weekly_assignments[n][w] == "Vac")
    print(f"\nVacations: {vac_ok}/{sum(len(ws) for ws in vac_requests.values() if ws)}")
    print(f"Penalty: {best.soft_penalty}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
