from __future__ import annotations

from dataclasses import dataclass, field

from .call_schedule_common import (
    DEFAULT_ALWAYS_STROKE_ELIGIBLE,
    DEFAULT_CCM_FELLOWS,
    DEFAULT_STROKE_ONLY_ELIGIBLE,
    DEFAULT_TELESTROKE_STROKE_ELIGIBLE,
    is_weekend_blocked as common_is_weekend_blocked,
)


DEFAULT_EXACT_NCC_TOTALS = {
    "Cindy Wong": 12,
    "Alex Hanson": 12,
    "Raya Aliakbar": 15,
    "Joseph Conovaloff": 15,
    "Aditya Srivatsan": 7,
    "Cameron Schmidt": 7,
    "Harneet Dhillon": 7,
    "Helena Xeros": 7,
    "Jinyuan Liu": 1,
    "Sokena Zaidi": 1,
}
DEFAULT_EXACT_STROKE_TOTALS = {
    "Cindy Wong": 0,
    "Alex Hanson": 0,
    "Raya Aliakbar": 2,
    "Joseph Conovaloff": 2,
    "Jinyuan Liu": 2,
    "Sokena Zaidi": 2,
}
DEFAULT_STROKE_COHORT = ("Aditya Srivatsan", "Cameron Schmidt", "Harneet Dhillon", "Helena Xeros")


@dataclass(frozen=True)
class WeekendSolverConfig:
    ncc_totals: dict[str, int] = None
    stroke_totals: dict[str, int] = None
    # Per-fellow weekend-NCC RANGES [lo, hi] (hard band, no tolerance). Used for
    # groups (e.g. CCM) whose weekend load is proportionate to on-service weeks
    # rather than an even split. Fellows here are NOT in ncc_totals.
    ncc_ranges: dict[str, tuple[int, int]] | None = None
    # Group-sum constraints: (fellow_names, exact_total). Pairs with ncc_ranges
    # so a range group still hits its combined weekend-NCC total exactly.
    ncc_group_sums: tuple[tuple[tuple[str, ...], int], ...] = ()
    # Fellows under a HARD every-other-weekend rule (no two consecutive weekends,
    # no buffer exemption). Feasible only with proportionate ncc_ranges.
    every_other_weekend_fellows: frozenset[str] = frozenset()
    stroke_cohort: tuple[str, ...] = DEFAULT_STROKE_COHORT
    stroke_cohort_total: int | None = 45
    stroke_cohort_min: int = 11
    stroke_cohort_max: int = 12
    ccm_fellows: frozenset[str] = DEFAULT_CCM_FELLOWS
    ccm_ncc_total: int | None = 22
    always_stroke_eligible: frozenset[str] = DEFAULT_ALWAYS_STROKE_ELIGIBLE
    telestroke_stroke_eligible: frozenset[str] = DEFAULT_TELESTROKE_STROKE_ELIGIBLE
    stroke_only_eligible: frozenset[str] = DEFAULT_STROKE_ONLY_ELIGIBLE

    spacing_max_weekends: int = 1
    spacing_window_weekends: int = 2
    blocking_exact_services: tuple[str, ...] = ("Vacation", "NS SCVMC", "AAN", "Elective/NCS 2026", "")
    blocking_substring_services: tuple[str, ...] = ("SICU", "MSICU")
    stroke_eligible_services: tuple[str, ...] = ("Stroke",)
    penalty_weights: dict[str, int] = field(default_factory=lambda: {
        "anaesthesia": 1, "clinic": 1, "stroke": 5,
        "friday_weekend_ncc1": 1,
    })

    def __post_init__(self) -> None:
        object.__setattr__(self, "ncc_totals", dict(DEFAULT_EXACT_NCC_TOTALS if self.ncc_totals is None else self.ncc_totals))
        object.__setattr__(self, "stroke_totals", dict(DEFAULT_EXACT_STROKE_TOTALS if self.stroke_totals is None else self.stroke_totals))


@dataclass(frozen=True)
class WeekendScheduleSolution:
    assignments_by_week: list[dict[str, str]]


@dataclass(frozen=True)
class BackupScheduleSolution:
    """Per-week Backup assignments. Each dict has keys "Backup" (weekday) and
    "Weekend Backup", mapping to a fellow name (or "" if uncovered)."""
    assignments_by_week: list[dict[str, str]]


def is_weekend_blocked(weekday_assignment: str, *, config: WeekendSolverConfig | None = None) -> bool:
    if config is not None:
        return common_is_weekend_blocked(
            weekday_assignment,
            blocking_exact_services=config.blocking_exact_services,
            blocking_substring_services=config.blocking_substring_services,
        )
    return common_is_weekend_blocked(weekday_assignment)
