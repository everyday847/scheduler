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

import sys
import time
from dataclasses import dataclass, field
from datetime import date
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
from scheduler.night_call_solver import (
    NightSolverConfig,
    NightScheduleSolution,
    CountMultiset,
    holiday_indices_for_config,
)
from scheduler.weekend_call_solver import WeekendSolverConfig, WeekendScheduleSolution
from scheduler.call_schedule_common import NIGHT_ROLES, WEEKEND_ROLES
from scheduler.night_call_solver_policy import (
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


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NIGHT_BLOCKED_SHIFTS = frozenset({"SICU", "Vac", "NS"})
ANAESTHESIA_SHIFTS = frozenset({"Anaesthesia"})
CLINIC_SHIFTS = frozenset({"Clinic/Elective", "Telestroke/Clinic"})
STROKE_SHIFTS = frozenset({"Stroke"})
NON_PREFERRED_SUNDAY_FOLLOWING = frozenset(
    {"Anaesthesia", "Clinic/Elective", "Telestroke/Clinic", "Vac", "NS", "NIR", "SICU", "SCVMC Rehab"}
)
HOLIDAY_ELIGIBLE_SHIFTS = frozenset({"NCC1", "NCC2", "Stroke"})
WEEKEND_BLOCKED_SHIFTS = frozenset({"SICU", "Vac"})

_ROLE_NCC1 = 0
_ROLE_NCC2 = 1
_ROLE_STROKE = 2
_WEEKEND_ROLE_NAMES = ("Weekend NCC1", "Weekend NCC2", "Weekend Stroke")

DEFAULT_WEEKLY_SOFT_WEIGHT = 100
DEFAULT_WEEKEND_MISMATCH_WEIGHT = 20
DEFAULT_SWING_UNCOVERED_WEIGHT = 100


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScheduleSolverConfig:
    fellow_groups: dict[str, list[str]]
    shifts: list[str]
    constraints: list[SemanticConstraint]
    night_config: NightSolverConfig
    weekend_config: WeekendSolverConfig
    night_weights: NightPolicyWeights = field(default_factory=NightPolicyWeights)
    night_hard_criteria: frozenset[str] = frozenset(
        {CRITERION_ANAESTHESIA, CRITERION_FRIDAY_WEEKEND_NCC1, CRITERION_SUNDAY_FOLLOWING}
    )
    num_weeks: int = 52
    weekly_soft_weight: int = DEFAULT_WEEKLY_SOFT_WEIGHT
    weekend_mismatch_weight: int = DEFAULT_WEEKEND_MISMATCH_WEIGHT
    swing_uncovered_weight: int = DEFAULT_SWING_UNCOVERED_WEIGHT


@dataclass(frozen=True)
class FullScheduleSolution:
    weekly_assignments: dict[str, list[str]]  # fellow_name → [shift_per_week]
    weekend_solution: WeekendScheduleSolution
    night_solution: NightScheduleSolution
    soft_penalty: int


@dataclass
class ScheduleVarMap:
    """Tracks OPB variable indices for the full joint formula."""

    fellow_names: list[str]
    shifts: list[str]
    num_weeks: int
    num_fellows: int
    num_shifts: int

    # xs[f][w][s] = OPB variable index (or 0 if forbidden)
    xs: list[list[list[int]]]
    # wr[w][role_idx] = {fellow_idx: var}
    wr: list[list[dict[int, int]]]
    # xn[d][f] = OPB variable index (or 0 if blocked)
    xn: list[list[int]]

    # Auxiliary variables
    soft_violations: list[tuple[int, int]]  # (var, weight) pairs


# ---------------------------------------------------------------------------
# Main build function
# ---------------------------------------------------------------------------

def build_full_schedule_opb(
    config: ScheduleSolverConfig,
    soft_bound: int | None = None,
) -> tuple[OpbBuilder, ScheduleVarMap]:
    """Build the complete OPB formula for the joint scheduling problem."""

    fellow_mapping = _build_fellow_mapping(config.fellow_groups)
    fellow_names = [
        fellow_mapping.get_fellow_name(i) for i in range(fellow_mapping.total_fellows)
    ]
    shifts = config.shifts
    num_weeks = config.num_weeks
    num_fellows = len(fellow_names)
    num_shifts = len(shifts)
    shift_idx = {s: i for i, s in enumerate(shifts)}

    opb = OpbBuilder()
    soft_violations: list[tuple[int, int]] = []

    # Pre-compute forbidden assignments from hard constraints
    forbidden = _compute_forbidden(config.constraints, fellow_mapping, shifts, num_weeks)

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
    # 3. Encode weekly shift rules from YAML config
    # -------------------------------------------------------------------
    _encode_weekly_rules(
        opb, xs, config, fellow_mapping, shift_idx, soft_violations,
    )

    # -------------------------------------------------------------------
    # 4. Weekend variables: wr[w][role][f]
    # -------------------------------------------------------------------
    opb.add_comment("Weekend assignment variables")
    wr: list[list[dict[int, int]]] = []
    for w in range(num_weeks):
        wr.append([])
        for role_idx in range(3):
            role_vars: dict[int, int] = {}
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
    # 6. Night variables: xn[d][f]
    # -------------------------------------------------------------------
    opb.add_comment("Night assignment variables")
    num_days = num_weeks * 7
    xn: list[list[int]] = []
    for d in range(num_days):
        xn.append([])
        for f in range(num_fellows):
            if fellow_names[f] in config.night_config.ccm_fellows:
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
    # 8. Soft penalty bound
    # -------------------------------------------------------------------
    if soft_bound is not None and soft_violations:
        opb.add_comment(f"Soft penalty bound <= {soft_bound}")
        weighted_terms = [(var, weight) for var, weight in soft_violations]
        opb.weighted_sum_at_most(weighted_terms, soft_bound)

    var_map = ScheduleVarMap(
        fellow_names=fellow_names,
        shifts=shifts,
        num_weeks=num_weeks,
        num_fellows=num_fellows,
        num_shifts=num_shifts,
        xs=xs,
        wr=wr,
        xn=xn,
        soft_violations=soft_violations,
    )

    return opb, var_map


# ---------------------------------------------------------------------------
# Weekly rule encoders
# ---------------------------------------------------------------------------

def _encode_weekly_rules(
    opb: OpbBuilder,
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_mapping: FellowMapping,
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
) -> None:
    """Encode all active weekly shift constraints from the YAML config."""

    num_weeks = config.num_weeks
    num_fellows = fellow_mapping.total_fellows
    shifts = config.shifts

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
        opb.add_comment(f"Rule: {constraint.params.get('name', constraint.kind)} ({constraint.strength.value})")
        handler(
            opb, xs, constraint, fellow_indices,
            num_weeks=num_weeks,
            num_fellows=num_fellows,
            shifts=shifts,
            shift_idx=shift_idx,
            soft_violations=soft_violations,
            config=config,
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
                # covered=1 iff any fellow on swing: covered <= sum(swing), covered + ~all >= 1
                # Forward: sum(swing) >= covered
                opb.weighted_sum_at_least(
                    [(v, 1) for v in swing_vars_w] + [(-covered, 1)], 0
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
                    # aux <= sum(shifts): sum(shifts) - aux >= 0
                    opb.weighted_sum_at_least(
                        [(v, 1) for v in week_shift_vars] + [(-aux, 1)], 0
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
                opb.weighted_sum_at_least(
                    [(v, 1) for v in trigger_vars] + [(-trigger, 1)], 0
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
# Weekend constraint encoders
# ---------------------------------------------------------------------------

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
    for w in range(num_weeks):
        for role_idx in range(3):
            role_vars = list(wr[w][role_idx].values())
            if role_vars:
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

    # Weekend totals: soft (hard floor makes feasibility too tight with spacing)
    wknd_weight = config.weekly_soft_weight
    opb.add_comment("Weekend: NCC totals per fellow (soft)")
    for fellow_name, total in wk_config.ncc_totals.items():
        if fellow_name not in fellow_names:
            continue
        fi = fellow_names.index(fellow_name)
        ncc_vars = []
        for w in range(num_weeks):
            if fi in wr[w][_ROLE_NCC1]:
                ncc_vars.append(wr[w][_ROLE_NCC1][fi])
            if fi in wr[w][_ROLE_NCC2]:
                ncc_vars.append(wr[w][_ROLE_NCC2][fi])
        if ncc_vars:
            _add_cardinality_constraint(
                opb, ncc_vars, "exactly", total,
                is_soft=True, weight=wknd_weight, soft_violations=soft_violations,
            )

    opb.add_comment("Weekend: Stroke totals per fellow (soft)")
    for fellow_name, total in wk_config.stroke_totals.items():
        if fellow_name not in fellow_names:
            continue
        fi = fellow_names.index(fellow_name)
        stroke_vars = [wr[w][_ROLE_STROKE][fi] for w in range(num_weeks) if fi in wr[w][_ROLE_STROKE]]
        if stroke_vars:
            _add_cardinality_constraint(
                opb, stroke_vars, "exactly", total,
                is_soft=True, weight=wknd_weight, soft_violations=soft_violations,
            )

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
                opb.weighted_sum_at_least([(v, 1) for v in roles_for_f] + [(-aux, 1)], 0)
                work_vars.append((w, aux))

        # No two consecutive
        for i in range(len(work_vars) - 1):
            w1, v1 = work_vars[i]
            w2, v2 = work_vars[i + 1]
            if w2 - w1 == 1:
                opb.at_most_k([v1, v2], 1)

        # At most 2 in any 4-week window
        for i in range(len(work_vars)):
            window = [(w, v) for w, v in work_vars[i:] if w < work_vars[i][0] + 4]
            if len(window) > 2:
                opb.at_most_k([v for _, v in window], 2)

    # Weekend role matches weekday service (soft bonus for matching)
    opb.add_comment("Weekend: prefer role matches weekday service (soft)")
    _encode_weekend_mismatch_penalty(opb, wr, xs, config, fellow_names, shift_idx, soft_violations)


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
                    # wr_stroke → OR(enabling)
                    # wr_stroke + sum(~enabling) <= len(enabling)
                    # Equivalently: wr_stroke <= sum(enabling)
                    opb.weighted_sum_at_least(
                        [(v, 1) for v in enabling_vars] + [(-wr[w][_ROLE_STROKE][f], 1)], 0
                    )
                else:
                    opb.add_unit(-wr[w][_ROLE_STROKE][f])
                continue
            # Stroke only eligible: must be on Stroke
            if name in wk_config.stroke_only_eligible:
                if s_stroke is not None and xs[f][w][s_stroke] != 0:
                    # wr_stroke <= xs_stroke
                    opb.weighted_sum_at_least(
                        [(xs[f][w][s_stroke], 1), (-wr[w][_ROLE_STROKE][f], 1)], 0
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
                    # mismatch <= wr_var: ~wr + ~mismatch >= 1
                    opb.weighted_sum_at_least([(-wr_var, 1), (-mismatch, 1)], 1)
                    # mismatch <= ~weekday_var: weekday + ~mismatch >= 1
                    opb.weighted_sum_at_least([(weekday_var, 1), (-mismatch, 1)], 1)
                    soft_violations.append((mismatch, weight))


# ---------------------------------------------------------------------------
# Night constraint encoders
# ---------------------------------------------------------------------------

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
    num_weeks = config.num_weeks
    num_days = num_weeks * 7
    num_fellows = len(fellow_names)
    night_config = config.night_config
    weights = config.night_weights
    hard_criteria = config.night_hard_criteria

    holiday_set = set(holiday_indices_for_config(night_config))

    # Exactly one fellow per night
    opb.add_comment("Night: exactly one fellow per night")
    for d in range(num_days):
        active = [xn[d][f] for f in range(num_fellows) if xn[d][f] != 0]
        if active:
            opb.exactly_one(active)

    # Night blocking based on weekly shift (dynamic)
    opb.add_comment("Night: service-based blocking")
    blocked_shift_indices = [shift_idx[s] for s in NIGHT_BLOCKED_SHIFTS if s in shift_idx]

    for d in range(num_days):
        week_idx, day_of_week = divmod(d, 7)
        for f in range(num_fellows):
            if xn[d][f] == 0:
                continue
            # Hard block from night-blocking shifts
            for si in blocked_shift_indices:
                if xs[f][week_idx][si] != 0:
                    # xs[f][w][blocked] + xn[d][f] <= 1
                    opb.at_most_k([xs[f][week_idx][si], xn[d][f]], 1)

            # ISC: blocked Tue-Fri (day_of_week 1-4)
            s_isc = shift_idx.get("ISC")
            if s_isc is not None and 1 <= day_of_week <= 4:
                if xs[f][week_idx][s_isc] != 0:
                    opb.at_most_k([xs[f][week_idx][s_isc], xn[d][f]], 1)

            # Holiday: only NCC1/NCC2/Stroke can work holidays
            if d in holiday_set:
                holiday_shifts = [shift_idx[s] for s in HOLIDAY_ELIGIBLE_SHIFTS if s in shift_idx]
                eligible_vars = [xs[f][week_idx][si] for si in holiday_shifts if xs[f][week_idx][si] != 0]
                if eligible_vars:
                    # xn[d][f] → OR(eligible_shifts)
                    opb.weighted_sum_at_least(
                        [(v, 1) for v in eligible_vars] + [(-xn[d][f], 1)], 0
                    )
                else:
                    opb.add_unit(-xn[d][f])

    # Night policy criteria (soft/hard depending on config)
    opb.add_comment("Night: policy criteria (anaesthesia, clinic, stroke, sunday_following)")
    _encode_night_policy_criteria(
        opb, xn, xs, wr, config, fellow_names, shift_idx, soft_violations,
    )

    # No 3 consecutive nights
    opb.add_comment("Night: no 3 consecutive nights per fellow")
    for f in range(num_fellows):
        for start in range(num_days - 2):
            consec = [xn[start + offset][f] for offset in range(3) if xn[start + offset][f] != 0]
            if len(consec) == 3:
                opb.at_most_k(consec, 1)

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
        friday_vars = [xn[d][fi] for d in range(4, num_days, 7) if xn[d][fi] != 0]
        if friday_vars:
            _add_cardinality_constraint(
                opb, friday_vars, "exactly", total,
                is_soft=True, weight=night_weight, soft_violations=soft_violations,
            )

    # Multiset constraints
    opb.add_comment("Night: multiset constraints")
    for multiset in night_config.total_night_multisets:
        _encode_night_multiset(opb, xn, multiset, fellow_names, num_days, friday_only=False)
    for multiset in night_config.friday_night_multisets:
        _encode_night_multiset(opb, xn, multiset, fellow_names, num_days, friday_only=True)


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
    num_weeks = config.num_weeks
    num_days = num_weeks * 7
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

    # Anaesthesia criterion: weekday nights only (Mon-Fri, day_of_week 0-4)
    for d in range(num_days):
        week_idx, day_of_week = divmod(d, 7)
        if day_of_week > 4:
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

    # Clinic criterion: weekday nights only
    for d in range(num_days):
        week_idx, day_of_week = divmod(d, 7)
        if day_of_week > 4:
            continue
        for f in range(num_fellows):
            if xn[d][f] == 0:
                continue
            for si in clinic_indices:
                if xs[f][week_idx][si] == 0:
                    continue
                _encode_night_criterion_pair(
                    opb, xs[f][week_idx][si], xn[d][f],
                    CRITERION_CLINIC, hard_criteria, weights, soft_violations,
                )

    # Stroke criterion: weekday nights only, exempt in dual-stroke weeks
    if stroke_idx is not None:
        for d in range(num_days):
            week_idx, day_of_week = divmod(d, 7)
            if day_of_week > 4:
                continue
            ds_var = dual_stroke_vars[week_idx]
            for f in range(num_fellows):
                if xn[d][f] == 0:
                    continue
                if xs[f][week_idx][stroke_idx] == 0:
                    continue
                if ds_var == 0:
                    # Not a dual-stroke week (can never be)
                    _encode_night_criterion_pair(
                        opb, xs[f][week_idx][stroke_idx], xn[d][f],
                        CRITERION_STROKE, hard_criteria, weights, soft_violations,
                    )
                else:
                    # Conditional: only penalize if NOT dual-stroke
                    # The criterion applies when stroke_shift AND night AND NOT dual_stroke
                    _encode_night_criterion_triple(
                        opb, xs[f][week_idx][stroke_idx], xn[d][f], ds_var,
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
                for day_of_week in (5, 6):  # Sat, Sun
                    d = w * 7 + day_of_week
                    if d >= num_days or xn[d][f] == 0:
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
        friday_d = w * 7 + 4
        if friday_d >= num_days:
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
        sunday_d = w * 7 + 6
        if sunday_d >= num_days:
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
                opb.weighted_sum_at_least(
                    [(v, 1) for v in next_week_non_pref] + [(-non_pref_var, 1)], 0
                )
            _encode_night_criterion_pair(
                opb, non_pref_var, xn[sunday_d][f],
                CRITERION_SUNDAY_FOLLOWING, hard_criteria, weights, soft_violations,
            )


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
        # ind <= condition
        opb.weighted_sum_at_least([(-condition_var, 1), (-ind, 1)], 1)
        # ind <= night
        opb.weighted_sum_at_least([(-night_var, 1), (-ind, 1)], 1)
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
        # ind <= condition
        opb.weighted_sum_at_least([(-condition_var, 1), (-ind, 1)], 1)
        # ind <= night
        opb.weighted_sum_at_least([(-night_var, 1), (-ind, 1)], 1)
        # ind <= ~exempt
        opb.weighted_sum_at_least([(exempt_var, 1), (-ind, 1)], 1)
        soft_violations.append((ind, weights.for_criterion(criterion)))


def _encode_night_multiset(
    opb: OpbBuilder,
    xn: list[list[int]],
    multiset: CountMultiset,
    fellow_names: list[str],
    num_days: int,
    *,
    friday_only: bool,
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
        day_filter = lambda d: d % 7 == 4
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
) -> None:
    """Add a cardinality constraint (exactly/at_least/at_most) with soft support."""
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
    else:
        n = len(vars)
        # Skip trivially-satisfied soft constraints
        if relation == "at_most" and n <= target:
            return
        if relation == "at_least" and n < target:
            # Inherently unsatisfiable — always violated
            v = opb.new_var()
            opb.add_unit(v)
            soft_violations.append((v, weight))
            return

        v = opb.new_var()
        if relation == "exactly":
            # Lower bound: sum >= target (bypassed when v=1)
            # sum + target*v >= target → v=1: sum >= 0 (trivial)
            opb.weighted_sum_at_least(
                [(x, 1) for x in vars] + [(v, max(target, 1))], target
            )
            # Upper bound: sum <= target (bypassed when v=1)
            if n > target:
                opb.weighted_sum_at_least(
                    [(-x, 1) for x in vars] + [(v, n - target)], n - target
                )
        elif relation == "at_least":
            # sum + target*v >= target
            opb.weighted_sum_at_least(
                [(x, 1) for x in vars] + [(v, target)], target
            )
        elif relation == "at_most":
            # sum(~vars) + (n-target)*v >= (n-target)
            opb.weighted_sum_at_least(
                [(-x, 1) for x in vars] + [(v, n - target)], n - target
            )
        soft_violations.append((v, weight))


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
# Palette v2 generic encoders
# ---------------------------------------------------------------------------

def _encode_shift_total(opb, xs, constraint, fellow_indices, **kw):
    """Per-fellow total of specific shifts, optionally windowed."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]
    is_soft = constraint.strength == ConstraintStrength.SOFT
    weight = kw["config"].weekly_soft_weight
    soft_violations = kw["soft_violations"]

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

        _add_cardinality_constraint(
            opb, all_vars, relation, count,
            is_soft=is_soft, weight=weight, soft_violations=soft_violations,
        )


def _encode_staffing_per_week(opb, xs, constraint, fellow_indices, **kw):
    """Per-week staffing requirement: N fellows from groups on shifts."""
    shift_idx = kw["shift_idx"]
    num_weeks = kw["num_weeks"]
    num_fellows = kw["num_fellows"]
    is_soft = constraint.strength == ConstraintStrength.SOFT
    weight = kw["config"].weekly_soft_weight
    soft_violations = kw["soft_violations"]

    relation = constraint.params["relation"]
    count = constraint.params["count"]
    target_shifts = list(constraint.shifts.shifts) if constraint.shifts else []
    s_indices = [shift_idx[s] for s in target_shifts if s in shift_idx]

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

        _add_cardinality_constraint(
            opb, week_vars, relation, count,
            is_soft=is_soft, weight=weight, soft_violations=soft_violations,
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
            opb.weighted_sum_at_least(
                [(v, 1) for v in week_vars] + [(-covered, 1)], 0
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
    num_days = num_weeks * 7
    for w in range(num_weeks):
        week_nights = {}
        for day_of_week, role in enumerate(NIGHT_ROLES):
            d = w * 7 + day_of_week
            for f in range(num_fellows):
                var = var_map.xn[d][f]
                if var != 0 and assignment.get(var, False):
                    week_nights[role] = fellow_names[f]
                    break
            else:
                week_nights[role] = ""
        night_by_week.append(week_nights)

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
    )


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

    # Coarse linear scan downward
    if emit_progress:
        print(f"Starting coarse scan (step={coarse_step})...", flush=True)

    current = upper_bound - coarse_step
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
            current -= coarse_step
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
    max_soft: int | None = None,
    coarse_step: int = 100,
    fine_step: int = 5,
    coarse_timeout: float = 15.0,
    fine_timeout: float = 60.0,
):
    """Generator that yields solver events for progressive optimization.

    Each yield is a dict with a ``type`` key:

    - ``{"type": "status", "phase": ..., "vars": ..., "constraints": ...}``
    - ``{"type": "solution", ...solution_to_json..., "elapsed": float}``
    - ``{"type": "done", "optimal_penalty": int, "total_seconds": float}``
    - ``{"type": "error", "message": str}``
    """
    t0 = time.time()

    yield {"type": "status", "phase": "building"}

    opb_check, var_map = build_full_schedule_opb(config, soft_bound=None)
    upper_bound = sum(w for _, w in var_map.soft_violations)
    if max_soft is not None:
        upper_bound = min(upper_bound, max_soft)

    yield {
        "type": "status",
        "phase": "built",
        "vars": opb_check.num_vars,
        "constraints": opb_check.num_constraints,
        "soft_indicators": len(var_map.soft_violations),
        "upper_bound": upper_bound,
    }

    # Feasibility check
    yield {"type": "status", "phase": "feasibility"}
    opb_feasible, _ = build_full_schedule_opb(config, soft_bound=upper_bound)
    try:
        result = runner.solve(opb_feasible, timeout=120.0)
    except Exception as exc:
        yield {"type": "error", "message": f"Feasibility check timed out: {exc}"}
        return
    if not result.satisfiable:
        yield {"type": "error", "message": f"Infeasible with full soft budget ({upper_bound})"}
        return

    best_assignment = result.assignment
    best_bound = upper_bound

    # Yield first feasible solution
    _, decode_map = build_full_schedule_opb(config, soft_bound=best_bound)
    first_solution = decode_solution(best_assignment, decode_map)
    yield {
        "type": "solution",
        **solution_to_json(first_solution),
        "elapsed": time.time() - t0,
    }

    # Coarse scan — start from the first solution's actual penalty, not the upper bound
    yield {"type": "status", "phase": "coarse_scan", "step": coarse_step}
    last_yielded_penalty = first_solution.soft_penalty
    current = first_solution.soft_penalty - coarse_step
    while current >= 0:
        opb_probe, _ = build_full_schedule_opb(config, soft_bound=current)
        try:
            result = runner.solve(opb_probe, timeout=coarse_timeout)
        except Exception:
            break
        if result.satisfiable:
            best_assignment = result.assignment
            best_bound = current
            _, decode_map = build_full_schedule_opb(config, soft_bound=best_bound)
            improved = decode_solution(best_assignment, decode_map)
            if improved.soft_penalty < last_yielded_penalty:
                last_yielded_penalty = improved.soft_penalty
                yield {
                    "type": "solution",
                    **solution_to_json(improved),
                    "elapsed": time.time() - t0,
                }
            current -= coarse_step
        else:
            break

    # Fine scan
    if coarse_step > fine_step:
        yield {"type": "status", "phase": "fine_scan", "step": fine_step}
        fine_start = best_bound - fine_step
        fine_end = max(current, 0)
        current = fine_start
        while current >= fine_end:
            opb_probe, _ = build_full_schedule_opb(config, soft_bound=current)
            try:
                result = runner.solve(opb_probe, timeout=fine_timeout)
            except Exception:
                break
            if result.satisfiable:
                best_assignment = result.assignment
                best_bound = current
                _, decode_map = build_full_schedule_opb(config, soft_bound=best_bound)
                improved = decode_solution(best_assignment, decode_map)
                if improved.soft_penalty < last_yielded_penalty:
                    last_yielded_penalty = improved.soft_penalty
                    yield {
                        "type": "solution",
                        **solution_to_json(improved),
                        "elapsed": time.time() - t0,
                    }
                current -= fine_step
            else:
                break

    yield {
        "type": "done",
        "optimal_penalty": best_bound,
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
    num_weeks: int = 52,
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
            {CRITERION_ANAESTHESIA, CRITERION_FRIDAY_WEEKEND_NCC1, CRITERION_SUNDAY_FOLLOWING}
        ),
        num_weeks=num_weeks,
    )
