from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Protocol

from .semantic_constraints import FellowSelector, SemanticConstraint


class FellowMappingLike(Protocol):
    total_fellows: int
    all_fellow_indices: list[int]

    def get_fellow(self, name: str): ...
    def get_fellow_index(self, name: str) -> int: ...
    def get_fellow_indices_by_groups(self, *groups: str) -> list[int]: ...


RuleHandler = Callable[
    [object, object, "RuleApplicationContext", SemanticConstraint, list[int]],
    None,
]


@dataclass(frozen=True)
class RuleApplicationContext:
    fellow_mapping: FellowMappingLike
    shifts: list[str]
    week_count: int


def apply_constraints(
    optimizer,
    variables,
    constraints: Iterable[SemanticConstraint],
    context: RuleApplicationContext,
    handlers: dict[str, RuleHandler],
) -> None:
    for constraint in constraints:
        handler = handlers.get(constraint.kind)
        if handler is None:
            raise ValueError(f"Unsupported rule kind: {constraint.kind}")
        handler(
            optimizer,
            variables,
            context,
            constraint,
            _resolve_fellow_indices(context.fellow_mapping, constraint.fellows),
        )


def _resolve_fellow_indices(
    fellow_mapping: FellowMappingLike,
    selector: FellowSelector | None,
) -> list[int]:
    if selector is None:
        return list(fellow_mapping.all_fellow_indices)
    if selector.groups:
        return fellow_mapping.get_fellow_indices_by_groups(*selector.groups)
    return [
        fellow_mapping.get_fellow_index(name)
        for name in selector.names
        if fellow_mapping.get_fellow(name) is not None
    ]
