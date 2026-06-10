from __future__ import annotations

from typing import Any

from .semantic_constraints import (
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
    SemanticConstraint,
    ShiftSet,
)


def derive_forbidden_shifts(
    rules: list[dict[str, Any]],
    all_shifts: list[str],
    fellow_groups: dict[str, list[str]],
    call_rules: list[dict[str, Any]] | None = None,
) -> list[SemanticConstraint]:
    """Compute forbidden shift constraints from shift_total rules.

    For each group that has at least one shift_total rule, any shift not
    mentioned in any of that group's shift_total rules is forbidden (hard zero).

    Groups with NO shift_total rules get no forbidden shifts (they're not
    "owned" groups — e.g., rotating fellows whose schedule isn't fully
    managed by this system).
    """
    fellow_to_group: dict[str, str] = {}
    for group, fellows in fellow_groups.items():
        for f in fellows:
            fellow_to_group[f] = group

    # Collect mentioned shifts per group. Two passes so the result is
    # order-independent: first seed every group named by a group-scoped
    # shift_total, THEN fold single-fellow budgets into already-seeded groups
    # (mirroring the legacy two-loop structure — group rules, then per-fellow).
    mentioned: dict[str, set[str]] = {}
    # Pass 1: group-scoped shift_total rules seed each group's budget.
    for rule in rules:
        if rule.get("type") != "shift_total" or not rule.get("active", True):
            continue
        shifts = rule.get("shifts", [])
        for group in rule.get("groups", []):
            mentioned.setdefault(group, set()).update(shifts)
        for group in rule.get("fellow_groups", []):
            mentioned.setdefault(group, set()).update(shifts)
    # Pass 2: single-fellow budgets. After the per_fellow_shift_total cutover a
    # single-fellow rule lives in `rules` carrying a `fellow:`/`fellows:`
    # selector; it keeps its original YAML type string `per_fellow_shift_total`
    # (the solver bridge maps it to kind shift_total for the encoder). Fold its
    # shifts into the owning group's budget — additively, only for already-seeded
    # ("owned") groups, exactly as the legacy call_rules path did below.
    for rule in rules:
        if rule.get("type") not in ("shift_total", "per_fellow_shift_total"):
            continue
        if not rule.get("active", True):
            continue
        shifts = rule.get("shifts", [])
        single_fellows = list(rule.get("fellows", []))
        if rule.get("fellow"):
            single_fellows.append(rule["fellow"])
        for fellow_name in single_fellows:
            group = fellow_to_group.get(fellow_name)
            if group and group in mentioned:
                mentioned[group].update(shifts)

    # Also include shifts from per_fellow_shift_total call_rules
    for rule in (call_rules or []):
        if rule.get("type") != "per_fellow_shift_total":
            continue
        if not rule.get("active", True):
            continue
        fellow_name = rule.get("fellow", "")
        group = fellow_to_group.get(fellow_name)
        if group and group in mentioned:
            mentioned[group].update(rule.get("shifts", []))

    all_shifts_set = set(all_shifts)
    constraints: list[SemanticConstraint] = []

    for group, budgeted_shifts in sorted(mentioned.items()):
        if group not in fellow_groups:
            continue
        forbidden = sorted(all_shifts_set - budgeted_shifts)
        if not forbidden:
            continue
        constraints.append(SemanticConstraint(
            kind="zero_shifts",
            lifecycle=ConstraintLifecycle.ANNUAL_RULE,
            strength=ConstraintStrength.HARD,
            fellows=FellowSelector.by_groups(group),
            shifts=ShiftSet(f"{group}_forbidden", tuple(forbidden)),
            params={"name": f"{group} Forbidden Shifts", "zero_shifts": forbidden},
        ))

    return constraints
