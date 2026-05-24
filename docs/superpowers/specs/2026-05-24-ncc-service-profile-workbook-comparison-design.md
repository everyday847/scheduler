# NCC Service Profile Workbook Comparison Design

## Goal

Show each NCC junior and senior fellow's current schedule against the inactive `ncc_jr_service_profile` and `ncc_sr_service_profile` Standing Rules in the per-fellow Excel worksheet.

## Behavior

The workbook's `Per-Fellow Schedule` sheet will keep the existing week-by-fellow schedule table. To the right of that table, the sheet will add a service profile comparison table for fellows in `NCC_JR` and `NCC_SR`.

Each comparison row will represent one rule requirement for one fellow:

- `totals`: compare the fellow's count of the listed shifts across the full 52-week schedule.
- `zero_shifts`: compare the fellow's full-year count for a forbidden shift against zero.
- `window_totals`: compare the fellow's count of the listed shifts inside the configured window.

The table will show the fellow, group, rule name, requirement, current count, and gap/status. Disabled service profile rules remain unenforced by the solver; the workbook reads them as reporting requirements only.

## Architecture

`src/scheduler/service.py` already owns workbook generation and has access to the normalized request and `shifts_for_fellows`. It should also read the standing rule YAML already used for solve configuration and extract the inactive NCC service profiles by rule name.

The comparison logic should be small pure helpers in `service.py` so it can be tested without invoking the optimizer. The workbook writer will call those helpers and write the report table starting a few blank columns after the schedule grid.

## Testing

Add focused tests in `tests/test_scheduler_service.py` that build a workbook from synthetic schedules and inspect the generated worksheet with `openpyxl`. The tests should verify:

- The right-side comparison table is present on `Per-Fellow Schedule`.
- NCC junior and senior fellows are included.
- `totals`, `zero_shifts`, and `window_totals` requirements produce current counts and gap/status values.
- Non-NCC fellows are not included in the NCC comparison table.
