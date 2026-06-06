"""Shared config-assembly for the scheduler — ONE source of truth.

Every entry point (optimize / SAT-check / MUS / diagnose) builds its
ScheduleSolverConfig the same way: import a workbook, canonicalize its shift
names via SHIFT_MAP, lock the imported NCC+CCM fellows, inject the STROKE/NH
specific_assignment pins, soften the NCC Team Cap, and merge the standing rules.
This module is that single implementation, replacing the copies that used to
live in run_v3_optimize*.py.

Behavior is pinned by tests/test_experiment_assemble.py (golden equivalence to
the legacy run_v3_optimize_wb6.load_config()).
"""

from __future__ import annotations

from pathlib import Path

import yaml

import csv as _csv

from scheduler.solver_bridge import build_solver_config_from_request
from scheduler.schedule_import import parse_schedule_file
from scheduler.call_schedule_common import WEEKEND_ROLES, ParsedCallScheduleCsv, WeekRow

# Canonical (post-import) shift renames. The imported workbook uses display
# names; the solver/config use canonical names. SHIFT_MAP is the single place
# this mapping is defined.
SHIFT_MAP = {
    "MSICU": "MICU",
    "Anesthesia": "Anaesthesia",
    "Vacation": "Vac",
    "Elective": "Elec",
    "Elective/SICU": "SICU",
    "NS SCVMC": "NS",
    # APBN was a typo for ABPN (American Board of Psychiatry & Neurology);
    # canonicalize any imported-workbook "APBN" cells to the correct "ABPN".
    "APBN": "ABPN",
}

# Default config locations (repo-relative).
_REPO = Path(__file__).resolve().parents[3]
DEFAULT_ANNUAL = _REPO / "config/annual/my-2026-2027-v3.yaml"
DEFAULT_STANDING = _REPO / "config/standing/stanford-fellowship-v3.yaml"
# Locked groups: the fellows whose weekly schedule is frozen from the workbook.
_LOCKED_GROUPS = ("NCC_JR", "NCC_SR", "CCM")
# Groups whose specific_assignments are injected as hard pins (solver-managed).
_INJECT_GROUPS = ("STROKE", "NH")


def assemble_annual_dict(
    workbook: Path | None,
    *,
    annual_path: Path = DEFAULT_ANNUAL,
    standing_path: Path = DEFAULT_STANDING,
    soften: tuple[str, ...] = ("NCC Team Cap",),
    inject_specific: bool = True,
    verbose: bool = True,
) -> dict:
    """Assemble the mutable annual request dict (no ScheduleSolverConfig yet).

    Callers may mutate the returned dict (e.g. flip a rule's strength) before
    calling build_solver_config_from_request.
    """
    annual = yaml.safe_load(Path(annual_path).read_text())

    locked: dict[str, list[str]] = {}
    wb_groups = set()
    for g in _LOCKED_GROUPS:
        wb_groups.update(annual["fellow_groups"].get(g, []))
    if workbook is not None and Path(workbook).exists():
        wb = parse_schedule_file(Path(workbook).read_bytes(), Path(workbook).name)
        if verbose:
            print(f"Imported workbook: {Path(workbook).name}, {len(wb.fellow_names)} fellows")
        for name, shifts in wb.assignments.items():
            if name not in wb_groups:
                continue
            locked[name] = [SHIFT_MAP.get(s, s) if s else "" for s in shifts]
        if verbose:
            print(f"Locked {len(locked)} fellows from workbook (NCC + CCM)")
    annual["locked_assignments"] = locked

    if inject_specific:
        specific = annual.get("specific_assignments", {})
        for group in _INJECT_GROUPS:
            for name in annual["fellow_groups"].get(group, []):
                if name not in specific or name in locked:
                    continue
                for w, shift in enumerate(specific[name]):
                    if shift:
                        annual.setdefault("rules", []).append({
                            "type": "specific_assignment", "fellow": name,
                            "week": w, "shift": shift, "strength": "hard",
                            "active": True, "name": f"{name} w{w} {shift}", "groups": [],
                        })

    standing = yaml.safe_load(Path(standing_path).read_text())
    for r in standing["rules"]:
        if r.get("name") in soften:
            r["strength"] = "soft"
    annual["standing_rules"] = standing["rules"]

    return annual


def assemble_config(
    workbook: Path | None,
    *,
    annual_path: Path = DEFAULT_ANNUAL,
    standing_path: Path = DEFAULT_STANDING,
    soften: tuple[str, ...] = ("NCC Team Cap",),
    inject_specific: bool = True,
    verbose: bool = True,
):
    """Build a ScheduleSolverConfig from a workbook + config YAMLs.

    Returns ``(config, annual_dict)``. The annual_dict is returned so callers
    that need the raw request (e.g. vacation-request reporting) still have it.
    """
    annual = assemble_annual_dict(
        workbook, annual_path=annual_path, standing_path=standing_path,
        soften=soften, inject_specific=inject_specific, verbose=verbose,
    )
    config = build_solver_config_from_request(annual, standing_path=standing_path)
    return config, annual


# ---------------------------------------------------------------------------
# Output helpers (shared by the CLI and the run-script shims)
# ---------------------------------------------------------------------------

def solution_to_parsed(sol, fellow_order: list[str]) -> ParsedCallScheduleCsv:
    """Reconstruct a ParsedCallScheduleCsv from a solved schedule (for the
    workbook writer, which colors against the solved weekday + weekend data)."""
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


def swap_ncc_weekend_roles(parsed, weekend_solution, night_solution):
    """Return a new WeekendScheduleSolution with NCC1/NCC2 weekend roles swapped
    per week WHEN the swap strictly improves weekday<->weekend NCC alignment and
    does NOT put the incoming Weekend-NCC1 fellow on that week's Friday night
    (a friday_weekend_ncc1 HARD violation). The two NCC weekend roles are
    interchangeable to every other constraint, so this Friday check is the only
    guard needed to guarantee the result never scores worse. Weekend Stroke is
    never touched. Pure function -- does not mutate its inputs.
    """
    from scheduler.weekend_call_types import WeekendScheduleSolution

    new_weeks = []
    for w, assignments in enumerate(weekend_solution.assignments_by_week):
        a = assignments.get("Weekend NCC1", "")
        b = assignments.get("Weekend NCC2", "")
        new = dict(assignments)
        if a and b:
            wd = parsed.week_rows[w].weekday_assignments
            wd_a = wd.get(a, "")
            wd_b = wd.get(b, "")
            cur = (1 if wd_a == "NCC1" else 0) + (1 if wd_b == "NCC2" else 0)
            swp = (1 if wd_b == "NCC1" else 0) + (1 if wd_a == "NCC2" else 0)
            if swp > cur:
                fri = night_solution.assignments_by_week[w].get("Night Fri", "")
                # After swap, b would hold Weekend NCC1; forbid if b is on Friday night.
                if fri != b:
                    new["Weekend NCC1"], new["Weekend NCC2"] = b, a
        new_weeks.append(new)
    return WeekendScheduleSolution(assignments_by_week=new_weeks)


def write_csv(sol, output_path: Path) -> None:
    """Write the solved schedule as a CSV (fellows | weekend roles | night roles)."""
    fellow_names = list(sol.weekly_assignments.keys())
    num_weeks = len(next(iter(sol.weekly_assignments.values())))
    wk_keys = list(sol.weekend_solution.assignments_by_week[0].keys())
    nk_keys = list(sol.night_solution.assignments_by_week[0].keys())
    with Path(output_path).open("w", newline="", encoding="utf-8") as fh:
        writer = _csv.writer(fh)
        writer.writerow(fellow_names + wk_keys + nk_keys)
        for w in range(num_weeks):
            row = [sol.weekly_assignments[name][w] for name in fellow_names]
            row += [sol.weekend_solution.assignments_by_week[w].get(r, "") for r in wk_keys]
            row += [sol.night_solution.assignments_by_week[w].get(r, "") for r in nk_keys]
            writer.writerow(row)
