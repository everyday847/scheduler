"""Evaluate a schedule solution against all constraints.

Reports which constraints are violated, by how much, and the penalty contribution.
Works with either a FullScheduleSolution object or a CSV file.

Usage:
    PYTHONPATH=src python evaluate_schedule.py output_v3.csv
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from scheduler.solver_bridge import build_solver_config_from_request
from scheduler.schedule_import import parse_schedule_file
from scheduler.semantic_constraints import ConstraintStrength, SemanticConstraint
from scheduler.call_schedule_common import NIGHT_ROLES, WEEKEND_ROLES

ANNUAL = Path("config/annual/my-2026-2027-v3.yaml")
STANDING = Path("config/standing/stanford-fellowship-v3.yaml")
WORKBOOK = Path("workbook_partial_input.xlsx")
SHIFT_MAP = {
    'MSICU': 'MICU', 'Anesthesia': 'Anaesthesia', 'Vacation': 'Vac',
    'Elective': 'Elec', 'Elective/SICU': 'SICU', 'Elective/NCS 2026': 'Elec',
    'Elec/APBN': 'Elec', 'NS SCVMC': 'NS',
}


@dataclass
class Violation:
    rule_name: str
    strength: str  # "hard" or "soft"
    kind: str
    detail: str
    penalty: int = 0


def load_solution_from_csv(csv_path: Path) -> dict:
    """Load a solution CSV into {fellow: [shifts], weekend: [{role: name}], night: [{role: name}]}."""
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))

    header = rows[0]
    night_set = set(NIGHT_ROLES)
    weekend_set = set(WEEKEND_ROLES)

    fellow_cols = []
    weekend_cols = []
    night_cols = []
    for i, h in enumerate(header):
        if h in weekend_set:
            weekend_cols.append((i, h))
        elif h in night_set:
            night_cols.append((i, h))
        else:
            fellow_cols.append((i, h))

    weekly = {h: [] for _, h in fellow_cols}
    weekend_by_week = []
    night_by_week = []

    for row in rows[1:]:
        padded = row + [""] * max(0, len(header) - len(row))
        for i, name in fellow_cols:
            weekly[name].append(padded[i])
        weekend_by_week.append({h: padded[i] for i, h in weekend_cols})
        night_by_week.append({h: padded[i] for i, h in night_cols})

    return {
        "weekly": weekly,
        "weekend": weekend_by_week,
        "night": night_by_week,
        "fellow_names": [h for _, h in fellow_cols],
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
    for name in annual["fellow_groups"]["STROKE"] + annual["fellow_groups"]["NH"]:
        if name in annual.get("locked_assignments", {}):
            locked[name] = annual["locked_assignments"][name]
    annual["locked_assignments"] = locked
    return annual


def evaluate(annual: dict, solution: dict) -> list[Violation]:
    """Evaluate solution against all configured constraints."""
    violations = []
    weekly = solution["weekly"]
    fellow_names = solution["fellow_names"]
    num_weeks = len(next(iter(weekly.values())))
    weight = 100  # DEFAULT_WEEKLY_SOFT_WEIGHT

    groups = annual["fellow_groups"]
    group_for_fellow = {}
    for g, fellows in groups.items():
        for f in fellows:
            group_for_fellow[f] = g

    # --- Shift total rules ---
    for rule in annual.get("rules", []):
        if not rule.get("active", True):
            continue
        rtype = rule.get("type")
        name = rule.get("name", rtype)
        strength = rule.get("strength", "hard")
        target_groups = rule.get("groups", [])
        target_fellows = [f for f in fellow_names if group_for_fellow.get(f) in target_groups]

        if rtype == "shift_total":
            shifts = set(rule.get("shifts", []))
            relation = rule.get("relation", "exactly")
            count = rule.get("count", 0)
            window = rule.get("window")
            w_start = window[0] if window else 0
            w_end = window[1] if window else num_weeks

            for f in target_fellows:
                total = sum(1 for w in range(w_start, min(w_end, num_weeks))
                            if weekly[f][w] in shifts)
                ok = (
                    (relation == "exactly" and total == count) or
                    (relation == "at_least" and total >= count) or
                    (relation == "at_most" and total <= count)
                )
                if not ok:
                    violations.append(Violation(
                        rule_name=name, strength=strength, kind="shift_total",
                        detail=f"{f}: {relation} {count} of {sorted(shifts)}, got {total}"
                            + (f" (weeks {w_start}-{w_end})" if window else ""),
                        penalty=weight if strength == "soft" else 0,
                    ))

        elif rtype == "staffing_per_week":
            shifts = set(rule.get("shifts", []))
            relation = rule.get("relation", "at_least")
            count = rule.get("count", 1)

            for w in range(num_weeks):
                total = sum(1 for f in target_fellows if weekly[f][w] in shifts)
                ok = (
                    (relation == "exactly" and total == count) or
                    (relation == "at_least" and total >= count) or
                    (relation == "at_most" and total <= count)
                )
                if not ok:
                    violations.append(Violation(
                        rule_name=name, strength=strength, kind="staffing_per_week",
                        detail=f"week {w}: {relation} {count} of {sorted(shifts)}, got {total}",
                        penalty=weight if strength == "soft" else 0,
                    ))

        elif rtype == "max_consecutive":
            shifts = set(rule.get("shifts", []))
            max_weeks = rule.get("max_weeks", 8)
            for f in target_fellows:
                run = 0
                for w in range(num_weeks):
                    if weekly[f][w] in shifts:
                        run += 1
                        if run > max_weeks:
                            violations.append(Violation(
                                rule_name=name, strength=strength, kind="max_consecutive",
                                detail=f"{f}: {run} consecutive at week {w} (max {max_weeks})",
                                penalty=weight if strength == "soft" else 0,
                            ))
                            break
                    else:
                        run = 0

        elif rtype == "vacation_request_policy":
            hard_count = rule.get("hard_request_count", 0)
            for f, weeks in annual.get("fellow_week_pairs", {}).items():
                if f not in weekly:
                    continue
                for i, w in enumerate(weeks):
                    if w >= num_weeks:
                        continue
                    is_hard = i < hard_count
                    if weekly[f][w] != "Vac":
                        violations.append(Violation(
                            rule_name=f"Vacation request: {f} week {w}",
                            strength="hard" if is_hard else "soft",
                            kind="vacation_request",
                            detail=f"{f} week {w}: expected Vac, got '{weekly[f][w]}'",
                            penalty=weight if not is_hard else 0,
                        ))

    # --- Per-fellow shift totals from call_rules ---
    for rule in annual.get("call_rules", []):
        if not rule.get("active", True):
            continue
        if rule.get("type") != "per_fellow_shift_total":
            continue
        f = rule.get("fellow")
        if f not in weekly:
            continue
        shifts = set(rule.get("shifts", []))
        relation = rule.get("relation", "exactly")
        count = rule.get("count", 0)
        strength = rule.get("strength", "hard")
        total = sum(1 for w in range(num_weeks) if weekly[f][w] in shifts)
        ok = (
            (relation == "exactly" and total == count) or
            (relation == "at_least" and total >= count) or
            (relation == "at_most" and total <= count)
        )
        if not ok:
            violations.append(Violation(
                rule_name=rule.get("name", "per_fellow_shift_total"),
                strength=strength, kind="per_fellow_shift_total",
                detail=f"{f}: {relation} {count} of {sorted(shifts)}, got {total}",
                penalty=weight if strength == "soft" else 0,
            ))

    # --- Full assignment check ---
    standing = yaml.safe_load(STANDING.read_text())
    for rule in standing.get("rules", []):
        if rule.get("type") == "full_assignment" and rule.get("active", True):
            fa_groups = rule.get("groups", [])
            fa_fellows = [f for f in fellow_names if group_for_fellow.get(f) in fa_groups]
            for f in fa_fellows:
                for w in range(num_weeks):
                    if not weekly[f][w]:
                        violations.append(Violation(
                            rule_name="Full Assignment",
                            strength=rule.get("strength", "hard"),
                            kind="full_assignment",
                            detail=f"{f} week {w}: no assignment",
                        ))

    # --- Standing rules (shift_total, staffing_per_week) ---
    for rule in standing.get("rules", []):
        if not rule.get("active", True):
            continue
        rtype = rule.get("type")
        name = rule.get("name", rtype)
        strength = rule.get("strength", "hard")
        target_groups = rule.get("groups", [])
        target_fellows = [f for f in fellow_names if group_for_fellow.get(f) in target_groups]

        if rtype == "shift_total":
            shifts = set(rule.get("shifts", []))
            relation = rule.get("relation", "exactly")
            count = rule.get("count", 0)
            window = rule.get("window")
            w_start = window[0] if window else 0
            w_end = window[1] if window else num_weeks
            for f in target_fellows:
                total = sum(1 for w in range(w_start, min(w_end, num_weeks))
                            if weekly[f][w] in shifts)
                ok = (
                    (relation == "exactly" and total == count) or
                    (relation == "at_least" and total >= count) or
                    (relation == "at_most" and total <= count)
                )
                if not ok:
                    violations.append(Violation(
                        rule_name=name, strength=strength, kind="shift_total",
                        detail=f"{f}: {relation} {count} of {sorted(shifts)}, got {total}"
                            + (f" (weeks {w_start}-{w_end})" if window else ""),
                        penalty=weight if strength == "soft" else 0,
                    ))

        elif rtype == "staffing_per_week":
            shifts = set(rule.get("shifts", []))
            relation = rule.get("relation", "at_least")
            count = rule.get("count", 1)
            for w in range(num_weeks):
                total = sum(1 for f in target_fellows if weekly[f][w] in shifts)
                ok = (
                    (relation == "exactly" and total == count) or
                    (relation == "at_least" and total >= count) or
                    (relation == "at_most" and total <= count)
                )
                if not ok:
                    violations.append(Violation(
                        rule_name=name, strength=strength, kind="staffing_per_week",
                        detail=f"week {w}: {relation} {count} of {sorted(shifts)}, got {total}",
                        penalty=weight if strength == "soft" else 0,
                    ))

    return violations


def print_report(violations: list[Violation]):
    hard = [v for v in violations if v.strength == "hard"]
    soft = [v for v in violations if v.strength == "soft"]

    if hard:
        print(f"\n{'='*60}")
        print(f"HARD VIOLATIONS ({len(hard)}) — these should never happen!")
        print(f"{'='*60}")
        by_rule = defaultdict(list)
        for v in hard:
            by_rule[v.rule_name].append(v)
        for rule, vs in sorted(by_rule.items()):
            print(f"\n  {rule} ({len(vs)} violations):")
            for v in vs[:5]:
                print(f"    {v.detail}")
            if len(vs) > 5:
                print(f"    ... and {len(vs)-5} more")
    else:
        print("\nNo hard violations.")

    if soft:
        print(f"\n{'='*60}")
        print(f"SOFT VIOLATIONS ({len(soft)}, total penalty ~{sum(v.penalty for v in soft)})")
        print(f"{'='*60}")
        by_rule = defaultdict(list)
        for v in soft:
            by_rule[v.rule_name].append(v)
        for rule, vs in sorted(by_rule.items(), key=lambda x: -sum(v.penalty for v in x[1])):
            total_pen = sum(v.penalty for v in vs)
            print(f"\n  {rule} ({len(vs)} violations, penalty={total_pen}):")
            for v in vs[:5]:
                print(f"    {v.detail}")
            if len(vs) > 5:
                print(f"    ... and {len(vs)-5} more")
    else:
        print("\nNo soft violations.")

    print(f"\n{'='*60}")
    print(f"Total: {len(hard)} hard, {len(soft)} soft (penalty ~{sum(v.penalty for v in soft)})")
    print(f"{'='*60}")


def main():
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output_v3.csv")
    if not csv_path.exists():
        print(f"Error: {csv_path} not found")
        return 1

    print(f"Evaluating: {csv_path}")
    annual = load_config()
    solution = load_solution_from_csv(csv_path)
    violations = evaluate(annual, solution)
    print_report(violations)
    return 0


if __name__ == "__main__":
    sys.exit(main())
