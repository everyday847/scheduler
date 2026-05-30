"""Joint weekend + night call solver using RoundingSat (pseudo-Boolean format).

Optimizes both weekend assignments (NCC1, NCC2, Stroke) and night assignments
(Mon-Sun for each week) simultaneously in a single OPB formula.

Key advantage over the two-stage pipeline: the ``friday_weekend_ncc1`` penalty
can be jointly minimized.  In the two-stage pipeline, weekend assignments are
fixed first, then the night solver tries to avoid assigning the Weekend NCC1
fellow on Friday night — but it has no ability to choose a weekend assignment
that makes Friday avoidance easier.  In the joint model, both decision levels
are simultaneous, so the solver can choose a weekend NCC1 assignment *and* a
Friday night assignment together.

Formula structure
-----------------
Weekend variables
    wr[week][role][fellow] — boolean, True if fellow is assigned to role in week.
    role_index: 0=NCC1, 1=NCC2, 2=Stroke.
    Variables are only created for eligible (week, role, fellow) triples.
    Ineligible fellows have no variable; they are implicitly false.

Night variables
    x[day][fellow] — boolean, True if fellow works night on this absolute day.
    Same layout as the existing NightOpbVarMap.

Linking constraint (friday_weekend_ncc1)
    indicator[week][fellow] = x[week*7+4][fellow] AND wr[week][NCC1][fellow]
    Linearized as:
        indicator >= x[...] + wr[...][NCC1][...] - 1
        indicator <= x[...]
        indicator <= wr[...][NCC1][...]
    If the criterion is hard: simply add x[...] + wr[...][NCC1][...] <= 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations

from scheduler.call_schedule_common import (
    NIGHT_ROLES,
    WEEKEND_ROLES,
    ParsedCallScheduleCsv,
    is_anaesthesia_service,
    is_clinic_service,
    is_night_blocked,
    is_night_holiday_eligible,
    is_preferred_sunday_following_service,
    weekend_roles_for_fellow,
)
from scheduler.night_call_solver import (
    NightScheduleSolution,
    NightSolverConfig,
    CountMultiset,
    absolute_day_index,
    holiday_indices_for_config,
)
from scheduler.weekend_call_solver import (
    WeekendScheduleSolution,
    WeekendSolverConfig,
)
from scheduler.night_call_solver_policy import (
    ALL_POLICY_CRITERIA,
    CRITERION_ANAESTHESIA,
    CRITERION_CLINIC,
    CRITERION_FRIDAY_WEEKEND_NCC1,
    CRITERION_STROKE,
    CRITERION_SUNDAY_FOLLOWING,
    NightPolicyCounts,
    NightPolicySolveResult,
    NightPolicyWeights,
    criteria_counts_for_solution,
)

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.night_solver_pb import _weeks_with_dual_stroke

# Role index constants
_ROLE_NCC1 = 0
_ROLE_NCC2 = 1
_ROLE_STROKE = 2
_ROLE_INDICES = {
    "Weekend NCC1": _ROLE_NCC1,
    "Weekend NCC2": _ROLE_NCC2,
    "Weekend Stroke": _ROLE_STROKE,
}


@dataclass(frozen=True)
class JointSolveResult:
    """Result from the joint weekend+night solver."""

    weekend_solution: WeekendScheduleSolution
    night_solution: NightScheduleSolution
    night_policy_result: NightPolicySolveResult


@dataclass
class JointVarMap:
    """Variable mapping for the joint OPB formula.

    Attributes
    ----------
    x : list[list[int]]
        x[day_index][fellow_index] = OPB variable ID for night assignment.
    wr : list[list[dict[int, int]]]
        wr[week_index][role_index][fellow_index] = OPB variable ID.
        Only eligible (week, role, fellow) triples have entries.
    indicator : list[dict[int, int]]
        indicator[week_index][fellow_index] = OPB variable ID for the
        friday_weekend_ncc1 product indicator (only if soft criterion).
    num_days : int
    num_weeks : int
    num_fellows : int
    """

    x: list[list[int]]
    wr: list[list[dict[int, int]]]  # [week][role_index][fellow_index] -> var
    indicator: list[dict[int, int]]  # [week][fellow_index] -> var (soft only)
    num_days: int
    num_weeks: int
    num_fellows: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _eligible_fellows_for_role(
    parsed: ParsedCallScheduleCsv,
    week_index: int,
    role: str,
    weekend_config: WeekendSolverConfig,
) -> list[int]:
    """Return fellow indices eligible for (week_index, role)."""
    eligible = []
    week_row = parsed.week_rows[week_index]
    for fi, fellow_name in enumerate(parsed.fellow_names):
        roles = weekend_roles_for_fellow(
            fellow_name,
            week_row.weekday_assignments[fellow_name],
            ccm_fellows=weekend_config.ccm_fellows,
            always_stroke_eligible=weekend_config.always_stroke_eligible,
            telestroke_stroke_eligible=weekend_config.telestroke_stroke_eligible,
            stroke_only_eligible=weekend_config.stroke_only_eligible,
        )
        if role in roles:
            eligible.append(fi)
    return eligible


def _add_multiset_opb_joint(
    opb: OpbBuilder,
    parsed: ParsedCallScheduleCsv,
    x: list[list[int]],
    multiset: CountMultiset,
    *,
    friday_only: bool,
    blocked_days: list[set[int]],
) -> None:
    """Encode a night-solver multiset cardinality constraint in OPB (joint version)."""
    unique_perms = {tuple(p) for p in permutations(multiset.values)}
    fellow_names = parsed.fellow_names
    num_days = len(x)

    def _eligible_lits(fellow_name: str) -> list[int]:
        fi = fellow_names.index(fellow_name)
        bd = blocked_days[fi]
        if friday_only:
            return [x[d][fi] for d in range(num_days) if d % 7 == 4 and d not in bd]
        return [x[d][fi] for d in range(num_days) if d not in bd]

    if len(unique_perms) == 1:
        ordering = next(iter(unique_perms))
        for fellow_name, count in zip(multiset.names, ordering, strict=True):
            lits = _eligible_lits(fellow_name)
            opb.exactly_k(lits, count)
    else:
        selectors = opb.new_vars(len(unique_perms))
        opb.exactly_one(selectors)
        for selector, ordering in zip(selectors, unique_perms):
            for fellow_name, count in zip(multiset.names, ordering, strict=True):
                lits = _eligible_lits(fellow_name)
                opb.conditional_exactly_k(lits, count, selector)


# ---------------------------------------------------------------------------
# Public API: build formula
# ---------------------------------------------------------------------------

def build_joint_opb(
    parsed: ParsedCallScheduleCsv,
    *,
    night_config: NightSolverConfig,
    weekend_config: WeekendSolverConfig,
    hard_criteria: frozenset[str],
    weights: NightPolicyWeights,
    soft_bound: int | None,
) -> tuple[OpbBuilder, JointVarMap]:
    """Encode the joint weekend+night scheduling problem as an OPB formula.

    Parameters
    ----------
    parsed:
        Parsed schedule CSV (no existing weekend or night columns required).
    night_config:
        Night solver configuration (counts, multisets, etc.).
    weekend_config:
        Weekend solver configuration (totals, eligibility, cohort, etc.).
    hard_criteria:
        Criteria to enforce as hard blocking constraints.
    weights:
        Weights for soft criteria.
    soft_bound:
        Maximum allowed total weighted soft violations (None = unconstrained).

    Returns
    -------
    (opb, var_map):
        The formula builder and joint variable mapping.
    """
    opb = OpbBuilder()
    num_weeks = len(parsed.week_rows)
    num_days = num_weeks * 7
    num_fellows = len(parsed.fellow_names)

    # -----------------------------------------------------------------------
    # 1. Night decision variables: x[day][fellow]
    # -----------------------------------------------------------------------
    opb.add_comment("Night decision variables: x[day][fellow]")
    x: list[list[int]] = [
        [opb.new_var() for _ in range(num_fellows)]
        for _ in range(num_days)
    ]

    # -----------------------------------------------------------------------
    # 2. Weekend decision variables: wr[week][role_index][fellow_index]
    #    Only eligible triples get a variable.
    # -----------------------------------------------------------------------
    opb.add_comment("Weekend decision variables: wr[week][role_index][fellow_index]")
    # wr[week][role_index] is a dict from fellow_index -> var_id
    wr: list[list[dict[int, int]]] = [
        [{} for _ in range(3)]
        for _ in range(num_weeks)
    ]
    for w in range(num_weeks):
        for role, ri in _ROLE_INDICES.items():
            eligible_fis = _eligible_fellows_for_role(parsed, w, role, weekend_config)
            for fi in eligible_fis:
                wr[w][ri][fi] = opb.new_var()

    # -----------------------------------------------------------------------
    # 3. Night: exactly one fellow per day
    # -----------------------------------------------------------------------
    opb.add_comment("Night: exactly one fellow per day")
    for d in range(num_days):
        opb.exactly_one(x[d])

    # -----------------------------------------------------------------------
    # 4. Night: blocking constraints
    # -----------------------------------------------------------------------
    opb.add_comment("Night: blocking constraints")
    holiday_indices = set(holiday_indices_for_config(night_config))
    blocked_days: list[set[int]] = [set() for _ in range(num_fellows)]
    for d in range(num_days):
        week_index, day_of_week = divmod(d, 7)
        week_row = parsed.week_rows[week_index]
        for fi, fellow_name in enumerate(parsed.fellow_names):
            weekday_service = week_row.weekday_assignments[fellow_name]
            blocked = False
            if fellow_name in night_config.ccm_fellows or is_night_blocked(weekday_service):
                blocked = True
            elif d in holiday_indices and not is_night_holiday_eligible(weekday_service):
                blocked = True
            # Conference blocking: ISC/NHS block Tue-Fri nights (Mon available;
            # weekends always available for conference attendees)
            elif weekday_service == "NHS":
                blocked = True
            elif day_of_week >= 1 and day_of_week <= 4 and "ISC" in weekday_service:
                blocked = True
            elif day_of_week <= 4 and "APBN" in weekday_service:
                blocked = True
            if blocked:
                opb.add_unit(-x[d][fi])
                blocked_days[fi].add(d)

    # -----------------------------------------------------------------------
    # 5. Night: no-3-consecutive constraint
    # -----------------------------------------------------------------------
    opb.add_comment("Night: no 3 consecutive nights per fellow")
    for fi in range(num_fellows):
        for s in range(num_days - 2):
            opb.at_most_k([x[s][fi], x[s + 1][fi], x[s + 2][fi]], 1)

    # -----------------------------------------------------------------------
    # 6. Night: total night counts
    # -----------------------------------------------------------------------
    opb.add_comment("Night: total night counts")
    for fellow_name, total in night_config.total_nights.items():
        fi = parsed.fellow_names.index(fellow_name)
        lits = [x[d][fi] for d in range(num_days) if d not in blocked_days[fi]]
        opb.exactly_k(lits, total)

    # -----------------------------------------------------------------------
    # 7. Night: Friday night counts
    # -----------------------------------------------------------------------
    opb.add_comment("Night: Friday night counts")
    for fellow_name, total in night_config.friday_nights.items():
        fi = parsed.fellow_names.index(fellow_name)
        lits = [x[d][fi] for d in range(num_days) if d % 7 == 4 and d not in blocked_days[fi]]
        opb.exactly_k(lits, total)

    # -----------------------------------------------------------------------
    # 8. Night: multiset constraints
    # -----------------------------------------------------------------------
    opb.add_comment("Night: multiset constraints")
    for multiset in night_config.total_night_multisets:
        _add_multiset_opb_joint(opb, parsed, x, multiset, friday_only=False, blocked_days=blocked_days)
    for multiset in night_config.friday_night_multisets:
        _add_multiset_opb_joint(opb, parsed, x, multiset, friday_only=True, blocked_days=blocked_days)

    # -----------------------------------------------------------------------
    # 9. Weekend: exactly one fellow per (week, role)
    # -----------------------------------------------------------------------
    opb.add_comment("Weekend: exactly one fellow per (week, role)")
    for w in range(num_weeks):
        for ri in range(3):
            role_vars = list(wr[w][ri].values())
            if not role_vars:
                raise ValueError(
                    f"No eligible fellows for week {w}, role index {ri} — "
                    "weekend problem is infeasible"
                )
            opb.exactly_one(role_vars)

    # -----------------------------------------------------------------------
    # 10. Weekend: all-different within week (no fellow fills two roles)
    # -----------------------------------------------------------------------
    opb.add_comment("Weekend: at most one role per fellow per week")
    for w in range(num_weeks):
        for fi in range(num_fellows):
            role_vars = []
            for ri in range(3):
                if fi in wr[w][ri]:
                    role_vars.append(wr[w][ri][fi])
            if len(role_vars) >= 2:
                opb.at_most_k(role_vars, 1)

    # -----------------------------------------------------------------------
    # 11. Weekend: NCC totals per fellow
    # -----------------------------------------------------------------------
    opb.add_comment("Weekend: NCC totals per fellow")
    for fellow_name, total in weekend_config.ncc_totals.items():
        fi = parsed.fellow_names.index(fellow_name)
        lits: list[int] = []
        for w in range(num_weeks):
            if fi in wr[w][_ROLE_NCC1]:
                lits.append(wr[w][_ROLE_NCC1][fi])
            if fi in wr[w][_ROLE_NCC2]:
                lits.append(wr[w][_ROLE_NCC2][fi])
        opb.exactly_k(lits, total)

    # -----------------------------------------------------------------------
    # 12. Weekend: Stroke totals per fellow
    # -----------------------------------------------------------------------
    opb.add_comment("Weekend: Stroke totals per fellow")
    for fellow_name, total in weekend_config.stroke_totals.items():
        fi = parsed.fellow_names.index(fellow_name)
        lits = [wr[w][_ROLE_STROKE][fi] for w in range(num_weeks) if fi in wr[w][_ROLE_STROKE]]
        opb.exactly_k(lits, total)

    # -----------------------------------------------------------------------
    # 13. Weekend: stroke cohort bounds
    # -----------------------------------------------------------------------
    opb.add_comment("Weekend: stroke cohort per-fellow bounds")
    if weekend_config.stroke_cohort:
        cohort_total_lits: list[int] = []
        for fellow_name in weekend_config.stroke_cohort:
            fi = parsed.fellow_names.index(fellow_name)
            lits = [wr[w][_ROLE_STROKE][fi] for w in range(num_weeks) if fi in wr[w][_ROLE_STROKE]]
            opb.at_least_k(lits, weekend_config.stroke_cohort_min)
            opb.at_most_k(lits, weekend_config.stroke_cohort_max)
            cohort_total_lits.extend(lits)
        if weekend_config.stroke_cohort_total is not None:
            opb.exactly_k(cohort_total_lits, weekend_config.stroke_cohort_total)

    # -----------------------------------------------------------------------
    # 14. Weekend: CCM NCC total
    # -----------------------------------------------------------------------
    if weekend_config.ccm_ncc_total is not None and weekend_config.ccm_fellows:
        opb.add_comment("Weekend: CCM NCC total")
        ccm_lits: list[int] = []
        for fellow_name in weekend_config.ccm_fellows:
            if fellow_name not in parsed.fellow_names:
                continue
            fi = parsed.fellow_names.index(fellow_name)
            for w in range(num_weeks):
                if fi in wr[w][_ROLE_NCC1]:
                    ccm_lits.append(wr[w][_ROLE_NCC1][fi])
                if fi in wr[w][_ROLE_NCC2]:
                    ccm_lits.append(wr[w][_ROLE_NCC2][fi])
        opb.exactly_k(ccm_lits, weekend_config.ccm_ncc_total)

    # -----------------------------------------------------------------------
    # 15. Weekend: spacing constraints
    #     No fellow works 2 consecutive weekends; at most 2 of 4 consecutive.
    # -----------------------------------------------------------------------
    opb.add_comment("Weekend: spacing constraints (no consecutive weekends)")
    for fi in range(num_fellows):
        # Build work indicator vars: for week w, fellow fi works if in any role
        work_vars: list[list[int]] = []  # work_vars[w] = list of vars for fi in week w
        for w in range(num_weeks):
            wv: list[int] = []
            for ri in range(3):
                if fi in wr[w][ri]:
                    wv.append(wr[w][ri][fi])
            work_vars.append(wv)

        # No consecutive weekends
        for w in range(num_weeks - 1):
            combined = work_vars[w] + work_vars[w + 1]
            if len(combined) >= 2:
                opb.at_most_k(combined, 1)

        # At most 2 of 4 consecutive weekends
        for start in range(num_weeks - 3):
            combined = []
            for offset in range(4):
                combined.extend(work_vars[start + offset])
            if len(combined) >= 3:
                opb.at_most_k(combined, 2)

    # -----------------------------------------------------------------------
    # 16. Linking: friday_weekend_ncc1 criterion
    #
    # For each week w and fellow f:
    #   violation[w][f] = x[w*7+4][f] AND wr[w][NCC1][f]
    #
    # Hard: x[w*7+4][f] + wr[w][NCC1][f] <= 1
    # Soft: introduce indicator variable ind, add to weighted sum.
    # -----------------------------------------------------------------------
    opb.add_comment("Linking: friday_weekend_ncc1 criterion")
    indicator: list[dict[int, int]] = [{} for _ in range(num_weeks)]
    weighted_soft_pairs: list[tuple[int, int]] = []

    friday_ncc1_is_hard = CRITERION_FRIDAY_WEEKEND_NCC1 in hard_criteria

    for w in range(num_weeks):
        friday_d = w * 7 + 4  # absolute day index for Friday of week w
        for fi in range(num_fellows):
            # Fellow must be eligible for NCC1 in this week AND not blocked on Friday
            if fi not in wr[w][_ROLE_NCC1]:
                continue  # fellow can't be NCC1 this week — no link needed
            if friday_d in blocked_days[fi]:
                continue  # fellow blocked on Friday — can't both be NCC1 and work Friday

            night_var = x[friday_d][fi]
            ncc1_var = wr[w][_ROLE_NCC1][fi]

            if friday_ncc1_is_hard:
                # Hard constraint: can't work Friday night AND be Weekend NCC1
                opb.at_most_k([night_var, ncc1_var], 1)
            else:
                # Soft: introduce indicator variable
                ind = opb.new_var()
                indicator[w][fi] = ind
                # ind >= night_var + ncc1_var - 1  →  night_var + ncc1_var - ind <= 1
                # Encoded as: -ind + night_var + ncc1_var <= 1
                # OPB: +1 night_var +1 ncc1_var +1 ~ind <= 1
                # Equivalently as at-most: at_most_k([night, ncc1, ~ind], 1)
                # But that's wrong — let's do it directly as >= constraints:
                # ind >= night_var + ncc1_var - 1
                # → night_var + ncc1_var - ind <= 1
                # → -ind + night_var + ncc1_var <= 1
                # → +1 ~ind + +1 night_var + +1 ncc1_var <= 1  (rewrite -ind as ~ind - 0)
                # Actually: ind >= x + w - 1
                # In OPB: +1 x + +1 w >= +1 ind + 1
                # i.e. +1 night_var +1 ncc1_var -1 ind >= 1
                # We add: [night_var, ncc1_var, -ind] >= 1 (weighted)
                opb._add_weighted_pb_constraint(
                    [(night_var, 1), (ncc1_var, 1), (-ind, 1)], ">=", 1
                )
                # ind <= night_var  → ind - night_var <= 0 → -night_var + ind <= 0 → ind <= night_var
                # In OPB: +1 ind -1 night_var <= 0 → not directly supported (negative coeff)
                # Rewrite: +1 ~night_var + +1 ~ind >= 1 (at least one of them is false)
                # i.e. if night_var=0 then ind=0 automatically; if ind=1 then night_var=1
                # ~night_var + ~ind >= 1  ↔  at_most_k([night_var, ind], 1) ... NO that's at most 1 of {night,ind}
                # at_most_k([night_var, ind], 1) means ind + night_var <= 1, so if night_var=1 then ind could be 0
                # That's not right.
                # Correct: ind <= night_var  →  ind - night_var <= 0
                # OPB natively supports >=, so rewrite:
                # -(ind - night_var) >= 0  →  night_var - ind >= 0  →  +1 night_var -1 ind >= 0
                # = +1 night_var +1 ~ind >= 1  (since ~ind = 1-ind → night_var + 1 - ind >= 1 → night_var - ind >= 0 ✓)
                opb._add_weighted_pb_constraint(
                    [(night_var, 1), (-ind, 1)], ">=", 0
                )
                # ind <= ncc1_var  →  ncc1_var - ind >= 0  →  +1 ncc1_var +1 ~ind >= 1
                opb._add_weighted_pb_constraint(
                    [(ncc1_var, 1), (-ind, 1)], ">=", 0
                )
                # Add to weighted soft sum
                weight = weights.for_criterion(CRITERION_FRIDAY_WEEKEND_NCC1)
                weighted_soft_pairs.append((ind, weight))

    # -----------------------------------------------------------------------
    # 17. Other policy criteria (night only, not linked to weekend variables)
    # -----------------------------------------------------------------------
    opb.add_comment("Night: other policy criteria (hard blocking + soft collection)")
    dual_stroke_weeks = _weeks_with_dual_stroke(parsed)
    for d in range(num_days):
        week_index, day_of_week = divmod(d, 7)
        is_weekday_night = day_of_week <= 4  # Mon-Fri
        for fi, fellow_name in enumerate(parsed.fellow_names):
            weekday_service = parsed.week_rows[week_index].weekday_assignments[fellow_name]
            criteria_to_check: list[str] = []
            # Anaesthesia: weekday nights only
            if is_weekday_night and is_anaesthesia_service(weekday_service):
                criteria_to_check.append(CRITERION_ANAESTHESIA)
            # Clinic: weekday nights only
            if is_weekday_night and is_clinic_service(weekday_service):
                criteria_to_check.append(CRITERION_CLINIC)
            # Stroke (weekday service): weekday nights only
            if is_weekday_night and "Stroke" in weekday_service and "Telestroke" not in weekday_service:
                if week_index not in dual_stroke_weeks:
                    criteria_to_check.append(CRITERION_STROKE)
            if day_of_week == 6 and week_index + 1 < num_weeks:
                following_service = parsed.week_rows[week_index + 1].weekday_assignments[fellow_name]
                if not is_preferred_sunday_following_service(following_service):
                    criteria_to_check.append(CRITERION_SUNDAY_FOLLOWING)
            # Note: CRITERION_FRIDAY_WEEKEND_NCC1 is handled separately via linking variables

            for criterion in criteria_to_check:
                if criterion in hard_criteria:
                    opb.add_unit(-x[d][fi])
                else:
                    weight = weights.for_criterion(criterion)
                    weighted_soft_pairs.append((x[d][fi], weight))

    # 17b. Weekend stroke → weekend nights (Sat/Sun)
    # If fellow is assigned Weekend Stroke, penalize their Sat/Sun night assignments
    opb.add_comment("Night: weekend stroke → weekend night penalty")
    stroke_is_hard = CRITERION_STROKE in hard_criteria
    stroke_weight = weights.for_criterion(CRITERION_STROKE)
    for w in range(num_weeks):
        if w in dual_stroke_weeks:
            continue
        sat_d = w * 7 + 5
        sun_d = w * 7 + 6
        stroke_role_vars = wr[w][_ROLE_STROKE]
        for fi, wr_var in stroke_role_vars.items():
            for night_d in (sat_d, sun_d):
                if night_d >= num_days:
                    continue
                if stroke_is_hard:
                    opb.weighted_sum_at_most([(wr_var, 1), (x[night_d][fi], 1)], 1)
                else:
                    ind = opb.new_var()
                    # Linearize: ind = wr_var AND x[night_d][fi]
                    # (1) ind >= wr + x - 1:  wr + x + ~ind <= 2
                    opb.weighted_sum_at_most([(wr_var, 1), (x[night_d][fi], 1), (-ind, 1)], 2)
                    # (2) ind <= wr:  ~wr + ~ind >= 1
                    opb.weighted_sum_at_least([(-wr_var, 1), (-ind, 1)], 1)
                    # (3) ind <= x:  ~x + ~ind >= 1
                    opb.weighted_sum_at_least([(-x[night_d][fi], 1), (-ind, 1)], 1)
                    weighted_soft_pairs.append((ind, stroke_weight))

    # -----------------------------------------------------------------------
    # 18. Soft bound
    # -----------------------------------------------------------------------
    if soft_bound is not None and weighted_soft_pairs:
        opb.add_comment(f"Weighted soft violations <= {soft_bound}")
        opb.weighted_sum_at_most(weighted_soft_pairs, soft_bound)

    var_map = JointVarMap(
        x=x,
        wr=wr,
        indicator=indicator,
        num_days=num_days,
        num_weeks=num_weeks,
        num_fellows=num_fellows,
    )
    return opb, var_map


# ---------------------------------------------------------------------------
# Public API: extract solutions
# ---------------------------------------------------------------------------

def extract_weekend_solution(
    parsed: ParsedCallScheduleCsv,
    var_map: JointVarMap,
    assignment: dict[int, bool],
) -> WeekendScheduleSolution:
    """Map OPB variable assignments back to weekend role assignments."""
    assignments_by_week: list[dict[str, str]] = []
    for w in range(var_map.num_weeks):
        week_assignment: dict[str, str] = {}
        for role, ri in _ROLE_INDICES.items():
            assigned_fellow = None
            for fi, var in var_map.wr[w][ri].items():
                if assignment.get(var, False):
                    assigned_fellow = parsed.fellow_names[fi]
                    break
            if assigned_fellow is None:
                raise ValueError(
                    f"No fellow assigned for week {w}, role {role} in RoundingSat solution"
                )
            week_assignment[role] = assigned_fellow
        assignments_by_week.append(week_assignment)
    return WeekendScheduleSolution(assignments_by_week=assignments_by_week)


def extract_night_solution(
    parsed: ParsedCallScheduleCsv,
    var_map: JointVarMap,
    assignment: dict[int, bool],
) -> NightScheduleSolution:
    """Map OPB variable assignments back to night role assignments."""
    assignments_by_week: list[dict[str, str]] = []
    for week_index in range(var_map.num_weeks):
        week_assignments: dict[str, str] = {}
        for day_of_week, role in enumerate(NIGHT_ROLES):
            d = absolute_day_index(week_index, day_of_week)
            assigned_fellow = None
            for fi, fellow_name in enumerate(parsed.fellow_names):
                var = var_map.x[d][fi]
                if assignment.get(var, False):
                    assigned_fellow = fellow_name
                    break
            if assigned_fellow is None:
                raise ValueError(
                    f"No fellow assigned for week {week_index} day {day_of_week} "
                    f"(role {role}) in RoundingSat solution"
                )
            week_assignments[role] = assigned_fellow
        assignments_by_week.append(week_assignments)
    return NightScheduleSolution(assignments_by_week=assignments_by_week)


def _criteria_counts_joint(
    parsed: ParsedCallScheduleCsv,
    weekend_solution: WeekendScheduleSolution,
    night_solution: NightScheduleSolution,
    weights: NightPolicyWeights,
) -> NightPolicyCounts:
    """Compute policy criterion counts from joint solution.

    For the friday_weekend_ncc1 criterion, uses the jointly-solved weekend
    assignments rather than any pre-existing schedule_assignments in the parsed
    CSV.
    """
    counts = {criterion: 0 for criterion in ALL_POLICY_CRITERIA}
    dual_stroke_weeks = _weeks_with_dual_stroke(parsed)
    for week_index, week_night_assignments in enumerate(night_solution.assignments_by_week):
        weekend_ncc1_fellow = weekend_solution.assignments_by_week[week_index]["Weekend NCC1"]
        weekend_stroke_fellow = weekend_solution.assignments_by_week[week_index]["Weekend Stroke"]
        for day_of_week, role in enumerate(NIGHT_ROLES):
            fellow_name = week_night_assignments[role]
            weekday_service = parsed.week_rows[week_index].weekday_assignments[fellow_name]
            is_weekday_night = day_of_week <= 4
            # Anaesthesia: weekday nights only
            if is_weekday_night and is_anaesthesia_service(weekday_service):
                counts[CRITERION_ANAESTHESIA] += 1
            # Clinic: weekday nights only
            if is_weekday_night and is_clinic_service(weekday_service):
                counts[CRITERION_CLINIC] += 1
            # Stroke (weekday service): weekday nights only
            if is_weekday_night and "Stroke" in weekday_service and "Telestroke" not in weekday_service:
                if week_index not in dual_stroke_weeks:
                    counts[CRITERION_STROKE] += 1
            # Stroke (weekend assignment): weekend nights only
            if not is_weekday_night and fellow_name == weekend_stroke_fellow:
                if week_index not in dual_stroke_weeks:
                    counts[CRITERION_STROKE] += 1
            if day_of_week == 4 and fellow_name == weekend_ncc1_fellow:
                counts[CRITERION_FRIDAY_WEEKEND_NCC1] += 1
            if day_of_week == 6 and week_index + 1 < len(parsed.week_rows):
                following_service = parsed.week_rows[week_index + 1].weekday_assignments[fellow_name]
                if not is_preferred_sunday_following_service(following_service):
                    counts[CRITERION_SUNDAY_FOLLOWING] += 1
    return NightPolicyCounts(
        by_criterion=counts,
        weighted_total=sum(
            count * weights.for_criterion(criterion)
            for criterion, count in counts.items()
        ),
    )


def weighted_upper_bound_joint(
    parsed: ParsedCallScheduleCsv,
    weights: NightPolicyWeights,
    hard_criteria: frozenset[str],
    weekend_config: WeekendSolverConfig,
) -> int:
    """Maximum possible weighted soft violations for the joint problem.

    For criteria other than friday_weekend_ncc1, this is the sum of weights
    over all (day, fellow) pairs that could trigger that criterion.

    For friday_weekend_ncc1, the theoretical maximum is min(num_weeks,
    num_fellows) since each week has exactly one NCC1 and one Friday night.
    We conservatively use num_weeks.
    """
    total = 0
    num_weeks = len(parsed.week_rows)
    num_days = num_weeks * 7
    dual_stroke_weeks = _weeks_with_dual_stroke(parsed)
    for d in range(num_days):
        week_index, day_of_week = divmod(d, 7)
        for fellow_name in parsed.fellow_names:
            weekday_service = parsed.week_rows[week_index].weekday_assignments[fellow_name]
            for criterion in [
                CRITERION_ANAESTHESIA,
                CRITERION_CLINIC,
                CRITERION_STROKE,
                CRITERION_SUNDAY_FOLLOWING,
            ]:
                if criterion in hard_criteria:
                    continue
                applies = False
                is_weekday_night = day_of_week <= 4
                if criterion == CRITERION_ANAESTHESIA:
                    applies = is_weekday_night and is_anaesthesia_service(weekday_service)
                elif criterion == CRITERION_CLINIC:
                    applies = is_weekday_night and is_clinic_service(weekday_service)
                elif criterion == CRITERION_STROKE:
                    if is_weekday_night:
                        applies = (
                            "Stroke" in weekday_service
                            and "Telestroke" not in weekday_service
                            and week_index not in dual_stroke_weeks
                        )
                    else:
                        # Weekend nights: any fellow could be weekend stroke
                        applies = week_index not in dual_stroke_weeks
                elif criterion == CRITERION_SUNDAY_FOLLOWING:
                    if day_of_week == 6 and week_index + 1 < num_weeks:
                        following = parsed.week_rows[week_index + 1].weekday_assignments[fellow_name]
                        applies = not is_preferred_sunday_following_service(following)
                if applies:
                    total += weights.for_criterion(criterion)

    if CRITERION_FRIDAY_WEEKEND_NCC1 not in hard_criteria:
        # Each Friday has exactly 1 night person; at most 1 violation per Friday
        total += num_weeks * weights.for_criterion(CRITERION_FRIDAY_WEEKEND_NCC1)

    return total


# ---------------------------------------------------------------------------
# Public API: solve
# ---------------------------------------------------------------------------

def solve_joint_schedule(
    parsed: ParsedCallScheduleCsv,
    *,
    night_config: NightSolverConfig = NightSolverConfig(),
    weekend_config: WeekendSolverConfig = WeekendSolverConfig(),
    hard_criteria: frozenset[str] = frozenset(),
    weights: NightPolicyWeights = NightPolicyWeights(),
    max_violation_limit: int | None = None,
    runner: RoundingSatRunner,
    emit_summary: bool = True,
) -> JointSolveResult:
    """Solve the joint weekend+night scheduling problem with a single OPB solve.

    This is an unoptimized bounded solve — it finds *a* feasible solution
    within the soft-violation budget.  Use ``solve_joint_schedule_incremental``
    for binary-search optimization.

    Parameters
    ----------
    parsed:
        Parsed schedule CSV (weekend columns not required — they are variables).
    night_config, weekend_config:
        Solver configurations.
    hard_criteria:
        Criteria to enforce as hard blocking constraints.
    weights:
        Weights for soft criteria.
    max_violation_limit:
        Maximum allowed total weighted soft violations (None = unconstrained).
    runner:
        RoundingSat runner instance.
    emit_summary:
        Print a policy summary after solving.

    Returns
    -------
    JointSolveResult
        Contains weekend_solution, night_solution, and night_policy_result.
    """
    limit = (
        weighted_upper_bound_joint(parsed, weights, hard_criteria, weekend_config)
        if max_violation_limit is None
        else max_violation_limit
    )
    opb, var_map = build_joint_opb(
        parsed,
        night_config=night_config,
        weekend_config=weekend_config,
        hard_criteria=hard_criteria,
        weights=weights,
        soft_bound=limit,
    )
    result = runner.solve(opb)
    if not result.satisfiable:
        raise ValueError(
            f"Joint schedule is unsatisfiable with <= {limit} weighted soft violations"
        )

    weekend_sol = extract_weekend_solution(parsed, var_map, result.assignment)
    night_sol = extract_night_solution(parsed, var_map, result.assignment)
    counts = _criteria_counts_joint(parsed, weekend_sol, night_sol, weights)
    policy_result = NightPolicySolveResult(
        tier=f"joint-roundingsat-unoptimized-soft<={limit}",
        solution=night_sol,
        counts=counts,
        hard_criteria=hard_criteria,
        optimized=False,
    )
    if emit_summary:
        from scheduler.night_call_solver_policy import print_policy_summary
        print_policy_summary(parsed, policy_result)

    return JointSolveResult(
        weekend_solution=weekend_sol,
        night_solution=night_sol,
        night_policy_result=policy_result,
    )


def solve_joint_schedule_incremental(
    parsed: ParsedCallScheduleCsv,
    *,
    night_config: NightSolverConfig = NightSolverConfig(),
    weekend_config: WeekendSolverConfig = WeekendSolverConfig(),
    hard_criteria: frozenset[str] = frozenset(),
    weights: NightPolicyWeights = NightPolicyWeights(),
    max_violation_limit: int | None = None,
    runner: RoundingSatRunner,
    emit_summary: bool = True,
) -> JointSolveResult:
    """Binary search to find minimum violations for the joint problem.

    Parameters
    ----------
    (same as solve_joint_schedule)

    Returns
    -------
    JointSolveResult
        The optimized joint solution.
    """
    upper = (
        weighted_upper_bound_joint(parsed, weights, hard_criteria, weekend_config)
        if max_violation_limit is None
        else max_violation_limit
    )

    best_weekend: WeekendScheduleSolution | None = None
    best_night: NightScheduleSolution | None = None
    best_counts: NightPolicyCounts | None = None
    best_limit: int | None = None
    best_var_map: JointVarMap | None = None

    low, high = 0, upper
    while low <= high:
        mid = (low + high) // 2
        opb, var_map = build_joint_opb(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=hard_criteria,
            weights=weights,
            soft_bound=mid,
        )
        result = runner.solve(opb)
        if result.satisfiable:
            weekend_sol = extract_weekend_solution(parsed, var_map, result.assignment)
            night_sol = extract_night_solution(parsed, var_map, result.assignment)
            counts = _criteria_counts_joint(parsed, weekend_sol, night_sol, weights)
            best_weekend = weekend_sol
            best_night = night_sol
            best_counts = counts
            best_limit = mid
            best_var_map = var_map
            high = mid - 1
        else:
            low = mid + 1

    if best_weekend is None or best_night is None or best_counts is None or best_limit is None:
        raise ValueError(
            f"Joint schedule is unsatisfiable with <= {upper} weighted soft violations"
        )

    policy_result = NightPolicySolveResult(
        tier=f"joint-roundingsat-incremental-soft<={best_limit}",
        solution=best_night,
        counts=best_counts,
        hard_criteria=hard_criteria,
        optimized=True,
    )
    if emit_summary:
        from scheduler.night_call_solver_policy import print_policy_summary
        print_policy_summary(parsed, policy_result)

    return JointSolveResult(
        weekend_solution=best_weekend,
        night_solution=best_night,
        night_policy_result=policy_result,
    )
