from __future__ import annotations

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
    "fellow",
    "fellow_groups",
    "fellows",
    "kind",
    "name",
    "shift",
    "shifts",
    "strength",
    "week",
}


def constraints_from_config(
    config: dict | None,
    *,
    fellow_week_pairs: dict[str, list[int]] | None = None,
) -> list[SemanticConstraint]:
    if not config:
        return []

    constraints: list[SemanticConstraint] = []
    for rule in config.get("rules", []):
        if not rule.get("active", True):
            continue
        if rule["kind"] == "vacation_request_policy":
            constraints.extend(vacation_request_constraints(
                fellow_week_pairs or {},
                hard_request_count=rule.get("hard_request_count", 3),
            ))
            continue
        constraints.append(_generic_rule(rule))
    return constraints


def vacation_request_constraints(
    fellow_week_pairs: dict[str, list[int]],
    hard_request_count: int,
) -> list[SemanticConstraint]:
    constraints: list[SemanticConstraint] = []
    for fellow_name, weeks in fellow_week_pairs.items():
        for index, week in enumerate(weeks):
            is_hard = index < hard_request_count
            constraints.append(
                named_assignment(
                    fellow_name,
                    week=week,
                    shift="Vac",
                    hard=is_hard,
                    params={"request_rank": index + 1},
                )
            )
    return constraints


def named_assignment(
    fellow_name: str,
    *,
    week: int,
    shift: str,
    hard: bool = True,
    params: dict | None = None,
) -> SemanticConstraint:
    return SemanticConstraint(
        kind="specific_assignment",
        lifecycle=ConstraintLifecycle.ANNUAL_RULE,
        strength=ConstraintStrength.HARD if hard else ConstraintStrength.SOFT,
        fellows=FellowSelector.by_names(fellow_name),
        weeks=WeekSpan(week, week + 1),
        shifts=ShiftSet.single(shift),
        params=params or {},
    )


def _generic_rule(rule: dict) -> SemanticConstraint:
    name = rule["name"]
    shift_values = _shifts(rule)
    return SemanticConstraint(
        kind=rule["kind"],
        lifecycle=ConstraintLifecycle.ANNUAL_RULE,
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


def _strength(value: str) -> ConstraintStrength:
    try:
        return ConstraintStrength(value)
    except ValueError as exc:
        raise ValueError(f"Unknown constraint strength: {value}") from exc


def _fellow_selector(rule: dict) -> FellowSelector | None:
    if "fellow_groups" in rule:
        return FellowSelector.by_groups(*rule["fellow_groups"])
    if "fellows" in rule:
        return FellowSelector.by_names(*rule["fellows"])
    if "fellow" in rule:
        return FellowSelector.by_names(rule["fellow"])
    return None


def _shifts(rule: dict) -> list[str]:
    if "shifts" in rule:
        return rule["shifts"]
    if "shift" in rule:
        return [rule["shift"]]
    return []


def _week_span(rule: dict) -> WeekSpan | None:
    if "week" in rule:
        return WeekSpan(rule["week"], rule["week"] + 1)
    if isinstance(rule.get("weeks"), (list, tuple)):
        start, end = rule["weeks"]
        return WeekSpan(start, end)
    return None
