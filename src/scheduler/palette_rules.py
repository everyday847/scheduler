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


PALETTE_TYPES = frozenset({
    "shift_total",
    "staffing_per_week",
    "coverage_target",
    "max_consecutive",
    "block_rotation",
    "rotation_continuity",
    "prerequisite",
    "windowed_balance",
    "group_count_balance",
})


def palette_rule_to_constraints(
    rule: dict[str, Any],
    *,
    lifecycle: ConstraintLifecycle = ConstraintLifecycle.ANNUAL_RULE,
) -> list[SemanticConstraint]:
    if not rule.get("active", True):
        return []

    rule_type = rule["type"]
    if rule_type not in PALETTE_TYPES:
        raise ValueError(f"Unknown palette rule type: {rule_type!r}")

    converter = _CONVERTERS[rule_type]
    return converter(rule, lifecycle)


def _convert_shift_total(
    rule: dict[str, Any], lifecycle: ConstraintLifecycle
) -> list[SemanticConstraint]:
    name = rule["name"]
    groups = rule["groups"]
    shifts = rule["shifts"]
    relation = rule["relation"]
    count = rule["count"]
    strength = _parse_strength(rule["strength"])
    window = rule.get("window")

    weeks = WeekSpan(window[0], window[1]) if window else None

    return [SemanticConstraint(
        kind="shift_total",
        lifecycle=lifecycle,
        strength=strength,
        fellows=FellowSelector.by_groups(*groups),
        weeks=weeks,
        shifts=ShiftSet(name, tuple(shifts)),
        params={
            "name": name,
            "relation": relation,
            "count": count,
        },
    )]


def _convert_staffing_per_week(
    rule: dict[str, Any], lifecycle: ConstraintLifecycle
) -> list[SemanticConstraint]:
    name = rule["name"]
    strength = _parse_strength(rule["strength"])
    window = rule.get("window")
    weeks = WeekSpan(window[0], window[1]) if window else None

    return [SemanticConstraint(
        kind="staffing_per_week",
        lifecycle=lifecycle,
        strength=strength,
        fellows=FellowSelector.by_groups(*rule["groups"]),
        weeks=weeks,
        shifts=ShiftSet(name, tuple(rule["shifts"])),
        params={
            "name": name,
            "relation": rule["relation"],
            "count": rule["count"],
        },
    )]


def _convert_coverage_target(
    rule: dict[str, Any], lifecycle: ConstraintLifecycle
) -> list[SemanticConstraint]:
    name = rule["name"]
    strength = _parse_strength(rule["strength"])

    return [SemanticConstraint(
        kind="coverage_target",
        lifecycle=lifecycle,
        strength=strength,
        fellows=FellowSelector.by_groups(*rule["groups"]),
        shifts=ShiftSet(name, tuple(rule["shifts"])),
        params={
            "name": name,
            "max_uncovered_weeks": rule["max_uncovered_weeks"],
        },
    )]


def _convert_max_consecutive(
    rule: dict[str, Any], lifecycle: ConstraintLifecycle
) -> list[SemanticConstraint]:
    name = rule["name"]
    strength = _parse_strength(rule["strength"])

    return [SemanticConstraint(
        kind="max_consecutive",
        lifecycle=lifecycle,
        strength=strength,
        fellows=FellowSelector.by_groups(*rule["groups"]),
        shifts=ShiftSet(name, tuple(rule["shifts"])),
        params={
            "name": name,
            "weeks": rule["max_weeks"],
        },
    )]


def _convert_block_rotation(
    rule: dict[str, Any], lifecycle: ConstraintLifecycle
) -> list[SemanticConstraint]:
    name = rule["name"]
    strength = _parse_strength(rule["strength"])

    return [SemanticConstraint(
        kind="all_or_none_block",
        lifecycle=lifecycle,
        strength=strength,
        fellows=FellowSelector.by_groups(*rule["groups"]),
        shifts=ShiftSet(name, tuple(rule["shifts"])),
        params={
            "name": name,
            "block_size": rule["block_size"],
        },
    )]


def _convert_rotation_continuity(
    rule: dict[str, Any], lifecycle: ConstraintLifecycle
) -> list[SemanticConstraint]:
    name = rule["name"]
    strength = _parse_strength(rule["strength"])
    all_shifts = sorted({s for choice in rule["choices"] for s in choice})

    return [SemanticConstraint(
        kind="block_shift_set_choice",
        lifecycle=lifecycle,
        strength=strength,
        fellows=FellowSelector.by_groups(*rule["groups"]),
        shifts=ShiftSet(name, tuple(all_shifts)),
        params={
            "name": name,
            "block_size": rule["block_size"],
            "choices": rule["choices"],
            "allow_none": rule.get("allow_none", False),
        },
    )]


def _convert_prerequisite(
    rule: dict[str, Any], lifecycle: ConstraintLifecycle
) -> list[SemanticConstraint]:
    name = rule["name"]
    strength = _parse_strength(rule["strength"])
    all_shifts = rule["prerequisite_shifts"] + rule["target_shifts"]

    return [SemanticConstraint(
        kind="prerequisite",
        lifecycle=lifecycle,
        strength=strength,
        fellows=FellowSelector.by_groups(*rule["groups"]),
        shifts=ShiftSet(name, tuple(all_shifts)),
        params={
            "name": name,
            "prerequisite_shifts": rule["prerequisite_shifts"],
            "target_shifts": rule["target_shifts"],
            "min_prerequisite_weeks": rule["min_prerequisite_weeks"],
        },
    )]


def _convert_windowed_balance(
    rule: dict[str, Any], lifecycle: ConstraintLifecycle
) -> list[SemanticConstraint]:
    name = rule["name"]
    strength = _parse_strength(rule["strength"])

    return [SemanticConstraint(
        kind="windowed_balance",
        lifecycle=lifecycle,
        strength=strength,
        fellows=FellowSelector.by_groups(*rule["groups"]),
        shifts=ShiftSet(name, tuple(rule["shifts"])),
        params={
            "name": name,
            "window_a": rule["window_a"],
            "window_b": rule["window_b"],
            "max_difference": rule["max_difference"],
        },
    )]


def _convert_group_count_balance(
    rule: dict[str, Any], lifecycle: ConstraintLifecycle
) -> list[SemanticConstraint]:
    name = rule["name"]
    strength = _parse_strength(rule["strength"])
    window = rule.get("window")
    weeks = WeekSpan(window[0], window[1]) if window else None

    return [SemanticConstraint(
        kind="group_count_balance",
        lifecycle=lifecycle,
        strength=strength,
        fellows=FellowSelector.by_groups(*rule["groups"]),
        weeks=weeks,
        shifts=ShiftSet(name, tuple(rule["shifts"])),
        params={
            "name": name,
            "max_difference": rule["max_difference"],
        },
    )]


def _parse_strength(value: str) -> ConstraintStrength:
    try:
        return ConstraintStrength(value)
    except ValueError as exc:
        raise ValueError(f"Unknown constraint strength: {value!r}") from exc


_CONVERTERS: dict[str, Any] = {
    "shift_total": _convert_shift_total,
    "staffing_per_week": _convert_staffing_per_week,
    "coverage_target": _convert_coverage_target,
    "max_consecutive": _convert_max_consecutive,
    "block_rotation": _convert_block_rotation,
    "rotation_continuity": _convert_rotation_continuity,
    "prerequisite": _convert_prerequisite,
    "windowed_balance": _convert_windowed_balance,
    "group_count_balance": _convert_group_count_balance,
}
