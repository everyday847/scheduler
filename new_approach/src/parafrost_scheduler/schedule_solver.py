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


from parafrost_scheduler.schedule_loader import load_schedule_config


# ---------------------------------------------------------------------------
