from __future__ import annotations

from typing import Any

from .semantic_constraints import (
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
    SemanticConstraint,
    ShiftSet,
    WeekSpan,
)


_STANDARD_RULE_KEYS = {
    "active",
    "fellow_groups",
    "fellows",
    "kind",
    "name",
    "shift",
    "shifts",
    "strength",
    "week",
    "weeks",
}


def constraints_from_config(config: dict[str, Any]) -> list[SemanticConstraint]:
    constraints: list[SemanticConstraint] = []

    for rule in config.get("rules", []):
        if rule.get("active", True):
            constraints.append(_generic_rule(rule))

    for rule in config.get("block_rules", []):
        constraints.append(_block_rule(rule))

    for rule in config.get("max_consecutive", []):
        constraints.append(_max_consecutive_rule(rule))

    if "swing_deficit" in config:
        constraints.append(_swing_deficit_rule(config["swing_deficit"]))

    return constraints


def _generic_rule(rule: dict[str, Any]) -> SemanticConstraint:
    kind = rule["kind"]
    name = rule["name"]
    shift_values = _shifts(rule)
    return SemanticConstraint(
        kind=kind,
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=_strength(rule.get("strength", "hard")),
        fellows=_fellow_selector(rule),
        weeks=_week_span(rule),
        shifts=ShiftSet(name, tuple(shift_values)) if shift_values else None,
        params={
            "name": name,
            **{
                key: value
                for key, value in rule.items()
                if key not in _STANDARD_RULE_KEYS
            },
        },
    )


def _block_rule(rule: dict[str, Any]) -> SemanticConstraint:
    return SemanticConstraint(
        kind="all_or_none_block",
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=_strength(rule.get("strength", "hard")),
        fellows=FellowSelector.by_groups(*rule["fellow_groups"]),
        shifts=ShiftSet(rule["name"], tuple(rule["shifts"])),
        params={
            "block_size": rule["block_size"],
            "name": rule["name"],
        },
    )


def _max_consecutive_rule(rule: dict[str, Any]) -> SemanticConstraint:
    return SemanticConstraint(
        kind="max_consecutive",
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=_strength(rule.get("strength", "hard")),
        fellows=FellowSelector.by_groups(*rule["fellow_groups"]),
        shifts=ShiftSet(rule["name"], tuple(rule["shifts"])),
        params={
            "weeks": rule["weeks"],
            "name": rule["name"],
        },
    )


def _swing_deficit_rule(rule: dict[str, Any]) -> SemanticConstraint:
    shift = rule.get("shift", "Swing")
    return SemanticConstraint(
        kind="minimize_uncovered_shift_weeks",
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=ConstraintStrength.MINIMIZE,
        fellows=FellowSelector.by_groups(*rule["fellow_groups"]),
        shifts=ShiftSet.single(shift),
        params={"shift": shift},
    )


def _strength(value: str) -> ConstraintStrength:
    try:
        return ConstraintStrength(value)
    except ValueError as exc:
        raise ValueError(f"Unknown constraint strength: {value}") from exc


def _fellow_selector(rule: dict[str, Any]) -> FellowSelector | None:
    if "fellow_groups" in rule:
        return FellowSelector.by_groups(*rule["fellow_groups"])
    if "fellows" in rule:
        return FellowSelector.by_names(*rule["fellows"])
    return None


def _shifts(rule: dict[str, Any]) -> list[str]:
    if "shifts" in rule:
        return rule["shifts"]
    if "shift" in rule:
        return [rule["shift"]]
    return []


def _week_span(rule: dict[str, Any]) -> WeekSpan | None:
    if "week" in rule:
        return WeekSpan(rule["week"], rule["week"] + 1)
    if "weeks" in rule:
        start, end = rule["weeks"]
        return WeekSpan(start, end)
    return None
