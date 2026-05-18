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
| `ScheduleConstraints._add_one_rotation_per_week` and `everyone_one_rotation_per_week` | A Fellow can do at most one Shift in a week. | This is the cleanest current Solver Invariant. |
| `ScheduleConstraints._add_symmetry_breaking` and `first_thirteen_ccm_alphabetical` | Restricts CCM placeholder fellows to specific blocks in order. | Solver optimization over an **External Coverage Pool**. CCM identities are interchangeable for this Schedule, so avoiding permutations preserves relevant feasible schedules while reducing search space. |

## Standing Rules

| Current code | Current behavior | Notes |
| --- | --- | --- |
| `range_fellows_assigned_fully` | NCC and Stroke fellows get a full 52-week assignment. | Standing Rule because it reflects responsibility for these Fellow Groups, not solver coherence. |
| `ncc_shifts_covered_swing_deficit` | NCC1 and NCC2 have weekly coverage bounds; Swing has at most one person and permits uncovered weeks. | Mixed. NCC coverage is a Standing Rule; Swing deficit should become a soft minimization preference because the desired value is always as small as possible while still solving. |
| `ncc_stroke_oversight` | At least one NCC team each week has an NCC or Stroke Fellow. | Standing Rule. The comment says "ideally," so strength may need to become explicit. |
| `maximum_consecutive_icu_shifts` / `maximum_consecutive_icu_shifts_soft` | Limits consecutive weeks in selected Shift sets. | Standing Rule with configurable thresholds and hard/soft strength. |
| `jr_first_month_micu` | Junior NCC fellows start with MICU in the first month. | Standing Rule. |
| `jr_ncc_before_19` | Junior NCC fellows have NCC before week 19. | Standing Rule with a configurable week threshold. |
| `jr_fellows_n_ncc_before_swing` | Junior NCC fellows have N NCC weeks before first Swing. | Standing Rule with a configurable count. |
| `ccm_service_profile` in Standing Rule YAML | CCM fellows do one NCC-ish block with three NCC weeks, one Swing week, and no service on excluded shifts. | Standing Rule expressed declaratively with service totals and active-block counts. |
| `stroke_service_profile` in Standing Rule YAML | Stroke fellows have fixed service totals and forbidden Shifts. | Standing Rule expressed declaratively by Fellow Group. |
| `ncc_jr_service_profile` in Standing Rule YAML | Junior NCC fellows have fixed service totals and forbidden Shifts. | Standing Rule expressed declaratively by Fellow Group. |
| `ncc_sr_service_profile` in Standing Rule YAML | Senior NCC fellows have fixed service totals and forbidden Shifts. | Standing Rule expressed declaratively by Fellow Group. |
| `shift_blocked` / `shift_blocked_soft` | A Shift is all-or-none inside Blocks of a given size. | Standing Rule primitive. |
| `sicu_blocked`, `micu_blocked`, `anaesthesia_blocked`, `scvmc_blocked`, `vasc_blocked`, `ns_blocked` | Specific Shifts follow two- or four-week Block structure. | Standing Rules; the Block sizes belong in deliberate configuration. |
| `ncc_blocked` | NCC1/NCC2/Swing follow Block structure. | Standing Rule with a tiered solve strategy: first try hard four-week Blocks; fall back to soft four-week and hard two-week Blocks if needed. |
| `nir_one_week_per_half` | Stroke fellows get one NIR week in each half-year. | Standing Rule. |
| `scvmc_second_half` | Stroke fellows get SCVMC Rehab in the second half-year. | Standing Rule. |
| `comparable_amounts_each_half_year` | MICU and NCC-ish assignments cannot be too frontloaded or backloaded. | Standing Rule with a configurable tolerance. |
| `stroke_no_block_one_ncc` | Stroke fellows avoid NCC1/NCC2 and cannot do Swing in the first block. | Standing for Stroke if this is recurring onboarding policy; NH usage belongs with Annual Rules. |

## Annual Rules

| Current code | Current behavior | Notes |
| --- | --- | --- |
| `vacation_requests` | First N requests become hard Vacation; later requests become soft Elective. | Annual Rule and the best current candidate for UI exposure. |
| `specific_assignment` / `specific_assignment_soft` | Forces or prefers a named assignment in a week. | Annual Rule primitive. |
| `nh_first_stroke_with_victoria` | Requires a specific Fellow's first Stroke week to overlap with Victoria. | Annual Rule primitive; currently not called directly. |
| `stroke_shifts_covered` | Stroke and Telestroke/Clinic coverage, with a Victoria/Adam co-assignment exception. | Mixed. Coverage is a Standing Rule; the named co-assignment exception is an Annual Rule. |
| `nh_service_profile` in Annual Rule YAML | NH fellows have fixed service totals and forbidden Shifts for a year when NH service is present. | Annual Rule because NH service is not present every year. |
| `stroke_no_block_one_ncc` for NH fellows | NH fellows avoid NCC1/NCC2 and cannot do Swing in the first block. | Annual Rule when NH service is present. |
| `fourth_block_two_micu_fellows` | Requires two MICU fellows during weeks 12-15. | Annual Rule. |
| Jeff ABPN assignment in `optimize_schedule` | Forces Stroke Jeff to Stroke during the ABPN week. | Annual Rule. |
| first-week Stroke and Telestroke/Clinic assignments in `optimize_schedule` | Forces selected Fellows into first-week assignments. | Annual Rule unless first-week startup coverage is recurring policy. |
| soft ABPN Telestroke/Clinic assignment in `optimize_schedule` | Prefers NCC Prash or NCC David on Telestroke/Clinic during ABPN week. | Annual Rule. |
| Jeff first-block Elective loop in `optimize_schedule` | Forces Stroke Jeff to Elective for weeks 0-3. | Annual Rule. |
| `lia_thing` | Encodes NCC Lia's limited availability and preferred Swing count. | Annual Rule because it names a specific Fellow and date window. |
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
