"""OPB encoder for the full joint schedule solver.

Extracted from schedule_solver.py — builds the complete pseudo-Boolean formula
for weekly + weekend + night scheduling, encodes all constraints, and decodes
solutions back into structured types.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Any, Callable

from scheduler.fellow_mapping import FellowMapping
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
from parafrost_scheduler.constraint_sink import OpbConstraintSink
from schedule_rules.criteria.group_count_balance import GroupCountBalanceCriterion
from schedule_rules.criteria.weekend_role_pin import WeekendRolePin, PIN as _PIN, FORBID as _FORBID
from schedule_rules.criteria.weekend_role_prerequisite import (
    WeekendRolePrerequisiteCriterion,
)
from schedule_rules.weekly.full_assignment import FullAssignmentCriterion
from schedule_rules.weekly.specific_assignment import SpecificAssignmentCriterion
from schedule_rules.weekly.zero_shifts import ZeroShiftsCriterion
from schedule_rules.weekly.windowed_count_band import WindowedCountBandCriterion
from schedule_rules.criteria.windowed_supervision import WindowedSupervisionCriterion
from schedule_rules.criteria.night_gating import configured_night_gating
from schedule_rules.criteria.weekend_gating import configured_weekend_gating
from schedule_rules.criteria.weekend_night import configured_weekend_night
from schedule_rules.night.night_literal_pin import NightLiteralPin, PIN, FORBID
from schedule_rules.strength import HARD as _HARD, SOFT as _SOFT
from schedule_rules.strength import Strength as _Strength

_GROUP_COUNT_BALANCE_CRITERION = GroupCountBalanceCriterion()
_WEEKEND_ROLE_PIN = WeekendRolePin()
# The near-interchangeable weekday NCC trio. Under relaxed locking, a locked
# fellow's workbook-marked trio week floats freely among these three roles.
_RELAX_NCC_TRIO = ("NCC1", "NCC2", "Swing")
# Standing-tier weekend prerequisites: one archetype, two kind-specialized
# instances (role-set + prereq weekday shift-set). The role-name strings match
# _WEEKEND_ROLE_NAMES so the evaluate-side view lookups agree.
_WEEKEND_STROKE_PREREQ_CRITERION = WeekendRolePrerequisiteCriterion(
    role_names=("Weekend Stroke",), prereq_shifts=("Stroke",),
)
_WEEKEND_NCC_PREREQ_CRITERION = WeekendRolePrerequisiteCriterion(
    role_names=("Weekend NCC1", "Weekend NCC2"), prereq_shifts=("NCC1", "NCC2"),
)
_FULL_ASSIGNMENT_CRITERION = FullAssignmentCriterion()
_SPECIFIC_ASSIGNMENT_CRITERION = SpecificAssignmentCriterion()
_ZERO_SHIFTS_CRITERION = ZeroShiftsCriterion()
_WINDOWED_COUNT_BAND_CRITERION = WindowedCountBandCriterion()
_WINDOWED_SUPERVISION_CRITERION = WindowedSupervisionCriterion()
_NIGHT_LITERAL_PIN = NightLiteralPin()


def _strength_for(criterion: str, hard_criteria: frozenset[str]) -> "_Strength":
    return _HARD if criterion in hard_criteria else _SOFT
from parafrost_scheduler.schedule_types import (
    CALL_ROLES,
    NHS_NIGHT_PENALTY_WEIGHT,
    STROKE_WK2627_WEEKS,
    DUAL_STROKE_EARLY_END,
    DUAL_STROKE_BASE_EARLY,
    DUAL_STROKE_BASE_LATE,
    DUAL_STROKE_NO_HELENA_EARLY,
    DUAL_STROKE_NO_HELENA_LATE,
    _ROLE_NCC1,
    _ROLE_NCC2,
    _ROLE_STROKE,
    _WEEKEND_ROLE_NAMES,
    weekend_role_index,
    weekend_role_name,
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
    day_of_week,
    day_to_week,
    week_day,
    num_weeks_for,
    date_to_day_index,
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
# NF model: day-granular call-tier helpers
# ---------------------------------------------------------------------------


def _encode_call_tier_coverage(opb, call, fellow_names, num_days, start_dow):
    """HARD coverage for the day-granular call tier (NF model).

    Weekdays: exactly one fellow each on NCC1, NCC2, NF.
    Weekends: exactly one each on NCC1, NF; NCC2 forbidden.
    Per (day, fellow): at most one call role.
    """
    opb.add_comment("NF model: call-tier hard coverage (NCC1/NCC2/NF)")
    num_fellows = len(fellow_names)
    for d in range(num_days):
        dow = _day_of_week(d, start_dow)
        weekend = dow in (5, 6)
        for role in CALL_ROLES:
            holders = [call[d][f][role] for f in range(num_fellows)]
            if role == "NCC2" and weekend:
                for v in holders:           # NCC2 forbidden on weekends
                    opb.add_unit(-v)
            else:
                opb.exactly_one(holders)
        for f in range(num_fellows):
            opb.at_most_k([call[d][f][r] for r in CALL_ROLES], 1)



def _encode_call_weekly_link(opb, call, xs, shift_idx, fellow_names,
                             num_days, start_dow, num_weeks):
    """Link the weekly roster label to the day-granular call tier so the weekly
    calendar stays readable (apples-to-apples with Swing-style schedules):
        xs[f][w][role] == 1  iff  the fellow has a day of that call role in week w.
    NOTE (Phase 1 limitation): combined with at-most-one-shift-per-week, a fellow's
    call days within a week share ONE role; multi-role-within-week tours are a later
    continuity concern.
    """
    opb.add_comment("NF model: weekly label <=> day-granular call (per role)")
    num_fellows = len(fellow_names)
    days_in_week: dict[int, list[int]] = {}
    for d in range(num_days):
        days_in_week.setdefault(_day_to_week(d, start_dow), []).append(d)
    for role in CALL_ROLES:
        si = shift_idx.get(role)
        if si is None:
            continue
        for f in range(num_fellows):
            for w, days in days_in_week.items():
                wk_var = xs[f][w][si]
                if wk_var == 0:
                    continue
                day_vars = [call[d][f][role] for d in days]
                for cv in day_vars:                       # each call day => weekly label
                    opb.weighted_sum_at_least([(-cv, 1), (wk_var, 1)], 1)
                # weekly label => some call day
                opb.weighted_sum_at_least([(-wk_var, 1)] + [(cv, 1) for cv in day_vars], 1)


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
        relax = config.relax_locked_ncc_trio
        trio_si = [shift_idx[s] for s in _RELAX_NCC_TRIO if s in shift_idx]
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
                # Relaxed locking: a week the workbook marks NCC1/NCC2/Swing
                # becomes a floating "exactly one of the trio" choice instead of
                # an exact pin. The SET of trio weeks per fellow is unchanged, so
                # each fellow's NCC+Swing total is preserved; only the per-week
                # role floats — the freedom we want for NCC coverage + the Swing
                # deficit. Non-trio weeks (MICU, Elec, Vac, ...) stay literal.
                if relax and shift_name in _RELAX_NCC_TRIO:
                    trio_vars = [xs[f][w][ti] for ti in trio_si if xs[f][w][ti] != 0]
                    if trio_vars:
                        opb.at_least_k(trio_vars, 1)
                        opb.at_most_k(trio_vars, 1)
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
    # Weekend-layer registry walk (Option A): typed config.constraints routed to
    # the weekend layer. EMPTY in this foundation → encodes nothing.
    _encode_weekend_layer_rules(
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
    xn: list[list[int]] = []
    if not config.call_tier_day_granular:
        opb.add_comment("Night assignment variables")
        _diag_disable_nights = os.environ.get("SCHED_DIAG_DISABLE_NIGHTS") == "1"
        for d in range(num_days):
            xn.append([])
            for f in range(num_fellows):
                if _diag_disable_nights or fellow_names[f] in config.night_config.ccm_fellows:
                    xn[d].append(0)
                else:
                    xn[d].append(opb.new_var())
        _encode_night_constraints(
            opb, xn, xs, wr, config, fellow_names, shift_idx, soft_violations,
        )
        # Night-layer registry walk (Option A): typed config.constraints routed to
        # the night layer. EMPTY in this foundation → encodes nothing.
        _encode_night_layer_rules(
            opb, xn, xs, wr, config, fellow_mapping, fellow_names, shift_idx, soft_violations,
        )

    # -------------------------------------------------------------------
    # 6b. Day-granular call vars: call[d][f][role]  (NF model only)
    # -------------------------------------------------------------------
    call: list[list[dict[str, int]]] = []
    if config.call_tier_day_granular:
        opb.add_comment("NF model: day-granular call variables (NCC1/NCC2/NF)")
        for d in range(num_days):
            call.append([])
            for f in range(num_fellows):
                call[d].append({role: opb.new_var() for role in CALL_ROLES})
        _encode_call_tier_coverage(
            opb, call, fellow_names, num_days, start_dow)
        _encode_call_weekly_link(
            opb, call, xs, shift_idx, fellow_names, num_days, start_dow, num_weeks)

    # The former `call_rules` channel is fully dissolved: all its types now route
    # through the typed config.constraints pipeline (weekly/weekend/night layer
    # walks), including dual_stroke_window (kind handled by the weekly walk via
    # _encode_dual_stroke_window_rule).

    # -------------------------------------------------------------------
    # 7c. Experimental Stroke variants (default off; flags set by --variant)
    # -------------------------------------------------------------------
    opb.add_comment("Stroke variants: wk26/27 toggle + dual-stroke Helena preference")
    _encode_stroke_wk2627_toggle(opb, xs, config, fellow_names, shift_idx, soft_violations)
    _encode_dual_stroke_helena(opb, xs, config, fellow_names, shift_idx, soft_violations)

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
        call=call,
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
    "no_isolated_week",
})


# ---------------------------------------------------------------------------
# Layer-routed constraint registries (Option A)
# ---------------------------------------------------------------------------
# config.constraints is the single, unified typed-constraint list. Each kind is
# routed to the LAYER that owns its variables: "weekly" (xs), "weekend" (wr), or
# "night" (xn). build_full_schedule_opb makes one registry walk per layer, AFTER
# that layer's vars are allocated; a walk dispatches only the kinds whose layer
# is its own and SKIPS the rest. A kind absent from EVERY registry is a typo and
# fails fast in the weekly walk (the `handlers` dict in _encode_weekly_rules).
#
# The weekly registry is built inside _encode_weekly_rules (its handlers close
# over module-level encoder fns defined throughout this file). The weekend and
# night registries start EMPTY: this is a pure foundation seam. A migrating
# stage adds {kind: handler} entries here — a handler matching the layer-walk
# call shape (see _encode_weekend_layer_rules / _encode_night_layer_rules) — and
# nothing else in build_full_schedule_opb changes.
_WEEKEND_HANDLERS: dict[str, Callable] = {}
_NIGHT_HANDLERS: dict[str, Callable] = {}
# Gating-criteria kinds encoded specially inside the night-policy / weekend
# passes (NOT via a registry walk) — they need the dual-stroke exemption vars and
# the weekday/weekend/night var maps together. Listed so the weekly walk's
# unknown-kind guard skips rather than rejects them.
_NIGHT_POLICY_KINDS: frozenset[str] = frozenset(
    {"night_gating", "weekend_gating", "weekend_night"})


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
        "no_isolated_week": _encode_no_isolated_week,
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
        "group_count_balance": _encode_group_count_balance,
        "dual_stroke_window": _encode_dual_stroke_window_rule,
    }

    for constraint in config.constraints:
        handler = handlers.get(constraint.kind)
        if handler is None:
            # A kind owned by another layer (weekend/night) is encoded by that
            # layer's walk after its vars are allocated — skip it here, don't
            # raise. Only a kind registered to NO layer is a typo.
            if (constraint.kind in _WEEKEND_HANDLERS
                    or constraint.kind in _NIGHT_HANDLERS
                    or constraint.kind in _NIGHT_POLICY_KINDS):
                continue
            name = constraint.params.get("name", constraint.kind)
            known = sorted({*handlers, *_WEEKEND_HANDLERS, *_NIGHT_HANDLERS, *_NIGHT_POLICY_KINDS})
            raise ValueError(
                f"Unknown constraint kind '{constraint.kind}' "
                f"(rule: {name!r}). Known kinds: {', '.join(known)}. "
                f"A constraint must use a kind registered to the weekly, weekend, "
                f"or night layer; call rules belong in call_rules, not constraints."
            )
        fellow_indices = _resolve_fellow_indices(fellow_mapping, constraint.fellows)
        # Per-fellow rules are normally skipped for locked (IMPORTED) fellows —
        # their weekly layer is frozen, so per-fellow weekly rules don't apply.
        # EXCEPTION: under relaxed locking the trio weeks float (no longer frozen),
        # so a rule that declares `applies_under_relaxed_locks: true` keeps binding
        # those fellows. The skip is the encoder's only relax-aware seam; the rules
        # themselves stay in config (no rule is defined inline here).
        skip_locked = (
            locked_fellow_indices and constraint.kind in PER_FELLOW_KINDS
            and not (config.relax_locked_ncc_trio
                     and constraint.params.get("applies_under_relaxed_locks", False))
        )
        if skip_locked:
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


def _encode_weekend_layer_rules(
    opb: OpbBuilder,
    wr: list,
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_mapping: FellowMapping,
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
) -> None:
    """Weekend-layer registry walk: encode typed config.constraints whose kind is
    routed to the weekend layer. Runs AFTER wr is allocated. Skips kinds owned by
    other layers (they fail fast in the weekly walk if registered nowhere).

    EMPTY in this foundation (_WEEKEND_HANDLERS == {}): the loop dispatches
    nothing → zero new constraints. A migrating stage registers {kind: handler}
    in _WEEKEND_HANDLERS; the handler is called with the kwargs below."""
    for constraint in config.constraints:
        handler = _WEEKEND_HANDLERS.get(constraint.kind)
        if handler is None:
            continue
        opb.add_comment(
            f"Rule: {constraint.params.get('name', constraint.kind)} "
            f"({constraint.strength.value})"
        )
        handler(
            opb, wr, constraint,
            xs=xs,
            config=config,
            fellow_mapping=fellow_mapping,
            fellow_names=fellow_names,
            shift_idx=shift_idx,
            soft_violations=soft_violations,
        )


def _encode_weekend_prerequisite_rule(
    opb: OpbBuilder,
    wr: list,
    constraint,
    *,
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_mapping: FellowMapping,
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
) -> None:
    """Weekend-layer handler for the WeekendRolePrerequisite archetype (Standing
    tier). Routes the two prerequisite kinds through the one co-located Criterion,
    reproducing the encoder's prior `_encode_weekend_prerequisites` emission
    EXACTLY (same fellow/week/role iteration order, same per-cell shapes).

    constraint.strength decides hard-vs-soft per cell (HARD ⇒ forbid the role when
    no qualifying service is possible; SOFT ⇒ a penalized slack). This is the
    typed-pipeline equivalent of the old function's `soft_violations is None`
    switch."""
    if constraint.kind == "weekend_ncc_prerequisite":
        criterion = _WEEKEND_NCC_PREREQ_CRITERION
        role_indices = (_ROLE_NCC1, _ROLE_NCC2)
        prereq_si = [shift_idx[s] for s in ("NCC1", "NCC2") if s in shift_idx]
    else:  # "weekend_stroke_prerequisite"
        criterion = _WEEKEND_STROKE_PREREQ_CRITERION
        role_indices = (_ROLE_STROKE,)
        prereq_si = [shift_idx[s] for s in ("Stroke",) if s in shift_idx]
    if not prereq_si:
        return

    exempt = set(constraint.params.get("exempt_fellows", []))
    for g in constraint.params.get("exempt_groups", []):
        exempt.update(config.fellow_groups.get(g, []))

    strength = _SOFT if constraint.strength == ConstraintStrength.SOFT else _HARD
    weight = config.weekly_soft_weight
    sink = OpbConstraintSink(opb, soft_violations)
    num_weeks = config.num_weeks

    for fi, name in enumerate(fellow_names):
        if name in exempt:
            continue
        for w in range(num_weeks):
            for role_idx in role_indices:
                if fi not in wr[w][role_idx]:
                    continue
                # Inclusive window (w' <= w): same-week weekday service counts.
                prior = [xs[fi][wp][si]
                         for wp in range(w + 1)
                         for si in prereq_si
                         if xs[fi][wp][si] != 0]
                criterion.encode(
                    sink,
                    role_var=wr[w][role_idx][fi],
                    prior_vars=prior,
                    strength=strength,
                    weight=weight,
                )


# Register the two prerequisite kinds (kind strings == the old call-rule type
# strings, so the orchestrator's later YAML flip is a pure rename). Both delegate
# to the one archetype handler. These fire ONLY for kinds in config.constraints;
# the shipped config keeps these rules in call_rules (encoded by the still-wired
# _encode_weekend_prerequisites), so no cell is double-encoded at the default.
_WEEKEND_HANDLERS["weekend_stroke_prerequisite"] = _encode_weekend_prerequisite_rule
_WEEKEND_HANDLERS["weekend_ncc_prerequisite"] = _encode_weekend_prerequisite_rule


def _encode_night_layer_rules(
    opb: OpbBuilder,
    xn: list[list[int]],
    xs: list[list[list[int]]],
    wr: list,
    config: ScheduleSolverConfig,
    fellow_mapping: FellowMapping,
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
) -> None:
    """Night-layer registry walk: encode typed config.constraints whose kind is
    routed to the night layer. Runs AFTER xn is allocated. Skips kinds owned by
    other layers.

    EMPTY in this foundation (_NIGHT_HANDLERS == {}): dispatches nothing → zero
    new constraints. A migrating stage registers {kind: handler} in
    _NIGHT_HANDLERS; the handler is called with the kwargs below."""
    for constraint in config.constraints:
        handler = _NIGHT_HANDLERS.get(constraint.kind)
        if handler is None:
            continue
        opb.add_comment(
            f"Rule: {constraint.params.get('name', constraint.kind)} "
            f"({constraint.strength.value})"
        )
        handler(
            opb, xn, constraint,
            xs=xs,
            wr=wr,
            config=config,
            fellow_mapping=fellow_mapping,
            fellow_names=fellow_names,
            shift_idx=shift_idx,
            soft_violations=soft_violations,
        )


# ---------------------------------------------------------------------------
# Weekend-layer adapters (S3): weekend role pin/forbid
# ---------------------------------------------------------------------------
# Two thin adapters delegating to the ONE WeekendRolePin archetype. They own the
# wr/eligibility/role-expansion coordinate work; the archetype only signs the
# resolved vars (mirrors the weekly adapters that pass pre-built var lists).
def _resolve_pin_fellow_index(constraint, fellow_names):
    """The single fellow targeted by an annual weekend pin (by_names selector),
    or None if absent/unknown — mirroring the old branch's silent skip."""
    if constraint.fellows is None or not constraint.fellows.names:
        return None
    name = constraint.fellows.names[0]
    if name not in fellow_names:
        return None
    return fellow_names.index(name)


def _encode_specific_weekend_assignment(opb, wr, constraint, *, xs, config,
                                        fellow_mapping, fellow_names, shift_idx,
                                        soft_violations):
    """PIN one weekend role true for a fellow across the given weeks.

    Reproduces the old call_rules `specific_weekend_assignment` branch exactly:
    role token -> wr index via the canonical _WEEKEND_ROLE_NAMES helper; per week
    in range with the fellow weekend-eligible (fi in wr[w][role_idx]), pin the
    var. Resolved vars handed to WeekendRolePin.encode (action=pin)."""
    fi = _resolve_pin_fellow_index(constraint, fellow_names)
    if fi is None:
        return
    role_idx = weekend_role_index(constraint.params.get("role", ""))
    if role_idx is None:
        return
    num_weeks = config.num_weeks
    role_vars = [
        wr[w][role_idx][fi]
        for w in constraint.params.get("weeks", [])
        if 0 <= w < num_weeks and fi in wr[w][role_idx]
    ]
    _WEEKEND_ROLE_PIN.encode(
        OpbConstraintSink(opb, soft_violations), role_vars=role_vars, action=_PIN)


def _encode_blocked_weekend(opb, wr, constraint, *, xs, config, fellow_mapping,
                            fellow_names, shift_idx, soft_violations):
    """FORBID all three weekend roles for a fellow across the given weeks.

    Reproduces the old call_rules `blocked_weekend` branch exactly: per week in
    range, per role_idx in range(3) with the fellow weekend-eligible, forbid the
    var. Resolved vars handed to WeekendRolePin.encode (action=forbid)."""
    fi = _resolve_pin_fellow_index(constraint, fellow_names)
    if fi is None:
        return
    num_weeks = config.num_weeks
    role_vars = [
        wr[w][role_idx][fi]
        for w in constraint.params.get("weeks", [])
        if 0 <= w < num_weeks
        for role_idx in range(3)
        if fi in wr[w][role_idx]
    ]
    _WEEKEND_ROLE_PIN.encode(
        OpbConstraintSink(opb, soft_violations), role_vars=role_vars, action=_FORBID)


# Register the weekend-layer handlers. Kind strings are kept EQUAL to the old
# call-rule type strings so the later YAML flip is a pure rename (the cutover
# adds these kinds to config.constraints and drops the call_rules branches).
_WEEKEND_HANDLERS["specific_weekend_assignment"] = _encode_specific_weekend_assignment
_WEEKEND_HANDLERS["blocked_weekend"] = _encode_blocked_weekend


def _encode_full_assignment(opb, xs, constraint, fellow_indices, **kw):
    """Each fellow in scope must be assigned exactly one shift per week.

    Thin adapter: builds the per-(fellow, week) active-shift var lists and
    delegates emission to the co-located FullAssignmentCriterion (ADR-0005)."""
    num_weeks = kw["num_weeks"]
    num_shifts = len(kw["shifts"])
    weight = kw["config"].weekly_soft_weight
    strength = _SOFT if constraint.strength == ConstraintStrength.SOFT else _HARD

    week_var_lists = []
    for f in fellow_indices:
        for w in range(num_weeks):
            active = [xs[f][w][s] for s in range(num_shifts) if xs[f][w][s] != 0]
            week_var_lists.append(active)

    _FULL_ASSIGNMENT_CRITERION.encode(
        OpbConstraintSink(opb, kw["soft_violations"]),
        week_var_lists=week_var_lists,
        strength=strength,
        weight=weight,
    )


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


def _encode_no_isolated_week(opb, xs, constraint, fellow_indices, **kw):
    """Forbid an ISOLATED week on the target shift set: a fellow on the shift in
    week w must also be on it in w-1 or w+1. Combined with an exactly-N total this
    forces the weeks CONTIGUOUS (a block of >=2) ANYWHERE — unlike all_or_none_block
    it does NOT pin blocks to fixed even-week boundaries.

    Per week w, with `cur` = the fellow's OR-over-target-shifts indicator:
        cur => (prev OR next)   ==   ~cur + prev + next >= 1
    (boundary terms dropped at the horizon edges). Hard by default; soft adds a
    per-week penalty indicator that, when 1, satisfies the clause (never UNSAT)."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]
    is_soft = constraint.strength == ConstraintStrength.SOFT
    weight = kw["config"].weekly_soft_weight
    soft_violations = kw["soft_violations"]

    target_shifts = list(constraint.shifts.shifts) if constraint.shifts else []
    s_indices = [shift_idx[s] for s in target_shifts if s in shift_idx]
    if not s_indices:
        return

    def _week_indicator(f, w):
        """The fellow's OR-over-target-shifts var for week w, or 0 if none possible."""
        week_vars = [xs[f][w][si] for si in s_indices if xs[f][w][si] != 0]
        if not week_vars:
            return 0
        if len(week_vars) == 1:
            return week_vars[0]
        aux = opb.new_var()
        for sv in week_vars:
            opb.weighted_sum_at_least([(aux, 1), (-sv, 1)], 1)
        opb.weighted_sum_at_least([(v, 1) for v in week_vars] + [(-aux, 1)], 1)
        return aux

    for f in fellow_indices:
        for w in range(num_weeks):
            cur = _week_indicator(f, w)
            if cur == 0:
                continue
            terms = [(-cur, 1)]
            if w - 1 >= 0:
                prev = _week_indicator(f, w - 1)
                if prev != 0:
                    terms.append((prev, 1))
            if w + 1 < num_weeks:
                nxt = _week_indicator(f, w + 1)
                if nxt != 0:
                    terms.append((nxt, 1))
            if is_soft:
                v = opb.new_var()
                terms.append((v, 1))  # v=1 satisfies the clause (pays penalty)
                soft_violations.append((v, weight))
            opb.weighted_sum_at_least(terms, 1)


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
    """In each block, fellow must be on one of the allowed shift-set choices (or none).

    HARD: each block conforms to some choice (or none-if-allowed) — a structural
    requirement enforced via conditional_exactly_k selectors.

    SOFT: a per-block penalty when the block conforms to no choice. The soft path
    is GENUINELY soft — it emits NO hard clause on the xs vars. Each choice gets a
    conform selector `cf` defined ONE-directionally: `cf ⟹ every week in the block
    holds a choice-shift` (`sum(choice-vars at w) + ~cf >= 1`). That clause only
    ever constrains the aux var cf (the solver escapes by setting cf=0); it never
    forces an xs var true. `cf=1` is achievable iff the block truly conforms, so
    the penalty `pen` (absorbing when no selector holds) fires exactly on a
    non-conforming block. Reusing conditional_exactly_k here (as the old code did)
    was the bug: its at-least-k line collapses to a hard `sum(xs) >= block_len`
    when the term count equals block_len, so a "soft" rule could flip the model
    UNSAT. See ADR/feedback: a soft rule must never emit an ungated hard clause.
    """
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

            if is_soft:
                _emit_soft_block_choice(
                    opb, xs, f, block_start, block_end, choices, allow_none,
                    shift_idx, trigger_indices, soft_violations, weight)
                continue

            # HARD path (unchanged): selector ⟺ exactly block_len choice terms.
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
                if choice_terms:
                    opb.conditional_exactly_k(choice_terms, block_len, sel)
                else:
                    opb.add_unit(-sel)  # choice impossible this block
            if allow_none:
                sel_none = opb.new_var()
                selectors.append(sel_none)
                trigger_terms = []
                for w in range(block_start, block_end):
                    for si in trigger_indices:
                        if xs[f][w][si] != 0:
                            trigger_terms.append(xs[f][w][si])
                if trigger_terms:
                    opb.conditional_exactly_k(trigger_terms, 0, sel_none)
            if selectors:
                opb.at_least_k(selectors, 1)


def _emit_soft_block_choice(
    opb, xs, f, block_start, block_end, choices, allow_none,
    shift_idx, trigger_indices, soft_violations, weight,
):
    """Genuinely-soft block-choice penalty for one (fellow, block). NO hard clause
    on xs vars — see _encode_block_shift_set_choice docstring."""
    conform_vars = []
    for choice in choices:
        choice_indices = [shift_idx[s] for s in choice if s in shift_idx]
        cf = opb.new_var()
        # cf ⟹ every week in the block holds a choice-shift.
        for w in range(block_start, block_end):
            cterms = [xs[f][w][si] for si in choice_indices if xs[f][w][si] != 0]
            # sum(cterms) + ~cf >= 1: cf=1 needs a choice-shift in week w; cf=0 vacuous.
            # (cterms empty ⇒ +1 ~cf >= 1 ⇒ cf forced 0, never conforms — correct.)
            opb.weighted_sum_at_least([(v, 1) for v in cterms] + [(-cf, 1)], 1)
        conform_vars.append(cf)
    if allow_none:
        cn = opb.new_var()
        # cn ⟹ all trigger shifts off in the block (at_most_1 of trigger var + cn).
        for w in range(block_start, block_end):
            for si in trigger_indices:
                if xs[f][w][si] != 0:
                    opb.at_most_k([xs[f][w][si], cn], 1)
        conform_vars.append(cn)
    pen = opb.new_var()
    # Some choice conforms, OR pay the penalty.
    opb.at_least_k(conform_vars + [pen], 1)
    soft_violations.append((pen, weight))


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
    """A specific shift must be filled by someone from fellow_indices in a given
    week. Thin adapter delegating to SpecificAssignmentCriterion (ADR-0005)."""
    shift_idx = kw["shift_idx"]
    weight = kw["config"].weekly_soft_weight
    soft_violations = kw["soft_violations"]
    strength = _SOFT if constraint.strength == ConstraintStrength.SOFT else _HARD

    if not constraint.shifts or not constraint.weeks:
        return

    shift_name = constraint.shifts.shifts[0]
    si = shift_idx.get(shift_name)
    week = constraint.weeks.start
    if si is None:
        return

    vars_for_week = [xs[f][week][si] for f in fellow_indices if xs[f][week][si] != 0]

    _SPECIFIC_ASSIGNMENT_CRITERION.encode(
        OpbConstraintSink(opb, soft_violations),
        vars_for_week=vars_for_week,
        strength=strength,
        weight=weight,
    )


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
        (the "consec_weekend_buffer" shift attribute).

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


def _encode_ncc_weekend_alignment(
    opb: OpbBuilder,
    wr: list[list[dict[int, int]]],
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
) -> None:
    """SOFT nudge toward NCC weekday/weekend role alignment: a fellow on weekday
    NCC1 should take Weekend NCC1 (not NCC2), and vice versa. The two NCC weekend
    roles are interchangeable to every other constraint, so this is the only
    signal distinguishing them by the holder's weekday NCC service.
    """
    weight = config.ncc_weekend_misalign_penalty
    if weight == 0:
        return
    s_ncc1 = shift_idx.get("NCC1")
    s_ncc2 = shift_idx.get("NCC2")
    if s_ncc1 is None or s_ncc2 is None:
        return
    num_weeks = config.num_weeks

    def _and_penalty(a: int, b: int) -> None:
        pen = opb.new_var()
        opb.weighted_sum_at_most([(a, 1), (b, 1), (-pen, 1)], 2)
        opb.weighted_sum_at_least([(a, 1), (-pen, 1)], 1)
        opb.weighted_sum_at_least([(b, 1), (-pen, 1)], 1)
        soft_violations.append((pen, weight))

    for w in range(num_weeks):
        for f in range(len(fellow_names)):
            wd1 = xs[f][w][s_ncc1]
            wd2 = xs[f][w][s_ncc2]
            # Weekday NCC1 but Weekend NCC2.
            if wd1 != 0 and f in wr[w][_ROLE_NCC2]:
                _and_penalty(wd1, wr[w][_ROLE_NCC2][f])
            # Weekday NCC2 but Weekend NCC1.
            if wd2 != 0 and f in wr[w][_ROLE_NCC1]:
                _and_penalty(wd2, wr[w][_ROLE_NCC1][f])


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

    # NCC weekday/weekend role alignment (soft nudge). This is distinct from the
    # weekend_role_mismatch criterion: it expresses a PREFERENCE between the two
    # interchangeable NCC weekend roles (weekday-NCC1 prefers Weekend-NCC1 over
    # Weekend-NCC2), which the one-role→one-shift mismatch ALIGN shape can't model.
    opb.add_comment("Weekend: NCC1/NCC2 weekday-weekend alignment (soft)")
    _encode_ncc_weekend_alignment(
        opb, wr, xs, config, fellow_names, shift_idx, soft_violations,
    )
    # (The former dedicated Stroke weekday→weekend alignment nudge is folded into
    # the weekend_role_mismatch criterion's per-role weight — Weekend Stroke = 60.)

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
        # week w+2 weekday service is "light" (the "consec_weekend_buffer" attr).
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
        buffer_indices = [shift_idx[s] for s in config.shift_palette.shifts_with_attribute("consec_weekend_buffer") if s in shift_idx]
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

    # Weekend role prerequisites (Stroke/NCC) are now typed config.constraints
    # routed through the weekend-layer registry walk (_encode_weekend_prerequisite_rule),
    # not this legacy call_rules pass.

    # Weekend-role gating criteria (the WeekendGatingCriterion archetype): role/
    # weekday mismatch (ALIGN) and pre-vacation weekend (GATE), now config-driven.
    opb.add_comment("Weekend: weekend-role gating criteria (soft)")
    _encode_configured_weekend_gating(opb, wr, xs, config, fellow_names, shift_idx, soft_violations)


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

    blocked_indices = [shift_idx[s] for s in config.shift_palette.shifts_with_attribute("weekend_blocked") if s in shift_idx]
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


def _encode_configured_weekend_gating(
    opb: OpbBuilder,
    wr: list[list[dict[int, int]]],
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
) -> None:
    """Encode every `weekend_gating` constraint via the WeekendGatingCriterion
    archetype (ADR-0005). ALIGN: per week×role×holder, the gate var is the
    matching weekday-shift var (role_to_shift), or None when the fellow cannot be
    on that shift. GATE: per week×role×holder, the gate var is the gating service
    in the offset week (skip when absent). Per-criterion weight is sourced as the
    legacy inline code did, keyed off the criterion name."""
    configured = configured_weekend_gating(config.constraints)
    if not configured:
        return
    num_weeks = config.num_weeks
    sink = OpbConstraintSink(opb, soft_violations)
    for cwg in configured:
        crit = cwg.criterion
        if crit.mode == "align":
            # Per-role weight (role_weights from config), defaulting to
            # weekend_mismatch_weight. This carries the folded-in former
            # stroke_weekend_misalign penalty (Weekend Stroke -> 60).
            role_weights = getattr(crit, "role_weights", {}) or {}
            role_to_idx = {r: weekend_role_index(r) for r in crit.role_to_shift}
            for w in range(num_weeks):
                for role, role_idx in role_to_idx.items():
                    if role_idx is None:
                        continue
                    si = shift_idx.get(crit.role_to_shift[role])
                    w_role = role_weights.get(role, config.weekend_mismatch_weight)
                    for f, role_var in wr[w][role_idx].items():
                        weekday_var = xs[f][w][si] if si is not None else 0
                        crit.encode(
                            sink, role_var=role_var,
                            gate_var=(weekday_var if weekday_var != 0 else None),
                            strength=cwg.strength, weight=w_role,
                        )
        else:  # gate
            weight = 1  # prevacation GATE used a literal weight of 1
            targets = cwg.resolved_targets.get(crit.gate_target_key, frozenset())
            target_indices = [shift_idx[s] for s in targets if s in shift_idx]
            role_indices = [weekend_role_index(r) for r in crit.roles]
            for w in range(num_weeks):
                gw = w + crit.gate_week_offset
                if not (0 <= gw < num_weeks):
                    continue
                for f in range(len(fellow_names)):
                    gate_vars = [xs[f][gw][si] for si in target_indices if xs[f][gw][si] != 0]
                    if not gate_vars:
                        continue
                    for role_idx in role_indices:
                        if role_idx is None or f not in wr[w][role_idx]:
                            continue
                        for gate_var in gate_vars:
                            crit.encode(
                                sink, role_var=wr[w][role_idx][f],
                                gate_var=gate_var, strength=cwg.strength, weight=weight,
                            )


def _encode_configured_weekend_night(
    opb: OpbBuilder,
    xn: list[list[int]],
    wr: list[list[dict[int, int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    soft_violations: list[tuple[int, int]],
) -> None:
    """Encode every `weekend_night` constraint via the WeekendNightCriterion
    archetype (ADR-0005): the relationship between a fellow's weekend role and
    their weekend-night call. Per criterion (one weekend night dow), per fellow
    who could take that night, resolve the fellow's weekend-role vars for the
    criterion's roles and delegate to the archetype's encode. For REQUIRE, a
    fellow holding no var for any required role is `eligible=False` (the
    eligibility-forbid)."""
    configured = configured_weekend_night(config.constraints)
    if not configured:
        return
    num_weeks = config.num_weeks
    num_days = config.num_days
    start_dow = config.start_dow
    num_fellows = len(fellow_names)
    sink = OpbConstraintSink(opb, soft_violations)
    for cwn in configured:
        crit = cwn.criterion
        role_idx = {r: weekend_role_index(r) for r in crit.roles}
        for w in range(num_weeks):
            d = _week_day(w, crit.dow, start_dow)
            if not (0 <= d < num_days):
                continue
            for f in range(num_fellows):
                if xn[d][f] == 0:
                    continue
                role_vars = {
                    r: wr[w][ri][f]
                    for r, ri in role_idx.items()
                    if ri is not None and f in wr[w][ri]
                }
                eligible = bool(role_vars)  # can hold at least one required role
                # Strength is per-role (role_strengths), resolved inside crit.encode; the SemanticConstraint.strength is intentionally INERT for weekend_night — see tests/test_dispatch_strength_weekend_night.py::test_constraint_level_strength_is_inert_for_weekend_night
                crit.encode(sink, night_var=xn[d][f], role_vars=role_vars,
                            eligible=eligible)


# ---------------------------------------------------------------------------
# Night constraint encoders
# ---------------------------------------------------------------------------

def _encode_nhs_week_nights(
    opb: OpbBuilder,
    xn: list[list[int]],
    wr: list[list[dict[int, int]]],
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
) -> None:
    """NHS week: a fellow on NHS may work ONLY Monday + Tuesday night that week,
    and holds NO weekend role.

    Mon/Tue night each get a soft NHS_NIGHT_PENALTY_WEIGHT penalty (they SHOULDN'T
    have to, but may); Wed/Thu/Fri/Sat/Sun nights of the NHS week are hard-
    forbidden. All three weekend roles that week are likewise hard-forbidden (an
    NHS week is a primary-fellowship commitment, like AAN). Gated on the NHS shift
    var, so only whoever is on NHS is affected.
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
            # No weekend role on an NHS week (hard): at_most_1 of NHS-gate + role.
            for role_idx in range(3):
                if f in wr[w][role_idx]:
                    opb.at_most_k([nhs_var, wr[w][role_idx][f]], 1)


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


def _encode_nh_courtesy_weeks(
    opb: OpbBuilder,
    xn: list[list[int]],
    wr: list[list[dict[int, int]]],
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
) -> None:
    """Courtesy penalty: an NH-group fellow on AAN (or ABPN) for a week should
    avoid call that week. We only manage part of the NH fellows' time, and their
    primary fellowship may rely on the AAN/ABPN week being light. Per-occurrence
    SOFT penalty (each night worked + each weekend role held that week). Gated on
    the AAN/ABPN weekly shift var, so only the pinned fellow/week is affected.

    If config.nh_aan_week_call_hard is set, the AAN family becomes a HARD forbid
    (at-most-1 of gate + each night/weekend var) instead of a soft penalty; ABPN
    stays soft regardless.
    """
    nh_indices = set()
    for name in config.fellow_groups.get("NH", []):
        if name in fellow_names:
            nh_indices.add(fellow_names.index(name))
    if not nh_indices:
        return
    num_weeks = config.num_weeks
    start_dow = config.start_dow
    num_days = config.num_days

    def _and_penalty(a: int, b: int, weight: int) -> None:
        # pen = a AND b: pen >= a + b - 1; pen <= a; pen <= b.
        pen = opb.new_var()
        opb.weighted_sum_at_most([(a, 1), (b, 1), (-pen, 1)], 2)
        opb.weighted_sum_at_least([(a, 1), (-pen, 1)], 1)
        opb.weighted_sum_at_least([(b, 1), (-pen, 1)], 1)
        soft_violations.append((pen, weight))

    for shift_name, weight in (("AAN", config.nh_aan_week_call_penalty),
                               ("ABPN", config.nh_abpn_week_call_penalty)):
        # AAN may be promoted to a HARD forbid; ABPN is always soft. In hard mode
        # the soft weight is ignored (a 0 weight does NOT disable the hard forbid).
        hard = shift_name == "AAN" and config.nh_aan_week_call_hard
        if weight == 0 and not hard:
            continue
        si = shift_idx.get(shift_name)
        if si is None:
            continue
        for w in range(num_weeks):
            for f in nh_indices:
                gate = xs[f][w][si]
                if gate == 0:
                    continue
                # Each night the fellow works that week.
                for dow in range(7):
                    d = _week_day(w, dow, start_dow)
                    if 0 <= d < num_days and xn[d][f] != 0:
                        if hard:
                            opb.at_most_k([gate, xn[d][f]], 1)
                        else:
                            _and_penalty(gate, xn[d][f], weight)
                # Each weekend role the fellow holds that week.
                for role_idx in range(3):
                    if f in wr[w][role_idx]:
                        if hard:
                            opb.at_most_k([gate, wr[w][role_idx][f]], 1)
                        else:
                            _and_penalty(gate, wr[w][role_idx][f], weight)


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
    night_blocked_all_week = config.shift_palette.shifts_with_attribute("night_blocked_all_week")
    all_week_blocked = [shift_idx[s] for s in night_blocked_all_week if s in shift_idx]
    weekday_only_shifts = set(config.shift_palette.shifts_with_attribute("night_blocked"))
    # Variant: ABPN blocks prior-Sun..Thu weekday night call (like ISC/AAN).
    if config.abpn_night_block and "ABPN" in shift_idx:
        weekday_only_shifts.add("ABPN")
    weekday_only_blocked = [shift_idx[s] for s in weekday_only_shifts if s in shift_idx and s not in night_blocked_all_week]
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
                holiday_shifts = [shift_idx[s] for s in config.shift_palette.shifts_with_attribute("holiday_eligible") if s in shift_idx]
                eligible_vars = [xs[f][week_idx][si] for si in holiday_shifts if xs[f][week_idx][si] != 0]
                if eligible_vars:
                    # xn[d][f] -> OR(eligible): sum(eligible) + (1 - xn) >= 1
                    opb.weighted_sum_at_least(
                        [(v, 1) for v in eligible_vars] + [(-xn[d][f], 1)], 1
                    )
                else:
                    opb.add_unit(-xn[d][f])

    # NHS week: only Mon+Tue night (soft 100); Wed-Sun hard-forbidden; no weekend role.
    opb.add_comment("Night/Weekend: NHS week (Mon/Tue soft, Wed-Sun + weekend roles forbidden)")
    _encode_nhs_week_nights(opb, xn, wr, xs, config, fellow_names, shift_idx, soft_violations)

    # Week before AAN: forbid weekend roles + Fri/Sat/Sun nights.
    opb.add_comment("Night/Weekend: forbid the weekend before AAN")
    _encode_pre_aan_forbid(opb, xn, wr, xs, config, fellow_names, shift_idx)

    # NH courtesy: AAN/ABPN week call penalty (soft, per-occurrence).
    opb.add_comment("Night/Weekend: NH AAN/ABPN-week courtesy penalty")
    _encode_nh_courtesy_weeks(opb, xn, wr, xs, config, fellow_names, shift_idx, soft_violations)

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

    # Weekend night ↔ weekend role (WeekendNightCriterion archetype, config-driven).
    _encode_configured_weekend_night(opb, xn, wr, config, fellow_names, soft_violations)

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


def _encode_configured_night_gating(
    opb, xn, xs, wr, config, fellow_names, shift_idx, soft_violations,
    *, num_days, start_dow, num_weeks, num_fellows, dual_stroke_vars,
    weights, hard_criteria,
) -> None:
    """Encode every `night_gating` constraint via the NightGatingCriterion
    archetype (ADR-0005 — same object the evaluator uses). For each criterion,
    walk days×fellows; for each gating term the night fires on, resolve the
    term's solver vars (weekday-shift vars in the resolved target set, or the
    weekend-role var) and the gating week's dual-stroke exemption var, then emit.

    A weekday-shift term ORs the resolved-set's shift vars for that fellow/week;
    a weekend-role term contributes the single role var. Per-criterion weight and
    hard/soft are looked up by the criterion name (preserving the existing
    night_weights / night_hard_criteria plumbing and the CRITERION_* keys)."""
    configured = configured_night_gating(config.constraints)
    if not configured:
        return
    sink = OpbConstraintSink(opb, soft_violations)
    for cng in configured:
        crit = cng.criterion
        # Resolve each weekday-shift target key to its shift-index list once.
        target_indices = {
            key: [shift_idx[s] for s in shifts if s in shift_idx]
            for key, shifts in cng.resolved_targets.items()
        }
        strength = _strength_for(crit.name, hard_criteria)
        weight = weights.for_criterion(crit.name)
        for d in range(num_days):
            week = _day_to_week(d, start_dow)
            dow = _day_of_week(d, start_dow)
            for f in range(num_fellows):
                if xn[d][f] == 0:
                    continue
                for term in crit.gating_terms(week, dow):
                    tw = term.week
                    if not (0 <= tw < num_weeks):
                        continue
                    if term.is_weekend_role:
                        role_idx = weekend_role_index(term.target_key)
                        var = wr[tw][role_idx].get(f, 0) if role_idx is not None else 0
                        term_vars = [var] if var != 0 else []
                    else:
                        term_vars = [xs[f][tw][si] for si in target_indices.get(term.target_key, [])
                                     if xs[f][tw][si] != 0]
                    if not term_vars:
                        continue
                    exempt_var = None
                    if crit.exemption is not None:
                        ds = dual_stroke_vars[tw] if tw < len(dual_stroke_vars) else 0
                        exempt_var = ds if ds != 0 else None
                    crit.encode(
                        sink, night_var=xn[d][f], term_vars=term_vars,
                        exempt_var=exempt_var, strength=strength, weight=weight,
                    )


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

    # Config-driven night-gating criteria (the NightGatingCriterion archetype).
    # Each migrated criterion is a `night_gating` entry in config.constraints; its
    # former hardcoded inline block below is deleted as it moves to config.
    _encode_configured_night_gating(
        opb, xn, xs, wr, config, fellow_names, shift_idx, soft_violations,
        num_days=num_days, start_dow=start_dow, num_weeks=num_weeks,
        num_fellows=num_fellows, dual_stroke_vars=dual_stroke_vars,
        weights=weights, hard_criteria=hard_criteria,
    )




def _on_indicator(opb, parts: list[int]) -> int | None:
    """OR-indicator var over *parts* (the fellow is 'on' if any part is true)."""
    parts = [p for p in parts if p != 0]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    aux = opb.new_var()
    for p in parts:
        opb.weighted_sum_at_least([(aux, 1), (-p, 1)], 1)
    opb.weighted_sum_at_least([(v, 1) for v in parts] + [(-aux, 1)], 1)
    return aux


def _encode_stroke_wk2627_toggle(
    opb: OpbBuilder,
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
) -> None:
    """Stroke wk26/27 (1-indexed) on/off toggle: a STROKE fellow 'on' in week 25
    (0-indexed) must be 'off' in week 26 and vice versa. 'on' = Stroke /
    Telestroke/Clinic / Swing / NCC1 / NCC2. Mode: off | hard | soft."""
    mode = config.stroke_wk2627_toggle
    if mode == "off":
        return
    w_a, w_b = STROKE_WK2627_WEEKS
    if w_b >= config.num_weeks:
        return
    on_si = [shift_idx[s] for s in config.shift_palette.shifts_with_attribute("stroke_wk2627_on") if s in shift_idx]
    stroke_group = set(config.fellow_groups.get("STROKE", []))
    for f, name in enumerate(fellow_names):
        if name not in stroke_group:
            continue
        on_a = _on_indicator(opb, [xs[f][w_a][si] for si in on_si])
        on_b = _on_indicator(opb, [xs[f][w_b][si] for si in on_si])
        if on_a is None or on_b is None:
            continue
        if mode == "hard":
            opb.at_most_k([on_a, on_b], 1)
        else:  # soft: penalize being on both
            p = opb.new_var()
            # p >= on_a + on_b - 1
            opb.weighted_sum_at_least([(p, 1), (-on_a, 1), (-on_b, 1)], 1)
            soft_violations.append((p, config.weekly_soft_weight))


def _encode_dual_stroke_helena(
    opb: OpbBuilder,
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
) -> None:
    """Soft, two-tier dual-Stroke preference. For each week with >=2 fellows on
    Stroke: a base penalty (0 early / 20 late); plus, if Helena is NOT one of the
    Stroke fellows that week, an additional penalty (100 early / 80 late). 'Early'
    = weeks 0..19 (1-indexed 1..20)."""
    helena = config.dual_stroke_helena
    if not helena or helena not in fellow_names:
        return
    stroke_si = shift_idx.get("Stroke")
    if stroke_si is None:
        return
    hi = fellow_names.index(helena)
    num_weeks = config.num_weeks
    for w in range(num_weeks):
        stroke_vars = [xs[f][w][stroke_si] for f in range(len(fellow_names))
                       if xs[f][w][stroke_si] != 0]
        if len(stroke_vars) < 2:
            continue
        early = w < DUAL_STROKE_EARLY_END
        base_w = DUAL_STROKE_BASE_EARLY if early else DUAL_STROKE_BASE_LATE
        nohel_w = DUAL_STROKE_NO_HELENA_EARLY if early else DUAL_STROKE_NO_HELENA_LATE
        # dual[w] = 1 iff >= 2 on Stroke.  dual + (n-1)~? ... use: dual <-> sum>=2.
        n = len(stroke_vars)
        dual = opb.new_var()
        # dual=1 -> sum>=2:  sum + (2)*~dual >= 2  (when dual=0: sum+2>=2 trivial;
        #   when dual=1: sum>=2)
        opb.weighted_sum_at_least([(v, 1) for v in stroke_vars] + [(-dual, 2)], 2)
        # dual=0 -> sum<=1:  sum <= 1 + (n-1)*dual
        opb.weighted_sum_at_most([(v, 1) for v in stroke_vars] + [(-dual, n - 1)], n)
        if base_w > 0:
            soft_violations.append((dual, base_w))
        # no-Helena extra: viol = dual AND NOT helena_on_stroke.
        hel_var = xs[hi][w][stroke_si]
        if nohel_w > 0:
            viol = opb.new_var()
            if hel_var == 0:
                # Helena can't be on Stroke this week -> viol == dual.
                opb.weighted_sum_at_least([(viol, 1), (-dual, 1)], 0)  # viol>=dual
                opb.weighted_sum_at_least([(dual, 1), (-viol, 1)], 0)  # viol<=dual
            else:
                # viol >= dual - hel:  viol + hel - dual >= 0
                opb.weighted_sum_at_least([(viol, 1), (hel_var, 1), (-dual, 1)], 0)
            soft_violations.append((viol, nohel_w))




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
# Night-layer Rule Shape adapters (S2): four kinds -> NightLiteralPin
#
# Thin config->archetype translators registered in _NIGHT_HANDLERS. Each
# resolves coordinates (date->day, week->friday, group complement) and the
# action (pin/forbid), then hands an already-resolved var list to
# _NIGHT_LITERAL_PIN.encode — mirroring _encode_shift_total's adapter shape.
# They reproduce the legacy _encode_call_rules branches byte-for-byte.
# ---------------------------------------------------------------------------

def _night_selector_fellow_index(constraint, fellow_names):
    """The single fellow index a fellow-specific night rule targets, or None if
    the selector resolves to no known fellow (mirrors the old
    `fellow_name not in fellow_names` skip)."""
    selector = constraint.fellows
    if selector is None or not getattr(selector, "names", ()):
        return None
    fellow_name = selector.names[0]
    if fellow_name not in fellow_names:
        return None
    return fellow_names.index(fellow_name)


def _encode_group_night_requirement(opb, xn, constraint, **kw):
    """FORBID every non-member's night on each given date. Reproduces the legacy
    group_night_requirement branch: for each date, for each fellow NOT in the
    union of the allowed groups, forbid xn[d][fi]."""
    config = kw["config"]
    fellow_names = kw["fellow_names"]
    soft_violations = kw["soft_violations"]
    num_days = config.num_days
    horizon_start = config.night_config.horizon_start_date

    allowed_groups = constraint.params.get("groups", [])
    allowed_fellows: set[str] = set()
    for g in allowed_groups:
        allowed_fellows.update(config.fellow_groups.get(g, []))

    night_vars = []
    for date_str in constraint.params.get("dates", []):
        d = _date_to_day_index(date_str, horizon_start)
        if d < 0 or d >= num_days:
            continue
        for fi, name in enumerate(fellow_names):
            if name not in allowed_fellows and xn[d][fi] != 0:
                night_vars.append(xn[d][fi])

    _NIGHT_LITERAL_PIN.encode(
        OpbConstraintSink(opb, soft_violations), night_vars=night_vars, action=FORBID)


def _encode_specific_night_assignment(opb, xn, constraint, **kw):
    """PIN one fellow's night on each given date."""
    config = kw["config"]
    fellow_names = kw["fellow_names"]
    soft_violations = kw["soft_violations"]
    num_days = config.num_days
    horizon_start = config.night_config.horizon_start_date

    fi = _night_selector_fellow_index(constraint, fellow_names)
    if fi is None:
        return
    night_vars = []
    for date_str in constraint.params.get("dates", []):
        d = _date_to_day_index(date_str, horizon_start)
        if 0 <= d < num_days and xn[d][fi] != 0:
            night_vars.append(xn[d][fi])

    _NIGHT_LITERAL_PIN.encode(
        OpbConstraintSink(opb, soft_violations), night_vars=night_vars, action=PIN)


def _encode_blocked_night(opb, xn, constraint, **kw):
    """FORBID one fellow's night on each given date."""
    config = kw["config"]
    fellow_names = kw["fellow_names"]
    soft_violations = kw["soft_violations"]
    num_days = config.num_days
    horizon_start = config.night_config.horizon_start_date

    fi = _night_selector_fellow_index(constraint, fellow_names)
    if fi is None:
        return
    night_vars = []
    for date_str in constraint.params.get("dates", []):
        d = _date_to_day_index(date_str, horizon_start)
        if 0 <= d < num_days and xn[d][fi] != 0:
            night_vars.append(xn[d][fi])

    _NIGHT_LITERAL_PIN.encode(
        OpbConstraintSink(opb, soft_violations), night_vars=night_vars, action=FORBID)


def _encode_friday_call_assignment(opb, xn, constraint, **kw):
    """PIN one fellow's Friday (dow=4) night for each given week."""
    config = kw["config"]
    fellow_names = kw["fellow_names"]
    soft_violations = kw["soft_violations"]
    num_days = config.num_days
    start_dow = config.start_dow

    fi = _night_selector_fellow_index(constraint, fellow_names)
    if fi is None:
        return
    dow = constraint.params.get("dow", 4)
    night_vars = []
    for w in constraint.params.get("weeks", []):
        friday_d = _week_day(w, dow, start_dow)
        if 0 <= friday_d < num_days and xn[friday_d][fi] != 0:
            night_vars.append(xn[friday_d][fi])

    _NIGHT_LITERAL_PIN.encode(
        OpbConstraintSink(opb, soft_violations), night_vars=night_vars, action=PIN)


# Register the four night-pin kinds onto the night-layer walk. Keys EQUAL the
# legacy call-rule type strings so the later YAML flip is a pure rename. These
# fire only for matching kinds in config.constraints; the shipped config still
# carries them in call_rules (OLD path), so the default triple is unchanged.
_NIGHT_HANDLERS.update({
    "group_night_requirement": _encode_group_night_requirement,
    "specific_night_assignment": _encode_specific_night_assignment,
    "blocked_night": _encode_blocked_night,
    "friday_call_assignment": _encode_friday_call_assignment,
})


# ---------------------------------------------------------------------------
# Palette v2 generic encoders
# ---------------------------------------------------------------------------

def _encode_shift_total(opb, xs, constraint, fellow_indices, **kw):
    """Per-fellow total of specific shifts, optionally windowed. Thin adapter:
    builds each fellow's windowed var list and delegates the count-band emission
    to WindowedCountBandCriterion (ADR-0005)."""
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

    sink = OpbConstraintSink(opb, soft_violations)
    for f in fellow_indices:
        all_vars = []
        for w in range(w_start, min(w_end, num_weeks)):
            for si in s_indices:
                if xs[f][w][si] != 0:
                    all_vars.append(xs[f][w][si])

        if not all_vars:
            continue

        # A locked fellow's shift_total is normally softened (their weekly layer
        # is frozen, so a hard count could be infeasible against the import). But
        # under relaxed locking with this rule opted in, the trio weeks float, so
        # the rule binds at its declared strength like any managed fellow.
        relax_binds = (
            kw["config"].relax_locked_ncc_trio
            and constraint.params.get("applies_under_relaxed_locks", False)
        )
        locked_soft = f in locked_fellow_indices and not relax_binds
        fellow_is_soft = is_soft or locked_soft
        _WINDOWED_COUNT_BAND_CRITERION.encode(
            sink, vars=all_vars, relation=relation, target=count,
            strength=_SOFT if fellow_is_soft else _HARD, weight=weight,
        )


def _encode_staffing_per_week(opb, xs, constraint, fellow_indices, **kw):
    """Per-week staffing requirement: N fellows from groups on shifts. Thin
    adapter: builds each week's var list and delegates the count-band emission to
    WindowedCountBandCriterion (ADR-0005)."""
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

    sink = OpbConstraintSink(opb, soft_violations)
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

        _WINDOWED_COUNT_BAND_CRITERION.encode(
            sink, vars=week_vars, relation=relation, target=count,
            strength=_SOFT if week_is_soft else _HARD, weight=weight,
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
    """Forbid fellows from being assigned specific shifts. Thin adapter
    delegating to ZeroShiftsCriterion (ADR-0005)."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]

    forbidden_vars = []
    for shift_name in constraint.params.get("zero_shifts", []):
        si = shift_idx.get(shift_name)
        if si is None:
            continue
        for f in fellow_indices:
            for w in range(num_weeks):
                if xs[f][w][si] != 0:
                    forbidden_vars.append(xs[f][w][si])

    _ZERO_SHIFTS_CRITERION.encode(
        OpbConstraintSink(opb, kw["soft_violations"]),
        forbidden_vars=forbidden_vars,
    )


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


def _encode_group_count_balance(opb, xs, constraint, fellow_indices, **kw):
    """Comparable per-fellow counts of a shift-set within a window, ACROSS the
    fellows in the selector (pairwise |count_i - count_j| <= max_difference).
    Delegates to the co-located GroupCountBalanceCriterion (ADR-0005)."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]
    weight = kw["config"].weekly_soft_weight
    soft_violations = kw["soft_violations"]

    max_diff = constraint.params["max_difference"]
    target_shifts = list(constraint.shifts.shifts) if constraint.shifts else []
    s_indices = [shift_idx[s] for s in target_shifts if s in shift_idx]
    w_start, w_end = (constraint.weeks.start, constraint.weeks.end) if constraint.weeks else (0, num_weeks)

    fellow_var_lists = []
    for f in fellow_indices:
        vlist = [xs[f][w][si]
                 for w in range(w_start, min(w_end, num_weeks))
                 for si in s_indices if xs[f][w][si] != 0]
        fellow_var_lists.append(vlist)

    strength = _SOFT if constraint.strength == ConstraintStrength.SOFT else _HARD
    _GROUP_COUNT_BALANCE_CRITERION.encode(
        OpbConstraintSink(opb, soft_violations),
        fellow_var_lists=fellow_var_lists,
        max_difference=max_diff,
        strength=strength,
        weight=weight,
    )


def _encode_dual_stroke_window_rule(opb, xs, constraint, fellow_indices, **kw):
    """Windowed opportunistic supervision on Stroke (the 'may' shape). Thin
    adapter: per week, split the week's Stroke vars into supervisor/non-supervisor
    and delegate the cap to WindowedSupervisionCriterion (ADR-0005). Reproduces
    the prior `_encode_dual_stroke_window` emission EXACTLY — including skipping a
    week where an Imported fellow already occupies the Stroke slot (single source:
    config.imported_shift_counts)."""
    config = kw["config"]
    fellow_names = kw["fellow_names"]
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]

    stroke_si = shift_idx.get("Stroke")
    if stroke_si is None:
        return

    window = constraint.params.get("window", [0, 0])
    w_start, w_end = window[0], window[1]
    supervisors = set(constraint.params.get("supervisors", []))
    supervisor_indices = {fi for fi, name in enumerate(fellow_names) if name in supervisors}

    locked_stroke_per_week = config.imported_shift_counts(fellow_names, {"Stroke"})
    sink = OpbConstraintSink(opb, kw["soft_violations"])

    for w in range(num_weeks):
        if locked_stroke_per_week.get(w, 0) >= 1:
            continue
        sup_vars = [xs[fi][w][stroke_si] for fi in range(len(fellow_names))
                    if fi in supervisor_indices and xs[fi][w][stroke_si] != 0]
        non_sup_vars = [xs[fi][w][stroke_si] for fi in range(len(fellow_names))
                        if fi not in supervisor_indices and xs[fi][w][stroke_si] != 0]
        if not sup_vars and not non_sup_vars:
            continue
        _WINDOWED_SUPERVISION_CRITERION.encode(
            sink,
            supervisor_vars=sup_vars,
            non_supervisor_vars=non_sup_vars,
            in_window=(w_start <= w < w_end),
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
            if not var_map.xn:
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

    # Day-granular call tier (NF model). Empty when var_map.call is [].
    call_by_day: list[dict[str, str]] = []
    for d in range(len(var_map.call)):
        day_holders: dict[str, str] = {}
        for role in CALL_ROLES:
            day_holders[role] = ""
            for f in range(num_fellows):
                var = var_map.call[d][f].get(role, 0)
                if var != 0 and assignment.get(var, False):
                    day_holders[role] = fellow_names[f]
                    break
        call_by_day.append(day_holders)

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
        call_assignments_by_day=call_by_day,
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
