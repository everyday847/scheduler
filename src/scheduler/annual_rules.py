from __future__ import annotations

from .semantic_constraints import (
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
    SemanticConstraint,
    ShiftSet,
    WeekSpan,
)


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
                    shift="Vac" if is_hard else "Elec",
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
