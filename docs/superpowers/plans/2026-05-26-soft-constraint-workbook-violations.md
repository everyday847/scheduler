# Soft Constraint Workbook Violations Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the per-fellow Excel workbook report sparse active soft/minimize constraint violations instead of all NCC service profile rows.

**Architecture:** Keep workbook generation in `src/scheduler/service.py`. Reuse `_configured_constraints(request)` to get the same active standing and annual constraints used by the solver, then compute report rows from `shifts_for_fellows` and `fellows_for_shifts` without invoking Z3.

**Tech Stack:** Python, OpenPyXL, PyYAML, pytest.

---

## Chunk 1: Sparse Soft Violations Report

### Task 1: Replace NCC-specific comparison with active soft/minimize violations

**Files:**
- Modify: `tests/test_scheduler_service.py`
- Modify: `src/scheduler/service.py`

- [ ] **Step 1: Write failing workbook tests**

Update the existing workbook comparison test so it uses active soft standing rules and expects only nonzero rows. Add assertions for service profile gaps, block preference violations, swing deficit from `minimize_uncovered_shift_weeks`, and omission of zero-gap/hard/inactive rows.

- [ ] **Step 2: Run the targeted test red**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_scheduler_service.py::test_per_fellow_workbook_reports_active_soft_constraint_violations_only -q`

Expected: FAIL because the current implementation only reports NCC service profile rules and includes zero-gap rows.

- [ ] **Step 3: Implement report row builders**

In `src/scheduler/service.py`, replace the NCC-specific helper names with soft-constraint helpers. Add row builders for:

- `service_profile`: totals, zero shifts, window totals, active blocks.
- `block_shift_set_choice`: compare each fellow/block to allowed choices and allowed-none.
- `minimize_uncovered_shift_weeks`: count weeks where no selected fellow covers the selected shift.
- `specific_assignment`: report when the selected fellow/group is not assigned the requested shift in the requested week.

- [ ] **Step 4: Filter report rows**

Write rows only when `Violation > 0`. Omit hard constraints, inactive constraints, and unsupported soft kinds.

- [ ] **Step 5: Run targeted test green**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_scheduler_service.py::test_per_fellow_workbook_reports_active_soft_constraint_violations_only -q`

Expected: PASS.

- [ ] **Step 6: Run related tests**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_scheduler_service.py -q`

Expected: PASS.

- [ ] **Step 7: Commit implementation**

Commit only the implementation and test files:

```bash
git add src/scheduler/service.py tests/test_scheduler_service.py
git commit -m "feat: report active soft constraint violations"
```
