from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConstraintLifecycle(Enum):
    SOLVER_INVARIANT = "solver_invariant"
    STANDING_RULE = "standing_rule"
    ANNUAL_RULE = "annual_rule"


class ConstraintStrength(Enum):
    HARD = "hard"
    SOFT = "soft"
    MINIMIZE = "minimize"


@dataclass(frozen=True)
class FellowSelector:
    names: tuple[str, ...] = ()
    types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if bool(self.names) == bool(self.types):
            raise ValueError("FellowSelector must target exactly one of names or types.")
        _require_non_empty_strings(self.names or self.types, "FellowSelector targets")

    @classmethod
    def by_names(cls, *names: str) -> FellowSelector:
        return cls(names=tuple(names))

    @classmethod
    def by_types(cls, *types: str) -> FellowSelector:
        return cls(types=tuple(types))


@dataclass(frozen=True)
class WeekSpan:
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError("WeekSpan start must be non-negative.")
        if self.end <= self.start:
            raise ValueError("WeekSpan end must be greater than start.")

    def weeks(self) -> range:
        return range(self.start, self.end)

    def __contains__(self, week: int) -> bool:
        return self.start <= week < self.end


@dataclass(frozen=True)
class ShiftSet:
    name: str
    shifts: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ShiftSet name must not be empty.")
        _require_non_empty_strings(self.shifts, "ShiftSet shifts")

    @classmethod
    def single(cls, shift: str) -> ShiftSet:
        return cls(name=shift, shifts=(shift,))


@dataclass(frozen=True)
class SemanticConstraint:
    kind: str
    lifecycle: ConstraintLifecycle
    strength: ConstraintStrength
    fellows: FellowSelector | None = None
    weeks: WeekSpan | None = None
    shifts: ShiftSet | None = None
    params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("SemanticConstraint kind must not be empty.")


def _require_non_empty_strings(values: tuple[str, ...], label: str) -> None:
    if not values:
        raise ValueError(f"{label} must include at least one value.")
    if any(not value for value in values):
        raise ValueError(f"{label} must not include empty values.")
