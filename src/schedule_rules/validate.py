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
    def night_holder(self, day: int) -> str | None: ...


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
# Each delegates to the co-located weekly Rule's evaluate() (ADR-0005), wrapping
# the Rule's domain-level result in Violation records for the driver.
def _eval_full_assignment(view, constraint, fellows) -> list[Violation]:
    from schedule_rules.weekly.full_assignment import FullAssignmentCriterion
    cells = FullAssignmentCriterion().evaluate(
        view, fellows=fellows, window=_window(constraint, view.num_weeks))
    out = []
    for (f, w, n) in cells:
        what = "double-booked" if n > 1 else "unassigned"
        out.append(Violation("full_assignment",
                             f"{f} {what} (week {w}: {n} shifts)", week=w))
    return out


def _eval_specific_assignment(view, constraint, fellows) -> list[Violation]:
    from schedule_rules.weekly.specific_assignment import SpecificAssignmentCriterion
    target = constraint.shifts.shifts[0] if constraint.shifts else None
    pairs = SpecificAssignmentCriterion().evaluate(
        view, fellows=fellows, target=target,
        window=_window(constraint, view.num_weeks))
    return [Violation("specific_assignment", f"{f} not on {target} in week {w}", week=w)
            for (f, w) in pairs]


def _eval_zero_shifts(view, constraint, fellows) -> list[Violation]:
    from schedule_rules.weekly.zero_shifts import ZeroShiftsCriterion
    forbidden = set(constraint.shifts.shifts) if constraint.shifts else set()
    hits = ZeroShiftsCriterion().evaluate(
        view, fellows=fellows, forbidden=forbidden,
        window=_window(constraint, view.num_weeks))
    return [Violation("zero_shifts", f"{f} on forbidden {s} in week {w}", week=w)
            for (f, w, s) in hits]


def _eval_windowed_count_band(view, constraint, fellows) -> list[Violation]:
    from schedule_rules.weekly.windowed_count_band import WindowedCountBandCriterion
    relation = constraint.params["relation"]
    count = constraint.params["count"]
    target = set(constraint.shifts.shifts) if constraint.shifts else set()
    # The evaluate read is per-fellow over the window — shared by shift_total and
    # staffing_per_week (both flag a fellow whose own count breaks the relation).
    groups = [(f, [f]) for f in fellows]
    violated = WindowedCountBandCriterion().evaluate(
        view, groups=groups, shifts=target,
        window=_window(constraint, view.num_weeks),
        relation=relation, count=count)
    return [Violation(constraint.kind,
                     f"{label}: {total} of {sorted(target)} "
                     f"(needs {relation} {count})")
            for (label, total) in violated]


def _eval_group_count_balance(view, constraint, fellows) -> list[Violation]:
    from schedule_rules.criteria.group_count_balance import GroupCountBalanceCriterion
    shifts = constraint.shifts.shifts if constraint.shifts else ()
    window = ((constraint.weeks.start, constraint.weeks.end)
              if constraint.weeks else (0, view.num_weeks))
    max_diff = constraint.params["max_difference"]
    pairs = GroupCountBalanceCriterion().evaluate(
        view, fellows=fellows, shifts=shifts, window=window, max_difference=max_diff)
    return [Violation(constraint.kind,
                     f"{a} vs {b}: on-service counts differ by more than {max_diff}")
            for (a, b) in pairs]


# Canonical weekend role names + short-token -> full-name resolution, kept local
# to the leaf so validate carries no model-package dependency (the encoder owns
# the wr-index helpers; here evaluate only needs the full role NAME the view
# wants).
_WEEKEND_ROLE_NAMES = ("Weekend NCC1", "Weekend NCC2", "Weekend Stroke")


def _eval_weekend_role_pin(view, constraint, fellows) -> list[Violation]:
    """Shared evaluator for specific_weekend_assignment (PIN) and blocked_weekend
    (FORBID). Delegates to the co-located WeekendRolePin (ADR-0005)."""
    from schedule_rules.criteria.weekend_role_pin import WeekendRolePin, PIN, FORBID
    weeks = constraint.params.get("weeks", [])
    if constraint.kind == "blocked_weekend":
        action, role_name = FORBID, None
    else:
        action = PIN
        short = constraint.params.get("role", "")
        full = "Weekend " + short.removeprefix("Weekend ")
        role_name = full if full in _WEEKEND_ROLE_NAMES else None
    out: list[Violation] = []
    for f in fellows:
        flagged = WeekendRolePin().evaluate(
            view, fellow=f, weeks=weeks, role_name=role_name,
            all_role_names=_WEEKEND_ROLE_NAMES, action=action)
        out.extend(Violation(constraint.kind, detail, week=w) for (w, detail) in flagged)
    return out


# kind -> evaluator; multiple kinds may share one archetype evaluator.
_EVALUATORS: dict[str, Callable] = {
    "full_assignment": _eval_full_assignment,
    "specific_assignment": _eval_specific_assignment,
    "zero_shifts": _eval_zero_shifts,
    "shift_total": _eval_windowed_count_band,
    "staffing_per_week": _eval_windowed_count_band,
    "group_count_balance": _eval_group_count_balance,
    "specific_weekend_assignment": _eval_weekend_role_pin,
    "blocked_weekend": _eval_weekend_role_pin,
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
