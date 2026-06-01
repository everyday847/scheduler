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
) -> list[SemanticConstraint]:
    """Compute forbidden shift constraints from shift_total rules.

    For each group that has at least one shift_total rule, any shift not
    mentioned in any of that group's shift_total rules is forbidden (hard zero).

    Groups with NO shift_total rules get no forbidden shifts (they're not
    "owned" groups — e.g., rotating fellows whose schedule isn't fully
    managed by this system).
    """
    # Collect mentioned shifts per group from active shift_total rules
    mentioned: dict[str, set[str]] = {}
    for rule in rules:
        if rule.get("type") != "shift_total":
            continue
        if not rule.get("active", True):
            continue
        for group in rule.get("groups", []):
            if group not in mentioned:
                mentioned[group] = set()
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
