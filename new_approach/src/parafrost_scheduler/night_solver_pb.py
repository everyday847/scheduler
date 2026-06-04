"""Night call schedule solver using RoundingSat (pseudo-Boolean based).

Encodes the same scheduling constraints as ``night_solver.py`` but uses the
OPB format instead of CNF/DIMACS.

Key advantages over the CNF/ParaFROST path:

1. **Cardinality constraints are native** — ``+1 x1 ... = 60`` is one line
   instead of thousands of clauses from a totalizer encoding.  The original
   CNF formula has 80K+ variables; the OPB formula uses only the primary
   decision variables plus selector variables for multiset permutations.

2. **Weighted soft constraints are native** — the ``weighted_sum_at_most``
   constraint needs zero auxiliary variables in OPB format (one line vs.
   an entire adder encoding).

The solver interface is identical to the ParaFROST path so the two backends
can be compared directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations

from scheduler.call_schedule_common import (
    NIGHT_ROLES,
    ParsedCallScheduleCsv,
    is_night_blocked,
    is_night_holiday_eligible,
    is_anaesthesia_service,
    is_clinic_service,
    is_preferred_sunday_following_service,
)
from scheduler.night_call_types import (
    NightScheduleSolution,
    NightSolverConfig,
    CountMultiset,
    absolute_day_index,
    holiday_indices_for_config,
)
from scheduler.night_policy_types import (
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


@dataclass(frozen=True)
class NightOpbVarMap:
    """Mapping from (day, fellow) pairs to OPB variable IDs."""

    x: list[list[int]]   # x[day_index][fellow_index] = OPB variable ID (1-based)
    num_days: int
    num_fellows: int


# ---------------------------------------------------------------------------
# Internal helpers (mirroring night_solver.py)
# ---------------------------------------------------------------------------

def _weeks_with_dual_stroke(parsed: ParsedCallScheduleCsv) -> frozenset[int]:
    """Return week indices where 2+ fellows are on Stroke service (not Telestroke)."""
    dual = set()
    for week_index, row in enumerate(parsed.week_rows):
        stroke_count = sum(
            1 for svc in row.weekday_assignments.values()
            if "Stroke" in svc and "Telestroke" not in svc
        )
        if stroke_count >= 2:
            dual.add(week_index)
    return frozenset(dual)


def _criteria_for_assignment(
    parsed: ParsedCallScheduleCsv,
    week_index: int,
    day_of_week: int,
    fellow_name: str,
    *,
    dual_stroke_weeks: frozenset[int] = frozenset(),
    weekend_ncc1_fellow: str | None = None,
) -> tuple[str, ...]:
    """Return the policy criteria that apply to assigning fellow to this slot.

    Anaesthesia and clinic criteria apply only to weekday nights (Mon-Fri,
    day_of_week 0-4). Stroke criterion applies to weekday nights only when the
    fellow is on weekday Stroke service, and to weekend nights only when the
    fellow is on Weekend Stroke (handled via weekend_ncc1_fellow parameter for
    friday_weekend_ncc1).
    """
    week_row = parsed.week_rows[week_index]
    weekday_service = week_row.weekday_assignments[fellow_name]
    criteria: list[str] = []
    is_weekday_night = day_of_week <= 4  # Mon-Fri
    # Anaesthesia: weekday nights only
    if is_weekday_night and is_anaesthesia_service(weekday_service):
        criteria.append(CRITERION_ANAESTHESIA)
    # Clinic: weekday nights only
    if is_weekday_night and is_clinic_service(weekday_service):
        criteria.append(CRITERION_CLINIC)
    # Stroke: weekday stroke → weekday nights; weekend stroke → weekend nights
    if "Stroke" in weekday_service and "Telestroke" not in weekday_service:
        if week_index not in dual_stroke_weeks:
            if is_weekday_night:
                criteria.append(CRITERION_STROKE)
    if day_of_week == 4:
        # Use provided weekend_ncc1_fellow if available, else fall back to schedule_assignments
        ncc1 = weekend_ncc1_fellow if weekend_ncc1_fellow is not None else week_row.schedule_assignments.get("Weekend NCC1")
        if ncc1 == fellow_name:
            criteria.append(CRITERION_FRIDAY_WEEKEND_NCC1)
    if day_of_week == 6 and week_index + 1 < len(parsed.week_rows):
        following_service = parsed.week_rows[week_index + 1].weekday_assignments[fellow_name]
        if not is_preferred_sunday_following_service(following_service):
            criteria.append(CRITERION_SUNDAY_FOLLOWING)
    return tuple(criteria)


def _add_multiset_opb(
    opb: OpbBuilder,
    parsed: ParsedCallScheduleCsv,
    var_map: NightOpbVarMap,
    multiset: CountMultiset,
    *,
    friday_only: bool,
    blocked_days: list[set[int]],
) -> None:
    """Encode a multiset cardinality constraint in OPB.

    For the unique-permutation case this is unconditional exactly_k calls.
    For multiple permutations we introduce selector variables and use
    conditional_exactly_k — two native PB constraints per (selector, fellow)
    pair.  Still vastly more compact than the CNF sequential counter.
    """
    unique_perms = {tuple(p) for p in permutations(multiset.values)}
    fellow_names = parsed.fellow_names

    def _eligible_lits(fellow_name: str) -> list[int]:
        fi = fellow_names.index(fellow_name)
        bd = blocked_days[fi]
        if friday_only:
            return [var_map.x[d][fi] for d in range(var_map.num_days) if d % 7 == 4 and d not in bd]
        return [var_map.x[d][fi] for d in range(var_map.num_days) if d not in bd]

    if len(unique_perms) == 1:
        # Single permutation: encode unconditionally
        ordering = next(iter(unique_perms))
        for fellow_name, count in zip(multiset.names, ordering, strict=True):
            lits = _eligible_lits(fellow_name)
            opb.exactly_k(lits, count)
    else:
        # Multiple permutations: introduce selector variables + exactly_one
        selectors = opb.new_vars(len(unique_perms))
        opb.exactly_one(selectors)
        for selector, ordering in zip(selectors, unique_perms):
            for fellow_name, count in zip(multiset.names, ordering, strict=True):
                lits = _eligible_lits(fellow_name)
                opb.conditional_exactly_k(lits, count, selector)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_night_opb(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig,
    hard_criteria: frozenset[str],
    weights: NightPolicyWeights,
    soft_bound: int | None,
) -> tuple[OpbBuilder, NightOpbVarMap]:
    """Encode all scheduling constraints as an OPB pseudo-Boolean formula.

    The constraint structure mirrors ``build_night_cnf`` exactly, but the
    formula is dramatically smaller:

    - Cardinality constraints (sections 5, 6, 7) are each *one OPB line*
      instead of O(n·k) CNF clauses from a totalizer.
    - The weighted soft-violation bound (section 9) is *one OPB line*
      instead of an adder encoding.
    - Decision variable count is identical to the primary variables in CNF.
      No auxiliary variables for cardinality.  Selector variables for
      multisets are still needed but are far fewer.

    Parameters
    ----------
    parsed:
        Parsed schedule CSV.
    config:
        Night solver configuration (night counts, multisets, etc.).
    hard_criteria:
        Criteria to enforce as hard blocking constraints.
    weights:
        Weights for soft criteria.
    soft_bound:
        Maximum allowed total weighted soft violations (None = unconstrained).

    Returns
    -------
    (opb, var_map):
        The formula builder and variable mapping.
    """
    opb = OpbBuilder()
    num_days = len(parsed.week_rows) * 7
    num_fellows = len(parsed.fellow_names)
    dual_stroke_weeks = _weeks_with_dual_stroke(parsed)

    # 1. Decision variables: x[day][fellow]
    x: list[list[int]] = [
        [opb.new_var() for _ in range(num_fellows)]
        for _ in range(num_days)
    ]
    var_map = NightOpbVarMap(x=x, num_days=num_days, num_fellows=num_fellows)

    holiday_indices = set(holiday_indices_for_config(config))

    # 2. Exactly one fellow per day (one OPB line per day)
    opb.add_comment("Section 2: exactly one fellow per night")
    for d in range(num_days):
        opb.exactly_one(x[d])

    # 3. Blocking constraints
    opb.add_comment("Section 3: blocking constraints")
    blocked_days: list[set[int]] = [set() for _ in range(num_fellows)]
    for d in range(num_days):
        week_index, day_of_week = divmod(d, 7)
        week_row = parsed.week_rows[week_index]
        for fi, fellow_name in enumerate(parsed.fellow_names):
            weekday_service = week_row.weekday_assignments[fellow_name]
            blocked = False
            if fellow_name in config.ccm_fellows or is_night_blocked(weekday_service):
                blocked = True
            elif d in holiday_indices and not is_night_holiday_eligible(weekday_service):
                blocked = True
            # Conference/exam blocking: NHS blocks all nights; ISC blocks Tue-Fri; APBN blocks Mon-Fri
            elif weekday_service == "NHS":
                blocked = True
            elif day_of_week >= 1 and day_of_week <= 4 and "ISC" in weekday_service:
                blocked = True
            elif day_of_week <= 4 and "APBN" in weekday_service:
                blocked = True
            if blocked:
                opb.add_unit(-x[d][fi])
                blocked_days[fi].add(d)

    # 4. No-3-consecutive constraint
    opb.add_comment("Section 4: no 3 consecutive nights per fellow")
    for fi in range(num_fellows):
        for s in range(num_days - 2):
            opb.at_most_k([x[s][fi], x[s + 1][fi], x[s + 2][fi]], 1)

    # 5. Total night counts (one OPB line per fellow — the big win!)
    opb.add_comment("Section 5: total night counts (exactly-k, one line each)")
    for fellow_name, total in config.total_nights.items():
        fi = parsed.fellow_names.index(fellow_name)
        lits = [x[d][fi] for d in range(num_days) if d not in blocked_days[fi]]
        opb.exactly_k(lits, total)

    # 6. Friday night counts (one OPB line per fellow)
    opb.add_comment("Section 6: friday night counts")
    for fellow_name, total in config.friday_nights.items():
        fi = parsed.fellow_names.index(fellow_name)
        lits = [x[d][fi] for d in range(num_days) if d % 7 == 4 and d not in blocked_days[fi]]
        opb.exactly_k(lits, total)

    # 7. Multiset constraints
    opb.add_comment("Section 7: multiset constraints")
    for multiset in config.total_night_multisets:
        _add_multiset_opb(opb, parsed, var_map, multiset, friday_only=False, blocked_days=blocked_days)
    for multiset in config.friday_night_multisets:
        _add_multiset_opb(opb, parsed, var_map, multiset, friday_only=True, blocked_days=blocked_days)

    # 8. Policy criteria: hard blocking + soft violation collection
    opb.add_comment("Section 8: policy hard criteria blocking")
    weighted_soft_pairs: list[tuple[int, int]] = []

    for d in range(num_days):
        week_index, day_of_week = divmod(d, 7)
        for fi, fellow_name in enumerate(parsed.fellow_names):
            criteria = _criteria_for_assignment(
                parsed, week_index, day_of_week, fellow_name,
                dual_stroke_weeks=dual_stroke_weeks,
            )
            for criterion in criteria:
                if criterion in hard_criteria:
                    opb.add_unit(-x[d][fi])
                else:
                    weight = weights.for_criterion(criterion)
                    weighted_soft_pairs.append((x[d][fi], weight))

    # 9. Soft bound (one OPB line — the second big win!)
    if soft_bound is not None and weighted_soft_pairs:
        opb.add_comment(f"Section 9: weighted soft violations <= {soft_bound}")
        opb.weighted_sum_at_most(weighted_soft_pairs, soft_bound)

    return opb, var_map


def extract_solution_pb(
    parsed: ParsedCallScheduleCsv,
    var_map: NightOpbVarMap,
    assignment: dict[int, bool],
) -> NightScheduleSolution:
    """Map OPB variable assignments back to fellow names."""
    assignments_by_week: list[dict[str, str]] = []
    num_weeks = var_map.num_days // 7
    for week_index in range(num_weeks):
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


def weighted_upper_bound_pb(
    parsed: ParsedCallScheduleCsv,
    weights: NightPolicyWeights,
    hard_criteria: frozenset[str],
) -> int:
    """Maximum possible weighted soft violations (sum of all non-hard criteria weights)."""
    total = 0
    dual_stroke_weeks = _weeks_with_dual_stroke(parsed)
    for week_index in range(len(parsed.week_rows)):
        for day_of_week in range(7):
            for fellow_name in parsed.fellow_names:
                for criterion in _criteria_for_assignment(
                    parsed, week_index, day_of_week, fellow_name,
                    dual_stroke_weeks=dual_stroke_weeks,
                ):
                    if criterion not in hard_criteria:
                        total += weights.for_criterion(criterion)
    return total


def solve_night_schedule_roundingsat_at_limit(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig = NightSolverConfig(),
    hard_criteria: frozenset[str] = frozenset(),
    weights: NightPolicyWeights = NightPolicyWeights(),
    max_violation_limit: int | None = None,
    runner: RoundingSatRunner,
    emit_summary: bool = True,
) -> NightPolicySolveResult:
    """Single solve with bound using RoundingSat PB solver.

    Returns an unoptimized result (no binary search).
    """
    limit = (
        weighted_upper_bound_pb(parsed, weights, hard_criteria)
        if max_violation_limit is None
        else max_violation_limit
    )
    opb, var_map = build_night_opb(
        parsed,
        config=config,
        hard_criteria=hard_criteria,
        weights=weights,
        soft_bound=limit,
    )
    result = runner.solve(opb)
    if not result.satisfiable:
        raise ValueError(
            f"Night call schedule is unsatisfiable with <= {limit} weighted soft violations"
        )
    solution = extract_solution_pb(parsed, var_map, result.assignment)
    counts = criteria_counts_for_solution(parsed, solution, weights=weights)
    policy_result = NightPolicySolveResult(
        tier=f"roundingsat-unoptimized-soft<={limit}",
        solution=solution,
        counts=counts,
        hard_criteria=hard_criteria,
        optimized=False,
    )
    if emit_summary:
        from scheduler.night_policy_types import print_policy_summary
        print_policy_summary(parsed, policy_result)
    return policy_result


def solve_night_schedule_roundingsat_incremental(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig = NightSolverConfig(),
    hard_criteria: frozenset[str] = frozenset(),
    weights: NightPolicyWeights = NightPolicyWeights(),
    max_violation_limit: int | None = None,
    runner: RoundingSatRunner,
    emit_summary: bool = True,
) -> NightPolicySolveResult:
    """Binary search to find minimum violations using RoundingSat PB solver."""
    upper = (
        weighted_upper_bound_pb(parsed, weights, hard_criteria)
        if max_violation_limit is None
        else max_violation_limit
    )
    best_solution: NightScheduleSolution | None = None
    best_counts: NightPolicyCounts | None = None
    best_limit: int | None = None

    low, high = 0, upper
    while low <= high:
        mid = (low + high) // 2
        opb, var_map = build_night_opb(
            parsed,
            config=config,
            hard_criteria=hard_criteria,
            weights=weights,
            soft_bound=mid,
        )
        result = runner.solve(opb)
        if result.satisfiable:
            best_solution = extract_solution_pb(parsed, var_map, result.assignment)
            best_counts = criteria_counts_for_solution(parsed, best_solution, weights=weights)
            best_limit = mid
            high = mid - 1
        else:
            low = mid + 1

    if best_solution is None or best_counts is None or best_limit is None:
        raise ValueError(
            f"Night call schedule is unsatisfiable with <= {upper} weighted soft violations"
        )

    policy_result = NightPolicySolveResult(
        tier=f"roundingsat-incremental-soft<={best_limit}",
        solution=best_solution,
        counts=best_counts,
        hard_criteria=hard_criteria,
        optimized=True,
    )
    if emit_summary:
        from scheduler.night_policy_types import print_policy_summary
        print_policy_summary(parsed, policy_result)
    return policy_result
