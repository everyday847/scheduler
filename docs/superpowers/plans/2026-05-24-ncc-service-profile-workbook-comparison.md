# NCC Service Profile Workbook Comparison Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a right-side Excel report comparing NCC junior and senior fellows' schedules to inactive NCC service profile rules.

**Architecture:** Keep workbook generation in `src/scheduler/service.py`. Add pure helpers that extract relevant standing rules, count scheduled shifts, compute requirement gaps, and write the resulting rows beside the existing per-fellow table.

**Tech Stack:** Python, OpenPyXL, PyYAML, pytest.

---

## Chunk 1: Workbook Comparison Table

### Task 1: Add workbook report coverage

**Files:**
- Modify: `tests/test_scheduler_service.py`
- Modify: `src/scheduler/service.py`

- [ ] **Step 1: Write the failing test**

Add a test that monkeypatches `service.STANDING_RULE_CONFIG` to a temporary YAML file with inactive `ncc_jr_service_profile` and `ncc_sr_service_profile` rules. Build a workbook from a synthetic result containing one NCC junior, one NCC senior, and one Stroke fellow. Load the bytes with `openpyxl` and assert that the right-side table includes total, zero-shift, and window rows for the NCC fellows only.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_scheduler_service.py::test_per_fellow_workbook_reports_inactive_ncc_service_profile_gaps -q`

Expected: FAIL because the report headers are not present.

- [ ] **Step 3: Add comparison helpers**

In `src/scheduler/service.py`, add helpers to:

- Load `ncc_jr_service_profile` and `ncc_sr_service_profile` from the standing YAML regardless of `active`.
- Map those rules to fellows in `request["fellow_groups"]["NCC_JR"]` and `request["fellow_groups"]["NCC_SR"]`.
- Count matching shifts over all weeks or over a configured `[start, end)` window.
- Format requirement text and compute gaps for `exactly`, `at_least`, and `at_most`.

- [ ] **Step 4: Write the right-side table**

Update `_build_per_fellow_sheet` to write the existing schedule table first, then write the comparison table starting three columns to the right of the schedule grid. Use stable headers: `Fellow`, `Group`, `Rule`, `Scope`, `Requirement`, `Current`, `Gap`.

- [ ] **Step 5: Run the targeted test**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_scheduler_service.py::test_per_fellow_workbook_reports_inactive_ncc_service_profile_gaps -q`

Expected: PASS.

- [ ] **Step 6: Run related service tests**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_scheduler_service.py -q`

Expected: PASS, unless unrelated local standing-rule edits affect expectations.

- [ ] **Step 7: Commit**

Commit only `src/scheduler/service.py` and `tests/test_scheduler_service.py`:

```bash
git add src/scheduler/service.py tests/test_scheduler_service.py
git commit -m "feat: report ncc service profile gaps in workbook"
```
