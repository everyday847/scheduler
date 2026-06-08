"""v3 schedule optimization with workbook_partial_input2.xlsx.

Version: 2026-06-03b
Same as run_v3_optimize.py but uses workbook2 and keeps NCC Team Cap hard.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from scheduler.call_schedule_common import NIGHT_ROLES, WEEKEND_ROLES, ParsedCallScheduleCsv, WeekRow
from scheduler.night_call_types import NightScheduleSolution
from scheduler.weekend_call_types import WeekendScheduleSolution
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.schedule_types import FullScheduleSolution
from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb,
    decode_solution,
    soft_penalty_breakdown,
)
from parafrost_scheduler.schedule_optimizer import optimize_stream
from parafrost_scheduler.workbook import write_schedule_workbook

ANNUAL = Path("config/annual/my-2026-2027-v3.yaml")
STANDING = Path("config/standing/stanford-fellowship-v3.yaml")
WORKBOOK = Path("workbook_partial_input5.xlsx")
ROUNDINGSAT = Path("vendor/roundingsat/build/roundingsat")
OUTPUT_WORKBOOK = Path("output_v3_wb5_workbook.xlsx")
OUTPUT_CSV = Path("output_v3_wb5.csv")



def assemble_config_dicts(*, verbose=True):
    """Thin shim over the shared experiment.assemble_annual_dict. The
    config-assembly logic lives in ONE place now; harden_experiment / slurm_sat_job
    mutate WORKBOOK before calling, so this reads the module-global WORKBOOK."""
    from parafrost_scheduler.experiment import assemble_annual_dict
    return assemble_annual_dict(WORKBOOK, annual_path=ANNUAL,
                                standing_path=STANDING, verbose=verbose)


def load_config():
    """Thin shim over the shared experiment.assemble_config."""
    from parafrost_scheduler.experiment import assemble_config
    return assemble_config(WORKBOOK, annual_path=ANNUAL, standing_path=STANDING)


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
    print("\n--- Native optimization (streaming incumbents) ---", flush=True)
    print("  (total = weekly + weekend/night soft penalty)", flush=True)
    best = None
    for step in optimize_stream(config, runner, preview_seconds=preview_seconds,
                                max_seconds=max_seconds, echo_progress=True):
        best = step.solution
        tag = "  [OPTIMAL]" if step.optimal else ""
        gap = ""
        if step.lower_bound is not None:
            gap = f"  lb={step.lower_bound:6d}  gap={step.total_penalty - step.lower_bound:6d}"
        print(f"  [{step.elapsed:6.1f}s] total={step.total_penalty:6d}  "
              f"weekly={step.weekly_penalty:5d}  weekend/night={step.call_penalty:5d}{gap}{tag}",
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
