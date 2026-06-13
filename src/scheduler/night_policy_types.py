from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .call_schedule_common import (
    NIGHT_ROLES,
    ParsedCallScheduleCsv,
    WEEKEND_ROLES,
    parse_call_schedule_csv,
)
from .night_call_types import (
    NightScheduleSolution,
    NightSolverConfig,
    summarize_night_solution,
)
from .schedule_view import ParsedScheduleView


_DEFAULT_REGISTRIES = None


def _default_gating_registries():
    """Build the (night_gating, weekend_gating, weekend_night) registries from the
    on-disk standing config — the lazy default for evaluator callers that don't
    thread an explicit registry (the violation counter, focused tests). Cached:
    the standing gating rules don't change within a process. Built through the
    SAME converters the solver config uses, so the default evaluator agrees with
    the encoder. Imported lazily to avoid a circular import (solver_bridge imports
    this module)."""
    global _DEFAULT_REGISTRIES
    if _DEFAULT_REGISTRIES is not None:
        return _DEFAULT_REGISTRIES
    import yaml
    from pathlib import Path
    from .palette_rules import (
        night_gating_rule_to_constraint, weekend_gating_rule_to_constraint,
        weekend_night_rule_to_constraint)
    from .shift_palette import ShiftPalette
    from schedule_rules.criteria.night_gating import configured_night_gating
    from schedule_rules.criteria.weekend_gating import configured_weekend_gating
    from schedule_rules.criteria.weekend_night import configured_weekend_night

    standing_path = (Path(__file__).resolve().parents[2]
                     / "config" / "standing" / "stanford-fellowship-v3.yaml")
    standing = yaml.safe_load(standing_path.read_text())
    palette = ShiftPalette.from_config(standing.get("shift_palette"))
    cons = []
    # night_gating + weekend_night both live in the night_rules block.
    for r in standing.get("night_rules", []):
        if r.get("type") == "night_gating":
            c = night_gating_rule_to_constraint(r, palette)
        elif r.get("type") == "weekend_night":
            c = weekend_night_rule_to_constraint(r, palette)
        else:
            c = None
        if c is not None:
            cons.append(c)
    for r in standing.get("weekend_rules", []):
        if r.get("type") == "weekend_gating":
            c = weekend_gating_rule_to_constraint(r, palette)
            if c is not None:
                cons.append(c)
    _DEFAULT_REGISTRIES = (
        configured_night_gating(cons), configured_weekend_gating(cons),
        configured_weekend_night(cons))
    return _DEFAULT_REGISTRIES


def _night_gating_registry(night_gating):
    return night_gating if night_gating is not None else _default_gating_registries()[0]


def _weekend_gating_registry(weekend_gating):
    return weekend_gating if weekend_gating is not None else _default_gating_registries()[1]


def _weekend_night_registry(weekend_night):
    return weekend_night if weekend_night is not None else _default_gating_registries()[2]


CRITERION_ANAESTHESIA = "anaesthesia"
CRITERION_CLINIC = "clinic"
CRITERION_STROKE = "stroke"
CRITERION_FRIDAY_WEEKEND_NCC1 = "friday_weekend_ncc1"
CRITERION_SUNDAY_FOLLOWING = "sunday_following"
# A SPLIT-OUT variant of sunday_following targeting ONLY the Sunday-night-before-Vac
# transition, so it can be hardened (via night_hard_criteria) independently of the
# broader 7-shift sunday_following rule, which stays soft. night_gating strength and
# weight are keyed by criterion NAME, so a distinct rule needs a distinct name here.
CRITERION_SUNDAY_FOLLOWING_VAC = "sunday_following_vac"
ALL_POLICY_CRITERIA = frozenset(
    {
        CRITERION_ANAESTHESIA,
        CRITERION_CLINIC,
        CRITERION_STROKE,
        CRITERION_FRIDAY_WEEKEND_NCC1,
        CRITERION_SUNDAY_FOLLOWING,
        CRITERION_SUNDAY_FOLLOWING_VAC,
    }
)


@dataclass(frozen=True)
class NightPolicyWeights:
    anaesthesia: int = 1
    clinic: int = 1
    stroke: int = 5
    # friday_weekend_ncc1 is hard by default, so this weight only applies when the
    # criterion is toggled soft. It matches the weekend-night Friday linking weight
    # (40) so a soft Friday rule keeps the same cost as the NCC2/Stroke Friday cases.
    friday_weekend_ncc1: int = 40
    sunday_following: int = 1
    # Soft weight for the Vac-only split rule; only applies when it is NOT in
    # night_hard_criteria. Mirrors sunday_following's weight.
    sunday_following_vac: int = 1

    @classmethod
    def from_config(cls, penalty_weights: dict[str, int]) -> NightPolicyWeights:
        return cls(
            anaesthesia=penalty_weights.get("anaesthesia", 1),
            clinic=penalty_weights.get("clinic", 1),
            stroke=penalty_weights.get("stroke", 5),
            friday_weekend_ncc1=penalty_weights.get("friday_weekend_ncc1", 40),
            sunday_following=penalty_weights.get("sunday_following", 1),
            sunday_following_vac=penalty_weights.get("sunday_following_vac", 1),
        )

    def for_criterion(self, criterion: str) -> int:
        return getattr(self, criterion)


@dataclass(frozen=True)
class NightPolicyCounts:
    by_criterion: dict[str, int]
    weighted_total: int


@dataclass(frozen=True)
class NightPolicySpec:
    name: str
    hard_criteria: frozenset[str]


@dataclass(frozen=True)
class NightPolicySolveResult:
    tier: str
    solution: NightScheduleSolution
    counts: NightPolicyCounts
    hard_criteria: frozenset[str]
    optimized: bool


def staged_policy_specs() -> tuple[NightPolicySpec, ...]:
    return (
        NightPolicySpec("all-soft", frozenset()),
        NightPolicySpec("hard-sunday", frozenset({CRITERION_SUNDAY_FOLLOWING})),
        NightPolicySpec("hard-sunday-anaesthesia", frozenset({CRITERION_SUNDAY_FOLLOWING, CRITERION_ANAESTHESIA})),
        NightPolicySpec(
            "hard-sunday-anaesthesia-friday",
            frozenset({CRITERION_SUNDAY_FOLLOWING, CRITERION_ANAESTHESIA, CRITERION_FRIDAY_WEEKEND_NCC1}),
        ),
        NightPolicySpec(
            "hard-sunday-anaesthesia-friday-stroke",
            frozenset(
                {
                    CRITERION_SUNDAY_FOLLOWING,
                    CRITERION_ANAESTHESIA,
                    CRITERION_FRIDAY_WEEKEND_NCC1,
                    CRITERION_STROKE,
                }
            ),
        ),
    )


def criteria_counts_for_solution(
    parsed: ParsedCallScheduleCsv,
    solution: NightScheduleSolution,
    *,
    config: NightSolverConfig | None = None,
    weights: NightPolicyWeights = NightPolicyWeights(),
) -> NightPolicyCounts:
    counts = {criterion: 0 for criterion in ALL_POLICY_CRITERIA}
    for week_index, week_assignments in enumerate(solution.assignments_by_week):
        for day_of_week, role in enumerate(NIGHT_ROLES):
            fellow_name = week_assignments[role]
            for criterion in _criteria_for_assignment(parsed, week_index, day_of_week, fellow_name, config=config):
                counts[criterion] += 1
    return NightPolicyCounts(
        by_criterion=counts,
        weighted_total=sum(count * weights.for_criterion(criterion) for criterion, count in counts.items()),
    )


def print_policy_summary(parsed: ParsedCallScheduleCsv, result: NightPolicySolveResult, *, config: NightSolverConfig | None = None) -> None:
    print(f"Tier: {result.tier}")
    print(f"Hard criteria: {', '.join(sorted(result.hard_criteria)) or 'none'}")
    print(f"Weighted soft violations: {result.counts.weighted_total}")
    print("Policy criteria:")
    for criterion in sorted(ALL_POLICY_CRITERIA):
        print(f"  {criterion}: {result.counts.by_criterion[criterion]}")
    summary = summarize_night_solution(parsed, result.solution, config=config)
    print("Night summary:")
    for fellow in parsed.fellow_names:
        if summary.total_nights_by_fellow[fellow] or summary.friday_nights_by_fellow[fellow]:
            print(f"  {fellow}: total={summary.total_nights_by_fellow[fellow]} friday={summary.friday_nights_by_fellow[fellow]}")


def parse_night_call_csv(path: str | Path) -> ParsedCallScheduleCsv:
    parsed = parse_call_schedule_csv(path)
    missing = [role for role in WEEKEND_ROLES if role not in parsed.existing_schedule_columns]
    if missing:
        raise ValueError(f"Night solver requires weekend columns: {', '.join(missing)}")
    return parsed


def criteria_for_assignment(
    parsed: ParsedCallScheduleCsv,
    week_index: int,
    day_of_week: int,
    fellow_name: str,
    *,
    config: NightSolverConfig | None = None,
    weekend_solution=None,
    dual_stroke_weeks: frozenset[int] | None = None,
    night_gating=None,
    weekend_night=None,
) -> tuple[str, ...]:
    """The SINGLE source of truth for which night-policy criteria a given
    (week, day-of-week, fellow) night assignment triggers. Both the violation
    counter (criteria_counts_for_solution) and the workbook cell-colorer call
    this — do not re-implement the logic elsewhere.

    night_gating:
        The configured NightGatingCriterion registry (list of
        ConfiguredNightGating) threaded from the solver config. A criterion
        present here is evaluated from its config-driven archetype; one absent
        falls back to its module singleton (the migration is per-criterion).

    weekend_solution:
        The SOLVED weekend assignments. When provided, the Friday/Weekend-NCC1
        and weekend-Stroke checks read it; otherwise they fall back to the
        imported ``schedule_assignments`` on the parsed row.
    dual_stroke_weeks:
        Weeks with two Stroke fellows on service — the Stroke criterion is
        exempt there (the second fellow covers the night). Empty/None = no
        exemption.
    """
    if not fellow_name:
        return ()
    dual = dual_stroke_weeks or frozenset()
    # Night criteria are the config-driven NightGatingCriterion archetype: this
    # evaluator and the solver encoder build their instances from the SAME
    # SemanticConstraint params (one resolved-target source per criterion), so
    # they cannot drift (ADR-0005). The threaded *night_gating* registry IS the
    # set of active criteria; iterate it directly.
    view = ParsedScheduleView(parsed, weekend_solution=weekend_solution)
    criteria = []
    for cng in _night_gating_registry(night_gating):
        if cng.criterion.evaluate(
            view, week_index, day_of_week, fellow_name,
            resolved_targets=cng.resolved_targets, exempt_weeks=dual,
        ):
            criteria.append(cng.criterion.name)
    # Weekend-night ↔ weekend-role criteria (the WeekendNightCriterion archetype):
    # the fellow holds THIS night (week, dow); each criterion judges it against the
    # fellow's weekend roles that week.
    for cwn in _weekend_night_registry(weekend_night):
        if cwn.criterion.evaluate(view, week_index, day_of_week, fellow_name):
            criteria.append(cwn.criterion.name)
    return tuple(criteria)


# Backwards-compatible alias for existing callers/tests.
_criteria_for_assignment = criteria_for_assignment


CRITERION_WEEKEND_ROLE_MISMATCH = "weekend_role_mismatch"
CRITERION_PREVACATION_WEEKEND = "prevacation_weekend"
ALL_WEEKEND_CRITERIA = frozenset(
    {CRITERION_WEEKEND_ROLE_MISMATCH, CRITERION_PREVACATION_WEEKEND}
)


def weekend_criteria_for_role(
    parsed: ParsedCallScheduleCsv,
    week_index: int,
    role: str,
    fellow_name: str,
    *,
    weekend_solution=None,
    weekend_gating=None,
) -> tuple[str, ...]:
    """Which cell-localizable weekend criteria a given (week, role, fellow)
    weekend assignment triggers. Both criteria are delegated to their single
    co-located definitions (ADR-0005) — the same objects the encoder uses — so
    the workbook display cannot drift from the solver's penalties.

    These are the weekend faults where the weekend cell itself is at fault
    (role/weekday mismatch; weekend call before a vacation). Aggregate/spacing
    weekend penalties (consecutive-weekend, weekend-total band) have no single
    guilty cell and are intentionally not evaluated here.
    """
    if not fellow_name:
        return ()
    view = ParsedScheduleView(parsed, weekend_solution=weekend_solution)
    out = []
    for cwg in _weekend_gating_registry(weekend_gating):
        if cwg.criterion.evaluate(
            view, week_index, role, fellow_name,
            resolved_targets=cwg.resolved_targets,
        ):
            out.append(cwg.criterion.name)
    return tuple(out)


def _validate_hard_criteria(hard_criteria: set[str] | frozenset[str]) -> frozenset[str]:
    invalid = set(hard_criteria) - ALL_POLICY_CRITERIA
    if invalid:
        raise ValueError(f"Unknown policy criteria: {', '.join(sorted(invalid))}")
    return frozenset(hard_criteria)


def _staged_output_path(output_prefix: Path, spec_name: str, optimize: bool) -> Path:
    suffix = "optimized" if optimize else "unoptimized"
    if output_prefix.suffix:
        return output_prefix.with_name(f"{output_prefix.stem}.{spec_name}.{suffix}{output_prefix.suffix}")
    return output_prefix.parent / f"{output_prefix.name}.{spec_name}.{suffix}.csv"


def _format_hard_criteria(hard_criteria: frozenset[str]) -> str:
    return ", ".join(sorted(hard_criteria)) if hard_criteria else "none"
