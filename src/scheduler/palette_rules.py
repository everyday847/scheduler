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


def _parse_strength(value: str) -> ConstraintStrength:
    try:
        return ConstraintStrength(value)
    except ValueError as exc:
        raise ValueError(f"Unknown constraint strength: {value!r}") from exc


_CONVERTERS: dict[str, Any] = {
    "shift_total": _convert_shift_total,
}
