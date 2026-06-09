"""validate(schedule) — evaluate a concrete Schedule against the rules.

The evaluate-side mirror of the encoder: given any Schedule (solver output, an
Imported fellow's frozen weeks, a hand-edited or externally-supplied schedule)
and the active constraints, report which rules it violates. A rule's Strength
decides what a violation MEANS — HARD ⇒ the Schedule is invalid; SOFT ⇒ it adds
to a penalty total (CONTEXT.md: Constraint and Criterion are one object
differentiated by Strength).

This slice implements evaluate() for the archetypes a bad import/hand-edit
realistically breaks. A kind with no evaluator yet is reported in
`unevaluated_kinds` — never silently skipped (contrast the encoder's stderr-warn
+ continue). Adding an archetype here = one entry in _EVALUATORS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol


# --- result types ----------------------------------------------------------
@dataclass(frozen=True)
class Violation:
    kind: str
    detail: str
    week: int | None = None
    weight: int = 0


@dataclass
class ValidationResult:
    hard_violations: list[Violation] = field(default_factory=list)
    soft_violations: list[Violation] = field(default_factory=list)
    unevaluated_kinds: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.hard_violations

    @property
    def soft_penalty(self) -> int:
        return sum(v.weight for v in self.soft_violations)


# --- the view a rule's evaluate reads --------------------------------------
class ValidationView(Protocol):
    @property
    def num_weeks(self) -> int: ...
    def weekday_service(self, week: int, fellow: str) -> str: ...
    def all_services(self, week: int, fellow: str) -> list[str]: ...


# --- selector resolution ----------------------------------------------------
def _resolve_fellows(selector, fellow_groups: dict[str, list[str]]) -> list[str]:
    """Resolve a FellowSelector to fellow names. None ⇒ everyone."""
    if selector is None:
        seen: list[str] = []
        for members in fellow_groups.values():
            for name in members:
                if name not in seen:
                    seen.append(name)
        return seen
    if getattr(selector, "groups", ()):
        out: list[str] = []
        for g in selector.groups:
            for name in fellow_groups.get(g, []):
                if name not in out:
                    out.append(name)
        return out
    return list(selector.names)


def _window(constraint, num_weeks: int) -> range:
    if constraint.weeks is not None:
        return range(constraint.weeks.start, min(constraint.weeks.end, num_weeks))
    return range(num_weeks)


def _weight(constraint, default: int = 1) -> int:
    return int(constraint.params.get("weight", default))


def _is_hard(constraint) -> bool:
    # ConstraintStrength.HARD has value "hard"; treat MINIMIZE/SOFT as soft.
    return getattr(constraint.strength, "value", constraint.strength) == "hard"


# --- archetype evaluators ---------------------------------------------------
def _eval_full_assignment(view, constraint, fellows) -> list[Violation]:
    out = []
    for w in _window(constraint, view.num_weeks):
        for f in fellows:
            n = len(view.all_services(w, f))
            if n != 1:
                what = "double-booked" if n > 1 else "unassigned"
                out.append(Violation("full_assignment", f"{f} {what} (week {w}: {n} shifts)", week=w))
    return out


def _eval_specific_assignment(view, constraint, fellows) -> list[Violation]:
    target = constraint.shifts.shifts[0] if constraint.shifts else None
    out = []
    for w in _window(constraint, view.num_weeks):
        for f in fellows:
            if target not in view.all_services(w, f):
                out.append(Violation("specific_assignment",
                                     f"{f} not on {target} in week {w}", week=w))
    return out


def _eval_zero_shifts(view, constraint, fellows) -> list[Violation]:
    forbidden = set(constraint.shifts.shifts) if constraint.shifts else set()
    out = []
    for w in _window(constraint, view.num_weeks):
        for f in fellows:
            for s in view.all_services(w, f):
                if s in forbidden:
                    out.append(Violation("zero_shifts",
                                         f"{f} on forbidden {s} in week {w}", week=w))
    return out


def _eval_windowed_count_band(view, constraint, fellows) -> list[Violation]:
    relation = constraint.params["relation"]
    count = constraint.params["count"]
    target = set(constraint.shifts.shifts) if constraint.shifts else set()
    out = []
    for f in fellows:
        total = sum(
            1 for w in _window(constraint, view.num_weeks)
            for s in view.all_services(w, f) if s in target
        )
        if _relation_violated(relation, total, count):
            out.append(Violation(constraint.kind,
                                 f"{f}: {total} of {sorted(target)} "
                                 f"(needs {relation} {count})"))
    return out


def _relation_violated(relation: str, actual: int, bound: int) -> bool:
    if relation == "at_least":
        return actual < bound
    if relation == "at_most":
        return actual > bound
    if relation == "exactly":
        return actual != bound
    raise ValueError(f"Unknown relation: {relation}")


# kind -> evaluator; multiple kinds may share one archetype evaluator.
_EVALUATORS: dict[str, Callable] = {
    "full_assignment": _eval_full_assignment,
    "specific_assignment": _eval_specific_assignment,
    "zero_shifts": _eval_zero_shifts,
    "shift_total": _eval_windowed_count_band,
    "staffing_per_week": _eval_windowed_count_band,
}


# --- the driver -------------------------------------------------------------
def validate(view, constraints, fellow_groups: dict[str, list[str]]) -> ValidationResult:
    result = ValidationResult()
    for constraint in constraints:
        evaluator = _EVALUATORS.get(constraint.kind)
        if evaluator is None:
            if constraint.kind not in result.unevaluated_kinds:
                result.unevaluated_kinds.append(constraint.kind)
            continue
        fellows = _resolve_fellows(constraint.fellows, fellow_groups)
        violations = evaluator(view, constraint, fellows)
        if _is_hard(constraint):
            result.hard_violations.extend(violations)
        else:
            w = _weight(constraint)
            result.soft_violations.extend(
                Violation(v.kind, v.detail, v.week, weight=w) for v in violations
            )
    return result
