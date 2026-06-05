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


# ---------------------------------------------------------------------------
# Main build function
# ---------------------------------------------------------------------------

def build_full_schedule_opb(
    config: ScheduleSolverConfig,
    soft_bound: int | None = None,
    *,
    objective: bool = False,
) -> tuple[OpbBuilder, ScheduleVarMap]:
    """Build the complete OPB formula for the joint scheduling problem.

    soft_bound:
        If set, add a hard cap ``sum(soft penalties) <= soft_bound``. Used both
        for the legacy decision-scan and as the strict-improvement bound in the
        native-optimization streaming loop.
    objective:
        If True, emit a native ``min:`` objective over the soft penalties so
        RoundingSat minimizes them directly (via ``RoundingSatRunner.optimize``).
        Can be combined with soft_bound to minimize subject to an upper bound.
    """

    fellow_mapping = _build_fellow_mapping(config.fellow_groups)
    fellow_names = [
        fellow_mapping.get_fellow_name(i) for i in range(fellow_mapping.total_fellows)
    ]
    shifts = config.shifts
    num_days = config.num_days
    start_dow = config.start_dow
    num_weeks = config.num_weeks
    num_fellows = len(fellow_names)
    num_shifts = len(shifts)
    shift_idx = {s: i for i, s in enumerate(shifts)}

    opb = OpbBuilder()
    soft_violations: list[tuple[int, int]] = []

    # Pre-compute forbidden assignments from hard constraints
    forbidden = _compute_forbidden(config.constraints, fellow_mapping, shifts, num_weeks)

    # Locked fellows' empty weeks: forbid ALL shifts (no variables created).
    # This prevents the solver from assigning them anything in unscheduled weeks.
    if config.locked_assignments:
        for fellow_name, weekly_shifts in config.locked_assignments.items():
            try:
                f = fellow_mapping.get_fellow_index(fellow_name)
            except ValueError:
                continue
            for w in range(min(num_weeks, len(weekly_shifts))):
                if not weekly_shifts[w]:
                    for s in range(num_shifts):
                        forbidden.add((f, w, s))

    # -------------------------------------------------------------------
    # 1. Weekly shift variables: xs[f][w][s]
    # -------------------------------------------------------------------
    opb.add_comment("Weekly shift variables")
    xs: list[list[list[int]]] = []
    for f in range(num_fellows):
        xs.append([])
        for w in range(num_weeks):
            xs[f].append([])
            for s in range(num_shifts):
                if (f, w, s) in forbidden:
                    xs[f][w].append(0)
                else:
                    xs[f][w].append(opb.new_var())

    # -------------------------------------------------------------------
    # 2. Fundamental: at most one shift per fellow per week
    # -------------------------------------------------------------------
    opb.add_comment("Fundamental: at most one shift per week per fellow")
    for f in range(num_fellows):
        for w in range(num_weeks):
            active_vars = [xs[f][w][s] for s in range(num_shifts) if xs[f][w][s] != 0]
            if len(active_vars) > 1:
                opb.at_most_k(active_vars, 1)

    # -------------------------------------------------------------------
    # 2b. Pin IMPORTED fellows' frozen weekly assignments
    # -------------------------------------------------------------------
    # IMPORTED fellows (single source of truth: config.imported_fellow_*) have
    # their weekly schedule frozen here; their per-fellow weekly rules are then
    # skipped downstream via locked_fellow_indices.
    locked_fellow_indices = config.imported_fellow_indices(fellow_names)
    if config.locked_assignments:
        opb.add_comment("Locked fellow assignments (pinned)")
        for fellow_name, weekly_shifts in config.locked_assignments.items():
            try:
                f = fellow_mapping.get_fellow_index(fellow_name)
            except ValueError:
                continue
            for w, shift_name in enumerate(weekly_shifts):
                if w >= num_weeks or not shift_name:
                    continue
                si = shift_idx.get(shift_name)
                if si is None:
                    continue
                var = xs[f][w][si]
                if var != 0:
                    opb.add_unit(var)

    # -------------------------------------------------------------------
    # 3. Encode weekly shift rules from YAML config
    # -------------------------------------------------------------------
    _encode_weekly_rules(
        opb, xs, config, fellow_mapping, shift_idx, soft_violations,
        locked_fellow_indices=locked_fellow_indices,
    )
    # Soft violations appended so far are all WEEKLY; everything appended after
    # this point is weekend/night/call. This boundary lets the decoder report
    # the weekly vs. weekend-night soft penalty separately.
    weekly_soft_count = len(soft_violations)

    # -------------------------------------------------------------------
    # 4. Weekend variables: wr[w][role][f]
    # -------------------------------------------------------------------
    opb.add_comment("Weekend assignment variables")
    _diag_disable_weekends = os.environ.get("SCHED_DIAG_DISABLE_WEEKENDS") == "1"
    wr: list[list[dict[int, int]]] = []
    for w in range(num_weeks):
        wr.append([])
        # A partial final (or first) week may have no weekend day inside the
        # horizon; skip it so coverage/totals don't force a phantom weekend.
        sat_day = _week_day(w, 5, start_dow)
        week_has_weekend = 0 <= sat_day < num_days
        for role_idx in range(3):
            role_vars: dict[int, int] = {}
            if week_has_weekend and not _diag_disable_weekends:
                for f in range(num_fellows):
                    if _is_weekend_eligible_static(
                        fellow_names[f], role_idx, config.weekend_config
                    ):
                        role_vars[f] = opb.new_var()
            wr[w].append(role_vars)

    # -------------------------------------------------------------------
    # 5. Weekend constraints
    # -------------------------------------------------------------------
    _encode_weekend_constraints(
        opb, wr, xs, config, fellow_mapping, fellow_names, shift_idx, soft_violations,
    )

    # -------------------------------------------------------------------
    # 5b. Backup variables + constraints: bk[w][kind][f]
    # -------------------------------------------------------------------
    opb.add_comment("Backup assignment variables")
    bk = _allocate_backup_vars(opb, config, fellow_names)
    _encode_backup_constraints(
        opb, bk, wr, xs, config, fellow_names, shift_idx, soft_violations,
    )

    # -------------------------------------------------------------------
    # 6. Night variables: xn[d][f]
    # -------------------------------------------------------------------
    opb.add_comment("Night assignment variables")
    _diag_disable_nights = os.environ.get("SCHED_DIAG_DISABLE_NIGHTS") == "1"
    xn: list[list[int]] = []
    for d in range(num_days):
        xn.append([])
        for f in range(num_fellows):
            if _diag_disable_nights or fellow_names[f] in config.night_config.ccm_fellows:
                xn[d].append(0)
            else:
                xn[d].append(opb.new_var())

    # -------------------------------------------------------------------
    # 7. Night constraints
    # -------------------------------------------------------------------
    _encode_night_constraints(
        opb, xn, xs, wr, config, fellow_names, shift_idx, soft_violations,
    )

    # -------------------------------------------------------------------
    # 7b. Annual call rules (pin/block night/weekend)
    # -------------------------------------------------------------------
    if config.call_rules:
        opb.add_comment("Annual call rules (pin/block night/weekend)")
        _encode_call_rules(opb, xn, wr, config, fellow_names,
                           xs=xs, shift_idx=shift_idx, soft_violations=soft_violations)

        opb.add_comment("Dual Stroke window (from call_rules)")
        _encode_dual_stroke_window(opb, xs, config, fellow_names, shift_idx,
                                   soft_violations=soft_violations)

    # -------------------------------------------------------------------
    # 8. Soft penalty bound
    # -------------------------------------------------------------------
    if soft_bound is not None and soft_violations:
        opb.add_comment(f"Soft penalty bound <= {soft_bound}")
        weighted_terms = [(var, weight) for var, weight in soft_violations]
        opb.weighted_sum_at_most(weighted_terms, soft_bound)

    if objective and soft_violations:
        opb.set_objective([(var, weight) for var, weight in soft_violations])

    var_map = ScheduleVarMap(
        fellow_names=fellow_names,
        shifts=shifts,
        num_weeks=num_weeks,
        num_fellows=num_fellows,
        num_shifts=num_shifts,
        start_dow=start_dow,
        num_days=num_days,
        xs=xs,
        wr=wr,
        xn=xn,
        soft_violations=soft_violations,
        soft_weekly_count=weekly_soft_count,
        bk=bk,
    )

    return opb, var_map


# ---------------------------------------------------------------------------
# Weekly rule encoders
# ---------------------------------------------------------------------------

PER_FELLOW_KINDS = frozenset({
    "shift_total", "max_consecutive", "all_or_none_block",
    "block_shift_set_choice", "prerequisite", "windowed_balance",
    "service_profile", "nir_one_week_per_half", "scvmc_second_half",
    "stroke_no_block_one_ncc", "jr_ncc_before_swing",
    "comparable_half_year_distribution", "block_rotation",
    "rotation_continuity", "block_shift_count", "zero_shifts",
})


def _encode_weekly_rules(
    opb: OpbBuilder,
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_mapping: FellowMapping,
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
    *,
    locked_fellow_indices: frozenset[int] = frozenset(),
) -> None:
    """Encode all active weekly shift constraints from the YAML config."""

    num_weeks = config.num_weeks
    num_fellows = fellow_mapping.total_fellows
    shifts = config.shifts
    fellow_names = [fellow_mapping.get_fellow_name(i) for i in range(num_fellows)]

    handlers = {
        "full_assignment": _encode_full_assignment,
        "ncc_coverage": _encode_ncc_coverage,
        "minimize_uncovered_shift_weeks": _encode_minimize_uncovered,
        "max_consecutive": _encode_max_consecutive,
        "jr_ncc_before_swing": _encode_jr_ncc_before_swing,
        "block_shift_set_choice": _encode_block_shift_set_choice,
        "all_or_none_block": _encode_all_or_none_block,
        "block_shift_count": _encode_block_shift_count,
        "service_profile": _encode_service_profile,
        "ncc_stroke_oversight": _encode_ncc_stroke_oversight,
        "comparable_half_year_distribution": _encode_comparable_half_year,
        "stroke_shift_coverage": _encode_stroke_shift_coverage,
        "nir_one_week_per_half": _encode_nir_one_week_per_half,
        "scvmc_second_half": _encode_scvmc_second_half,
        "stroke_no_block_one_ncc": _encode_stroke_no_block_one_ncc,
        "fourth_block_two_micu_fellows": _encode_fourth_block_two_micu,
        "isc": _encode_isc,
        "specific_assignment": _encode_specific_assignment,
        # Palette v2 generic types
        "shift_total": _encode_shift_total,
        "staffing_per_week": _encode_staffing_per_week,
        "coverage_target": _encode_coverage_target,
        "zero_shifts": _encode_zero_shifts,
        "prerequisite": _encode_prerequisite,
        "windowed_balance": _encode_windowed_balance,
    }

    for constraint in config.constraints:
        handler = handlers.get(constraint.kind)
        if handler is None:
            print(f"Warning: skipping unsupported rule kind '{constraint.kind}'", file=sys.stderr)
            continue
        fellow_indices = _resolve_fellow_indices(fellow_mapping, constraint.fellows)
        if locked_fellow_indices and constraint.kind in PER_FELLOW_KINDS:
            fellow_indices = [f for f in fellow_indices if f not in locked_fellow_indices]
            if not fellow_indices:
                continue
        opb.add_comment(f"Rule: {constraint.params.get('name', constraint.kind)} ({constraint.strength.value})")
        handler(
            opb, xs, constraint, fellow_indices,
            num_weeks=num_weeks,
            num_fellows=num_fellows,
            shifts=shifts,
            shift_idx=shift_idx,
            fellow_names=fellow_names,
            soft_violations=soft_violations,
            config=config,
            locked_fellow_indices=locked_fellow_indices,
        )


def _encode_full_assignment(opb, xs, constraint, fellow_indices, **kw):
    """Each fellow in scope must be assigned exactly one shift per week."""
    num_weeks = kw["num_weeks"]
    num_shifts = len(kw["shifts"])
    is_soft = constraint.strength == ConstraintStrength.SOFT
    weight = kw["config"].weekly_soft_weight

    for f in fellow_indices:
        for w in range(num_weeks):
            active = [xs[f][w][s] for s in range(num_shifts) if xs[f][w][s] != 0]
            if not active:
                continue
            if is_soft:
                v = opb.new_var()
                # v=1 if no shift assigned (violation)
                # sum(active) + v >= 1
                opb.at_least_k(active + [v], 1)
                # v=0 if any shift assigned: v + active_i <= 1 for ... no, too many
                # Better: v <= 1 - sum(active)/1. Use: sum(active) + ~v >= 1 → already above
                # And: v <= ~active_i for each? No. Just:
                # v + sum(active) <= 1 would force at most one between v and the sum. But sum can be 1.
                # Actually sum is already <= 1 from fundamental. So sum ∈ {0,1}.
                # v=1 iff sum=0: v >= 1 - sum → v + sum >= 1 (above)
                # v <= 1 - sum: since sum <= 1, this means v + sum <= 1
                opb.at_most_k(active + [v], 1)
                kw["soft_violations"].append((v, weight))
            else:
                opb.at_least_k(active, 1)


def _encode_ncc_coverage(opb, xs, constraint, fellow_indices, **kw):
    """Per-week NCC staffing requirements."""
    num_weeks = kw["num_weeks"]
    shift_idx = kw["shift_idx"]
    num_fellows = kw["num_fellows"]
    max_ncc = constraint.params.get("max_ncc_fellows", 3)
    max_ncc_swing = constraint.params.get("max_ncc_plus_swing_fellows", 4)
    swing_deficit = constraint.params.get("swing_deficit", 0)
    is_soft = constraint.strength == ConstraintStrength.SOFT
    weight = kw["config"].weekly_soft_weight

    s_ncc1 = shift_idx.get("NCC1")
    s_ncc2 = shift_idx.get("NCC2")
    s_swing = shift_idx.get("Swing")

    if s_ncc1 is None or s_ncc2 is None or s_swing is None:
        return

    for w in range(num_weeks):
        ncc1_vars = [xs[f][w][s_ncc1] for f in fellow_indices if xs[f][w][s_ncc1] != 0]
        ncc2_vars = [xs[f][w][s_ncc2] for f in fellow_indices if xs[f][w][s_ncc2] != 0]
        swing_vars = [xs[f][w][s_swing] for f in fellow_indices if xs[f][w][s_swing] != 0]
        ncc_all = ncc1_vars + ncc2_vars
        ncc_swing_all = ncc_all + swing_vars

        if is_soft:
            # For soft, just add violations for each sub-constraint
            pass  # NCC coverage should really be hard
        else:
            if ncc1_vars:
                opb.at_least_k(ncc1_vars, 1)
                opb.at_most_k(ncc1_vars, 2)
            if ncc2_vars:
                opb.at_least_k(ncc2_vars, 1)
                opb.at_most_k(ncc2_vars, 2)
            if ncc_all:
                opb.at_most_k(ncc_all, max_ncc)
            if ncc_swing_all:
                opb.at_most_k(ncc_swing_all, max_ncc_swing)
            if swing_vars:
                opb.at_most_k(swing_vars, 1)

    # Swing deficit: Swing should be covered in all but swing_deficit weeks
    if swing_deficit > 0:
        swing_covered_vars = []
        for w in range(num_weeks):
            swing_vars_w = [xs[f][w][s_swing] for f in fellow_indices if xs[f][w][s_swing] != 0]
            if swing_vars_w:
                covered = opb.new_var()
                # covered <= sum(swing): sum(swing) + (1 - covered) >= 1
                opb.weighted_sum_at_least(
                    [(v, 1) for v in swing_vars_w] + [(-covered, 1)], 1
                )
                # Backward: covered >= 1 - (N-1)*(1 - any_swing)... complex
                # Simpler: covered + sum(~swing) >= 1 if only 1 swing var... but multiple
                # Actually: sum(swing) >= 1 → covered=1; sum(swing)=0 → covered=0
                # covered <= sum(swing): weighted_sum_at_most([(covered,1),(-v,-1) for v], 0)?
                # Let's just do: sum(swing) - covered >= 0 AND sum(swing) - covered <= N-1*(something)
                # Hmm. Actually we only need one direction for the deficit constraint.
                # "At least (W - deficit) weeks have swing covered" = sum(covered) >= W - deficit
                # If covered <= sum(swing_vars), then sum(covered) <= actual_covered_weeks.
                # That direction is enough: if the real coverage is ≥ W-deficit, there exists
                # an assignment of covered_vars that satisfies sum(covered) >= W-deficit.
                # But the solver might set covered=0 even when swing is assigned, making the
                # sum(covered) constraint trivially satisfiable. We need the forward direction too.
                #
                # Proper encoding: covered = OR(swing_vars)
                # covered >= swing_i for each i:
                for sv in swing_vars_w:
                    opb.weighted_sum_at_least([(covered, 1), (-sv, 1)], 1)
                # covered <= sum(swing_i): already have sum >= covered
                swing_covered_vars.append(covered)
            else:
                # No one can do swing this week — uncovered by definition
                pass

        if swing_covered_vars:
            required_covered = num_weeks - swing_deficit
            if required_covered > 0:
                opb.at_least_k(swing_covered_vars, min(required_covered, len(swing_covered_vars)))


def _encode_minimize_uncovered(opb, xs, constraint, fellow_indices, **kw):
    """Minimize weeks where no fellow is on a given shift (soft penalty)."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]
    weight = kw["config"].swing_uncovered_weight
    soft_violations = kw["soft_violations"]

    target_shifts = list(constraint.shifts.shifts) if constraint.shifts else ["Swing"]
    s_indices = [shift_idx[s] for s in target_shifts if s in shift_idx]

    for w in range(num_weeks):
        covered_vars = []
        for f in fellow_indices:
            for si in s_indices:
                if xs[f][w][si] != 0:
                    covered_vars.append(xs[f][w][si])
        if covered_vars:
            # uncovered[w] = 1 iff no fellow assigned
            uncovered = opb.new_var()
            # uncovered + sum(covered) >= 1
            opb.at_least_k(covered_vars + [uncovered], 1)
            # uncovered <= ~covered_i for each (uncovered=0 if any covered)
            for cv in covered_vars:
                opb.at_most_k([uncovered, cv], 1)
            soft_violations.append((uncovered, weight))
        else:
            # Always uncovered — add fixed penalty
            always_uncovered = opb.new_var()
            opb.add_unit(always_uncovered)
            soft_violations.append((always_uncovered, weight))


def _encode_max_consecutive(opb, xs, constraint, fellow_indices, **kw):
    """No fellow has more than K consecutive weeks on any shift in the set."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]
    is_soft = constraint.strength == ConstraintStrength.SOFT
    weight = kw["config"].weekly_soft_weight
    soft_violations = kw["soft_violations"]

    max_consec = constraint.params["weeks"]
    target_shifts = list(constraint.shifts.shifts) if constraint.shifts else []
    s_indices = [shift_idx[s] for s in target_shifts if s in shift_idx]
    if not s_indices:
        return

    for f in fellow_indices:
        for w_start in range(num_weeks - max_consec):
            window_size = max_consec + 1
            # For each week in the window, create OR indicator: "in_set this week"
            in_set_vars = []
            for offset in range(window_size):
                w = w_start + offset
                week_shift_vars = [xs[f][w][si] for si in s_indices if xs[f][w][si] != 0]
                if not week_shift_vars:
                    # Can't be in set this week — skip (contributes 0)
                    continue
                if len(week_shift_vars) == 1:
                    in_set_vars.append(week_shift_vars[0])
                else:
                    # OR of multiple shifts: introduce aux
                    aux = opb.new_var()
                    # aux >= each: aux + ~shift_i >= 1
                    for sv in week_shift_vars:
                        opb.weighted_sum_at_least([(aux, 1), (-sv, 1)], 1)
                    # aux <= sum(shifts): sum(shifts) + (1 - aux) >= 1
                    opb.weighted_sum_at_least(
                        [(v, 1) for v in week_shift_vars] + [(-aux, 1)], 1
                    )
                    in_set_vars.append(aux)

            if len(in_set_vars) <= max_consec:
                continue  # Can't violate

            if is_soft:
                v = opb.new_var()
                # Violation if sum(in_set) > max_consec
                # v=1 iff sum(in_set) >= max_consec+1
                # sum(in_set) + (window - max_consec - 1)*~v <= window - 1? complex
                # Simpler: at_most_k(in_set + [~v expanded], max_consec)?
                # Use: sum(in_set) - v*(len-max_consec) <= max_consec
                # When v=0: sum <= max_consec (constraint satisfied, no violation)
                # When v=1: sum <= max_consec + (len-max_consec) = len (always true)
                # This only constrains when v=0. We need v=1 forces nothing, v=0 forces constraint.
                # But we also need: if sum > max_consec, v MUST be 1.
                # sum(in_set) - max_consec <= (len-max_consec)*v
                # When v=0: sum <= max_consec (satisfied → no violation needed)
                # When v=1: sum <= len (trivial)
                # If sum > max_consec: v must be >= (sum-max_consec)/(len-max_consec) > 0 → v=1
                n = len(in_set_vars)
                slack = n - max_consec
                # sum(in_set) + slack*~v <= max_consec + slack
                opb.weighted_sum_at_most(
                    [(iv, 1) for iv in in_set_vars] + [(-v, slack)], max_consec + slack
                )
                # If sum > max_consec, v must be 1: sum - max_consec*1 >= 1*(1-~v)?
                # sum(in_set) - max_consec >= 1 - ~v... not quite right.
                # Better: sum(in_set) + slack*~v >= max_consec + 1 - (slack)*1?
                # Actually the first constraint already handles it:
                # When v=0 (~v=1): sum + slack <= max_consec + slack → sum <= max_consec
                # If sum > max_consec → must have v=1 (since v=0 would violate)
                # So v is correctly forced to 1 when the constraint is violated. ✓
                soft_violations.append((v, weight))
            else:
                opb.at_most_k(in_set_vars, max_consec)


def _encode_jr_ncc_before_swing(opb, xs, constraint, fellow_indices, **kw):
    """Junior fellows must have >= N weeks of NCC before first Swing."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]
    n_required = constraint.params.get("ncc_weeks", 4)

    s_ncc1 = shift_idx.get("NCC1")
    s_ncc2 = shift_idx.get("NCC2")
    s_swing = shift_idx.get("Swing")
    if s_swing is None:
        return

    for f in fellow_indices:
        # For weeks 0..n_required-1: can't do Swing (not enough prior NCC possible)
        for w in range(min(n_required, num_weeks)):
            if xs[f][w][s_swing] != 0:
                opb.add_unit(-xs[f][w][s_swing])

        # For weeks n_required..end: Swing implies >= n_required prior NCC weeks
        for w in range(n_required, num_weeks):
            swing_var = xs[f][w][s_swing]
            if swing_var == 0:
                continue
            # Collect prior NCC vars
            prior_ncc: list[int] = []
            for pw in range(w):
                for si in (s_ncc1, s_ncc2):
                    if si is not None and xs[f][pw][si] != 0:
                        prior_ncc.append(xs[f][pw][si])

            if len(prior_ncc) < n_required:
                # Can't possibly meet requirement — block swing
                opb.add_unit(-swing_var)
            else:
                # swing → sum(prior_ncc) >= n_required
                # Equivalently: sum(prior_ncc) + n_required * ~swing >= n_required
                opb.weighted_sum_at_least(
                    [(v, 1) for v in prior_ncc] + [(-swing_var, n_required)],
                    n_required,
                )


def _encode_block_shift_set_choice(opb, xs, constraint, fellow_indices, **kw):
    """In each block, fellow must be on one of the allowed shift-set choices (or none)."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]
    is_soft = constraint.strength == ConstraintStrength.SOFT
    weight = kw["config"].weekly_soft_weight
    soft_violations = kw["soft_violations"]

    block_size = constraint.params["block_size"]
    choices = constraint.params["choices"]  # list of lists of shift names
    allow_none = constraint.params.get("allow_none", False)

    all_trigger_shifts = sorted({s for choice in choices for s in choice})
    trigger_indices = [shift_idx[s] for s in all_trigger_shifts if s in shift_idx]

    for f in fellow_indices:
        for block_start in range(0, num_weeks, block_size):
            block_end = min(block_start + block_size, num_weeks)
            block_len = block_end - block_start

            # Collect shift term vars for each choice
            selectors = []

            for choice in choices:
                choice_indices = [shift_idx[s] for s in choice if s in shift_idx]
                choice_terms = []
                for w in range(block_start, block_end):
                    for si in choice_indices:
                        if xs[f][w][si] != 0:
                            choice_terms.append(xs[f][w][si])

                sel = opb.new_var()
                selectors.append(sel)
                # If sel=1: exactly block_len of choice_terms are true
                if choice_terms:
                    opb.conditional_exactly_k(choice_terms, block_len, sel)
                else:
                    # No vars available — this choice is impossible
                    opb.add_unit(-sel)

            if allow_none:
                sel_none = opb.new_var()
                selectors.append(sel_none)
                # If sel_none=1: all trigger shift terms in block are 0
                trigger_terms = []
                for w in range(block_start, block_end):
                    for si in trigger_indices:
                        if xs[f][w][si] != 0:
                            trigger_terms.append(xs[f][w][si])
                if trigger_terms:
                    opb.conditional_exactly_k(trigger_terms, 0, sel_none)

            # At least one selector must be true
            if is_soft:
                v = opb.new_var()
                # v=1 iff no selector is true (violation)
                opb.at_least_k(selectors + [v], 1)
                for sel in selectors:
                    opb.at_most_k([v, sel], 1)
                soft_violations.append((v, weight))
            else:
                if selectors:
                    opb.at_least_k(selectors, 1)


def _encode_all_or_none_block(opb, xs, constraint, fellow_indices, **kw):
    """Each block of K weeks is either fully assigned to shift or not at all."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]
    is_soft = constraint.strength == ConstraintStrength.SOFT
    weight = kw["config"].weekly_soft_weight
    soft_violations = kw["soft_violations"]

    block_size = constraint.params.get("block_size", 4)
    target_shifts = list(constraint.shifts.shifts) if constraint.shifts else []

    for shift_name in target_shifts:
        si = shift_idx.get(shift_name)
        if si is None:
            continue

        for f in fellow_indices:
            for block_start in range(0, num_weeks, block_size):
                block_end = min(block_start + block_size, num_weeks)
                block_len = block_end - block_start
                block_vars = [xs[f][w][si] for w in range(block_start, block_end) if xs[f][w][si] != 0]

                if not block_vars or len(block_vars) < block_len:
                    # Some weeks are forbidden — can't do full block
                    # Force all to 0
                    if not is_soft:
                        for bv in block_vars:
                            opb.add_unit(-bv)
                    continue

                # sum(block_vars) ∈ {0, block_len}
                # Introduce selector: sel=1 → all true, sel=0 → all false
                sel = opb.new_var()

                if is_soft:
                    # Soft: penalize if sum is neither 0 nor block_len
                    # Violation = NOT(sum=0 OR sum=block_len) = sum ∈ {1..block_len-1}
                    # With selector: sum = block_len * sel is what we want.
                    # If it's not met, we have a violation.
                    # Encode: sum >= block_len*sel AND sum <= block_len*sel
                    # Then violation v=1 iff we can't find a sel that makes this work.
                    # Actually for soft, just add the hard encoding with a violation bypass:
                    v = opb.new_var()
                    # If v=0 (no violation): sum = block_len*sel must hold
                    # sum + block_len*~sel >= block_len - (block_len-1)*v
                    # This is getting complex. Simpler approach:
                    # Just use hard encoding but allow violation indicator.
                    # sum + block_len*~sel + block_len*v >= block_len
                    opb.weighted_sum_at_least(
                        [(bv, 1) for bv in block_vars] + [(-sel, block_len)] + [(v, block_len)],
                        block_len,
                    )
                    # ~sum + block_len*sel + block_len*v >= block_len
                    opb.weighted_sum_at_least(
                        [(-bv, 1) for bv in block_vars] + [(sel, block_len)] + [(v, block_len)],
                        block_len,
                    )
                    soft_violations.append((v, weight))
                else:
                    # Hard: sum >= block_len*sel
                    opb.weighted_sum_at_least(
                        [(bv, 1) for bv in block_vars] + [(-sel, block_len)],
                        block_len,
                    )
                    # Hard: (block_len - sum) >= block_len*(1-sel)
                    opb.weighted_sum_at_least(
                        [(-bv, 1) for bv in block_vars] + [(sel, block_len)],
                        block_len,
                    )


def _encode_block_shift_count(opb, xs, constraint, fellow_indices, **kw):
    """In each block, the count of a shift must be in allowed_counts."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]
    is_soft = constraint.strength == ConstraintStrength.SOFT
    weight = kw["config"].weekly_soft_weight
    soft_violations = kw["soft_violations"]

    block_size = constraint.params["block_size"]
    allowed_counts = constraint.params["allowed_counts"]
    target_shifts = list(constraint.shifts.shifts) if constraint.shifts else constraint.params.get("shifts", [])
    s_indices = [shift_idx[s] for s in target_shifts if s in shift_idx]

    for f in fellow_indices:
        for block_start in range(0, num_weeks, block_size):
            block_end = min(block_start + block_size, num_weeks)
            block_vars = []
            for w in range(block_start, block_end):
                for si in s_indices:
                    if xs[f][w][si] != 0:
                        block_vars.append(xs[f][w][si])

            if not block_vars:
                continue

            # sum(block_vars) must be in allowed_counts
            # Encode as disjunction: OR(sum = c for c in allowed_counts)
            selectors = []
            for count in allowed_counts:
                sel = opb.new_var()
                selectors.append(sel)
                opb.conditional_exactly_k(block_vars, count, sel)

            if is_soft:
                v = opb.new_var()
                opb.at_least_k(selectors + [v], 1)
                for sel in selectors:
                    opb.at_most_k([v, sel], 1)
                soft_violations.append((v, weight))
            else:
                opb.at_least_k(selectors, 1)


def _encode_service_profile(opb, xs, constraint, fellow_indices, **kw):
    """Encode shift totals, zero_shifts, window_totals, and active_blocks."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]
    is_soft = constraint.strength == ConstraintStrength.SOFT
    weight = kw["config"].weekly_soft_weight
    soft_violations = kw["soft_violations"]

    # zero_shifts: ALWAYS hard — a fellow literally cannot do these shifts
    for shift_name in constraint.params.get("zero_shifts", []):
        si = shift_idx.get(shift_name)
        if si is None:
            continue
        for f in fellow_indices:
            for w in range(num_weeks):
                if xs[f][w][si] != 0:
                    opb.add_unit(-xs[f][w][si])

    # totals: count constraints over all weeks
    for total in constraint.params.get("totals", []):
        target_shifts = total["shifts"]
        relation = total["relation"]
        target_weeks = total["weeks"]
        s_indices = [shift_idx[s] for s in target_shifts if s in shift_idx]

        for f in fellow_indices:
            all_vars = []
            for w in range(num_weeks):
                for si in s_indices:
                    if xs[f][w][si] != 0:
                        all_vars.append(xs[f][w][si])

            if not all_vars:
                continue

            _add_cardinality_constraint(
                opb, all_vars, relation, target_weeks,
                is_soft=is_soft, weight=weight, soft_violations=soft_violations,
            )

    # window_totals: count constraints over a specific window
    for total in constraint.params.get("window_totals", []):
        target_shifts = total["shifts"]
        relation = total["relation"]
        target_weeks = total["weeks"]
        window_start, window_end = total["window"]
        s_indices = [shift_idx[s] for s in target_shifts if s in shift_idx]

        for f in fellow_indices:
            window_vars = []
            for w in range(window_start, min(window_end, num_weeks)):
                for si in s_indices:
                    if xs[f][w][si] != 0:
                        window_vars.append(xs[f][w][si])

            if not window_vars:
                continue

            _add_cardinality_constraint(
                opb, window_vars, relation, target_weeks,
                is_soft=is_soft, weight=weight, soft_violations=soft_violations,
            )

    # active_blocks: conditional constraints within blocks
    for active_block in constraint.params.get("active_blocks", []):
        block_size = active_block["block_size"]
        trigger_shifts = active_block["trigger_shifts"]
        trigger_indices = [shift_idx[s] for s in trigger_shifts if s in shift_idx]

        for f in fellow_indices:
            for block_start in range(0, num_weeks, block_size):
                block_end = min(block_start + block_size, num_weeks)

                # Trigger: any trigger shift in this block
                trigger_vars = []
                for w in range(block_start, block_end):
                    for si in trigger_indices:
                        if xs[f][w][si] != 0:
                            trigger_vars.append(xs[f][w][si])

                if not trigger_vars:
                    continue

                # Create trigger indicator: trigger=1 iff any trigger_var is true
                trigger = opb.new_var()
                for tv in trigger_vars:
                    opb.weighted_sum_at_least([(trigger, 1), (-tv, 1)], 1)
                # trigger <= sum(trigger_vars): sum + (1 - trigger) >= 1
                opb.weighted_sum_at_least(
                    [(v, 1) for v in trigger_vars] + [(-trigger, 1)], 1
                )

                # If trigger=1, enforce count constraints
                for count_rule in active_block["counts"]:
                    count_shifts = count_rule["shifts"]
                    count_relation = count_rule["relation"]
                    count_weeks = count_rule["weeks"]
                    count_indices = [shift_idx[s] for s in count_shifts if s in shift_idx]

                    count_vars = []
                    for w in range(block_start, block_end):
                        for ci in count_indices:
                            if xs[f][w][ci] != 0:
                                count_vars.append(xs[f][w][ci])

                    if not count_vars:
                        continue

                    # Conditional: trigger → cardinality(count_vars, relation, count_weeks)
                    _add_conditional_cardinality(
                        opb, count_vars, count_relation, count_weeks, trigger,
                        is_soft=is_soft, weight=weight, soft_violations=soft_violations,
                    )


def _encode_ncc_stroke_oversight(opb, xs, constraint, fellow_indices, **kw):
    """At least one NCC fellow on NCC1/NCC2 each week."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]
    is_soft = constraint.strength == ConstraintStrength.SOFT
    weight = kw["config"].weekly_soft_weight
    soft_violations = kw["soft_violations"]

    s_ncc1 = shift_idx.get("NCC1")
    s_ncc2 = shift_idx.get("NCC2")

    for w in range(num_weeks):
        ncc_vars = []
        for f in fellow_indices:
            if s_ncc1 is not None and xs[f][w][s_ncc1] != 0:
                ncc_vars.append(xs[f][w][s_ncc1])
            if s_ncc2 is not None and xs[f][w][s_ncc2] != 0:
                ncc_vars.append(xs[f][w][s_ncc2])

        if not ncc_vars:
            continue

        if is_soft:
            v = opb.new_var()
            opb.at_least_k(ncc_vars + [v], 1)
            for nv in ncc_vars:
                opb.at_most_k([v, nv], 1)
            soft_violations.append((v, weight))
        else:
            opb.at_least_k(ncc_vars, 1)


def _encode_comparable_half_year(opb, xs, constraint, fellow_indices, **kw):
    """MICU and NCC+Swing shouldn't differ by more than 4 between halves."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]
    is_soft = constraint.strength == ConstraintStrength.SOFT
    weight = kw["config"].weekly_soft_weight
    soft_violations = kw["soft_violations"]

    half = num_weeks // 2

    # For MICU
    s_micu = shift_idx.get("MICU")
    if s_micu is not None:
        for f in fellow_indices:
            first_half = [xs[f][w][s_micu] for w in range(half) if xs[f][w][s_micu] != 0]
            second_half = [xs[f][w][s_micu] for w in range(half, num_weeks) if xs[f][w][s_micu] != 0]
            _encode_balance_constraint(
                opb, first_half, second_half, 4,
                is_soft=is_soft, weight=weight, soft_violations=soft_violations,
            )

    # For NCC+Swing
    ncc_shifts = [shift_idx.get(s) for s in ("NCC1", "NCC2", "Swing") if shift_idx.get(s) is not None]
    for f in fellow_indices:
        first_half = []
        second_half = []
        for w in range(half):
            for si in ncc_shifts:
                if xs[f][w][si] != 0:
                    first_half.append(xs[f][w][si])
        for w in range(half, num_weeks):
            for si in ncc_shifts:
                if xs[f][w][si] != 0:
                    second_half.append(xs[f][w][si])
        _encode_balance_constraint(
            opb, first_half, second_half, 4,
            is_soft=is_soft, weight=weight, soft_violations=soft_violations,
        )


def _encode_stroke_shift_coverage(opb, xs, constraint, fellow_indices, **kw):
    """Exactly one Stroke and one Telestroke/Clinic per week."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]
    num_fellows = kw["num_fellows"]
    is_soft = constraint.strength == ConstraintStrength.SOFT
    weight = kw["config"].weekly_soft_weight
    soft_violations = kw["soft_violations"]

    s_stroke = shift_idx.get("Stroke")
    s_tele = shift_idx.get("Telestroke/Clinic")

    for w in range(num_weeks):
        if s_stroke is not None:
            stroke_vars = [xs[f][w][s_stroke] for f in range(num_fellows) if xs[f][w][s_stroke] != 0]
            if stroke_vars:
                _add_cardinality_constraint(
                    opb, stroke_vars, "exactly", 1,
                    is_soft=is_soft, weight=weight, soft_violations=soft_violations,
                )
        if s_tele is not None:
            tele_vars = [xs[f][w][s_tele] for f in range(num_fellows) if xs[f][w][s_tele] != 0]
            if tele_vars:
                _add_cardinality_constraint(
                    opb, tele_vars, "exactly", 1,
                    is_soft=is_soft, weight=weight, soft_violations=soft_violations,
                )


def _encode_nir_one_week_per_half(opb, xs, constraint, fellow_indices, **kw):
    """Each fellow does exactly 1 NIR week per half-year."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]

    s_nir = shift_idx.get("NIR")
    if s_nir is None:
        return

    half = num_weeks // 2
    for f in fellow_indices:
        first = [xs[f][w][s_nir] for w in range(half) if xs[f][w][s_nir] != 0]
        second = [xs[f][w][s_nir] for w in range(half, num_weeks) if xs[f][w][s_nir] != 0]
        if first:
            opb.exactly_k(first, 1)
        if second:
            opb.exactly_k(second, 1)


def _encode_scvmc_second_half(opb, xs, constraint, fellow_indices, **kw):
    """Each fellow does exactly 2 SCVMC Rehab weeks in second half."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]

    si = shift_idx.get("SCVMC Rehab")
    if si is None:
        return

    half = num_weeks // 2
    for f in fellow_indices:
        second = [xs[f][w][si] for w in range(half, num_weeks) if xs[f][w][si] != 0]
        if second:
            opb.exactly_k(second, 2)


def _encode_stroke_no_block_one_ncc(opb, xs, constraint, fellow_indices, **kw):
    """Stroke fellows don't do NCC1/NCC2 in block 1 (weeks 0-3)."""
    shift_idx = kw["shift_idx"]
    is_soft = constraint.strength == ConstraintStrength.SOFT
    weight = kw["config"].weekly_soft_weight
    soft_violations = kw["soft_violations"]

    for s_name in ("NCC1", "NCC2"):
        si = shift_idx.get(s_name)
        if si is None:
            continue
        for f in fellow_indices:
            for w in range(4):
                if xs[f][w][si] != 0:
                    if is_soft:
                        soft_violations.append((xs[f][w][si], weight))
                    else:
                        opb.add_unit(-xs[f][w][si])

    # Also block Swing in first 4 weeks (always hard in original)
    s_swing = shift_idx.get("Swing")
    if s_swing is not None:
        for f in fellow_indices:
            for w in range(4):
                if xs[f][w][s_swing] != 0:
                    opb.add_unit(-xs[f][w][s_swing])


def _encode_fourth_block_two_micu(opb, xs, constraint, fellow_indices, **kw):
    """Weeks 12-15 must have exactly 2 fellows on MICU."""
    shift_idx = kw["shift_idx"]
    num_fellows = kw["num_fellows"]

    s_micu = shift_idx.get("MICU")
    if s_micu is None:
        return

    for w in range(12, 16):
        micu_vars = [xs[f][w][s_micu] for f in fellow_indices if xs[f][w][s_micu] != 0]
        if micu_vars:
            opb.exactly_k(micu_vars, 2)


def _encode_isc(opb, xs, constraint, fellow_indices, **kw):
    """ISC is assigned in exactly one specific week."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]

    si = shift_idx.get("ISC")
    if si is None:
        return

    # Determine target week from params
    target_date = tuple(constraint.params.get("date", (2026, 2, 5)))
    from scheduler.date_to_week_index import date_to_week_index
    target_week = date_to_week_index(target_date)

    for f in fellow_indices:
        for w in range(num_weeks):
            if xs[f][w][si] != 0:
                if w == target_week:
                    opb.add_unit(xs[f][w][si])
                else:
                    opb.add_unit(-xs[f][w][si])


def _encode_specific_assignment(opb, xs, constraint, fellow_indices, **kw):
    """A specific shift must be filled by someone from fellow_indices in a given week."""
    shift_idx = kw["shift_idx"]
    is_soft = constraint.strength == ConstraintStrength.SOFT
    weight = kw["config"].weekly_soft_weight
    soft_violations = kw["soft_violations"]

    if not constraint.shifts or not constraint.weeks:
        return

    shift_name = constraint.shifts.shifts[0]
    si = shift_idx.get(shift_name)
    week = constraint.weeks.start
    if si is None:
        return

    vars_for_week = [xs[f][week][si] for f in fellow_indices if xs[f][week][si] != 0]
    if not vars_for_week:
        return

    if is_soft:
        v = opb.new_var()
        opb.at_least_k(vars_for_week + [v], 1)
        for vw in vars_for_week:
            opb.at_most_k([v, vw], 1)
        soft_violations.append((v, weight))
    else:
        opb.at_least_k(vars_for_week, 1)


# ---------------------------------------------------------------------------
# Backup role encoders
# ---------------------------------------------------------------------------

def _backup_eligible_shift_indices(
    group_of: dict[int, str], shift_idx: dict[str, int]
) -> dict[int, list[int]]:
    """Per-fellow-index list of shift var indices that make them backup-eligible.

    NCC_JR/NCC_SR -> Elec; STROKE -> Clinic/Elective or Telestroke/Clinic.
    Fellows not in a backup group get an empty list (no eligibility).
    """
    ncc_si = [shift_idx[s] for s in _BACKUP_ELIGIBLE_SHIFTS_NCC if s in shift_idx]
    stroke_si = [shift_idx[s] for s in _BACKUP_ELIGIBLE_SHIFTS_STROKE if s in shift_idx]
    out: dict[int, list[int]] = {}
    for f, grp in group_of.items():
        if grp in ("NCC_JR", "NCC_SR"):
            out[f] = ncc_si
        elif grp == "STROKE":
            out[f] = stroke_si
    return out


def _backup_group_of(config: "ScheduleSolverConfig", fellow_names: list[str]) -> dict[int, str]:
    """Map fellow index -> backup group name for fellows in a backup group."""
    name_to_group: dict[str, str] = {}
    for grp in _BACKUP_GROUPS:
        for name in config.fellow_groups.get(grp, []):
            name_to_group[name] = grp
    return {f: name_to_group[name] for f, name in enumerate(fellow_names)
            if name in name_to_group}


def _allocate_backup_vars(
    opb: OpbBuilder, config: "ScheduleSolverConfig", fellow_names: list[str]
) -> list[list[dict[int, int]]]:
    """Allocate bk[w][kind][f] for backup-eligible fellows (NCC_JR/NCC_SR/STROKE).

    Static (group) eligibility only; the weekday-shift gating is a dynamic
    constraint added in _encode_backup_constraints. A Weekend Backup var is
    allocated only for weeks that contain a weekend day.
    """
    num_weeks = config.num_weeks
    start_dow = config.start_dow
    num_days = config.num_days
    group_of = _backup_group_of(config, fellow_names)
    bk: list[list[dict[int, int]]] = []
    for w in range(num_weeks):
        sat_day = _week_day(w, 5, start_dow)
        week_has_weekend = 0 <= sat_day < num_days
        weekday_vars = {f: opb.new_var() for f in group_of}
        weekend_vars = {f: opb.new_var() for f in group_of} if week_has_weekend else {}
        bk.append([weekday_vars, weekend_vars])
    return bk


def _encode_backup_constraints(
    opb: OpbBuilder,
    bk: list[list[dict[int, int]]],
    wr: list[list[dict[int, int]]],
    xs: list[list[list[int]]],
    config: "ScheduleSolverConfig",
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
) -> None:
    """Encode the Backup / Weekend Backup roles.

    - Shift gating (hard): a backup var implies the fellow is on an eligible
      weekday shift that week (NCC->Elec; STROKE->Clinic/Elective|Telestroke/Clinic).
    - Weekend Backup excludes weekend-call-role holders (hard).
    - Coverage (hard): exactly one weekday Backup and one Weekend Backup per week.
    - Max 2 consecutive backup weeks per fellow (hard), over the combined
      (weekday OR weekend) backup indicator.
    """
    num_weeks = config.num_weeks
    group_of = _backup_group_of(config, fellow_names)
    eligible_shifts = _backup_eligible_shift_indices(group_of, shift_idx)
    # Holiday weeks: every backup-eligible fellow is gated on Telestroke/Clinic
    # only (overrides the per-group eligible-shift set).
    holiday_si = [shift_idx[s] for s in _BACKUP_HOLIDAY_SHIFTS if s in shift_idx]

    opb.add_comment("Backup: weekday-shift gating + weekend-role exclusion")
    for w in range(num_weeks):
        # Forbidden weeks (e.g. week 0 orientation): no backup at all.
        if w in _BACKUP_FORBIDDEN_WEEKS:
            for kind in (_BACKUP_WEEKDAY, _BACKUP_WEEKEND):
                for bk_var in bk[w][kind].values():
                    opb.add_unit(-bk_var)
            continue
        is_holiday = w in _BACKUP_HOLIDAY_WEEKS
        for kind in (_BACKUP_WEEKDAY, _BACKUP_WEEKEND):
            for f, bk_var in bk[w][kind].items():
                allowed_si = holiday_si if is_holiday else eligible_shifts.get(f, [])
                shift_vars = [xs[f][w][si] for si in allowed_si if xs[f][w][si] != 0]
                if not shift_vars:
                    # No eligible weekday shift available -> cannot be backup.
                    opb.add_unit(-bk_var)
                    continue
                # bk_var -> OR(shift_vars): sum(shift_vars) + (1 - bk_var) >= 1
                opb.weighted_sum_at_least(
                    [(v, 1) for v in shift_vars] + [(-bk_var, 1)], 1
                )
        # Weekend Backup excludes any weekend call role that week.
        for f, wb_var in bk[w][_BACKUP_WEEKEND].items():
            for role_idx in range(3):
                if f in wr[w][role_idx]:
                    opb.at_most_k([wb_var, wr[w][role_idx][f]], 1)

    # Hard coverage: exactly one weekday Backup + one Weekend Backup per week,
    # EXCEPT week 0 — the Stroke fellows are pinned to orientation Elec there, so
    # no fellow is backup-eligible (mirrors the NCC2/Telestroke week-0 exemption).
    opb.add_comment("Backup: hard coverage (exactly one weekday + one weekend per week, week 0 exempt)")
    for w in range(num_weeks):
        if w < _BACKUP_COVERAGE_FIRST_WEEK:
            continue
        wd_vars = list(bk[w][_BACKUP_WEEKDAY].values())
        if wd_vars:
            opb.exactly_one(wd_vars)
        we_vars = list(bk[w][_BACKUP_WEEKEND].values())
        if we_vars:
            opb.exactly_one(we_vars)

    opb.add_comment(f"Backup: max {_BACKUP_MAX_CONSECUTIVE_WEEKS} consecutive backup weeks")
    # on_backup[w][f] = OR(weekday backup, weekend backup) for fellow f, week w.
    on_backup: list[dict[int, int]] = [{} for _ in range(num_weeks)]
    for w in range(num_weeks):
        for f in group_of:
            parts = []
            if f in bk[w][_BACKUP_WEEKDAY]:
                parts.append(bk[w][_BACKUP_WEEKDAY][f])
            if f in bk[w][_BACKUP_WEEKEND]:
                parts.append(bk[w][_BACKUP_WEEKEND][f])
            if not parts:
                continue
            if len(parts) == 1:
                on_backup[w][f] = parts[0]
            else:
                aux = opb.new_var()
                for p in parts:
                    opb.weighted_sum_at_least([(aux, 1), (-p, 1)], 1)
                opb.weighted_sum_at_least([(v, 1) for v in parts] + [(-aux, 1)], 1)
                on_backup[w][f] = aux
    win = _BACKUP_MAX_CONSECUTIVE_WEEKS + 1
    for f in group_of:
        for w_start in range(num_weeks - win + 1):
            window = [on_backup[w_start + o][f] for o in range(win)
                      if f in on_backup[w_start + o]]
            if len(window) > _BACKUP_MAX_CONSECUTIVE_WEEKS:
                opb.at_most_k(window, _BACKUP_MAX_CONSECUTIVE_WEEKS)


# ---------------------------------------------------------------------------
# Weekend constraint encoders
# ---------------------------------------------------------------------------

def _encode_buffered_consecutive_pair(
    opb: OpbBuilder,
    f: int,
    w: int,
    v_w: int,
    v_w1: int,
    work_by_week: dict[int, int],
    xs: list[list[list[int]]],
    buffer_indices: list[int],
    num_weeks: int,
    *,
    hard: bool,
    weight: int,
    soft_violations: list[tuple[int, int]],
) -> None:
    """Handle the consecutive weekend pair (w, w+1) for fellow ``f``.

    A pair is BUFFERED — exempt from penalty (soft) / permitted (hard) — when the
    fellow never works a long unbroken stretch:
      * no weekend role in week w-1 (a weekend off before the pair), AND
      * a light rotation breaks the run: week w+2 weekday service is light, OR
        the MIDDLE week w+1 weekday service is itself light
        (CONSECUTIVE_WEEKEND_BUFFER_SHIFTS).

    The middle-week case matters because a light week w+1 means the fellow isn't
    working those intervening weekdays, so the run is already broken without a
    w+2 buffer. Blank weeks never count as light: a locked fellow's blank week
    has all shift vars forbidden (filtered out below), so it offers no buffer var.

    A pair is UN-buffered when it is active AND
        ( worked weekend w-1 )  OR  ( no light week in {w+1, w+2} ).

    hard=True forbids the un-buffered case; hard=False penalizes it once
    (one slack of *weight* per un-buffered pair).
    """
    # Light-shift vars that can break the run: middle week (w+1) or after (w+2).
    light_vars: list[int] = []
    for wk in (w + 1, w + 2):
        if 0 <= wk < num_weeks:
            light_vars += [xs[f][wk][si] for si in buffer_indices if xs[f][wk][si] != 0]
    prev = work_by_week.get(w - 1)

    if hard:
        # Forbid the two un-buffered combinations.
        # A) no weekend-off before: work[w-1] AND work[w] AND work[w+1] forbidden.
        if prev is not None:
            opb.at_most_k([prev, v_w, v_w1], 2)
        # B) pair requires a light week in {w+1, w+2}.
        if not light_vars:
            opb.at_most_k([v_w, v_w1], 1)
        else:
            # work[w] AND work[w+1] -> OR(light): +1 ~v_w +1 ~v_w1 + sum(light) >= 1
            opb.weighted_sum_at_least(
                [(-v_w, 1), (-v_w1, 1)] + [(b, 1) for b in light_vars], 1
            )
        return

    # Soft: one penalty var, forced true when the pair is active and un-buffered.
    p = opb.new_var()
    soft_violations.append((p, weight))
    # Condition A (no weekend-off before): p=1 when work[w-1]&work[w]&work[w+1].
    #   p + ~work[w-1] + ~work[w] + ~work[w+1] >= 1
    #   (= p - work[w-1] - work[w] - work[w+1] >= -2)
    if prev is not None:
        opb.weighted_sum_at_least([(p, 1), (-prev, 1), (-v_w, 1), (-v_w1, 1)], 1)
    # Condition B (no light week in {w+1, w+2}): p=1 when pair active & no light.
    #   p + sum(light) + ~work[w] + ~work[w+1] >= 1
    opb.weighted_sum_at_least(
        [(p, 1)] + [(b, 1) for b in light_vars] + [(-v_w, 1), (-v_w1, 1)], 1
    )


def _encode_weekend_constraints(
    opb: OpbBuilder,
    wr: list[list[dict[int, int]]],
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_mapping: FellowMapping,
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
) -> None:
    """Encode all weekend assignment constraints."""
    num_weeks = config.num_weeks
    num_fellows = len(fellow_names)
    wk_config = config.weekend_config

    opb.add_comment("Weekend: exactly one fellow per role per week")
    _diag_wk_coverage = os.environ.get("SCHED_DIAG_WEEKEND_COVERAGE")
    _diag_cov_weight = int(os.environ.get("SCHED_DIAG_COVERAGE_WEIGHT", "100000"))
    for w in range(num_weeks):
        for role_idx in range(3):
            role_vars = list(wr[w][role_idx].values())
            if role_vars:
                if _diag_wk_coverage == "relax":
                    opb.at_most_k(role_vars, 1)
                elif _diag_wk_coverage == "soft":
                    u = opb.new_var()
                    opb.weighted_sum_at_least([(v, 1) for v in role_vars] + [(u, 1)], 1)
                    opb.at_most_k(role_vars, 1)
                    soft_violations.append((u, _diag_cov_weight))
                else:
                    opb.exactly_one(role_vars)

    # All-different: no fellow fills two weekend roles in same week
    opb.add_comment("Weekend: distinct assignments per week")
    for w in range(num_weeks):
        for f in range(num_fellows):
            vars_for_f = [wr[w][role_idx][f] for role_idx in range(3) if f in wr[w][role_idx]]
            if len(vars_for_f) > 1:
                opb.at_most_k(vars_for_f, 1)

    # Weekend eligibility gated by weekly shift (dynamic)
    opb.add_comment("Weekend: dynamic eligibility based on weekly shift")
    _encode_weekend_eligibility(opb, wr, xs, config, fellow_names, shift_idx)

    # Weekend totals per fellow: HARD within a tolerance band [N-tol, N+tol].
    # Previously these were soft "exactly N", but the soft-exactly encoding uses a
    # single flat violation indicator (off-by-1 costs the same as off-by-32), so it
    # provided no gradient toward the target — the optimizer dumped nearly all
    # weekends onto a few always-eligible STROKE fellows and left NCC fellows at ~0.
    # A hard band guarantees the intended distribution; the targets sum to slightly
    # more than the weekend-role demand, so the band has room to satisfy coverage.
    # plus a small deviation-scaled soft penalty toward the exact target inside
    # the band (capped at tol slacks, since the hard band already bounds the count).
    tol = config.weekend_total_tolerance
    wknd_weight = config.weekly_soft_weight
    def _weekend_total_band(role_vars: list[int], total: int) -> None:
        """Enforce a per-fellow weekend-role total. A target of 0 is a HARD
        at-most-0 (forbid the role entirely — no +/-tol band, since 'zero' must
        mean zero). Positive targets use the hard [total-tol, total+tol] band
        plus a deviation-scaled soft nudge toward the exact target."""
        if not role_vars:
            return
        if total <= 0:
            opb.at_most_k(role_vars, 0)
            return
        lo = max(0, total - tol)
        if lo > 0:
            opb.at_least_k(role_vars, min(lo, len(role_vars)))
        opb.at_most_k(role_vars, total + tol)
        _add_cardinality_constraint(
            opb, role_vars, "exactly", total, is_soft=True, weight=wknd_weight,
            soft_violations=soft_violations, max_violation=tol,
        )

    def _ncc_vars_for(fi: int) -> list[int]:
        out = []
        for w in range(num_weeks):
            if fi in wr[w][_ROLE_NCC1]:
                out.append(wr[w][_ROLE_NCC1][fi])
            if fi in wr[w][_ROLE_NCC2]:
                out.append(wr[w][_ROLE_NCC2][fi])
        return out

    opb.add_comment(f"Weekend: NCC totals per fellow (hard within {tol}; 0 = hard zero)")
    for fellow_name, total in wk_config.ncc_totals.items():
        if fellow_name not in fellow_names:
            continue
        _weekend_total_band(_ncc_vars_for(fellow_names.index(fellow_name)), total)

    # Per-fellow weekend-NCC RANGES (proportionate model, e.g. CCM). Hard
    # [lo, hi] band — no tolerance, no soft nudge (any value in band is fine).
    if wk_config.ncc_ranges:
        opb.add_comment("Weekend: NCC per-fellow ranges (proportionate, hard band)")
        for fellow_name, (lo, hi) in wk_config.ncc_ranges.items():
            if fellow_name not in fellow_names:
                continue
            ncc_vars = _ncc_vars_for(fellow_names.index(fellow_name))
            if not ncc_vars:
                continue
            if lo > 0:
                opb.at_least_k(ncc_vars, min(lo, len(ncc_vars)))
            if hi < len(ncc_vars):
                opb.at_most_k(ncc_vars, hi)

    # Group-sum constraints: a range group's combined weekend-NCC total is exact.
    if wk_config.ncc_group_sums:
        opb.add_comment("Weekend: NCC group-sum (range groups hit combined total)")
        for group_fellows, total in wk_config.ncc_group_sums:
            group_vars: list[int] = []
            for fellow_name in group_fellows:
                if fellow_name in fellow_names:
                    group_vars.extend(_ncc_vars_for(fellow_names.index(fellow_name)))
            if group_vars:
                opb.exactly_k(group_vars, total)

    opb.add_comment(f"Weekend: Stroke totals per fellow (hard within {tol}; 0 = hard zero)")
    for fellow_name, total in wk_config.stroke_totals.items():
        if fellow_name not in fellow_names:
            continue
        fi = fellow_names.index(fellow_name)
        stroke_vars = [wr[w][_ROLE_STROKE][fi] for w in range(num_weeks) if fi in wr[w][_ROLE_STROKE]]
        _weekend_total_band(stroke_vars, total)

    # Stroke cohort bounds
    if wk_config.stroke_cohort:
        opb.add_comment("Weekend: Stroke cohort bounds")
        cohort_vars_all = []
        for fellow_name in wk_config.stroke_cohort:
            if fellow_name not in fellow_names:
                continue
            fi = fellow_names.index(fellow_name)
            fellow_stroke_vars = [wr[w][_ROLE_STROKE][fi] for w in range(num_weeks) if fi in wr[w][_ROLE_STROKE]]
            if fellow_stroke_vars:
                opb.at_least_k(fellow_stroke_vars, wk_config.stroke_cohort_min)
                opb.at_most_k(fellow_stroke_vars, wk_config.stroke_cohort_max)
                cohort_vars_all.extend(fellow_stroke_vars)
        if wk_config.stroke_cohort_total is not None and cohort_vars_all:
            opb.exactly_k(cohort_vars_all, wk_config.stroke_cohort_total)

    # Spacing: no two consecutive weekends, at most 2 in 4 weeks
    opb.add_comment("Weekend: spacing constraints")
    for f in range(num_fellows):
        # work[w] = OR(any weekend role in week w)
        work_vars = []
        for w in range(num_weeks):
            roles_for_f = [wr[w][r][f] for r in range(3) if f in wr[w][r]]
            if not roles_for_f:
                continue
            if len(roles_for_f) == 1:
                work_vars.append((w, roles_for_f[0]))
            else:
                aux = opb.new_var()
                for rv in roles_for_f:
                    opb.weighted_sum_at_least([(aux, 1), (-rv, 1)], 1)
                # aux <= sum(roles): sum(roles) + (1 - aux) >= 1
                opb.weighted_sum_at_least([(v, 1) for v in roles_for_f] + [(-aux, 1)], 1)
                work_vars.append((w, aux))

        # No two consecutive weekends, BUFFERED. A blanket hard rule is
        # infeasible on the production workbook (Slurm SAT bisect, 2026-06-05),
        # so the hard form permits a pair (w, w+1) only when it is buffered on
        # both sides — the fellow had no weekend role in week w-1 AND their
        # week w+2 weekday service is "light" (CONSECUTIVE_WEEKEND_BUFFER_SHIFTS).
        # That caps the run at Mon(w)->Sun(w+1) = 14 days. Soft mode (config flag
        # False or SCHED_DIAG_CONSECUTIVE=soft) degrades to a flat per-pair
        # penalty. SCHED_DIAG_CONSECUTIVE=hard forces the buffered hard rule.
        _diag_consec = os.environ.get("SCHED_DIAG_CONSECUTIVE")
        if _diag_consec == "soft":
            consecutive_hard = False
        elif _diag_consec == "hard":
            consecutive_hard = True
        else:
            consecutive_hard = config.weekend_consecutive_hard

        work_by_week = dict(work_vars)
        buffer_indices = [shift_idx[s] for s in CONSECUTIVE_WEEKEND_BUFFER_SHIFTS if s in shift_idx]
        # HARD every-other-weekend for flagged fellows (e.g. CCM): a strict
        # at_most-1 over each adjacent weekend pair, NO buffer exemption. Only
        # feasible with proportionate weekend ranges (see ncc_ranges).
        eow = fellow_names[f] in wk_config.every_other_weekend_fellows
        for i in range(len(work_vars) - 1):
            w1, v1 = work_vars[i]
            w2, v2 = work_vars[i + 1]
            if w2 - w1 != 1:
                continue
            if eow:
                opb.at_most_k([v1, v2], 1)
                continue
            # Both modes route through the buffer-aware helper: soft penalizes an
            # un-buffered pair once; hard forbids it. A buffered pair (weekend
            # off before + a light week w+1 or w+2) is exempt either way.
            _encode_buffered_consecutive_pair(
                opb, f, w1, v1, v2, work_by_week, xs, buffer_indices, num_weeks,
                hard=consecutive_hard, weight=config.weekend_mismatch_weight,
                soft_violations=soft_violations,
            )

        # At most 2 in any 4-week window (hard — a fellow should never work 3+
        # weekends within 4 weeks).
        for i in range(len(work_vars)):
            window = [(w, v) for w, v in work_vars[i:] if w < work_vars[i][0] + 4]
            if len(window) > 2:
                _add_cardinality_constraint(
                    opb, [v for _, v in window], "at_most", 2,
                    is_soft=False, weight=config.weekend_mismatch_weight,
                    soft_violations=soft_violations,
                )

    # Weekend role prerequisites (must have done weekday service before weekend role)
    opb.add_comment("Weekend: role prerequisites (Stroke/NCC)")
    _encode_weekend_prerequisites(opb, wr, xs, config, fellow_names, shift_idx,
                                  soft_violations=soft_violations)

    # Weekend role matches weekday service (soft bonus for matching)
    opb.add_comment("Weekend: prefer role matches weekday service (soft)")
    _encode_weekend_mismatch_penalty(opb, wr, xs, config, fellow_names, shift_idx, soft_violations)

    # Penalize weekend call in the week before a vacation
    opb.add_comment("Weekend: pre-vacation weekend penalty (soft)")
    _encode_prevacation_weekend_penalty(opb, wr, xs, config, fellow_names, shift_idx, soft_violations)


def _encode_prevacation_weekend_penalty(
    opb: OpbBuilder,
    wr: list[list[dict[int, int]]],
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
) -> None:
    """Soft penalty for weekend call in the week before vacation."""
    vac_idx = shift_idx.get("Vac")
    if vac_idx is None:
        return

    num_weeks = config.num_weeks
    num_fellows = len(fellow_names)

    for f in range(num_fellows):
        for w in range(1, num_weeks):  # Start at 1 (need week w-1)
            if xs[f][w][vac_idx] == 0:
                continue
            # Fellow f has vacation in week w.
            # Penalize any weekend role in week w-1.
            for role_idx in range(3):
                if f not in wr[w - 1][role_idx]:
                    continue
                wr_var = wr[w - 1][role_idx][f]
                # Create conjunction indicator: ind = vac[w] AND wr[w-1][role][f]
                ind = opb.new_var()
                # ind >= vac + wr - 1
                opb.weighted_sum_at_most(
                    [(xs[f][w][vac_idx], 1), (wr_var, 1), (-ind, 1)], 2
                )
                # ind <= vac  (vac + ~ind >= 1)
                opb.weighted_sum_at_least(
                    [(xs[f][w][vac_idx], 1), (-ind, 1)], 1
                )
                # ind <= wr  (wr + ~ind >= 1)
                opb.weighted_sum_at_least(
                    [(wr_var, 1), (-ind, 1)], 1
                )
                soft_violations.append((ind, 1))  # weight = 1


def _encode_weekend_eligibility(
    opb: OpbBuilder,
    wr: list[list[dict[int, int]]],
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    shift_idx: dict[str, int],
) -> None:
    """Block weekend roles when fellow is on a weekend-blocking shift."""
    num_weeks = config.num_weeks

    blocked_indices = [shift_idx[s] for s in WEEKEND_BLOCKED_SHIFTS if s in shift_idx]
    num_shifts = len(shift_idx)

    for w in range(num_weeks):
        for f in range(len(fellow_names)):
            # If on a blocked shift, can't do any weekend role
            for si in blocked_indices:
                if xs[f][w][si] == 0:
                    continue
                for role_idx in range(3):
                    if f in wr[w][role_idx]:
                        # xs[f][w][blocked_shift] + wr[w][role][f] <= 1
                        opb.at_most_k([xs[f][w][si], wr[w][role_idx][f]], 1)

            # A weekend role requires SOME weekday shift that week (hard). NH
            # fellows have no full_assignment rule, so without this they could be
            # given a weekend role in a week with an entirely empty weekday
            # schedule — which can never happen in reality.
            weekday_vars = [xs[f][w][si] for si in range(num_shifts) if xs[f][w][si] != 0]
            for role_idx in range(3):
                if f not in wr[w][role_idx]:
                    continue
                wr_var = wr[w][role_idx][f]
                if not weekday_vars:
                    opb.add_unit(-wr_var)
                else:
                    # wr_var -> OR(weekday_vars): sum(weekday) + (1 - wr_var) >= 1
                    opb.weighted_sum_at_least(
                        [(v, 1) for v in weekday_vars] + [(-wr_var, 1)], 1
                    )

    # Stroke eligibility: depends on fellow classification and weekday shift
    wk_config = config.weekend_config
    s_stroke = shift_idx.get("Stroke")
    s_tele = shift_idx.get("Telestroke/Clinic")

    for w in range(num_weeks):
        for f in range(len(fellow_names)):
            if f not in wr[w][_ROLE_STROKE]:
                continue
            name = fellow_names[f]
            # Always stroke eligible: no extra constraint needed
            if name in wk_config.always_stroke_eligible:
                continue
            # Telestroke+stroke eligible: must be on Stroke or Telestroke/Clinic
            if name in wk_config.telestroke_stroke_eligible:
                enabling_vars = []
                if s_stroke is not None and xs[f][w][s_stroke] != 0:
                    enabling_vars.append(xs[f][w][s_stroke])
                if s_tele is not None and xs[f][w][s_tele] != 0:
                    enabling_vars.append(xs[f][w][s_tele])
                if enabling_vars:
                    # wr_stroke -> OR(enabling), i.e. sum(enabling) >= wr_stroke.
                    # Encoded as sum(enabling) + (1 - wr_stroke) >= 1.
                    opb.weighted_sum_at_least(
                        [(v, 1) for v in enabling_vars] + [(-wr[w][_ROLE_STROKE][f], 1)], 1
                    )
                else:
                    opb.add_unit(-wr[w][_ROLE_STROKE][f])
                continue
            # Stroke only eligible: must be on Stroke
            if name in wk_config.stroke_only_eligible:
                if s_stroke is not None and xs[f][w][s_stroke] != 0:
                    # wr_stroke <= xs_stroke, encoded xs_stroke + (1 - wr_stroke) >= 1
                    opb.weighted_sum_at_least(
                        [(xs[f][w][s_stroke], 1), (-wr[w][_ROLE_STROKE][f], 1)], 1
                    )
                else:
                    opb.add_unit(-wr[w][_ROLE_STROKE][f])


def _encode_weekend_mismatch_penalty(
    opb: OpbBuilder,
    wr: list[list[dict[int, int]]],
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
) -> None:
    """Penalize weekend role not matching weekday service."""
    num_weeks = config.num_weeks
    weight = config.weekend_mismatch_weight

    # Matching: Weekend NCC1 ↔ NCC1, Weekend NCC2 ↔ NCC2, Weekend Stroke ↔ Stroke
    role_to_shift = {
        _ROLE_NCC1: shift_idx.get("NCC1"),
        _ROLE_NCC2: shift_idx.get("NCC2"),
        _ROLE_STROKE: shift_idx.get("Stroke"),
    }

    for w in range(num_weeks):
        for role_idx, si in role_to_shift.items():
            if si is None:
                continue
            for f, wr_var in wr[w][role_idx].items():
                weekday_var = xs[f][w][si]
                if weekday_var == 0:
                    # Fellow can't be on matching weekday shift → always a mismatch if assigned
                    soft_violations.append((wr_var, weight))
                else:
                    # Mismatch = wr_var AND NOT weekday_var
                    mismatch = opb.new_var()
                    # mismatch >= wr_var - weekday_var: wr + ~weekday + ~mismatch <= 2
                    opb.weighted_sum_at_most([(wr_var, 1), (-weekday_var, 1), (-mismatch, 1)], 2)
                    # mismatch <= wr_var  (wr + ~mismatch >= 1)
                    opb.weighted_sum_at_least([(wr_var, 1), (-mismatch, 1)], 1)
                    # mismatch <= ~weekday_var  (~weekday + ~mismatch >= 1)
                    opb.weighted_sum_at_least([(-weekday_var, 1), (-mismatch, 1)], 1)
                    soft_violations.append((mismatch, weight))


# ---------------------------------------------------------------------------
# Night constraint encoders
# ---------------------------------------------------------------------------

def _encode_nhs_week_nights(
    opb: OpbBuilder,
    xn: list[list[int]],
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
) -> None:
    """NHS week: a fellow on NHS may work ONLY Monday + Tuesday night that week.

    Mon/Tue night each get a soft NHS_NIGHT_PENALTY_WEIGHT penalty (they SHOULDN'T
    have to, but may); Wed/Thu/Fri/Sat/Sun nights of the NHS week are hard-
    forbidden. Gated on the NHS shift var, so only whoever is on NHS is affected.
    """
    nhs_si = shift_idx.get("NHS")
    if nhs_si is None:
        return
    num_weeks = config.num_weeks
    start_dow = config.start_dow
    num_days = config.num_days
    for w in range(num_weeks):
        for f in range(len(fellow_names)):
            nhs_var = xs[f][w][nhs_si]
            if nhs_var == 0:
                continue
            for dow in range(7):
                d = _week_day(w, dow, start_dow)
                if d < 0 or d >= num_days or xn[d][f] == 0:
                    continue
                if dow in (0, 1):  # Mon/Tue: allowed, soft penalty when both hold
                    pen = opb.new_var()
                    # pen = nhs AND night: pen >= nhs + night - 1
                    opb.weighted_sum_at_most([(nhs_var, 1), (xn[d][f], 1), (-pen, 1)], 2)
                    opb.weighted_sum_at_least([(nhs_var, 1), (-pen, 1)], 1)
                    opb.weighted_sum_at_least([(xn[d][f], 1), (-pen, 1)], 1)
                    soft_violations.append((pen, NHS_NIGHT_PENALTY_WEIGHT))
                else:  # Wed-Sun: hard-forbidden
                    opb.at_most_k([nhs_var, xn[d][f]], 1)


def _encode_pre_aan_forbid(
    opb: OpbBuilder,
    xn: list[list[int]],
    wr: list[list[dict[int, int]]],
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    shift_idx: dict[str, int],
) -> None:
    """Week before AAN: a fellow on AAN in week A is hard-forbidden, in week A-1,
    from any weekend role (Weekend NCC1/NCC2/Stroke) and from Fri/Sat/Sun nights.

    AAN happens early in its week, so the fellow must be free the preceding
    weekend to travel. Gated on the AAN shift var in week A.
    """
    aan_si = shift_idx.get("AAN")
    if aan_si is None:
        return
    num_weeks = config.num_weeks
    start_dow = config.start_dow
    num_days = config.num_days
    for w in range(1, num_weeks):  # w = AAN week; w-1 = week before
        for f in range(len(fellow_names)):
            aan_var = xs[f][w][aan_si]
            if aan_var == 0:
                continue
            prev = w - 1
            # Weekend roles in week w-1.
            for role_idx in range(3):
                if f in wr[prev][role_idx]:
                    opb.at_most_k([aan_var, wr[prev][role_idx][f]], 1)
            # Fri/Sat/Sun nights of week w-1.
            for dow in (4, 5, 6):
                d = _week_day(prev, dow, start_dow)
                if 0 <= d < num_days and xn[d][f] != 0:
                    opb.at_most_k([aan_var, xn[d][f]], 1)


def _encode_night_constraints(
    opb: OpbBuilder,
    xn: list[list[int]],
    xs: list[list[list[int]]],
    wr: list[list[dict[int, int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
) -> None:
    """Encode all night assignment constraints."""
    num_days = config.num_days
    start_dow = config.start_dow
    num_weeks = _num_weeks_for(start_dow, num_days)
    num_fellows = len(fellow_names)
    night_config = config.night_config
    weights = config.night_weights
    hard_criteria = config.night_hard_criteria

    holiday_set = set(holiday_indices_for_config(night_config))

    # Exactly one fellow per night (hard coverage)
    opb.add_comment("Night: exactly one fellow per night")
    _diag_coverage = os.environ.get("SCHED_DIAG_NIGHT_COVERAGE")
    _diag_cov_weight = int(os.environ.get("SCHED_DIAG_COVERAGE_WEIGHT", "100000"))
    for d in range(num_days):
        active = [xn[d][f] for f in range(num_fellows) if xn[d][f] != 0]
        if active:
            if _diag_coverage == "relax":
                # DIAGNOSTIC: relax coverage to at_most_one (no forced coverage).
                # If this turns the night layer SAT, the binding constraint is
                # genuinely the per-night coverage requirement vs. the eligible pool.
                opb.at_most_k(active, 1)
            elif _diag_coverage == "soft":
                # DIAGNOSTIC: soft coverage. slack u=1 means night d is left
                # uncovered, at a large penalty. Minimizing the penalty reveals
                # the minimum coverage deficit and which nights are uncoverable.
                u = opb.new_var()
                opb.weighted_sum_at_least([(v, 1) for v in active] + [(u, 1)], 1)
                opb.at_most_k(active, 1)
                soft_violations.append((u, _diag_cov_weight))
            else:
                opb.exactly_one(active)

    # Night blocking based on weekly shift (dynamic)
    # Vacation blocks ALL 7 nights; other blocked services only block Sun-Thu
    # (nights where the fellow works the next morning).
    opb.add_comment("Night: service-based blocking (vacation = all nights)")
    vac_idx = shift_idx.get("Vac")
    all_week_blocked = [shift_idx[s] for s in NIGHT_BLOCKED_ALL_WEEK if s in shift_idx]
    weekday_only_blocked = [shift_idx[s] for s in NIGHT_BLOCKED_SHIFTS if s in shift_idx and s not in NIGHT_BLOCKED_ALL_WEEK]
    s_isc = shift_idx.get("ISC")

    for d in range(num_days):
        week_idx = _day_to_week(d, start_dow)
        dow = _day_of_week(d, start_dow)
        next_day_week = _day_to_week(d + 1, start_dow) if d + 1 < num_days else week_idx
        for f in range(num_fellows):
            if xn[d][f] == 0:
                continue

            # MICU/SICU/Vac block ALL 7 nights of the week
            for si in all_week_blocked:
                if xs[f][week_idx][si] != 0:
                    opb.at_most_k([xs[f][week_idx][si], xn[d][f]], 1)

            # Other blocked services: only Sun-Thu nights (next morning is a workday)
            if dow not in (4, 5):  # Skip Fri/Sat nights
                for si in weekday_only_blocked:
                    if xs[f][next_day_week][si] != 0:
                        opb.at_most_k([xs[f][next_day_week][si], xn[d][f]], 1)

                # ISC: blocked Sun-Thu only (next morning is an ISC workday)
                if s_isc is not None and xs[f][next_day_week][s_isc] != 0:
                    opb.at_most_k([xs[f][next_day_week][s_isc], xn[d][f]], 1)

            # Holiday: only NCC1/NCC2/Stroke can work holidays
            if d in holiday_set:
                holiday_shifts = [shift_idx[s] for s in HOLIDAY_ELIGIBLE_SHIFTS if s in shift_idx]
                eligible_vars = [xs[f][week_idx][si] for si in holiday_shifts if xs[f][week_idx][si] != 0]
                if eligible_vars:
                    # xn[d][f] -> OR(eligible): sum(eligible) + (1 - xn) >= 1
                    opb.weighted_sum_at_least(
                        [(v, 1) for v in eligible_vars] + [(-xn[d][f], 1)], 1
                    )
                else:
                    opb.add_unit(-xn[d][f])

    # NHS week: only Mon+Tue night (soft 100); Wed-Sun hard-forbidden.
    opb.add_comment("Night: NHS week (Mon/Tue soft, Wed-Sun forbidden)")
    _encode_nhs_week_nights(opb, xn, xs, config, fellow_names, shift_idx, soft_violations)

    # Week before AAN: forbid weekend roles + Fri/Sat/Sun nights.
    opb.add_comment("Night/Weekend: forbid the weekend before AAN")
    _encode_pre_aan_forbid(opb, xn, wr, xs, config, fellow_names, shift_idx)

    # First-week restriction: NCC_JR and STROKE fellows blocked until first Friday
    opb.add_comment("Night: first-week restriction for NCC_JR and STROKE")
    first_friday = next((d for d in range(num_days) if _day_of_week(d, start_dow) == 4), None)
    if first_friday is not None:
        restricted_fellows = set()
        for group_name, fellows in config.fellow_groups.items():
            if group_name in ("NCC_JR", "STROKE"):
                for name in fellows:
                    if name in fellow_names:
                        restricted_fellows.add(fellow_names.index(name))
        for d in range(first_friday):
            for fi in restricted_fellows:
                if xn[d][fi] != 0:
                    opb.add_unit(-xn[d][fi])

    # Night requires weekly shift for fellows without full_assignment (NH).
    # Create explicit has_shift[f][w] indicator per fellow per week, then gate
    # each night variable: xn[d][f] <= has_shift[f][w].
    opb.add_comment("Night: require weekday assignment for non-full-assignment fellows")
    full_assignment_groups = set()
    for c in config.constraints:
        if c.kind == "full_assignment" and c.fellows and c.fellows.groups:
            full_assignment_groups.update(c.fellows.groups)
    non_fa_fellows = set()
    for group, fellows_list in config.fellow_groups.items():
        if group not in full_assignment_groups:
            for name in fellows_list:
                if name in fellow_names:
                    non_fa_fellows.add(fellow_names.index(name))

    for f in non_fa_fellows:
        for w in range(num_weeks):
            shift_vars = [xs[f][w][s] for s in range(len(xs[f][w])) if xs[f][w][s] != 0]
            # Collect all night vars for this fellow in this week
            night_vars_w = []
            for dow in range(7):
                d = w * 7 - start_dow + dow
                if 0 <= d < num_days and xn[d][f] != 0:
                    night_vars_w.append(xn[d][f])
            if not night_vars_w:
                continue
            if not shift_vars:
                # No shift possible this week → block all nights
                for nv in night_vars_w:
                    opb.add_unit(-nv)
            else:
                # Each night requires a weekday shift that week: nv <= sum(shifts).
                # Encoded as sum(shifts) + (1 - nv) >= 1, i.e. sum(shifts) >= nv.
                for nv in night_vars_w:
                    opb.weighted_sum_at_least(
                        [(v, 1) for v in shift_vars] + [(-nv, 1)], 1
                    )

    # Night policy criteria (soft/hard depending on config)
    opb.add_comment("Night: policy criteria (anaesthesia, clinic, stroke, sunday_following)")
    _encode_night_policy_criteria(
        opb, xn, xs, wr, config, fellow_names, shift_idx, soft_violations,
    )

    # Weekend night linking: Fri/Sat/Sun night ↔ weekend roles (soft)
    _encode_weekend_night_linking(opb, xn, wr, config, fellow_names, soft_violations)

    # Night spacing: at most maxNights in any windowDays window
    spacing_max = night_config.spacing_max_nights if hasattr(night_config, 'spacing_max_nights') else 1
    spacing_window = night_config.spacing_window_days if hasattr(night_config, 'spacing_window_days') else 3
    opb.add_comment(f"Night: at most {spacing_max} in any {spacing_window}-day window per fellow")
    _diag_no_spacing = os.environ.get("SCHED_DIAG_DISABLE_NIGHT_SPACING") == "1"
    for f in range(num_fellows) if not _diag_no_spacing else range(0):
        for start in range(num_days - spacing_window + 1):
            window_vars = [xn[start + offset][f] for offset in range(spacing_window)
                          if xn[start + offset][f] != 0]
            if len(window_vars) > spacing_max:
                opb.at_most_k(window_vars, spacing_max)

    # Night totals per fellow: soft only (service blocking makes hard infeasible)
    night_weight = config.weekly_soft_weight
    opb.add_comment("Night: total nights per fellow (soft)")
    for fellow_name, total in night_config.total_nights.items():
        if fellow_name not in fellow_names:
            continue
        fi = fellow_names.index(fellow_name)
        all_night_vars = [xn[d][fi] for d in range(num_days) if xn[d][fi] != 0]
        if all_night_vars:
            _add_cardinality_constraint(
                opb, all_night_vars, "exactly", total,
                is_soft=True, weight=night_weight, soft_violations=soft_violations,
            )

    # Friday night totals per fellow (soft)
    opb.add_comment("Night: Friday night totals per fellow (soft)")
    for fellow_name, total in night_config.friday_nights.items():
        if fellow_name not in fellow_names:
            continue
        fi = fellow_names.index(fellow_name)
        friday_vars = [xn[d][fi] for d in range(num_days) if _day_of_week(d, start_dow) == 4 and xn[d][fi] != 0]
        if friday_vars:
            _add_cardinality_constraint(
                opb, friday_vars, "exactly", total,
                is_soft=True, weight=night_weight, soft_violations=soft_violations,
            )

    # Multiset constraints (soft — distribution, not coverage)
    opb.add_comment("Night: multiset constraints (soft)")
    for multiset in night_config.total_night_multisets:
        _encode_night_multiset(opb, xn, multiset, fellow_names, num_days, start_dow,
                               friday_only=False, soft_violations=soft_violations, weight=night_weight)
    for multiset in night_config.friday_night_multisets:
        _encode_night_multiset(opb, xn, multiset, fellow_names, num_days, start_dow,
                               friday_only=True, soft_violations=soft_violations, weight=night_weight)


def _encode_night_policy_criteria(
    opb: OpbBuilder,
    xn: list[list[int]],
    xs: list[list[list[int]]],
    wr: list[list[dict[int, int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
) -> None:
    """Encode the night policy criteria as variable-gated constraints."""
    num_days = config.num_days
    start_dow = config.start_dow
    num_weeks = _num_weeks_for(start_dow, num_days)
    num_fellows = len(fellow_names)
    weights = config.night_weights
    hard_criteria = config.night_hard_criteria

    anaesthesia_indices = [shift_idx[s] for s in ANAESTHESIA_SHIFTS if s in shift_idx]
    clinic_indices = [shift_idx[s] for s in CLINIC_SHIFTS if s in shift_idx]
    stroke_idx = shift_idx.get("Stroke")

    # Compute dual-stroke indicator variables
    # dual_stroke[w] = 1 iff >= 2 fellows on Stroke in week w
    dual_stroke_vars = []
    if stroke_idx is not None:
        for w in range(num_weeks):
            stroke_vars_w = [xs[f][w][stroke_idx] for f in range(num_fellows) if xs[f][w][stroke_idx] != 0]
            if len(stroke_vars_w) >= 2:
                ds = opb.new_var()
                # ds=1 iff sum(stroke) >= 2
                # sum(stroke) + (N-2)*~ds >= 2
                opb.weighted_sum_at_least(
                    [(v, 1) for v in stroke_vars_w] + [(-ds, len(stroke_vars_w) - 2)],
                    2 if len(stroke_vars_w) > 2 else 2,
                )
                # sum(stroke) <= 1 + (N-1)*ds
                opb.weighted_sum_at_most(
                    [(v, 1) for v in stroke_vars_w] + [(-ds, len(stroke_vars_w) - 1)],
                    len(stroke_vars_w),
                )
                dual_stroke_vars.append(ds)
            else:
                dual_stroke_vars.append(0)
    else:
        dual_stroke_vars = [0] * num_weeks

    # Anaesthesia criterion: weekday nights only (Mon-Fri, dow 0-4)
    for d in range(num_days):
        week_idx = _day_to_week(d, start_dow)
        dow = _day_of_week(d, start_dow)
        if dow > 4:
            continue  # Weekend nights: no anaesthesia criterion
        for f in range(num_fellows):
            if xn[d][f] == 0:
                continue
            for si in anaesthesia_indices:
                if xs[f][week_idx][si] == 0:
                    continue
                _encode_night_criterion_pair(
                    opb, xs[f][week_idx][si], xn[d][f],
                    CRITERION_ANAESTHESIA, hard_criteria, weights, soft_violations,
                )

    # Clinic criterion: only Sun/Tue/Wed nights (before Mon/Wed/Thu clinic days)
    for d in range(num_days):
        dow = _day_of_week(d, start_dow)
        if dow not in (6, 1, 2):  # Only Sun, Tue, Wed nights
            continue
        next_day_week = _day_to_week(d + 1, start_dow) if d + 1 < num_days else _day_to_week(d, start_dow)
        for f in range(num_fellows):
            if xn[d][f] == 0:
                continue
            for si in clinic_indices:
                if xs[f][next_day_week][si] == 0:
                    continue
                _encode_night_criterion_pair(
                    opb, xs[f][next_day_week][si], xn[d][f],
                    CRITERION_CLINIC, hard_criteria, weights, soft_violations,
                )

    # Stroke criterion: block night before Stroke workday (Sun-Thu only)
    if stroke_idx is not None:
        for d in range(num_days):
            dow = _day_of_week(d, start_dow)
            if dow in (4, 5):  # Fri/Sat — next day is not a stroke workday
                continue
            next_day_week = _day_to_week(d + 1, start_dow) if d + 1 < num_days else _day_to_week(d, start_dow)
            ds_var = dual_stroke_vars[next_day_week] if next_day_week < num_weeks else 0
            for f in range(num_fellows):
                if xn[d][f] == 0:
                    continue
                if xs[f][next_day_week][stroke_idx] == 0:
                    continue
                if ds_var == 0:
                    _encode_night_criterion_pair(
                        opb, xs[f][next_day_week][stroke_idx], xn[d][f],
                        CRITERION_STROKE, hard_criteria, weights, soft_violations,
                    )
                else:
                    _encode_night_criterion_triple(
                        opb, xs[f][next_day_week][stroke_idx], xn[d][f], ds_var,
                        CRITERION_STROKE, hard_criteria, weights, soft_violations,
                    )

    # Weekend stroke → weekend night penalty (Sat/Sun)
    if stroke_idx is not None:
        for w in range(num_weeks):
            ds_var = dual_stroke_vars[w]
            for f in range(num_fellows):
                if f not in wr[w][_ROLE_STROKE]:
                    continue
                wr_stroke = wr[w][_ROLE_STROKE][f]
                for dow_target in (5, 6):  # Sat, Sun
                    d = _week_day(w, dow_target, start_dow)
                    if d < 0 or d >= num_days or xn[d][f] == 0:
                        continue
                    if ds_var == 0:
                        _encode_night_criterion_pair(
                            opb, wr_stroke, xn[d][f],
                            CRITERION_STROKE, hard_criteria, weights, soft_violations,
                        )
                    else:
                        _encode_night_criterion_triple(
                            opb, wr_stroke, xn[d][f], ds_var,
                            CRITERION_STROKE, hard_criteria, weights, soft_violations,
                        )

    # Friday/weekend NCC1 criterion
    for w in range(num_weeks):
        friday_d = _week_day(w, 4, start_dow)
        if friday_d < 0 or friday_d >= num_days:
            continue
        for f in range(num_fellows):
            if f not in wr[w][_ROLE_NCC1]:
                continue
            if xn[friday_d][f] == 0:
                continue
            wr_ncc1 = wr[w][_ROLE_NCC1][f]
            _encode_night_criterion_pair(
                opb, wr_ncc1, xn[friday_d][f],
                CRITERION_FRIDAY_WEEKEND_NCC1, hard_criteria, weights, soft_violations,
            )

    # Sunday following criterion: Sunday night fellow's next-week service is non-preferred
    non_preferred_indices = [shift_idx[s] for s in NON_PREFERRED_SUNDAY_FOLLOWING if s in shift_idx]
    for w in range(num_weeks - 1):
        sunday_d = _week_day(w, 6, start_dow)
        if sunday_d < 0 or sunday_d >= num_days:
            continue
        for f in range(num_fellows):
            if xn[sunday_d][f] == 0:
                continue
            # Check next week's service
            next_week_non_pref = [xs[f][w + 1][si] for si in non_preferred_indices if xs[f][w + 1][si] != 0]
            if not next_week_non_pref:
                continue
            # Violation = xn[sunday][f] AND any_non_preferred_next_week
            # Create OR of non-preferred
            if len(next_week_non_pref) == 1:
                non_pref_var = next_week_non_pref[0]
            else:
                non_pref_var = opb.new_var()
                for npv in next_week_non_pref:
                    opb.weighted_sum_at_least([(non_pref_var, 1), (-npv, 1)], 1)
                # non_pref <= sum(non_pref shifts): sum + (1 - non_pref) >= 1
                opb.weighted_sum_at_least(
                    [(v, 1) for v in next_week_non_pref] + [(-non_pref_var, 1)], 1
                )
            _encode_night_criterion_pair(
                opb, non_pref_var, xn[sunday_d][f],
                CRITERION_SUNDAY_FOLLOWING, hard_criteria, weights, soft_violations,
            )


def _encode_weekend_night_linking(
    opb: OpbBuilder,
    xn: list[list[int]],
    wr: list[list[dict[int, int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    soft_violations: list[tuple[int, int]],
) -> None:
    """Link weekend night call assignments to weekend role assignments.

    Per-occurrence rules (one penalty/forbid per offending fellow-week):
    - Friday night (soft, weight 40): fellow should NOT have a weekend role that
      week. The Friday + Weekend-NCC1 case is owned by the (hard-by-default)
      policy criterion ``friday_weekend_ncc1`` (see _encode_night_policy_criteria),
      so this loop covers only NCC2/Stroke to avoid double-encoding it.
    - Saturday night: fellow must be Weekend NCC1 or NCC2.
    - Sunday night: fellow must be Weekend Stroke.
    Saturday/Sunday are HARD by default (config flags), having verified a k=0
    mismatch bound is feasible; they fall back to scaled soft penalties if the
    flags are cleared.
    """
    num_days = config.num_days
    num_weeks = config.num_weeks
    start_dow = config.start_dow
    num_fellows = len(fellow_names)
    friday_weight = config.weekend_night_friday_weight
    saturday_weight = config.weekend_night_saturday_weight
    sunday_weight = config.weekend_night_sunday_weight
    # Pass the criterion name in hard_criteria to make _encode_night_criterion_pair
    # emit a hard at-most-1 instead of a penalized indicator.
    sat_hard = frozenset({"weekend_night_saturday"}) if config.weekend_night_saturday_hard else frozenset()
    sun_hard = frozenset({"weekend_night_sunday"}) if config.weekend_night_sunday_hard else frozenset()

    opb.add_comment("Night: weekend night linking (Fri soft; Sat/Sun hard by default)")

    for w in range(num_weeks):
        # --- Friday night: penalize having a weekend role (NCC1 handled by the
        # hard friday_weekend_ncc1 criterion, so only NCC2/Stroke here) ---
        friday_d = _week_day(w, 4, start_dow)
        if 0 <= friday_d < num_days:
            for f in range(num_fellows):
                if xn[friday_d][f] == 0:
                    continue
                for role_idx in (_ROLE_NCC2, _ROLE_STROKE):
                    if f in wr[w][role_idx]:
                        _encode_night_criterion_pair(
                            opb, xn[friday_d][f], wr[w][role_idx][f],
                            "weekend_night_friday", frozenset(),
                            _SoftWeightProxy(friday_weight), soft_violations,
                        )

        # --- Saturday night: prefer Weekend NCC1 or NCC2 ---
        sat_d = _week_day(w, 5, start_dow)
        if 0 <= sat_d < num_days:
            for f in range(num_fellows):
                if xn[sat_d][f] == 0:
                    continue
                ncc_vars = []
                if f in wr[w][_ROLE_NCC1]:
                    ncc_vars.append(wr[w][_ROLE_NCC1][f])
                if f in wr[w][_ROLE_NCC2]:
                    ncc_vars.append(wr[w][_ROLE_NCC2][f])

                if ncc_vars:
                    # Soft: xn[sat_d][f] AND NOT OR(ncc_vars) → penalty
                    # Create violation = xn AND all(~ncc)
                    not_ncc = opb.new_var()
                    # not_ncc >= 1 - sum(ncc_vars)  →  sum(ncc) + not_ncc >= 1
                    opb.weighted_sum_at_least(
                        [(v, 1) for v in ncc_vars] + [(not_ncc, 1)], 1
                    )
                    # not_ncc <= ~each_ncc  →  ncc_i + not_ncc <= 1
                    for nv in ncc_vars:
                        opb.at_most_k([nv, not_ncc], 1)
                    _encode_night_criterion_pair(
                        opb, xn[sat_d][f], not_ncc,
                        "weekend_night_saturday", sat_hard,
                        _SoftWeightProxy(saturday_weight), soft_violations,
                    )

        # --- Sunday night: must be covered by the Weekend Stroke fellow ---
        # Two cases per fellow who could work Sunday night:
        #  (a) stroke-eligible (has a weekend-Stroke var): working Sunday night
        #      implies holding the weekend-Stroke role that week.
        #  (b) NOT stroke-eligible (no weekend-Stroke var): cannot be the weekend
        #      -Stroke fellow, so cannot work Sunday night at all. Without this
        #      branch, NCC_JR fellows slipped through and took Sunday night while
        #      someone else held weekend-Stroke.
        sun_d = _week_day(w, 6, start_dow)
        if 0 <= sun_d < num_days:
            for f in range(num_fellows):
                if xn[sun_d][f] == 0:
                    continue
                if f in wr[w][_ROLE_STROKE]:
                    # xn AND NOT wr_stroke → violation (hard forbid, or penalty)
                    not_stroke = opb.new_var()
                    stroke_var = wr[w][_ROLE_STROKE][f]
                    opb.weighted_sum_at_least([(stroke_var, 1), (not_stroke, 1)], 1)
                    opb.at_most_k([stroke_var, not_stroke], 1)
                    _encode_night_criterion_pair(
                        opb, xn[sun_d][f], not_stroke,
                        "weekend_night_sunday", sun_hard,
                        _SoftWeightProxy(sunday_weight), soft_violations,
                    )
                elif config.weekend_night_sunday_hard:
                    # Not stroke-eligible → can never be the weekend-Stroke fellow,
                    # so forbid Sunday night outright.
                    opb.add_unit(-xn[sun_d][f])
                else:
                    # Soft mode: penalize a non-eligible fellow on Sunday night.
                    soft_violations.append((xn[sun_d][f], sunday_weight))


def _encode_weekend_prerequisites(
    opb: OpbBuilder,
    wr: list[list[dict[int, int]]],
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]] | None = None,
) -> None:
    """Require prior weekday service before weekend role assignment.

    weekend_stroke_prerequisite: wr[w][STROKE][f] <= sum(xs[f][w'][stroke] for w' < w)
    weekend_ncc_prerequisite: wr[w][NCC][f] <= sum(xs[f][w'][ncc1]+xs[f][w'][ncc2] for w' < w)
    """
    num_weeks = config.num_weeks
    stroke_si = shift_idx.get("Stroke")
    ncc1_si = shift_idx.get("NCC1")
    ncc2_si = shift_idx.get("NCC2")

    for rule in config.call_rules:
        if not rule.get("active", True):
            continue
        rule_type = rule.get("type")

        if rule_type == "weekend_stroke_prerequisite":
            if stroke_si is None:
                continue
            exempt = set(rule.get("exempt_fellows", []))
            exempt_groups = rule.get("exempt_groups", [])
            for g in exempt_groups:
                exempt.update(config.fellow_groups.get(g, []))
            for fi, name in enumerate(fellow_names):
                if name in exempt:
                    continue
                for w in range(num_weeks):
                    if fi not in wr[w][_ROLE_STROKE]:
                        continue
                    prior_stroke = [xs[fi][wp][stroke_si] for wp in range(w)
                                    if xs[fi][wp][stroke_si] != 0]
                    if not prior_stroke:
                        if soft_violations is not None:
                            v = opb.new_var()
                            opb.at_most_k([wr[w][_ROLE_STROKE][fi], v], 1)
                            soft_violations.append((v, config.weekly_soft_weight))
                        else:
                            opb.add_unit(-wr[w][_ROLE_STROKE][fi])
                    else:
                        # wr[w][STROKE][f] <= sum(prior_stroke)
                        opb.weighted_sum_at_least(
                            [(v, 1) for v in prior_stroke] + [(-wr[w][_ROLE_STROKE][fi], 1)],
                            0,
                        )

        elif rule_type == "weekend_ncc_prerequisite":
            if ncc1_si is None and ncc2_si is None:
                continue
            exempt = set(rule.get("exempt_fellows", []))
            exempt_groups = rule.get("exempt_groups", [])
            for g in exempt_groups:
                exempt.update(config.fellow_groups.get(g, []))
            for fi, name in enumerate(fellow_names):
                if name in exempt:
                    continue
                for w in range(num_weeks):
                    for role_idx in (_ROLE_NCC1, _ROLE_NCC2):
                        if fi not in wr[w][role_idx]:
                            continue
                        prior_ncc = []
                        for wp in range(w):
                            if ncc1_si is not None and xs[fi][wp][ncc1_si] != 0:
                                prior_ncc.append(xs[fi][wp][ncc1_si])
                            if ncc2_si is not None and xs[fi][wp][ncc2_si] != 0:
                                prior_ncc.append(xs[fi][wp][ncc2_si])
                        if not prior_ncc:
                            if soft_violations is not None:
                                v = opb.new_var()
                                opb.at_most_k([wr[w][role_idx][fi], v], 1)
                                soft_violations.append((v, config.weekly_soft_weight))
                            else:
                                opb.add_unit(-wr[w][role_idx][fi])
                        else:
                            opb.weighted_sum_at_least(
                                [(v, 1) for v in prior_ncc] + [(-wr[w][role_idx][fi], 1)],
                                0,
                            )


def _encode_dual_stroke_window(
    opb: OpbBuilder,
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]] | None = None,
) -> None:
    """Encode dual-Stroke window: allow 2 fellows on Stroke in a week range.

    Within the window: at_most 2 total, at_most 1 non-supervisor.
    Outside the window: at_most 1 total.
    Weeks where locked fellows already exceed the cap are softened.
    """
    num_weeks = config.num_weeks
    stroke_si = shift_idx.get("Stroke")
    if stroke_si is None:
        return

    for rule in config.call_rules:
        if not rule.get("active", True):
            continue
        if rule.get("type") != "dual_stroke_window":
            continue

        window = rule.get("window", [0, 0])
        w_start, w_end = window[0], window[1]
        supervisors = set(rule.get("supervisors", []))

        supervisor_indices = {fi for fi, name in enumerate(fellow_names) if name in supervisors}
        non_supervisor_indices = {fi for fi in range(len(fellow_names)) if fi not in supervisor_indices}

        # IMPORTED fellows already on Stroke that week (single source of truth).
        locked_stroke_per_week = config.imported_shift_counts(fellow_names, {"Stroke"})

        weight = config.weekly_soft_weight

        for w in range(num_weeks):
            locked_here = locked_stroke_per_week.get(w, 0)
            sup_vars = [xs[fi][w][stroke_si] for fi in supervisor_indices
                        if xs[fi][w][stroke_si] != 0]
            non_sup_vars = [xs[fi][w][stroke_si] for fi in non_supervisor_indices
                           if xs[fi][w][stroke_si] != 0]
            all_stroke_vars = sup_vars + non_sup_vars
            if not all_stroke_vars:
                continue

            if locked_here >= 1:
                continue

            if w_start <= w < w_end:
                # Window: at most 1 supervisor, at most 1 non-supervisor
                if sup_vars:
                    opb.at_most_k(sup_vars, 1)
                if non_sup_vars:
                    opb.at_most_k(non_sup_vars, 1)
            else:
                # Outside window: at most 1 total
                opb.at_most_k(all_stroke_vars, 1)


class _SoftWeightProxy:
    """Adapter so _encode_night_criterion_pair can use a fixed weight."""
    def __init__(self, w: int):
        self._w = w
    def for_criterion(self, _name: str) -> int:
        return self._w


def _encode_night_criterion_pair(
    opb: OpbBuilder,
    condition_var: int,
    night_var: int,
    criterion: str,
    hard_criteria: frozenset[str],
    weights: NightPolicyWeights,
    soft_violations: list[tuple[int, int]],
) -> None:
    """Encode: condition_var AND night_var triggers a criterion violation."""
    if criterion in hard_criteria:
        opb.at_most_k([condition_var, night_var], 1)
    else:
        ind = opb.new_var()
        # ind >= condition + night - 1
        opb.weighted_sum_at_most([(condition_var, 1), (night_var, 1), (-ind, 1)], 2)
        # ind <= condition  (ind implies condition: condition + ~ind >= 1)
        opb.weighted_sum_at_least([(condition_var, 1), (-ind, 1)], 1)
        # ind <= night  (ind implies night: night + ~ind >= 1)
        opb.weighted_sum_at_least([(night_var, 1), (-ind, 1)], 1)
        soft_violations.append((ind, weights.for_criterion(criterion)))


def _encode_night_criterion_triple(
    opb: OpbBuilder,
    condition_var: int,
    night_var: int,
    exempt_var: int,
    criterion: str,
    hard_criteria: frozenset[str],
    weights: NightPolicyWeights,
    soft_violations: list[tuple[int, int]],
) -> None:
    """Encode: condition AND night AND NOT exempt triggers violation."""
    if criterion in hard_criteria:
        # condition + night + ~exempt <= 2 (can't all be true)
        # i.e., condition + night - exempt <= 1 → condition + night + (1-exempt) <= 2
        opb.weighted_sum_at_most([(condition_var, 1), (night_var, 1), (-exempt_var, 1)], 2)
    else:
        ind = opb.new_var()
        # ind = condition AND night AND ~exempt
        # ind >= condition + night + ~exempt - 2
        opb.weighted_sum_at_most(
            [(condition_var, 1), (night_var, 1), (-exempt_var, 1), (-ind, 1)], 3
        )
        # ind <= condition  (condition + ~ind >= 1)
        opb.weighted_sum_at_least([(condition_var, 1), (-ind, 1)], 1)
        # ind <= night  (night + ~ind >= 1)
        opb.weighted_sum_at_least([(night_var, 1), (-ind, 1)], 1)
        # ind <= ~exempt  (~exempt + ~ind >= 1)
        opb.weighted_sum_at_least([(-exempt_var, 1), (-ind, 1)], 1)
        soft_violations.append((ind, weights.for_criterion(criterion)))


def _encode_night_multiset(
    opb: OpbBuilder,
    xn: list[list[int]],
    multiset: CountMultiset,
    fellow_names: list[str],
    num_days: int,
    start_dow: int,
    *,
    friday_only: bool,
    soft_violations: list[tuple[int, int]] | None = None,
    weight: int = 100,
) -> None:
    """Encode multiset total constraint (disjunction of count orderings)."""
    from itertools import permutations as perms
    unique_orderings = {tuple(o) for o in perms(multiset.values)}

    fellow_indices = []
    for name in multiset.names:
        if name in fellow_names:
            fellow_indices.append(fellow_names.index(name))
        else:
            return  # Can't encode if fellow not present

    if friday_only:
        day_filter = lambda d: _day_of_week(d, start_dow) == 4
    else:
        day_filter = lambda d: True

    # For each ordering, create a selector
    selectors = []
    for ordering in unique_orderings:
        sel = opb.new_var()
        selectors.append(sel)
        for fi, total in zip(fellow_indices, ordering):
            night_vars = [xn[d][fi] for d in range(num_days) if day_filter(d) and xn[d][fi] != 0]
            if night_vars:
                opb.conditional_exactly_k(night_vars, total, sel)

    if selectors:
        if soft_violations is not None:
            v = opb.new_var()
            opb.at_least_k(selectors + [v], 1)
            for sel in selectors:
                opb.at_most_k([v, sel], 1)
            soft_violations.append((v, weight))
        else:
            opb.at_least_k(selectors, 1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_fellow_mapping(fellow_groups: dict[str, list[str]]) -> FellowMapping:
    fm = FellowMapping()
    for group, fellows in fellow_groups.items():
        for name in fellows:
            fm.add_fellow(name, group)
    return fm


def _resolve_fellow_indices(fellow_mapping: FellowMapping, selector: FellowSelector | None) -> list[int]:
    if selector is None:
        return list(fellow_mapping.all_fellow_indices)
    if selector.groups:
        return fellow_mapping.get_fellow_indices_by_groups(*selector.groups)
    return [
        fellow_mapping.get_fellow_index(name)
        for name in selector.names
        if fellow_mapping.get_fellow(name) is not None
    ]


def _compute_forbidden(
    constraints: list[SemanticConstraint],
    fellow_mapping: FellowMapping,
    shifts: list[str],
    num_weeks: int,
) -> set[tuple[int, int, int]]:
    """Pre-compute (fellow_idx, week, shift_idx) triples that are always forbidden."""
    shift_idx = {s: i for i, s in enumerate(shifts)}
    forbidden = set()

    for constraint in constraints:
        if constraint.strength != ConstraintStrength.HARD:
            continue
        fellow_indices = _resolve_fellow_indices(fellow_mapping, constraint.fellows)

        if constraint.kind == "service_profile":
            for shift_name in constraint.params.get("zero_shifts", []):
                si = shift_idx.get(shift_name)
                if si is None:
                    continue
                for f in fellow_indices:
                    for w in range(num_weeks):
                        forbidden.add((f, w, si))

        elif constraint.kind == "isc" and "ISC" in shift_idx:
            from scheduler.date_to_week_index import date_to_week_index
            target_date = tuple(constraint.params.get("date", (2026, 2, 5)))
            target_week = date_to_week_index(target_date)
            si = shift_idx["ISC"]
            for f in fellow_indices:
                for w in range(num_weeks):
                    if w != target_week:
                        forbidden.add((f, w, si))

    return forbidden


def _is_weekend_eligible_static(
    fellow_name: str, role_idx: int, config: WeekendSolverConfig
) -> bool:
    """Static eligibility check (ignoring weekday shift, which is dynamic)."""
    if role_idx == _ROLE_STROKE:
        all_eligible = (
            config.always_stroke_eligible
            | config.telestroke_stroke_eligible
            | config.stroke_only_eligible
        )
        if not all_eligible:
            return True  # No eligibility sets configured — everyone can do stroke
        return fellow_name in all_eligible
    return True


def _encode_floor_ceil_total(
    opb: OpbBuilder,
    vars: list[int],
    target: int,
    weight: int,
    soft_violations: list[tuple[int, int]],
) -> None:
    """Encode a distribution total: hard at_least floor, soft at_most.

    Hard floor prevents under-assignment (can't steal from this fellow).
    Soft ceiling discourages over-assignment but doesn't block feasibility
    when service blocking or spacing makes exact counts impossible.
    """
    if not vars or target < 0:
        return
    n = len(vars)
    if target > n:
        # Can't possibly reach target — soft only
        _add_cardinality_constraint(
            opb, vars, "at_least", target,
            is_soft=True, weight=weight, soft_violations=soft_violations,
        )
        return

    # Hard floor: must get at least target
    if target > 0:
        opb.at_least_k(vars, target)
    # Soft ceiling: penalize over-assignment
    if target < n:
        _add_cardinality_constraint(
            opb, vars, "at_most", target,
            is_soft=True, weight=weight, soft_violations=soft_violations,
        )


def _add_cardinality_constraint(
    opb: OpbBuilder,
    vars: list[int],
    relation: str,
    target: int,
    *,
    is_soft: bool,
    weight: int,
    soft_violations: list[tuple[int, int]],
    max_violation: int | None = None,
) -> None:
    """Add a cardinality constraint (exactly/at_least/at_most).

    Soft constraints use a DEVIATION-SCALED penalty: the cost is
    ``weight * |count - target|`` (proportional to how far the count is from the
    target), not a flat per-constraint penalty. This is encoded with unary slack
    variables — one penalized slack per unit of allowed deviation — so the
    optimizer is pulled toward the target rather than paying the same price for
    being off by 1 or by 30.

    ``max_violation`` caps the number of slacks per direction. Leave it None for a
    pure soft penalty (slacks span the full possible deviation, so the constraint
    can never become infeasible). Set it only when an OUTER hard bound already
    limits the count (e.g. the weekend hard band), so the bounded slacks just
    measure the within-band deviation without adding a second hard restriction.
    """
    if not vars:
        return

    if not is_soft:
        if relation == "exactly":
            opb.exactly_k(vars, target)
        elif relation == "at_least":
            if target <= len(vars):
                opb.at_least_k(vars, target)
        elif relation == "at_most":
            opb.at_most_k(vars, target)
        return

    n = len(vars)

    def _slacks(k: int) -> list[int]:
        s = [opb.new_var() for _ in range(max(0, k))]
        for v in s:
            soft_violations.append((v, weight))
        return s

    if relation == "at_least":
        if target <= 0:
            return
        # shortfall = max(0, target - count); sum(vars) + sum(slacks) >= target
        k = target if max_violation is None else min(target, max_violation)
        slacks = _slacks(k)
        if slacks or target <= n:
            opb.weighted_sum_at_least(
                [(x, 1) for x in vars] + [(s, 1) for s in slacks], target
            )
    elif relation == "at_most":
        if n <= target:
            return  # trivially satisfied
        # excess = max(0, count - target); sum(~vars) + sum(slacks) >= n - target
        k = (n - target) if max_violation is None else min(n - target, max_violation)
        slacks = _slacks(k)
        opb.weighted_sum_at_least(
            [(-x, 1) for x in vars] + [(s, 1) for s in slacks], n - target
        )
    elif relation == "exactly":
        # Penalize both directions: shortfall (under) and excess (over).
        if target > 0:
            ku = target if max_violation is None else min(target, max_violation)
            under = _slacks(ku)
            opb.weighted_sum_at_least(
                [(x, 1) for x in vars] + [(s, 1) for s in under], target
            )
        if n > target:
            ko = (n - target) if max_violation is None else min(n - target, max_violation)
            over = _slacks(ko)
            opb.weighted_sum_at_least(
                [(-x, 1) for x in vars] + [(s, 1) for s in over], n - target
            )


def _add_conditional_cardinality(
    opb: OpbBuilder,
    vars: list[int],
    relation: str,
    target: int,
    trigger: int,
    *,
    is_soft: bool,
    weight: int,
    soft_violations: list[tuple[int, int]],
) -> None:
    """Add: trigger → cardinality(vars, relation, target)."""
    if not vars:
        return
    n = len(vars)

    if not is_soft:
        if relation == "exactly":
            opb.conditional_exactly_k(vars, target, trigger)
        elif relation == "at_least":
            if target <= n:
                opb.weighted_sum_at_least(
                    [(v, 1) for v in vars] + [(-trigger, target)], target
                )
        elif relation == "at_most":
            if n > target:
                opb.weighted_sum_at_least(
                    [(-v, 1) for v in vars] + [(-trigger, n - target)], n - target
                )
    else:
        v = opb.new_var()
        bypass = max(target, n, 1)
        if relation == "exactly":
            opb.weighted_sum_at_least(
                [(x, 1) for x in vars] + [(-trigger, max(target, 1))] + [(v, bypass)], target
            )
            if n > target:
                opb.weighted_sum_at_least(
                    [(-x, 1) for x in vars] + [(-trigger, n - target)] + [(v, bypass)], n - target
                )
        elif relation == "at_least":
            opb.weighted_sum_at_least(
                [(x, 1) for x in vars] + [(-trigger, max(target, 1))] + [(v, max(target, 1))], target
            )
        elif relation == "at_most":
            if n > target:
                opb.weighted_sum_at_least(
                    [(-x, 1) for x in vars] + [(-trigger, n - target)] + [(v, n - target)], n - target
                )
        soft_violations.append((v, weight))


def _encode_balance_constraint(
    opb: OpbBuilder,
    first_half_vars: list[int],
    second_half_vars: list[int],
    max_diff: int,
    *,
    is_soft: bool,
    weight: int,
    soft_violations: list[tuple[int, int]],
) -> None:
    """Encode |sum(first) - sum(second)| <= max_diff."""
    if not first_half_vars and not second_half_vars:
        return

    # sum(first) - sum(second) <= max_diff
    # → sum(first) + sum(~second) <= max_diff + len(second)
    # sum(second) - sum(first) <= max_diff
    # → sum(second) + sum(~first) <= max_diff + len(first)

    if not is_soft:
        # Direction 1: sum(first) - sum(second) <= max_diff
        opb.weighted_sum_at_most(
            [(v, 1) for v in first_half_vars] + [(-v, 1) for v in second_half_vars],
            max_diff + len(second_half_vars),
        )
        # Direction 2: sum(second) - sum(first) <= max_diff
        opb.weighted_sum_at_most(
            [(v, 1) for v in second_half_vars] + [(-v, 1) for v in first_half_vars],
            max_diff + len(first_half_vars),
        )
    else:
        v = opb.new_var()
        n1 = len(first_half_vars)
        n2 = len(second_half_vars)
        big_m = max(n1, n2)
        # With violation bypass
        opb.weighted_sum_at_most(
            [(x, 1) for x in first_half_vars] + [(-x, 1) for x in second_half_vars] + [(-v, big_m)],
            max_diff + n2 + big_m,
        )
        opb.weighted_sum_at_most(
            [(x, 1) for x in second_half_vars] + [(-x, 1) for x in first_half_vars] + [(-v, big_m)],
            max_diff + n1 + big_m,
        )
        soft_violations.append((v, weight))


# ---------------------------------------------------------------------------
# Annual call rules (pin/block night/weekend)
# ---------------------------------------------------------------------------

def _encode_call_rules(
    opb: OpbBuilder,
    xn: list[list[int]],
    wr: list[list[dict[int, int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    xs: list[list[list[int]]] | None = None,
    shift_idx: dict[str, int] | None = None,
    soft_violations: list[tuple[int, int]] | None = None,
) -> None:
    """Encode annual call rules (night/weekend pin/block assignments).

    These are NOT palette rules — they produce unit constraints directly
    on the ``xn`` (night) and ``wr`` (weekend) variable layers.
    """
    start_dow = config.start_dow
    num_days = config.num_days
    num_weeks = config.num_weeks
    horizon_start = config.night_config.horizon_start_date

    _NON_FELLOW_TYPES = {"group_night_requirement", "weekend_stroke_prerequisite",
                         "weekend_ncc_prerequisite", "dual_stroke_window"}

    for rule in config.call_rules:
        if not rule.get("active", True):
            continue
        rule_type = rule.get("type")

        # --- Non-fellow-specific rule types ---
        if rule_type == "group_night_requirement":
            allowed_groups = rule.get("groups", [])
            allowed_fellows: set[str] = set()
            for g in allowed_groups:
                allowed_fellows.update(config.fellow_groups.get(g, []))
            for date_str in rule.get("dates", []):
                d = _date_to_day_index(date_str, horizon_start)
                if d < 0 or d >= num_days:
                    continue
                for fi, name in enumerate(fellow_names):
                    if name not in allowed_fellows and xn[d][fi] != 0:
                        opb.add_unit(-xn[d][fi])
            continue

        if rule_type == "per_fellow_shift_total":
            if xs is None or shift_idx is None or soft_violations is None:
                continue
            fellow_name = rule.get("fellow")
            if fellow_name not in fellow_names:
                continue
            fi = fellow_names.index(fellow_name)
            shifts_list = rule.get("shifts", [])
            s_indices = [shift_idx[s] for s in shifts_list if s in shift_idx]
            relation = rule.get("relation", "exactly")
            count = rule.get("count", 0)
            is_soft = rule.get("strength", "hard") == "soft"
            weight = config.weekly_soft_weight
            fellow_vars = []
            for w in range(num_weeks):
                for si in s_indices:
                    if xs[fi][w][si] != 0:
                        fellow_vars.append(xs[fi][w][si])
            if fellow_vars:
                _add_cardinality_constraint(
                    opb, fellow_vars, relation, count,
                    is_soft=is_soft, weight=weight, soft_violations=soft_violations,
                )
            continue

        if rule_type in _NON_FELLOW_TYPES:
            continue

        # --- Fellow-specific rule types ---
        fellow_name = rule.get("fellow")
        if fellow_name not in fellow_names:
            continue
        fi = fellow_names.index(fellow_name)

        if rule_type == "specific_night_assignment":
            for date_str in rule.get("dates", []):
                d = _date_to_day_index(date_str, horizon_start)
                if 0 <= d < num_days and xn[d][fi] != 0:
                    opb.add_unit(xn[d][fi])

        elif rule_type == "blocked_night":
            for date_str in rule.get("dates", []):
                d = _date_to_day_index(date_str, horizon_start)
                if 0 <= d < num_days and xn[d][fi] != 0:
                    opb.add_unit(-xn[d][fi])

        elif rule_type == "specific_weekend_assignment":
            role_name = rule.get("role", "")
            role_idx = {"NCC1": 0, "NCC2": 1, "Stroke": 2}.get(role_name)
            if role_idx is None:
                continue
            for w in rule.get("weeks", []):
                if 0 <= w < num_weeks and fi in wr[w][role_idx]:
                    opb.add_unit(wr[w][role_idx][fi])

        elif rule_type == "blocked_weekend":
            for w in rule.get("weeks", []):
                if 0 <= w < num_weeks:
                    for role_idx in range(3):
                        if fi in wr[w][role_idx]:
                            opb.add_unit(-wr[w][role_idx][fi])

        elif rule_type == "friday_call_assignment":
            for w in rule.get("weeks", []):
                friday_d = _week_day(w, 4, start_dow)
                if 0 <= friday_d < num_days and xn[friday_d][fi] != 0:
                    opb.add_unit(xn[friday_d][fi])


# ---------------------------------------------------------------------------
# Palette v2 generic encoders
# ---------------------------------------------------------------------------

def _encode_shift_total(opb, xs, constraint, fellow_indices, **kw):
    """Per-fellow total of specific shifts, optionally windowed."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]
    is_soft = constraint.strength == ConstraintStrength.SOFT
    weight = kw["config"].weekly_soft_weight
    soft_violations = kw["soft_violations"]
    locked_fellow_indices = kw.get("locked_fellow_indices", frozenset())

    relation = constraint.params["relation"]
    count = constraint.params["count"]
    target_shifts = list(constraint.shifts.shifts) if constraint.shifts else []
    s_indices = [shift_idx[s] for s in target_shifts if s in shift_idx]

    if constraint.weeks:
        w_start, w_end = constraint.weeks.start, constraint.weeks.end
    else:
        w_start, w_end = 0, num_weeks

    for f in fellow_indices:
        all_vars = []
        for w in range(w_start, min(w_end, num_weeks)):
            for si in s_indices:
                if xs[f][w][si] != 0:
                    all_vars.append(xs[f][w][si])

        if not all_vars:
            continue

        fellow_is_soft = is_soft or f in locked_fellow_indices
        _add_cardinality_constraint(
            opb, all_vars, relation, count,
            is_soft=fellow_is_soft, weight=weight, soft_violations=soft_violations,
        )


def _encode_staffing_per_week(opb, xs, constraint, fellow_indices, **kw):
    """Per-week staffing requirement: N fellows from groups on shifts."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]
    num_fellows = kw["num_fellows"]
    config = kw["config"]
    is_soft = constraint.strength == ConstraintStrength.SOFT
    weight = config.weekly_soft_weight
    soft_violations = kw["soft_violations"]

    relation = constraint.params["relation"]
    count = constraint.params["count"]
    target_shifts = list(constraint.shifts.shifts) if constraint.shifts else []
    s_indices = [shift_idx[s] for s in target_shifts if s in shift_idx]

    # Precompute IMPORTED contribution per week for at_most/exactly checks.
    # Only count IMPORTED fellows in the constraint's target groups (single
    # source of truth: config.imported_shift_counts).
    locked_count_per_week: dict[int, int] | None = None
    if not is_soft and config.locked_assignments and relation in ("at_most", "exactly"):
        locked_count_per_week = config.imported_shift_counts(
            kw["fellow_names"], set(target_shifts),
            restrict_to=frozenset(fellow_indices),
        )

    if constraint.weeks:
        w_start, w_end = constraint.weeks.start, constraint.weeks.end
    else:
        w_start, w_end = 0, num_weeks

    for w in range(w_start, min(w_end, num_weeks)):
        week_vars = []
        for f in fellow_indices:
            for si in s_indices:
                if xs[f][w][si] != 0:
                    week_vars.append(xs[f][w][si])

        if not week_vars:
            continue

        # Soften only weeks where locked fellows already fill/exceed the cap
        week_is_soft = is_soft
        if not week_is_soft and locked_count_per_week is not None:
            if locked_count_per_week.get(w, 0) >= count:
                week_is_soft = True

        _add_cardinality_constraint(
            opb, week_vars, relation, count,
            is_soft=week_is_soft, weight=weight, soft_violations=soft_violations,
        )


def _encode_coverage_target(opb, xs, constraint, fellow_indices, **kw):
    """At least (W - max_uncovered) weeks have the shift covered."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]
    is_soft = constraint.strength == ConstraintStrength.SOFT
    weight = kw["config"].swing_uncovered_weight
    soft_violations = kw["soft_violations"]

    max_uncovered = constraint.params["max_uncovered_weeks"]
    target_shifts = list(constraint.shifts.shifts) if constraint.shifts else []
    s_indices = [shift_idx[s] for s in target_shifts if s in shift_idx]

    covered_vars = []
    for w in range(num_weeks):
        week_vars = []
        for f in fellow_indices:
            for si in s_indices:
                if xs[f][w][si] != 0:
                    week_vars.append(xs[f][w][si])
        if week_vars:
            covered = opb.new_var()
            for wv in week_vars:
                opb.weighted_sum_at_least([(covered, 1), (-wv, 1)], 1)
            # covered <= sum(week_vars): sum + (1 - covered) >= 1
            opb.weighted_sum_at_least(
                [(v, 1) for v in week_vars] + [(-covered, 1)], 1
            )
            covered_vars.append(covered)

    if not covered_vars:
        return

    required = num_weeks - max_uncovered
    if required <= 0:
        return

    if is_soft:
        for cv in covered_vars:
            uncov = opb.new_var()
            opb.at_least_k([cv, uncov], 1)
            opb.at_most_k([cv, uncov], 1)
            soft_violations.append((uncov, weight))
    else:
        opb.at_least_k(covered_vars, min(required, len(covered_vars)))


def _encode_zero_shifts(opb, xs, constraint, fellow_indices, **kw):
    """Forbid fellows from being assigned specific shifts."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]

    for shift_name in constraint.params.get("zero_shifts", []):
        si = shift_idx.get(shift_name)
        if si is None:
            continue
        for f in fellow_indices:
            for w in range(num_weeks):
                if xs[f][w][si] != 0:
                    opb.add_unit(-xs[f][w][si])


def _encode_prerequisite(opb, xs, constraint, fellow_indices, **kw):
    """N weeks of prerequisite shifts before any target shift."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]

    prereq_shifts = constraint.params["prerequisite_shifts"]
    target_shifts = constraint.params["target_shifts"]
    n_required = constraint.params["min_prerequisite_weeks"]

    prereq_indices = [shift_idx[s] for s in prereq_shifts if s in shift_idx]
    target_indices = [shift_idx[s] for s in target_shifts if s in shift_idx]

    if not prereq_indices or not target_indices:
        return

    for f in fellow_indices:
        for w in range(min(n_required, num_weeks)):
            for si in target_indices:
                if xs[f][w][si] != 0:
                    opb.add_unit(-xs[f][w][si])

        for w in range(n_required, num_weeks):
            for si in target_indices:
                target_var = xs[f][w][si]
                if target_var == 0:
                    continue
                prior_vars = []
                for pw in range(w):
                    for pi in prereq_indices:
                        if xs[f][pw][pi] != 0:
                            prior_vars.append(xs[f][pw][pi])
                if len(prior_vars) < n_required:
                    opb.add_unit(-target_var)
                else:
                    opb.weighted_sum_at_least(
                        [(v, 1) for v in prior_vars] + [(-target_var, n_required)],
                        n_required,
                    )


def _encode_windowed_balance(opb, xs, constraint, fellow_indices, **kw):
    """Balance shifts across two arbitrary week windows."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]
    is_soft = constraint.strength == ConstraintStrength.SOFT
    weight = kw["config"].weekly_soft_weight
    soft_violations = kw["soft_violations"]

    window_a = constraint.params["window_a"]
    window_b = constraint.params["window_b"]
    max_diff = constraint.params["max_difference"]
    target_shifts = list(constraint.shifts.shifts) if constraint.shifts else []
    s_indices = [shift_idx[s] for s in target_shifts if s in shift_idx]

    for f in fellow_indices:
        vars_a = []
        for w in range(window_a[0], min(window_a[1], num_weeks)):
            for si in s_indices:
                if xs[f][w][si] != 0:
                    vars_a.append(xs[f][w][si])
        vars_b = []
        for w in range(window_b[0], min(window_b[1], num_weeks)):
            for si in s_indices:
                if xs[f][w][si] != 0:
                    vars_b.append(xs[f][w][si])

        _encode_balance_constraint(
            opb, vars_a, vars_b, max_diff,
            is_soft=is_soft, weight=weight, soft_violations=soft_violations,
        )


# ---------------------------------------------------------------------------
# Solution decoding
# ---------------------------------------------------------------------------

def decode_solution(
    assignment: dict[int, bool],
    var_map: ScheduleVarMap,
) -> FullScheduleSolution:
    """Decode RoundingSat variable assignment into a schedule."""
    fellow_names = var_map.fellow_names
    shifts = var_map.shifts
    num_weeks = var_map.num_weeks
    num_fellows = var_map.num_fellows
    num_shifts = var_map.num_shifts

    # Weekly assignments
    weekly = {name: [""] * num_weeks for name in fellow_names}
    for f in range(num_fellows):
        for w in range(num_weeks):
            for s in range(num_shifts):
                var = var_map.xs[f][w][s]
                if var != 0 and assignment.get(var, False):
                    weekly[fellow_names[f]][w] = shifts[s]

    # Weekend assignments
    weekend_by_week = []
    for w in range(num_weeks):
        week_assignments = {}
        for role_idx, role_name in enumerate(_WEEKEND_ROLE_NAMES):
            for fi, var in var_map.wr[w][role_idx].items():
                if assignment.get(var, False):
                    week_assignments[role_name] = fellow_names[fi]
                    break
            else:
                week_assignments[role_name] = ""
        weekend_by_week.append(week_assignments)

    # Night assignments
    night_by_week = []
    num_days = var_map.num_days
    start_dow = var_map.start_dow
    for w in range(num_weeks):
        week_nights = {}
        for dow_target, role in enumerate(NIGHT_ROLES):
            d = _week_day(w, dow_target, start_dow)
            if d < 0 or d >= num_days:
                week_nights[role] = ""
                continue
            for f in range(num_fellows):
                var = var_map.xn[d][f]
                if var != 0 and assignment.get(var, False):
                    week_nights[role] = fellow_names[f]
                    break
            else:
                week_nights[role] = ""
        night_by_week.append(week_nights)

    # Backup assignments: bk[w][kind][f]
    backup_by_week: list[dict[str, str]] = []
    for w in range(num_weeks):
        week_backup: dict[str, str] = {}
        for kind, role_name in enumerate(_BACKUP_ROLE_NAMES):
            week_backup[role_name] = ""
            if w < len(var_map.bk):
                for fi, var in var_map.bk[w][kind].items():
                    if assignment.get(var, False):
                        week_backup[role_name] = fellow_names[fi]
                        break
        backup_by_week.append(week_backup)

    # Compute soft penalty
    penalty = sum(
        weight for var, weight in var_map.soft_violations
        if assignment.get(var, False)
    )

    return FullScheduleSolution(
        weekly_assignments=weekly,
        weekend_solution=WeekendScheduleSolution(assignments_by_week=weekend_by_week),
        night_solution=NightScheduleSolution(assignments_by_week=night_by_week),
        soft_penalty=penalty,
        backup_solution=BackupScheduleSolution(assignments_by_week=backup_by_week),
    )


def soft_penalty_breakdown(
    assignment: dict[int, bool], var_map: ScheduleVarMap,
) -> tuple[int, int, int]:
    """Split the soft penalty into (weekly, weekend_night, total).

    Weekly = soft violations from weekly shift rules; weekend_night = weekend +
    night + call-rule soft violations. The split is the soft_weekly_count
    boundary recorded at build time.
    """
    sv = var_map.soft_violations
    k = var_map.soft_weekly_count
    weekly = sum(w for v, w in sv[:k] if assignment.get(v, False))
    call = sum(w for v, w in sv[k:] if assignment.get(v, False))
    return weekly, call, weekly + call


@dataclass(frozen=True)
class OptimizeStreamStep:
    """One streamed incumbent from native optimization."""
    solution: FullScheduleSolution
    weekly_penalty: int
    call_penalty: int       # weekend + night + call-rule soft penalty
    total_penalty: int
    optimal: bool           # True iff this incumbent is proven optimal
    elapsed: float
    lower_bound: int | None = None  # best objective lower bound (gap = total - lower_bound)


def optimize_stream(
    config: ScheduleSolverConfig,
    runner: RoundingSatRunner,
    *,
    preview_seconds: tuple[float, ...] = (8.0, 25.0),
    max_seconds: float = 180.0,
    opt_mode: str = "hybrid",
    echo_progress: bool = False,
):
    """Stream improving full schedules via native optimization (hybrid schedule).

    RoundingSat minimizes the soft-penalty objective natively — far faster and
    far better than the legacy decision-scan (it finds a feasible point in ~1s
    then descends) — but it prints its incumbent model only once, at the end,
    and cold restarts lose progress. So we run a few short *preview* solves
    (each a quick streamed schedule for the "instant feedback, then streamed
    improvements" UX) and then ONE long *final* solve that gets the entire
    remaining budget for the best-quality result. No hard objective bound is
    added (that would make initial feasibility slow). We keep the best incumbent
    seen and yield only strict improvements, so the stream is monotonic.

    preview_seconds:
        Per-run budgets for the short preview solves. The final run always
        consumes whatever budget remains, so it should dominate (keep previews
        short relative to *max_seconds*).

    Yields :class:`OptimizeStreamStep` per improvement; the final step has
    ``optimal=True`` iff optimality was proven.

    BACK-BURNER: the clean long-term alternative is to patch RoundingSat to emit
    each improving incumbent's model, enabling true single-run streaming with no
    restart tax. Tracked separately; not done here.
    """
    t0 = time.time()
    opb, var_map = build_full_schedule_opb(config, objective=True)
    best_total: int | None = None

    # Short previews first, then a final run that takes all remaining budget.
    budgets: list[float | None] = list(preview_seconds) + [None]
    for b in budgets:
        remaining = max_seconds - (time.time() - t0)
        if remaining < 1.0:
            return
        budget = remaining if b is None else min(b, remaining)
        # Echo RoundingSat's bound progress live only on the final (longest) run,
        # where the heartbeat matters; the short previews stay quiet.
        res = runner.optimize(opb, time_limit=budget, opt_mode=opt_mode,
                              echo_progress=echo_progress and b is None)

        if res.satisfiable and res.assignment is not None:
            sol = decode_solution(res.assignment, var_map)
            wk, cn, tot = soft_penalty_breakdown(res.assignment, var_map)
            if best_total is None or tot < best_total:
                best_total = tot
                yield OptimizeStreamStep(sol, wk, cn, tot, res.optimal,
                                         time.time() - t0, res.lower_bound)
            if res.optimal or best_total == 0:
                return
        elif res.proven_unsat:
            # The whole problem is infeasible (no objective bound was added).
            return
        # Otherwise (unknown / no incumbent in this slice): fall through to the
        # next, longer budget.


# ---------------------------------------------------------------------------
# Optimization loop (linear scan)
# ---------------------------------------------------------------------------

def solve_full_schedule(
    config: ScheduleSolverConfig,
    runner: RoundingSatRunner,
    *,
    max_soft: int | None = None,
    coarse_step: int = 5,
    fine_step: int = 1,
    coarse_timeout: float = 10.0,
    fine_timeout: float = 60.0,
    emit_progress: bool = True,
) -> FullScheduleSolution | None:
    """Solve the joint schedule using linear-scan optimization over soft bound."""

    # First: find any feasible solution (no soft bound)
    if emit_progress:
        print("Building formula (no soft bound) to check feasibility...", flush=True)

    opb_check, var_map = build_full_schedule_opb(config, soft_bound=None)
    if emit_progress:
        print(
            f"Formula: {opb_check.num_vars} vars, {opb_check.num_constraints} constraints, "
            f"{len(var_map.soft_violations)} soft indicators",
            flush=True,
        )

    # Compute upper bound
    upper_bound = sum(w for _, w in var_map.soft_violations)
    if max_soft is not None:
        upper_bound = min(upper_bound, max_soft)

    if emit_progress:
        print(f"Soft penalty upper bound: {upper_bound}", flush=True)
        print("Checking feasibility with full soft budget...", flush=True)

    t0 = time.time()
    opb_feasible, _ = build_full_schedule_opb(config, soft_bound=upper_bound)
    try:
        result = runner.solve(opb_feasible, timeout=120.0)
    except Exception as exc:
        if emit_progress:
            print(f"Feasibility check timed out: {exc}", flush=True)
        return None
    if not result.satisfiable:
        if emit_progress:
            print(f"INFEASIBLE even with full soft budget ({upper_bound}). Check hard constraints.", flush=True)
        return None

    best_assignment = result.assignment
    best_bound = upper_bound
    if emit_progress:
        elapsed = time.time() - t0
        print(f"Feasible at bound={upper_bound} ({elapsed:.1f}s)", flush=True)

    # Coarse linear scan downward — step proportional to starting penalty
    effective_coarse = max(coarse_step, best_bound // 20)
    if emit_progress:
        print(f"Starting coarse scan (step={effective_coarse})...", flush=True)

    current = upper_bound - effective_coarse
    while current >= 0:
        opb_probe, _ = build_full_schedule_opb(config, soft_bound=current)
        try:
            result = runner.solve(opb_probe, timeout=coarse_timeout)
        except Exception:
            if emit_progress:
                print(f"  Timeout at {current}", flush=True)
            break
        if result.satisfiable:
            best_assignment = result.assignment
            best_bound = current
            if emit_progress:
                print(f"  SAT at {current}", flush=True)
            current -= effective_coarse
        else:
            if emit_progress:
                print(f"  UNSAT at {current}", flush=True)
            break

    # Fine scan
    if coarse_step > fine_step:
        fine_start = best_bound - fine_step
        fine_end = max(current, 0)
        if emit_progress:
            print(f"Starting fine scan ({fine_end}..{fine_start}, step={fine_step})...", flush=True)

        current = fine_start
        while current >= fine_end:
            opb_probe, _ = build_full_schedule_opb(config, soft_bound=current)
            try:
                result = runner.solve(opb_probe, timeout=fine_timeout)
            except Exception:
                if emit_progress:
                    print(f"  Timeout at {current}", flush=True)
                break
            if result.satisfiable:
                best_assignment = result.assignment
                best_bound = current
                if emit_progress:
                    print(f"  SAT at {current}", flush=True)
                current -= fine_step
            else:
                if emit_progress:
                    print(f"  UNSAT at {current}", flush=True)
                break

    if emit_progress:
        print(f"Optimal soft penalty: {best_bound}", flush=True)

    # Decode the best solution
    # Re-build var_map for decoding (same config, same soft_bound for var layout)
    _, decode_map = build_full_schedule_opb(config, soft_bound=best_bound)
    return decode_solution(best_assignment, decode_map)


# ---------------------------------------------------------------------------
# Progressive solver (yields events for SSE streaming)
# ---------------------------------------------------------------------------

def solution_to_json(solution: FullScheduleSolution) -> dict:
    """Convert a FullScheduleSolution to a JSON-serializable dict."""
    return {
        "weekly_assignments": solution.weekly_assignments,
        "weekend_assignments": solution.weekend_solution.assignments_by_week,
        "night_assignments": solution.night_solution.assignments_by_week,
        "soft_penalty": solution.soft_penalty,
    }


def solve_full_schedule_progressive(
    config: ScheduleSolverConfig,
    runner: RoundingSatRunner,
    *,
    preview_seconds: tuple[float, ...] = (8.0, 25.0),
    max_seconds: float = 180.0,
    **_legacy,
):
    """Generator that yields solver events for progressive optimization,
    powered by native RoundingSat minimization (:func:`optimize_stream`).

    Each yield is a dict with a ``type`` key:

    - ``{"type": "status", "phase": ..., "vars": ..., "constraints": ...}``
    - ``{"type": "solution", ...solution_to_json..., weekly_penalty, call_penalty,
       optimal, "elapsed": float}``
    - ``{"type": "done", "optimal_penalty": int, "optimal": bool, "total_seconds": float}``
    - ``{"type": "error", "message": str}``

    Streams the first feasible/optimized schedule fast, then improving schedules
    as the budget is spent. ``_legacy`` absorbs old scan-era kwargs harmlessly.
    """
    t0 = time.time()

    yield {"type": "status", "phase": "building"}
    opb_check, var_map = build_full_schedule_opb(config, soft_bound=None)
    yield {
        "type": "status",
        "phase": "built",
        "vars": opb_check.num_vars,
        "constraints": opb_check.num_constraints,
        "soft_indicators": len(var_map.soft_violations),
    }

    yield {"type": "status", "phase": "optimizing"}
    any_solution = False
    last_step = None
    try:
        for step in optimize_stream(
            config, runner,
            preview_seconds=preview_seconds, max_seconds=max_seconds,
        ):
            any_solution = True
            last_step = step
            yield {
                "type": "solution",
                **solution_to_json(step.solution),
                "weekly_penalty": step.weekly_penalty,
                "call_penalty": step.call_penalty,
                "optimal": step.optimal,
                "elapsed": step.elapsed,
            }
    except Exception as exc:  # surface solver/runtime errors to the client
        yield {"type": "error", "message": str(exc)}
        return

    if not any_solution:
        yield {"type": "error", "message": "Infeasible with the current hard constraints"}
        return

    yield {
        "type": "done",
        "optimal_penalty": last_step.total_penalty,
        "optimal": last_step.optimal,
        "total_seconds": time.time() - t0,
    }


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_schedule_config(
    annual_config_path: str | Path,
    standing_config_path: str | Path,
    *,
    night_config: NightSolverConfig | None = None,
    weekend_config: WeekendSolverConfig | None = None,
    night_weights: NightPolicyWeights | None = None,
    night_hard_criteria: frozenset[str] | None = None,
) -> ScheduleSolverConfig:
    """Load schedule solver config from YAML files."""
    annual = yaml.safe_load(Path(annual_config_path).read_text())
    standing = yaml.safe_load(Path(standing_config_path).read_text())

    fellow_groups = annual["fellow_groups"]
    shifts = annual["shifts"]
    constraints = standing_constraints_from_config(standing)

    return ScheduleSolverConfig(
        fellow_groups=fellow_groups,
        shifts=shifts,
        constraints=constraints,
        night_config=night_config or NightSolverConfig(),
        weekend_config=weekend_config or WeekendSolverConfig(),
        night_weights=night_weights or NightPolicyWeights(),
        night_hard_criteria=night_hard_criteria or frozenset(
            {CRITERION_ANAESTHESIA, CRITERION_FRIDAY_WEEKEND_NCC1}
        ),
    )
