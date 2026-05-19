# Constraint Lifecycle Classification

This document classifies the current constraint logic in `src/scheduler/main.py` using the project vocabulary in `CONTEXT.md`.

## Lifecycle Policy

- **Solver Invariants** are not user configurable. They make a **Schedule** coherent enough for solver optimization.
- **Standing Rules** are deliberate program policy. They may eventually come from YAML or another reviewed configuration file.
- **Annual Rules** are the only lifecycle that should be exposed through Streamlit, React, or command line arguments.

## Solver Invariants

| Current code | Current behavior | Notes |
| --- | --- | --- |
| `ScheduleConstraints._create_variables` in `constraints.py` | Creates a Boolean variable for each Fellow/week/Shift tuple. | Solver representation, not fellowship policy. |
| `ScheduleConstraints._add_one_rotation_per_week` | A Fellow can do at most one Shift in a week. | This is the cleanest current Solver Invariant. |
| `ScheduleConstraints._add_symmetry_breaking` | Restricts CCM placeholder fellows to specific blocks in order. | Solver optimization over an **External Coverage Pool**. CCM identities are interchangeable for this Schedule, so avoiding permutations preserves relevant feasible schedules while reducing search space. |

## Standing Rules

| Current code | Current behavior | Notes |
| --- | --- | --- |
| `full_assignment_for_owned_schedules` in Standing Rule YAML | NCC and Stroke fellows get a full 52-week assignment. | Standing Rule because it reflects responsibility for these Fellow Groups, not solver coherence. |
| `ncc_coverage` and `minimize_swing_deficit` in Standing Rule YAML | NCC1 and NCC2 have weekly coverage bounds; Swing has at most one person and uncovered Swing weeks are minimized. | Coverage bounds and the minimization target are separate declarative Standing Rules. |
| `ncc_stroke_oversight` | At least one NCC team each week has an NCC or Stroke Fellow. | Standing Rule. The comment says "ideally," so strength may need to become explicit. |
| `maximum_consecutive_icu_shifts` / `maximum_consecutive_icu_shifts_soft` | Limits consecutive weeks in selected Shift sets. | Standing Rule with configurable thresholds and hard/soft strength. |
| `ncc_jr_service_profile` window totals | Junior NCC fellows start with MICU in the first month and have NCC before week 19. | Standing Rule expressed declaratively with service-profile window counts. |
| `jr_fellows_n_ncc_before_swing` | Junior NCC fellows have N NCC weeks before first Swing. | Standing Rule with a configurable count. |
| `ccm_service_profile` in Standing Rule YAML | CCM fellows do one NCC-ish block with three NCC weeks, one Swing week, and no service on excluded shifts. | Standing Rule expressed declaratively with service totals and active-block counts. |
| `stroke_service_profile` in Standing Rule YAML | Stroke fellows have fixed service totals and forbidden Shifts. | Standing Rule expressed declaratively by Fellow Group. |
| `ncc_jr_service_profile` in Standing Rule YAML | Junior NCC fellows have fixed service totals and forbidden Shifts. | Standing Rule expressed declaratively by Fellow Group. |
| `ncc_sr_service_profile` in Standing Rule YAML | Senior NCC fellows have fixed service totals and forbidden Shifts. | Standing Rule expressed declaratively by Fellow Group. |
| `shift_blocked` / `shift_blocked_soft` | A Shift is all-or-none inside Blocks of a given size. | Standing Rule primitive. |
| `all_or_none_block`, `block_shift_count`, and `block_shift_set_choice` in Standing Rule YAML | Specific Shifts and Shift sets follow configured Block structure. | Standing Rules; block sizes, allowed counts, hard requirements, and soft preferences live in YAML. |
| `ncc_two_week_block_choice` and `ncc_four_week_block_preference` in Standing Rule YAML | NCC1/NCC2/Swing follow hard two-week side-consistent Blocks with a soft four-week preference. | Declarative replacement for the former NCC-specific block helper. |
| `nir_one_week_per_half` | Stroke fellows get one NIR week in each half-year. | Standing Rule. |
| `scvmc_second_half` | Stroke fellows get SCVMC Rehab in the second half-year. | Standing Rule. |
| `comparable_amounts_each_half_year` | MICU and NCC-ish assignments cannot be too frontloaded or backloaded. | Standing Rule with a configurable tolerance. |
| `stroke_no_block_one_ncc` | Stroke fellows avoid NCC1/NCC2 and cannot do Swing in the first block. | Standing for Stroke if this is recurring onboarding policy; NH usage belongs with Annual Rules. |

## Annual Rules

| Current code | Current behavior | Notes |
| --- | --- | --- |
| `vacation_request_policy` in Annual Rule YAML | First N requests become hard Vacation; later requests become soft Elective. | Annual Rule and the best current candidate for UI exposure. |
| `specific_assignment` / `specific_assignment_soft` | Forces or prefers a named assignment in a week. | Annual Rule primitive. |
| `stroke_shifts_covered` | Stroke and Telestroke/Clinic coverage. | Standing Rule in current YAML; named supervision exceptions should become Annual Rules when needed. |
| `nh_service_profile` in Annual Rule YAML | NH fellows have fixed service totals and forbidden Shifts for a year when NH service is present. | Annual Rule because NH service is not present every year. |
| `stroke_no_block_one_ncc` for NH fellows | NH fellows avoid NCC1/NCC2 and cannot do Swing in the first block. | Annual Rule when NH service is present. |
| `fourth_block_two_micu_fellows` | Requires two MICU fellows during weeks 12-15. | Annual Rule. |
| `jeff_abpn_stroke`, `first_week_senior_on_stroke`, `first_week_telestroke`, and `abpn_backup_telestroke` in Annual Rule YAML | Forces or prefers selected named/year-specific assignments. | Annual Rules exposed through request YAML. |
| `isc` | Forces ISC on the week containing 2026-02-05 and forbids it otherwise. | Annual Rule because the date is schedule-year-specific. |

## Needs A Lifecycle Decision

| Current code | Why it is ambiguous |
| --- | --- |
| `stroke_no_block_one_ncc` for Stroke fellows | Needs confirmation whether first-block NCC avoidance is recurring Stroke onboarding policy or current-cohort policy. |

## Refactoring Implications

The first deepening seam should separate constraint lifecycle from solver encoding:

- The solver Adapter owns variable creation, `Optimize`, hard/soft encoding, and model extraction.
- The Standing Rule catalog owns reusable fellowship policy by Fellow Group, Shift, and Block.
- The Annual Rule intake owns user-provided requests, named exceptions, dates, and strength changes.
- Tiered solving belongs above individual Z3 expressions: for example, NCC Block policy should try stricter Standing Rules first, then relax to fallback strengths when needed.
- Soft minimization preferences, such as Swing deficit, should be modeled explicitly rather than exposed as ordinary Annual Rule counts.

The deletion test supports this seam: without it, lifecycle knowledge reappears in `optimize_schedule`, UI forms, future YAML loading, tests, and any natural-language-to-constraint path.
