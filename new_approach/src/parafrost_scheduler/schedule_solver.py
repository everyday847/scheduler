"""Full joint schedule solver: weekly shifts + weekends + nights via RoundingSat.

Encodes the complete scheduling problem — weekly service assignments, weekend
call roles, and nightly call — into a single pseudo-Boolean (OPB) formula.
RoundingSat solves feasibility probes; a linear scan over the soft penalty
bound finds the optimum.

Variable layers:
    xs[f][w][s] — fellow f assigned to shift s in week w  (weekly schedule)
    wr[w][role][f] — fellow f assigned to weekend role in week w
    xn[d][f] — fellow f works night on absolute day d

Soft constraints are collected as weighted indicator variables; the total
weighted penalty is bounded by a single PB constraint and scanned downward.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from scheduler.fellow_mapping import FellowMapping
from scheduler.semantic_constraints import (
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
    SemanticConstraint,
    ShiftSet,
)
from scheduler.standing_rules import constraints_from_config as standing_constraints_from_config
from scheduler.night_call_types import (
    NightSolverConfig,
    NightScheduleSolution,
    CountMultiset,
    holiday_indices_for_config,
)
from scheduler.weekend_call_types import (
    WeekendSolverConfig,
    WeekendScheduleSolution,
    BackupScheduleSolution,
)
from scheduler.call_schedule_common import NIGHT_ROLES, WEEKEND_ROLES
from scheduler.night_policy_types import (
    ALL_POLICY_CRITERIA,
    CRITERION_ANAESTHESIA,
    CRITERION_CLINIC,
    CRITERION_FRIDAY_WEEKEND_NCC1,
    CRITERION_STROKE,
    CRITERION_SUNDAY_FOLLOWING,
    NightPolicyCounts,
    NightPolicyWeights,
)

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

from parafrost_scheduler.schedule_types import (
    # Constants
    NIGHT_BLOCKED_SHIFTS,
    NIGHT_BLOCKED_ALL_WEEK,
    NHS_NIGHT_PENALTY_WEIGHT,
    ANAESTHESIA_SHIFTS,
    CLINIC_SHIFTS,
    STROKE_SHIFTS,
    NON_PREFERRED_SUNDAY_FOLLOWING,
    HOLIDAY_ELIGIBLE_SHIFTS,
    WEEKEND_BLOCKED_SHIFTS,
    CONSECUTIVE_WEEKEND_BUFFER_SHIFTS,
    _ROLE_NCC1,
    _ROLE_NCC2,
    _ROLE_STROKE,
    _WEEKEND_ROLE_NAMES,
    _BACKUP_WEEKDAY,
    _BACKUP_WEEKEND,
    _BACKUP_ROLE_NAMES,
    _BACKUP_ELIGIBLE_SHIFTS_NCC,
    _BACKUP_ELIGIBLE_SHIFTS_STROKE,
    _BACKUP_GROUPS,
    _BACKUP_MAX_CONSECUTIVE_WEEKS,
    _BACKUP_FORBIDDEN_WEEKS,
    _BACKUP_COVERAGE_FIRST_WEEK,
    _BACKUP_HOLIDAY_WEEKS,
    _BACKUP_HOLIDAY_SHIFTS,
    DEFAULT_WEEKLY_SOFT_WEIGHT,
    DEFAULT_WEEKEND_MISMATCH_WEIGHT,
    DEFAULT_SWING_UNCOVERED_WEIGHT,
    DEFAULT_WEEKEND_NIGHT_FRIDAY_WEIGHT,
    DEFAULT_WEEKEND_NIGHT_SATURDAY_WEIGHT,
    DEFAULT_WEEKEND_NIGHT_SUNDAY_WEIGHT,
    # Calendar helpers (public — aliased below for internal backward compat)
    day_of_week,
    day_to_week,
    week_day,
    num_weeks_for,
    date_to_day_index,
    # Types
    ManagementMode,
    ScheduleSolverConfig,
    FullScheduleSolution,
    ScheduleVarMap,
)

_day_of_week = day_of_week
_day_to_week = day_to_week
_week_day = week_day
_num_weeks_for = num_weeks_for
_date_to_day_index = date_to_day_index


from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb,
    decode_solution,
    soft_penalty_breakdown,
    _allocate_backup_vars,
    _encode_backup_constraints,
    _encode_call_rules,
    _encode_dual_stroke_window,
    _encode_nhs_week_nights,
    _encode_night_constraints,
    _encode_night_policy_criteria,
    _encode_prevacation_weekend_penalty,
    _encode_pre_aan_forbid,
    _encode_shift_total,
    _encode_staffing_per_week,
    _encode_weekend_constraints,
    _encode_weekend_eligibility,
    _encode_weekend_night_linking,
    _encode_weekend_prerequisites,
)

from parafrost_scheduler.schedule_optimizer import (
    optimize_stream,
    solve_full_schedule,
    solution_to_json,
    solve_full_schedule_progressive,
    OptimizeStreamStep,
)
