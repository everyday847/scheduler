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
    constraints = converter(rule, lifecycle)
    # Generic passthrough of the relax opt-in flag: a per-fellow rule that sets
    # `applies_under_relaxed_locks: true` keeps binding locked fellows when relaxed
    # locking floats their trio weeks (honored by the encoder's per-fellow skip).
    # Injected here so every converter need not thread it.
    if rule.get("applies_under_relaxed_locks"):
        for c in constraints:
            c.params["applies_under_relaxed_locks"] = True
    return constraints


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


def night_gating_rule_to_constraint(
    rule: dict[str, Any],
    palette,
    *,
    lifecycle: ConstraintLifecycle = ConstraintLifecycle.STANDING_RULE,
) -> SemanticConstraint | None:
    """Convert a `night_gating` rule into a typed SemanticConstraint, resolving
    each term's target to a concrete shift-name set HERE (config-assembly time,
    where the ShiftPalette is available) so the schedule_rules leaf stays
    palette-free and encode + evaluate consume one resolved source.

    A term names its target one of three (mutually exclusive) ways:
      target_attribute: <flag>  → resolved via palette.shifts_with_attribute(flag)
      target_shifts: [<shift>]  → a rule-level explicit set (stored verbatim)
      target_role: "<role>"     → a Weekend Role name (is_weekend_role, no set)
    """
    if not rule.get("active", True):
        return None

    terms: list[dict[str, Any]] = []
    resolved_targets: dict[str, list[str]] = {}
    for term in rule["terms"]:
        dows = list(term["dows"])
        week_offset = int(term.get("week_offset", 0))
        if "target_role" in term:
            terms.append({"dows": dows, "week_offset": week_offset,
                          "target_key": term["target_role"], "is_weekend_role": True})
            continue
        if "target_attribute" in term:
            attr = term["target_attribute"]
            key = attr
            resolved_targets[key] = sorted(palette.shifts_with_attribute(attr))
        else:
            shifts = list(term["target_shifts"])
            key = term.get("target_key") or f"{rule['criterion']}_targets"
            resolved_targets[key] = shifts
        terms.append({"dows": dows, "week_offset": week_offset,
                      "target_key": key, "is_weekend_role": False})

    params: dict[str, Any] = {
        "name": rule.get("name", rule["criterion"]),
        "criterion": rule["criterion"],
        "terms": terms,
        "resolved_targets": resolved_targets,
    }
    ex = rule.get("exemption")
    if ex is not None:
        if "target_attribute" in ex:
            shifts = sorted(palette.shifts_with_attribute(ex["target_attribute"]))
            target_shift = shifts[0] if shifts else None
        else:
            target_shift = ex["target_shift"]
        params["exemption"] = {"target_shift": target_shift,
                               "threshold": int(ex["threshold"])}

    return SemanticConstraint(
        kind="night_gating",
        lifecycle=lifecycle,
        strength=ConstraintStrength.SOFT,  # per-criterion hard/soft resolved at
        # encode time from night_hard_criteria (keyed by `criterion`); this field
        # is unused by the night_gating handler.
        fellows=None,
        params=params,
    )


def weekend_gating_rule_to_constraint(
    rule: dict[str, Any],
    palette,
    *,
    lifecycle: ConstraintLifecycle = ConstraintLifecycle.STANDING_RULE,
) -> SemanticConstraint | None:
    """Convert a `weekend_gating` rule into a typed SemanticConstraint.

    ALIGN mode carries a role_to_shift map (role ↔ required same-week weekday
    shift). GATE mode carries the armed roles, a gating-service target (resolved
    here from target_attribute or target_shifts), and a week offset.
    """
    if not rule.get("active", True):
        return None

    mode = rule["mode"]
    params: dict[str, Any] = {
        "name": rule.get("name", rule["criterion"]),
        "criterion": rule["criterion"],
        "mode": mode,
    }
    if mode == "align":
        params["role_to_shift"] = dict(rule["role_to_shift"])
        # Per-role mismatch weight (default per role -> weekend_mismatch_weight at
        # encode time). Lets Weekend Stroke carry a heavier pull than NCC matching
        # — the folded-in former stroke_weekend_misalign penalty.
        params["role_weights"] = dict(rule.get("role_weights", {}))
        # Conditional hard (align only): when hard, bind a fellow who CAN match the
        # weekday shift but do NOT forbid the role to one who cannot (else weekend
        # coverage starves). Ignored when soft.
        params["conditional"] = bool(rule.get("conditional", False))
    else:  # gate
        params["roles"] = list(rule["roles"])
        params["gate_week_offset"] = int(rule.get("gate_week_offset", 0))
        if "target_attribute" in rule:
            key = rule["target_attribute"]
            resolved = sorted(palette.shifts_with_attribute(rule["target_attribute"]))
        else:
            key = rule.get("gate_target_key") or f"{rule['criterion']}_targets"
            resolved = list(rule["target_shifts"])
        params["gate_target_key"] = key
        params["resolved_targets"] = {key: resolved}

    # Default soft (the historical behavior for both weekend_gating criteria); a
    # rule may opt into hard via `strength: hard` (e.g. conditional alignment).
    strength = _parse_strength(rule["strength"]) if "strength" in rule else ConstraintStrength.SOFT
    return SemanticConstraint(
        kind="weekend_gating",
        lifecycle=lifecycle,
        strength=strength,
        fellows=None,
        params=params,
    )


def weekend_night_rule_to_constraint(
    rule: dict[str, Any],
    palette,
    *,
    lifecycle: ConstraintLifecycle = ConstraintLifecycle.STANDING_RULE,
) -> SemanticConstraint | None:
    """Convert a `weekend_night` rule (the WeekendNightCriterion archetype) into a
    typed SemanticConstraint. The role set is intrinsic (weekend-role names), so
    there is no palette resolution — `palette` is accepted only for a uniform
    converter signature. params carries the criterion name, the night dow, the
    polarity, the per-role strengths, and an optional eligibility-forbid.
    """
    if not rule.get("active", True):
        return None

    role_strengths = [
        {"role": rs["role"], "hard": bool(rs.get("hard", False)),
         "weight": int(rs.get("weight", 0))}
        for rs in rule["role_strengths"]
    ]
    params: dict[str, Any] = {
        "name": rule.get("name", rule["criterion"]),
        "criterion": rule["criterion"],
        "dow": int(rule["dow"]),
        "polarity": rule["polarity"],
        "role_strengths": role_strengths,
    }
    el = rule.get("eligibility")
    if el is not None:
        params["eligibility"] = {
            "role": el["role"], "hard": bool(el.get("hard", False)),
            "weight": int(el.get("weight", 0)),
        }

    return SemanticConstraint(
        kind="weekend_night",
        lifecycle=lifecycle,
        strength=ConstraintStrength.SOFT,
        fellows=None,
        params=params,
    )


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
