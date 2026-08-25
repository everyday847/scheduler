# Remove Legacy Z3 Solver Code — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract config/data types from Z3 solver files into 3 new type modules, delete all Z3 solver implementations, and update all import paths.

**Architecture:** New type modules (`night_call_types.py`, `night_policy_types.py`, `weekend_call_types.py`) hold dataclasses and utility functions with no Z3 dependency. The 14 old Z3 solver files are deleted. ~30 import statements updated across `new_approach/`, `tests/`, `src/scheduler/`, and root scripts.

**Tech Stack:** Python 3.12, no Z3 dependency in production path (only RoundingSat)

---

### Task 1: Create `src/scheduler/night_call_types.py`

**Files:**
- Create: `src/scheduler/night_call_types.py`
- Delete later: `src/scheduler/night_call_solver.py`

**Extract from `night_call_solver.py`:** All non-Z3 code:
- All imports except `from z3 import ...`
- `HORIZON_START_DATE`, `DEFAULT_HOLIDAY_DATES`
- `CountMultiset`, `NightSolverConfig`, `NightScheduleSolution`, `NightSummary` dataclasses
- `absolute_day_index()`, `date_to_week_and_day()`, `holiday_indices_for_config()`
- `is_night_blocked()`, `is_night_holiday_eligible()`, `is_preferred_sunday_following_service()`
- `parse_night_call_csv()`, `summarize_night_solution()`, `write_night_schedule_csv()`

Do NOT include: `solve_night_schedule()`, `_add_night_constraints()`, `_add_night_objectives()`, `_add_multiset_constraint()`, `_count_assignments()`, `_count_friday_assignments()`, `_eq_indicator()`, `_sum_or_zero()`, `count_night_soft_violations()`, `_night_soft_violation_terms()`, `_build_night_solution()`, `_print_night_summary()`, `main()`

Import from `scheduler.call_schedule_common` instead of aliasing through `common_is_*`.

### Task 2: Create `src/scheduler/night_policy_types.py`

**Files:**
- Create: `src/scheduler/night_policy_types.py`
- Delete later: `src/scheduler/night_call_solver_policy.py`

**Extract from `night_call_solver_policy.py`:** All non-Z3 code:
- All imports except `from z3 import ...`
- `CRITERION_*` constants, `ALL_POLICY_CRITERIA`
- `NightPolicyWeights`, `NightPolicyCounts`, `NightPolicySpec`, `NightPolicySolveResult`
- `staged_policy_specs()`
- `criteria_counts_for_solution()`, `print_policy_summary()`
- `parse_night_call_csv()`, `_criteria_for_assignment()`, `_validate_hard_criteria()`, `_format_hard_criteria()`
- `_staged_output_path()`, `_build_arg_parser()`, `main()` — _actually main() imports from the Z3 solver functions, so include it only if it doesn't depend on deleted Z3 code..._

Actually, I need to think carefully. `night_call_solver_policy.py` `main()` calls `solve_night_schedule_policy_incremental` and `solve_night_schedule_policy_at_limit` — these are Z3 solver functions being deleted. So `main()` can't survive in the types module. But `main()` is only a CLI entry point, so it's fine to delete.

Similarly, `run_staged_policy_schedules()` calls `_solve_policy_worker()` which calls the Z3 solve functions — this must stay with the Z3 code (and be deleted).

`print_policy_summary()` calls `summarize_night_solution()` from `night_call_solver` — this will be available from `night_call_types`.

So extract:
- All constants and dataclasses
- `staged_policy_specs()`
- `criteria_counts_for_solution()`
- `print_policy_summary()`
- `parse_night_call_csv()`
- `_criteria_for_assignment()`, `_validate_hard_criteria()`, `_format_hard_criteria()`, `_staged_output_path()`
- `_build_arg_parser()`

Do NOT include:
- `solve_night_schedule_policy_incremental()`, `solve_night_schedule_policy_at_limit()`
- `run_staged_policy_schedules()`, `_solve_policy_worker()`
- `_build_policy_solver()`, `_add_base_bool_constraints()`
- `_result_from_model()`, `_build_bool_matrix_solution()`
- `_add_multiset_constraint()`, `_count_assignments()`, `_count_friday_assignments()`
- `_weighted_upper_bound()`, `_indicator()`
- `main()`

### Task 3: Create `src/scheduler/weekend_call_types.py`

**Files:**
- Create: `src/scheduler/weekend_call_types.py`
- Delete later: `src/scheduler/weekend_call_solver.py`

**Extract from `weekend_call_solver.py`:** All non-Z3 code:
- All imports except `from z3 import ...`
- Default dict constants (`DEFAULT_EXACT_NCC_TOTALS`, etc.)
- `WeekendSolverConfig`, `WeekendScheduleSolution`, `WeekendSummary`
- `is_weekend_blocked()`, `parse_weekend_call_csv()`
- `summarize_weekend_solution()`, `count_weekend_role_matches()`, `write_weekend_schedule_csv()`
- `_print_weekend_summary()`

Do NOT include: `solve_weekend_schedule()`, `solve_weekend_schedule_staged()`, `_add_weekend_constraints()`, `_add_weekend_objectives()`, `_weekend_soft_violation_terms()`, `_solve_stroke_stage()`, `_solve_ncc_stage()`, `_count_exact_matches()`, `_matching_role_constraint()`, `_build_weekend_solution()`, `_count_role_assignments()`, `_add_spacing_constraints()`, `_count_assignments()`, `_eq_indicator()`, `main()`

### Task 4: Update import paths in `src/scheduler/`

**Files to modify:**
- `src/scheduler/web_app.py` — line 8: remove `from .service import ...`; lines 175-176: update `NightSolverConfig`/`WeekendSolverConfig` imports; remove 3 endpoints
- `src/scheduler/solver_bridge.py` — update imports from `night_call_solver` → `night_call_types`, `weekend_call_solver` → `weekend_call_types`

### Task 5: Update import paths in `new_approach/`

**Files to modify:**
- `new_approach/src/parafrost_scheduler/schedule_solver.py` (lines 39-56)
- `new_approach/src/parafrost_scheduler/night_solver.py`
- `new_approach/src/parafrost_scheduler/night_solver_pb.py`
- `new_approach/src/parafrost_scheduler/joint_solver.py`
- `new_approach/src/parafrost_scheduler/workbook.py`
- `new_approach/src/parafrost_scheduler/cli.py`

### Task 6: Update import paths in `tests/` (type-only imports)

**Files to modify (keep, update imports):**
- `tests/test_night_constraint_fixes.py`
- `tests/test_constraint_semantics.py`
- `tests/test_new_constraints.py`
- `tests/test_weekend_night_linking.py`
- `tests/test_prevacation_penalty.py`
- `tests/test_annual_call_rules.py`
- `tests/test_solver_bridge.py`
- `tests/test_calendar_model.py`

### Task 7: Update import paths in root scripts

**Files to modify:**
- `run_v3_optimize.py`
- `run_v3_optimize_wb2.py`
- `run_v3_no_workbook.py`
- `evaluate_schedule.py`
- `diagnose_coverage_deficit.py`

### Task 8: Delete old Z3 solver files

**Delete:**
- `src/scheduler/night_call_solver.py`
- `src/scheduler/night_call_solver_relaxed.py`
- `src/scheduler/night_call_solver_policy.py`
- `src/scheduler/night_call_solver_bool_matrix.py`
- `src/scheduler/night_call_solver_bool_matrix_incremental.py`
- `src/scheduler/night_call_solver_incremental_relaxed.py`
- `src/scheduler/night_call_solver_optimize_relaxed.py`
- `src/scheduler/weekend_call_solver.py`
- `src/scheduler/weekend_call_solver_relaxed.py`
- `src/scheduler/z3_adapter.py`
- `src/scheduler/main.py`
- `src/scheduler/service.py`
- `src/scheduler/constraints.py`
- `src/scheduler/app.py`

### Task 9: Delete Z3 solver test files

**Delete:**
- `tests/test_night_call_solver.py`
- `tests/test_night_call_solver_relaxed.py`
- `tests/test_night_call_solver_relaxed_variants.py`
- `tests/test_night_call_solver_policy.py`
- `tests/test_weekend_call_solver.py`
- `tests/test_weekend_call_solver_relaxed.py`
- `tests/test_main_solver_flow.py`
- `tests/test_rule_application.py`
- `tests/test_configuration_examples.py`
- `tests/test_constraints.py`
- `tests/test_z3_adapter.py`
- `tests/test_scheduler_service.py`

### Task 10: Remove old endpoints from `web_app.py`

**Remove endpoints:**
- `GET /api/default-schedule`
- `POST /api/schedule`
- `POST /api/schedule.xlsx`

**Remove imports:**
- `from .service import build_schedule_workbook, get_default_schedule_request, solve_schedule`

**Update imports:**
- `from .night_call_solver import NightSolverConfig` → `from .night_call_types import NightSolverConfig`
- `from .weekend_call_solver import WeekendSolverConfig` → `from .weekend_call_types import WeekendSolverConfig`

### Task 11: Verification

Run `run_solve_with_workbook.py` and surviving tests.
