# Remove Legacy Z3 Solver Code

## Motivation

The codebase has two parallel solver stacks: a Z3-based stack (original, now legacy) and a pseudo-Boolean RoundingSat stack (new, production). The Z3 stack is:

- **Night call solvers**: 7 variants (`night_call_solver.py`, `night_call_solver_relaxed.py`, `night_call_solver_policy.py`, `night_call_solver_bool_matrix.py`, `night_call_solver_bool_matrix_incremental.py`, `night_call_solver_incremental_relaxed.py`, `night_call_solver_optimize_relaxed.py`)
- **Weekend call solvers**: 2 variants (`weekend_call_solver.py`, `weekend_call_solver_relaxed.py`)
- **Weekly schedule pipeline**: `main.py` → `service.py` → `constraints.py`
- **Abandoned adapter**: `z3_adapter.py`

None of the actual Z3 solver functions are called by the production path (`web_app.py` → `solver_bridge.py` → `new_approach/.../schedule_solver.py` → RoundingSat). The production path imports only config/data types (`NightSolverConfig`, `WeekendSolverConfig`, etc.) from the Z3 files.

This extraction removes ~3,500 lines of Z3 solver code, deletes the dual-pipeline maintenance burden, and keeps only the config types that the RoundingSat path needs.

## What Stays

Three new type-only modules are extracted from the Z3 solver files:

### `scheduler/night_call_types.py`
- `NightSolverConfig` — config dataclass for night call solver parameters
- `NightScheduleSolution` — solved night schedule result type
- `CountMultiset` — utility for counting constrained permutations
- `holiday_indices_for_config` — computes holiday day indices from config
- `absolute_day_index` — converts (week, role, day-of-week) → absolute day
- `is_night_blocked`, `is_night_holiday_eligible`, `is_preferred_sunday_following_service` — predicates

### `scheduler/night_policy_types.py`
- `NightPolicyWeights`, `NightPolicyCounts`, `NightPolicySolveResult`, `NightPolicySpec` — dataclasses
- `CRITERION_ANAESTHESIA`, `CRITERION_CLINIC`, `CRITERION_FRIDAY_WEEKEND_NCC1`, `CRITERION_STROKE`, `CRITERION_SUNDAY_FOLLOWING`, `ALL_POLICY_CRITERIA` — constants
- `criteria_counts_for_solution`, `print_policy_summary` — utility functions

### `scheduler/weekend_call_types.py`
- `WeekendSolverConfig`, `WeekendScheduleSolution` — dataclasses
- `count_weekend_role_matches`, `summarize_weekend_solution`, `write_weekend_schedule_csv` — utility functions

### `scheduler/call_schedule_common.py`
- Stays as-is (no Z3 dependencies)

## What Gets Deleted (14 files)

**Z3 solver implementations (10):**
- `night_call_solver.py` — after type extraction
- `night_call_solver_relaxed.py` — pure Z3, no types to extract
- `night_call_solver_policy.py` — after type extraction
- `night_call_solver_bool_matrix.py` — pure Z3
- `night_call_solver_bool_matrix_incremental.py` — pure Z3
- `night_call_solver_incremental_relaxed.py` — pure Z3
- `night_call_solver_optimize_relaxed.py` — pure Z3
- `weekend_call_solver.py` — after type extraction
- `weekend_call_solver_relaxed.py` — pure Z3
- `z3_adapter.py` — abandoned Z3 adapter

**Old Z3 weekly-schedule pipeline (3):**
- `main.py` — `optimize_schedule()` and Z3 constraint helpers
- `service.py` — Z3 orchestration: `solve_schedule()` → `optimize_schedule()`
- `constraints.py` — Z3 `ScheduleConstraints` wrapper

**Legacy Streamlit UI (1):**
- `app.py` — replaced by React frontend

## What Gets Modified (import path updates)

All files that import from the deleted modules update their imports to point to the new type modules:

- `web_app.py` — remove 3 endpoints (`/api/schedule`, `/api/schedule.xlsx`, `/api/default-schedule`), update 2 imports
- `solver_bridge.py` — update 3 import paths
- `schedule_solver.py` + 6 sibling files in `new_approach/` — update import paths
- ~8 test files (type-only imports) — update import paths
- ~5 root scripts — update import paths

## Test Files to Delete (12)

Tests that exercise Z3 solver functions directly — preserved in git history + `z3-archive` branch:

- `test_night_call_solver.py`
- `test_night_call_solver_relaxed.py`
- `test_night_call_solver_relaxed_variants.py`
- `test_night_call_solver_policy.py`
- `test_weekend_call_solver.py`
- `test_weekend_call_solver_relaxed.py`
- `test_main_solver_flow.py`
- `test_rule_application.py`
- `test_configuration_examples.py`
- `test_constraints.py`
- `test_z3_adapter.py`
- `test_scheduler_service.py`

## Import Update Map

| Old import | New import |
|---|---|
| `scheduler.night_call_solver` | `scheduler.night_call_types` |
| `scheduler.night_call_solver_policy` | `scheduler.night_policy_types` |
| `scheduler.weekend_call_solver` | `scheduler.weekend_call_types` |

## Verification

- `run_solve_with_workbook.py` — the key RoundingSat path
- `pytest tests/` — surviving tests pass (baseline: 18 pre-existing failures)
