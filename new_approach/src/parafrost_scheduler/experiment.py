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

from scheduler.solver_bridge import build_solver_config_from_request
from scheduler.schedule_import import parse_schedule_file

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
