"""Night call schedule solver using ParaFROST (SAT-based).

Encodes the scheduling constraints as CNF, optionally with a weighted soft
violation budget, and calls ParaFROST to find a satisfying assignment.
Binary search finds the minimum soft-violation count.
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
from scheduler.night_call_solver import (
    NightScheduleSolution,
    NightSolverConfig,
    CountMultiset,
    absolute_day_index,
    holiday_indices_for_config,
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

from parafrost_scheduler.cnf_builder import CnfBuilder
from parafrost_scheduler.parafrost_runner import ParaFrostRunner


@dataclass(frozen=True)
class NightCnfVarMap:
    x: list[list[int]]   # x[day_index][fellow_index] = DIMACS variable ID
    num_days: int
    num_fellows: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _criteria_for_assignment(
    parsed: ParsedCallScheduleCsv,
    week_index: int,
    day_of_week: int,
    fellow_name: str,
) -> tuple[str, ...]:
    week_row = parsed.week_rows[week_index]
    weekday_service = week_row.weekday_assignments[fellow_name]
    criteria: list[str] = []
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
        if not is_preferred_sunday_following_service(following_service):
            criteria.append(CRITERION_SUNDAY_FOLLOWING)
    return tuple(criteria)


def _conditional_at_most_k(cnf: CnfBuilder, literals: list[int], k: int, selector: int) -> None:
    """Encode: if selector is true, at most k of literals are true.

    Adds -selector to all constraining clauses as relaxation literal.
    """
    n = len(literals)
    if k >= n:
        return
    if k == 0:
        for lit in literals:
            cnf.add_clause([-selector, -lit])
        return
    if k == 1 and n <= 20:
        for i in range(n):
            for j in range(i + 1, n):
                cnf.add_clause([-selector, -literals[i], -literals[j]])
        return
    # Full sequential counter with -selector added to each clause
    register = [[cnf.new_var() for _ in range(k)] for _ in range(n - 1)]
    cnf.add_clause([-selector, -literals[0], register[0][0]])
    for j in range(1, k):
        cnf.add_clause([-selector, -register[0][j]])
    for i in range(1, n - 1):
        cnf.add_clause([-selector, -literals[i], register[i][0]])
        cnf.add_clause([-selector, -register[i - 1][0], register[i][0]])
        for j in range(1, k):
            cnf.add_clause([-selector, -literals[i], -register[i - 1][j - 1], register[i][j]])
            cnf.add_clause([-selector, -register[i - 1][j], register[i][j]])
        cnf.add_clause([-selector, -literals[i], -register[i - 1][k - 1]])
    cnf.add_clause([-selector, -literals[n - 1], -register[n - 2][k - 1]])


def _conditional_exactly_k(cnf: CnfBuilder, literals: list[int], k: int, selector: int) -> None:
    """Encode: if selector is true, exactly k of literals are true."""
    _conditional_at_most_k(cnf, literals, k, selector)
    # at_least_k conditional: at_most (n-k) of negations
    _conditional_at_most_k(cnf, [-lit for lit in literals], len(literals) - k, selector)


def _add_multiset_cnf(
    cnf: CnfBuilder,
    parsed: ParsedCallScheduleCsv,
    var_map: NightCnfVarMap,
    multiset: CountMultiset,
    *,
    friday_only: bool,
    blocked_days: list[set[int]],
) -> None:
    """Encode a multiset cardinality constraint in CNF.

    Literals for days where the fellow is definitively blocked are excluded
    from the cardinality encoding to keep the sequential counter small.
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
        # Only one possible assignment — encode unconditionally
        ordering = next(iter(unique_perms))
        for fellow_name, count in zip(multiset.names, ordering, strict=True):
            lits = _eligible_lits(fellow_name)
            cnf.exactly_k(lits, count)
    else:
        # Multiple permutations: introduce selector variables
        selectors = [cnf.new_var() for _ in unique_perms]
        # Exactly one selector is true
        cnf.exactly_one(selectors)
        for selector, ordering in zip(selectors, unique_perms):
            for fellow_name, count in zip(multiset.names, ordering, strict=True):
                lits = _eligible_lits(fellow_name)
                _conditional_exactly_k(cnf, lits, count, selector)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_night_cnf(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig,
    hard_criteria: frozenset[str],
    weights: NightPolicyWeights,
    soft_bound: int | None,
) -> tuple[CnfBuilder, NightCnfVarMap]:
    """Encode all scheduling constraints as CNF.

    If soft_bound is not None, adds a weighted_sum_at_most constraint for
    soft violations.
    """
    cnf = CnfBuilder()
    num_days = len(parsed.week_rows) * 7
    num_fellows = len(parsed.fellow_names)

    # 1. Decision variables: x[day][fellow]
    x: list[list[int]] = [
        [cnf.new_var() for _ in range(num_fellows)]
        for _ in range(num_days)
    ]
    var_map = NightCnfVarMap(x=x, num_days=num_days, num_fellows=num_fellows)

    holiday_indices = set(holiday_indices_for_config(config))

    # 2. Exactly one fellow per day
    for d in range(num_days):
        cnf.exactly_one(x[d])

    # 3. Blocking constraints — also record which days are blocked per fellow
    #    so that cardinality constraints can be filtered to eligible days only.
    blocked_days: list[set[int]] = [set() for _ in range(num_fellows)]
    for d in range(num_days):
        week_index, day_of_week = divmod(d, 7)
        week_row = parsed.week_rows[week_index]
        for fi, fellow_name in enumerate(parsed.fellow_names):
            weekday_service = week_row.weekday_assignments[fellow_name]
            if fellow_name in config.ccm_fellows or is_night_blocked(weekday_service):
                cnf.add_clause([-x[d][fi]])
                blocked_days[fi].add(d)
            elif d in holiday_indices and not is_night_holiday_eligible(weekday_service):
                cnf.add_clause([-x[d][fi]])
                blocked_days[fi].add(d)

    # 4. No-3-consecutive constraint: for each fellow, no 3 consecutive nights
    for fi in range(num_fellows):
        for s in range(num_days - 2):
            cnf.at_most_k([x[s][fi], x[s + 1][fi], x[s + 2][fi]], 1)

    # 5. Total night counts — filter out days where the fellow is definitely
    #    blocked; those literals are unit-forced false so including them would
    #    inflate the sequential counter with dead weight.
    for fellow_name, total in config.total_nights.items():
        fi = parsed.fellow_names.index(fellow_name)
        lits = [x[d][fi] for d in range(num_days) if d not in blocked_days[fi]]
        cnf.exactly_k(lits, total)

    # 6. Friday night counts (day_of_week == 4, i.e., d % 7 == 4) — same filter
    for fellow_name, total in config.friday_nights.items():
        fi = parsed.fellow_names.index(fellow_name)
        lits = [x[d][fi] for d in range(num_days) if d % 7 == 4 and d not in blocked_days[fi]]
        cnf.exactly_k(lits, total)

    # 7. Multiset constraints — pass blocked_days so they can filter too
    for multiset in config.total_night_multisets:
        _add_multiset_cnf(cnf, parsed, var_map, multiset, friday_only=False, blocked_days=blocked_days)
    for multiset in config.friday_night_multisets:
        _add_multiset_cnf(cnf, parsed, var_map, multiset, friday_only=True, blocked_days=blocked_days)

    # 8 & 9. Policy criteria: hard blocking + soft violation collection
    weighted_soft_pairs: list[tuple[int, int]] = []  # (literal, weight)

    for d in range(num_days):
        week_index, day_of_week = divmod(d, 7)
        for fi, fellow_name in enumerate(parsed.fellow_names):
            criteria = _criteria_for_assignment(parsed, week_index, day_of_week, fellow_name)
            for criterion in criteria:
                if criterion in hard_criteria:
                    cnf.add_clause([-x[d][fi]])
                else:
                    weight = weights.for_criterion(criterion)
                    weighted_soft_pairs.append((x[d][fi], weight))

    # 9. Soft bound (weighted sum of soft violations)
    if soft_bound is not None and weighted_soft_pairs:
        cnf.weighted_sum_at_most(weighted_soft_pairs, soft_bound)

    return cnf, var_map


def extract_solution(
    parsed: ParsedCallScheduleCsv,
    var_map: NightCnfVarMap,
    assignment: dict[int, bool],
) -> NightScheduleSolution:
    """Map variable assignments back to fellow names."""
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
                    f"(role {role}) in SAT solution"
                )
            week_assignments[role] = assigned_fellow
        assignments_by_week.append(week_assignments)
    return NightScheduleSolution(assignments_by_week=assignments_by_week)


def weighted_upper_bound(
    parsed: ParsedCallScheduleCsv,
    weights: NightPolicyWeights,
    hard_criteria: frozenset[str],
) -> int:
    """Maximum possible weighted soft violations (sum of all non-hard criteria weights)."""
    total = 0
    for week_index in range(len(parsed.week_rows)):
        for day_of_week in range(7):
            for fellow_name in parsed.fellow_names:
                for criterion in _criteria_for_assignment(parsed, week_index, day_of_week, fellow_name):
                    if criterion not in hard_criteria:
                        total += weights.for_criterion(criterion)
    return total


def solve_night_schedule_parafrost_at_limit(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig = NightSolverConfig(),
    hard_criteria: frozenset[str] = frozenset(),
    weights: NightPolicyWeights = NightPolicyWeights(),
    max_violation_limit: int | None = None,
    runner: ParaFrostRunner,
    emit_summary: bool = True,
) -> NightPolicySolveResult:
    """Single solve with bound. Returns unoptimized result."""
    limit = weighted_upper_bound(parsed, weights, hard_criteria) if max_violation_limit is None else max_violation_limit
    cnf, var_map = build_night_cnf(
        parsed,
        config=config,
        hard_criteria=hard_criteria,
        weights=weights,
        soft_bound=limit,
    )
    result = runner.solve(cnf)
    if not result.satisfiable:
        raise ValueError(
            f"Night call schedule is unsatisfiable with <= {limit} weighted soft violations"
        )
    solution = extract_solution(parsed, var_map, result.assignment)
    counts = criteria_counts_for_solution(parsed, solution, weights=weights)
    policy_result = NightPolicySolveResult(
        tier=f"parafrost-unoptimized-soft<={limit}",
        solution=solution,
        counts=counts,
        hard_criteria=hard_criteria,
        optimized=False,
    )
    if emit_summary:
        from scheduler.night_call_solver_policy import print_policy_summary
        print_policy_summary(parsed, policy_result)
    return policy_result


def solve_night_schedule_parafrost_incremental(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig = NightSolverConfig(),
    hard_criteria: frozenset[str] = frozenset(),
    weights: NightPolicyWeights = NightPolicyWeights(),
    max_violation_limit: int | None = None,
    runner: ParaFrostRunner,
    emit_summary: bool = True,
) -> NightPolicySolveResult:
    """Binary search to find minimum violations."""
    upper = (
        weighted_upper_bound(parsed, weights, hard_criteria)
        if max_violation_limit is None
        else max_violation_limit
    )
    best_solution: NightScheduleSolution | None = None
    best_counts: NightPolicyCounts | None = None
    best_limit: int | None = None

    low, high = 0, upper
    while low <= high:
        mid = (low + high) // 2
        cnf, var_map = build_night_cnf(
            parsed,
            config=config,
            hard_criteria=hard_criteria,
            weights=weights,
            soft_bound=mid,
        )
        result = runner.solve(cnf)
        if result.satisfiable:
            best_solution = extract_solution(parsed, var_map, result.assignment)
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
        tier=f"parafrost-incremental-soft<={best_limit}",
        solution=best_solution,
        counts=best_counts,
        hard_criteria=hard_criteria,
        optimized=True,
    )
    if emit_summary:
        from scheduler.night_call_solver_policy import print_policy_summary
        print_policy_summary(parsed, policy_result)
    return policy_result
