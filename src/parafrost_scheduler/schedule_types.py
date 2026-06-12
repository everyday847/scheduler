"""Types and constants for the full joint schedule solver.

Extracted from schedule_solver.py — the single source of truth for config
shapes, variable-map structure, enumeration helpers, and solver-wide constants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from scheduler.semantic_constraints import (
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
    SemanticConstraint,
    ShiftSet,
)
from scheduler.night_call_types import (
    NightSolverConfig,
    NightScheduleSolution,
    CountMultiset,
)
from scheduler.weekend_call_types import (
    WeekendSolverConfig,
    WeekendScheduleSolution,
    BackupScheduleSolution,
)
from scheduler.night_policy_types import (
    CRITERION_ANAESTHESIA,
    CRITERION_CLINIC,
    CRITERION_FRIDAY_WEEKEND_NCC1,
    CRITERION_STROKE,
    CRITERION_SUNDAY_FOLLOWING,
    NightPolicyCounts,
    NightPolicyWeights,
)
from scheduler.shift_palette import ShiftPalette


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Shift-name sets that used to live here as hardcoded frozensets are now
# config-driven Shift Attributes (see ADR-0003). The encoder reads them as
# projections over ``config.shift_palette`` via ``shifts_with_attribute(...)``;
# the Stanford instance populates the attribute table in
# config/standing/stanford-fellowship-v3.yaml. Only the non-shift-set tuning
# constants remain here.
NHS_NIGHT_PENALTY_WEIGHT = 100
# wk26/27 one-indexed -> 0-indexed weeks 25 & 26.
STROKE_WK2627_WEEKS = (25, 26)
# Dual-stroke Helena soft-penalty tiers (week split at index 20 = 1-indexed wk21).
DUAL_STROKE_EARLY_END = 20            # weeks 0..19 are "early"
DUAL_STROKE_BASE_EARLY = 0            # dual-stroke base penalty, early
DUAL_STROKE_BASE_LATE = 20           # dual-stroke base penalty, late
DUAL_STROKE_NO_HELENA_EARLY = 100    # extra if dual & not Helena, early
DUAL_STROKE_NO_HELENA_LATE = 80      # extra if dual & not Helena, late

_ROLE_NCC1 = 0
_ROLE_NCC2 = 1
_ROLE_STROKE = 2
_WEEKEND_ROLE_NAMES = ("Weekend NCC1", "Weekend NCC2", "Weekend Stroke")
# Short role tokens ("NCC1"/"NCC2"/"Stroke") map to wr role indices 0/1/2
# DERIVED from the canonical tuple above (strip the "Weekend " prefix) rather
# than re-hardcoding the literal dict — single source of truth for the order.
_WEEKEND_PREFIX = "Weekend "
_WEEKEND_ROLE_INDEX = {
    name.removeprefix(_WEEKEND_PREFIX): idx
    for idx, name in enumerate(_WEEKEND_ROLE_NAMES)
}


def weekend_role_index(role: str) -> int | None:
    """Resolve a short weekend role token to its wr index, or None if unknown.

    Accepts either the short token ("NCC1") or the full canonical name
    ("Weekend NCC1"); both resolve against _WEEKEND_ROLE_NAMES."""
    short = role.removeprefix(_WEEKEND_PREFIX)
    return _WEEKEND_ROLE_INDEX.get(short)


def weekend_role_name(role_idx: int) -> str:
    """The full canonical weekend role name for a wr index (e.g. 'Weekend NCC1')."""
    return _WEEKEND_ROLE_NAMES[role_idx]

_BACKUP_WEEKDAY = 0
_BACKUP_WEEKEND = 1
_BACKUP_ROLE_NAMES = ("Backup", "Weekend Backup")
_BACKUP_ELIGIBLE_SHIFTS_NCC = frozenset({"Elec", "Telestroke/Clinic"})
_BACKUP_ELIGIBLE_SHIFTS_STROKE = frozenset({"Elec", "Clinic/Elective", "Telestroke/Clinic"})
_BACKUP_GROUPS = ("NCC_JR", "NCC_SR", "STROKE")
_BACKUP_MAX_CONSECUTIVE_WEEKS = 2
_BACKUP_FORBIDDEN_WEEKS = frozenset({0})
_BACKUP_COVERAGE_FIRST_WEEK = 1
_BACKUP_HOLIDAY_WEEKS = frozenset({25, 26})
_BACKUP_HOLIDAY_SHIFTS = frozenset({"Telestroke/Clinic"})

DEFAULT_WEEKLY_SOFT_WEIGHT = 100
DEFAULT_WEEKEND_MISMATCH_WEIGHT = 20
DEFAULT_SWING_UNCOVERED_WEIGHT = 100
DEFAULT_WEEKEND_NIGHT_FRIDAY_WEIGHT = 40
DEFAULT_WEEKEND_NIGHT_SATURDAY_WEIGHT = 10
DEFAULT_WEEKEND_NIGHT_SUNDAY_WEIGHT = 10


# ---------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------

def day_of_week(d: int, start_dow: int) -> int:
    """Return the day-of-week (Mon=0 ... Sun=6) for absolute day *d*."""
    return (start_dow + d) % 7


def day_to_week(d: int, start_dow: int) -> int:
    """Return the academic-year week index for absolute day *d*."""
    return (start_dow + d) // 7


def week_day(w: int, dow_target: int, start_dow: int) -> int:
    """Return absolute day *d* for weekday *dow_target* in week *w*.

    May return a negative value (before the academic year) or a value
    >= num_days (after the academic year); callers must bounds-check.
    """
    return w * 7 - start_dow + dow_target


def num_weeks_for(start_dow: int, num_days: int) -> int:
    """Number of (possibly partial) weeks that span *num_days* starting on *start_dow*."""
    return (start_dow + num_days - 1) // 7 + 1


def date_to_day_index(date_str: str | date, horizon_start: date) -> int:
    """Convert a date string (or date object) to absolute day index from horizon start."""
    if isinstance(date_str, str):
        d = date.fromisoformat(date_str)
    else:
        d = date_str
    return (d - horizon_start).days


# Private aliases for backward compatibility (tests and encoder import them)
_day_of_week = day_of_week
_day_to_week = day_to_week
_week_day = week_day
_num_weeks_for = num_weeks_for
_date_to_day_index = date_to_day_index

# ---------------------------------------------------------------------------
# Enums & data classes
# ---------------------------------------------------------------------------

class ManagementMode(Enum):
    IMPORTED = "imported"
    MANAGED = "managed"


@dataclass(frozen=True)
class ScheduleSolverConfig:
    fellow_groups: dict[str, list[str]]
    shifts: list[str]
    constraints: list[SemanticConstraint]
    night_config: NightSolverConfig
    weekend_config: WeekendSolverConfig
    night_weights: NightPolicyWeights = field(default_factory=NightPolicyWeights)
    night_hard_criteria: frozenset[str] = frozenset(
        {CRITERION_ANAESTHESIA, CRITERION_FRIDAY_WEEKEND_NCC1}
    )
    start_dow: int = 0
    num_days: int = 365
    num_weeks: int = field(init=False)
    weekly_soft_weight: int = DEFAULT_WEEKLY_SOFT_WEIGHT
    weekend_mismatch_weight: int = DEFAULT_WEEKEND_MISMATCH_WEIGHT
    weekend_night_friday_weight: int = DEFAULT_WEEKEND_NIGHT_FRIDAY_WEIGHT
    weekend_night_saturday_weight: int = DEFAULT_WEEKEND_NIGHT_SATURDAY_WEIGHT
    weekend_night_sunday_weight: int = DEFAULT_WEEKEND_NIGHT_SUNDAY_WEIGHT
    weekend_night_saturday_hard: bool = True
    weekend_night_sunday_hard: bool = False
    weekend_consecutive_hard: bool = False
    weekend_total_tolerance: int = 1
    swing_uncovered_weight: int = DEFAULT_SWING_UNCOVERED_WEIGHT
    locked_assignments: dict[str, list[str]] = field(default_factory=dict)
    # Relaxed locking: float a locked fellow's workbook-marked NCC1/NCC2/Swing
    # weeks among the trio (exactly-one per such week) instead of pinning the
    # exact role. The set of trio weeks is preserved (totals unchanged); only the
    # per-week role floats. CCM Swing guardrails + soft team-continuity are added
    # in the encoder (see _encode_relaxed_lock_guardrails). Default off.
    relax_locked_ncc_trio: bool = False
    call_rules: list[dict] = field(default_factory=list)
    # Shift Attributes (ADR-0003): config-driven table mapping shift name -> the
    # attribute flags it carries. The encoder projects historical shift-name
    # frozensets out of this via shifts_with_attribute(...). Defaults to an empty
    # palette so direct-construction call-sites (tests) need no change.
    shift_palette: ShiftPalette = field(default_factory=ShiftPalette)
    # --- Experimental variant flags (default off; the shipped config is unchanged) ---
    # ABPN hard-blocks prior-Sun..Thu weekday night call (like the generic
    # night-block) for ALL fellows. Default ON: an ABPN week should be free of
    # weekday (next-morning-is-a-workday) night call. Set False to allow it.
    abpn_night_block: bool = True
    # Preferred fellow for dual-Stroke weeks (soft, two-tier early/late). None = off.
    dual_stroke_helena: str | None = None
    # Stroke wk26/27 (1-indexed) on/off toggle: "off" | "hard" | "soft".
    stroke_wk2627_toggle: str = "off"
    # --- NH courtesy + NCC alignment soft penalties (set weight 0 to deactivate) ---
    # NH-group fellow on AAN/ABPN that week: per-occurrence soft penalty for each
    # night worked and each weekend role held (we only manage part of their time;
    # their primary fellowship may rely on a light AAN/ABPN week).
    nh_aan_week_call_penalty: int = 100
    nh_abpn_week_call_penalty: int = 100
    # When True, the AAN-week avoidance is HARD: an NH fellow on AAN is forbidden
    # any night/weekend role that week (overrides nh_aan_week_call_penalty). ABPN
    # stays soft regardless. (Mon-Thu AAN nights are already hard-blocked via
    # NIGHT_BLOCKED_SHIFTS; this newly forbids Fri/Sat/Sun nights + weekend roles.)
    nh_aan_week_call_hard: bool = False
    # Fellow on weekday NCC1 but Weekend NCC2 (or weekday NCC2 but Weekend NCC1):
    # soft nudge toward weekday/weekend NCC role alignment.
    ncc_weekend_misalign_penalty: int = 10
    # (The former stroke_weekend_misalign_penalty is folded into the
    # weekend_role_mismatch criterion's per-role weight — Weekend Stroke = 60 in
    # config/standing — so a Stroke mismatch is one criterion at its full cost.)

    def __post_init__(self):
        object.__setattr__(self, 'num_weeks', num_weeks_for(self.start_dow, self.num_days))

    @property
    def imported_fellow_names(self) -> frozenset[str]:
        return frozenset(self.locked_assignments)

    def management_mode(self, fellow_name: str) -> ManagementMode:
        return (ManagementMode.IMPORTED if fellow_name in self.locked_assignments
                else ManagementMode.MANAGED)

    def imported_fellow_indices(self, fellow_names: list[str]) -> frozenset[int]:
        return frozenset(
            i for i, name in enumerate(fellow_names)
            if name in self.locked_assignments
        )

    def imported_shift_counts(
        self,
        fellow_names: list[str],
        shift_names: set[str] | frozenset[str],
        restrict_to: frozenset[int] | None = None,
    ) -> dict[int, int]:
        target = set(shift_names)
        counts: dict[int, int] = {}
        for i, name in enumerate(fellow_names):
            if restrict_to is not None and i not in restrict_to:
                continue
            weekly = self.locked_assignments.get(name)
            if not weekly:
                continue
            for w, shift in enumerate(weekly):
                if shift and shift in target:
                    counts[w] = counts.get(w, 0) + 1
        return counts


@dataclass(frozen=True)
class FullScheduleSolution:
    weekly_assignments: dict[str, list[str]]
    weekend_solution: WeekendScheduleSolution
    night_solution: NightScheduleSolution
    soft_penalty: int
    backup_solution: BackupScheduleSolution | None = None


@dataclass
class ScheduleVarMap:
    fellow_names: list[str]
    shifts: list[str]
    num_weeks: int
    num_fellows: int
    num_shifts: int
    start_dow: int
    num_days: int

    xs: list[list[list[int]]]
    wr: list[list[dict[int, int]]]
    xn: list[list[int]]

    soft_violations: list[tuple[int, int]]
    soft_weekly_count: int = 0
    bk: list[list[dict[int, int]]] = field(default_factory=list)
