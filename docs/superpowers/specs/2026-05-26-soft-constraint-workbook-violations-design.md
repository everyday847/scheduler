# Soft Constraint Workbook Violations Design

## Goal

Replace the NCC-only service profile comparison in the per-fellow workbook sheet with a sparse report of active soft/minimize constraint violations.

## Behavior

The `Per-Fellow Schedule` worksheet will keep the existing week-by-fellow schedule table. To the right of it, the workbook will write a `Soft Constraint Violations` table that includes only active configured constraints whose quantified violation is nonzero.

Hard active constraints are omitted because solved schedules should satisfy them. Inactive constraints are omitted because they were not selected for the solve. The report includes both standing and annual rules by reusing the same configured constraints that are passed to the optimizer.

## Supported Rules

The report will quantify the soft/minimize rules that have clear schedule-derived measurements today:

- `service_profile`: full-year totals, zero shifts, window totals, and active-block count requirements.
- `block_shift_set_choice`: one row for each fellow/block that does not match any allowed block choice or allowed-none state.
- `minimize_uncovered_shift_weeks`: one summary row with the actual uncovered shift count, such as swing deficit.
- `specific_assignment`: one row for each unsatisfied soft assignment.

Unsupported soft rule kinds are ignored rather than shown as unquantified rows, to keep the report sparse and action-oriented.

## Table Shape

The report columns are:

- `Fellow`: fellow name for per-fellow rules, or blank for global summary rows.
- `Group`: fellow group for per-fellow rules, or blank for global summary rows.
- `Rule`: configured rule name when present.
- `Scope`: the relevant total, window, block, or week.
- `Requirement`: concise expected behavior.
- `Current`: measured schedule value.
- `Violation`: positive violation count.

Only rows with `Violation > 0` are written.

## Testing

Tests should build synthetic workbooks with temporary standing-rule YAML and inspect the generated worksheet with OpenPyXL. Coverage should prove that:

- active soft service-profile violations are included;
- zero-gap rows are omitted;
- active soft block preference violations are included;
- minimize swing deficit is reported from the schedule;
- inactive or hard rules are omitted.
