from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .call_schedule_common import (
    NIGHT_ROLES,
    ParsedCallScheduleCsv,
    WEEKEND_ROLES,
    is_anaesthesia_service,
    is_clinic_service,
    parse_call_schedule_csv,
)
from .night_call_types import (
    NightScheduleSolution,
    NightSolverConfig,
    is_night_blocked,
    is_night_holiday_eligible,
    is_preferred_sunday_following_service,
    summarize_night_solution,
)


CRITERION_ANAESTHESIA = "anaesthesia"
CRITERION_CLINIC = "clinic"
CRITERION_STROKE = "stroke"
CRITERION_FRIDAY_WEEKEND_NCC1 = "friday_weekend_ncc1"
CRITERION_SUNDAY_FOLLOWING = "sunday_following"
ALL_POLICY_CRITERIA = frozenset(
    {
        CRITERION_ANAESTHESIA,
        CRITERION_CLINIC,
        CRITERION_STROKE,
        CRITERION_FRIDAY_WEEKEND_NCC1,
        CRITERION_SUNDAY_FOLLOWING,
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

    @classmethod
    def from_config(cls, penalty_weights: dict[str, int]) -> NightPolicyWeights:
        return cls(
            anaesthesia=penalty_weights.get("anaesthesia", 1),
            clinic=penalty_weights.get("clinic", 1),
            stroke=penalty_weights.get("stroke", 5),
            friday_weekend_ncc1=penalty_weights.get("friday_weekend_ncc1", 40),
            sunday_following=penalty_weights.get("sunday_following", 1),
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


def _criteria_for_assignment(
    parsed: ParsedCallScheduleCsv,
    week_index: int,
    day_of_week: int,
    fellow_name: str,
    *,
    config: NightSolverConfig | None = None,
) -> tuple[str, ...]:
    week_row = parsed.week_rows[week_index]
    weekday_service = week_row.weekday_assignments[fellow_name]
    criteria = []
    if is_anaesthesia_service(weekday_service):
        criteria.append(CRITERION_ANAESTHESIA)
    if is_clinic_service(weekday_service):
        criteria.append(CRITERION_CLINIC)
    if "Stroke" in weekday_service:
        criteria.append(CRITERION_STROKE)
    if day_of_week == 4 and week_row.schedule_assignments.get("Weekend NCC1") == fellow_name:
        criteria.append(CRITERION_FRIDAY_WEEKEND_NCC1)
    if day_of_week == 6 and week_index + 1 < len(parsed.week_rows):
        following_service = parsed.week_rows[week_index + 1].weekday_assignments[fellow_name]
        if not is_preferred_sunday_following_service(following_service, config=config):
            criteria.append(CRITERION_SUNDAY_FOLLOWING)
    return tuple(criteria)


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
