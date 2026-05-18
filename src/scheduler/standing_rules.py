from __future__ import annotations

from typing import Any

from .semantic_constraints import (
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
    SemanticConstraint,
    ShiftSet,
)


def constraints_from_config(config: dict[str, Any]) -> list[SemanticConstraint]:
    constraints: list[SemanticConstraint] = []

    for rule in config.get("block_rules", []):
        constraints.append(_block_rule(rule))

    for rule in config.get("max_consecutive", []):
        constraints.append(_max_consecutive_rule(rule))

    if "swing_deficit" in config:
        constraints.append(_swing_deficit_rule(config["swing_deficit"]))

    return constraints


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
