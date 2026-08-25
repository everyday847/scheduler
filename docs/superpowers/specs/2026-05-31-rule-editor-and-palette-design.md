# Rule Editor & Generic Constraint Palette

**Date**: 2026-05-31
**Status**: Approved

## Overview

Replace the current flat rule tables (toggle active/strength only) with a full rule editor built on a **generic constraint palette** — 8 reusable rule types that can express every existing scheduling constraint. The fellowship director creates, edits, and removes rules using domain language without understanding the solver internals.

This also decomposes the monolithic `service_profile` kind into individual rules (separate Shift Total rules per group/shift, with forbidden shifts derived automatically), and reclassifies most current "standing" rules as annual.

## Target User

A fellowship program director who understands the medical domain (NCC coverage, block rotations, service weeks) but not constraint programming. The UI speaks in terms like "NCC juniors need 20 weeks of MICU" and "no more than 6 consecutive weeks on the NCC team."

## The Rule Type Palette

8 generic constraint types that cover every existing rule:

| # | Director-facing name | Type key | Parameters |
|---|---|---|---|
| 1 | **Shift Total** | `shift_total` | groups, shifts[], relation (exactly/at_least/at_most), count, window (optional: start_week, end_week) |
| 2 | **Staffing Per Week** | `staffing_per_week` | groups[], shifts[], relation, count, window (optional) |
| 3 | **Coverage Target** | `coverage_target` | groups[], shifts[], max_uncovered_weeks |
| 4 | **Max Consecutive** | `max_consecutive` | groups[], shifts[], max_weeks |
| 5 | **Block Rotation** | `block_rotation` | groups[], shifts[], block_size |
| 6 | **Rotation Continuity** | `rotation_continuity` | groups[], choices (list of shift-set arrays), block_size, allow_none |
| 7 | **Prerequisite** | `prerequisite` | groups[], prerequisite_shifts[], target_shifts[], min_prerequisite_weeks |
| 8 | **Windowed Balance** | `windowed_balance` | groups[], shifts[], window_a (start, end), window_b (start, end), max_difference |

Plus three non-palette types retained as-is:
- **Full Assignment** (`full_assignment`) — every fellow in the specified groups must be assigned exactly one shift every week. A structural invariant, not user-editable through the palette.
- **Specific Assignment** (`specific_assignment`) — Fellow X on shift Y in week Z (used for ISC, pinned assignments)
- **Vacation Request** — handled through the existing vacation date picker UI

### Derived Behavior: Automatic Forbidden Shifts

For each fellow group, any shift that does not appear in any of that group's Shift Total rules is automatically forbidden. No separate "Forbidden Shifts" rule type is needed. The solver bridge computes the forbidden set at formula-build time.

## Rule Decomposition: Current Config to Palette

### Standing Rules (remain standing)

These are inter-program commitments and structural invariants that rarely change:

| Current rule | Palette type | New name |
|---|---|---|
| `full_assignment_for_owned_schedules` | Full Assignment (non-palette) | Full Assignment for NCC/Stroke |
| `ncc_coverage` (decomposed, sans swing) | 5x Staffing Per Week | NCC1 Coverage, NCC2 Coverage, NCC Team Cap, NCC+Swing Cap, Swing Cap |
| `junior_ncc_before_swing` | Prerequisite | NCC Junior: NCC Before Swing |
| `micu_four_week_blocks` | Block Rotation | MICU 4-Week Blocks |
| `sicu_four_week_blocks` | Block Rotation | SICU 4-Week Blocks |
| `anaesthesia_four_week_blocks` | Block Rotation | Anaesthesia 4-Week Blocks |
| `ns_four_week_blocks` | Block Rotation | NS 2-Week Blocks |
| `ncc_stroke_oversight` | Staffing Per Week | NCC/Stroke Oversight |
| `stroke_shift_coverage` | Staffing Per Week | Stroke Shift Coverage |
| `stroke_no_first_block_ncc` | Shift Total (window 0-3, count=0) | Stroke: No NCC in First Block |
| NCC JR `window_totals` (MICU wks 0-4) | Shift Total (windowed) | NCC Junior Orientation MICU |
| NCC JR `window_totals` (NCC wks 4-19) | Shift Total (windowed) | NCC Junior Early NCC Exposure |

### Annual Rules (moved from standing or decomposed from service profiles)

These change year-to-year based on fellow count, program structure, and director preference:

| Current rule | Palette type | New name |
|---|---|---|
| `core_icu` | Max Consecutive | Core ICU Max Consecutive |
| `ncc_team` | Max Consecutive | NCC Team Max Consecutive |
| `swing` | Max Consecutive | Swing Max Consecutive |
| `ncc_two_week_block_choice` | Rotation Continuity | NCC 2-Week Team Continuity |
| `ncc_four_week_block_preference` | Rotation Continuity | NCC 4-Week Team Preference |
| `minimize_swing_deficit` + swing part of `ncc_coverage` | Coverage Target | Swing Coverage Target |
| `comparable_half_year_distribution` | Windowed Balance | Half-Year Balance |
| `stroke_short_core_icu_runs` | Max Consecutive | Stroke Short Core ICU Runs |
| `stroke_nir_one_week_per_half` | 2x Shift Total (windowed) | Stroke NIR First Half, Stroke NIR Second Half |
| `stroke_scvmc_second_half` | Shift Total (windowed) | Stroke SCVMC Second Half |
| `stroke_two_week_blocks` | Block Rotation | Stroke 2-Week Blocks |
| NCC JR totals (from service_profile) | ~6x Shift Total | NCC Junior: MICU Total, NCC Junior: Anaesthesia Total, etc. |
| NCC SR totals | ~6x Shift Total | NCC Senior: MICU Total, NCC Senior: NS Total, etc. |
| Stroke totals | ~9x Shift Total | Stroke: Stroke Service Total, Stroke: Telestroke Total, etc. |
| NH totals | 2x Shift Total | NH: NCC Total, NH: Swing Total |
| CCM totals | 2x Shift Total | CCM: NCC Total, CCM: Swing Total |
| CCM `active_blocks` | Block Rotation | CCM NCC Block |

Zero-shifts for all groups are derived automatically from their Shift Total rules (see Derived Behavior above).

## YAML Schema (Palette Format)

Both standing and annual configs use the same `rules:` format. Every rule has `name`, `type`, `groups`, `strength`, and `active`. Type-specific parameters vary per palette type.

```yaml
fellow_groups:
  NCC_JR: [NCC Alaric, NCC Bertram]
  NCC_SR: [NCC Durga, NCC Eos]
  STROKE: [Stroke Gabi, Stroke Jeff, Stroke Victoria, Stroke Parshva]
  CCM: [CCM Generic]
  NH: [NH Adam, NH Barry]

shifts:
  - SICU
  - MICU
  - NS
  - Anaesthesia
  - NCC1
  - NCC2
  - Swing
  - Elec
  - Vac
  - Stroke
  - Telestroke/Clinic
  - Clinic/Elective
  - SCVMC Rehab
  - NIR
  - ISC

num_weeks: 52
horizon_start: 2026-06-29

rules:
  - name: NCC Junior MICU Total
    type: shift_total
    groups: [NCC_JR]
    shifts: [MICU]
    relation: exactly
    count: 20
    strength: hard
    active: true

  - name: Core ICU Max Consecutive
    type: max_consecutive
    groups: [NCC_JR, NCC_SR, STROKE, NH]
    shifts: [NCC1, NCC2, Swing, SICU, MICU, Stroke]
    max_weeks: 8
    strength: hard
    active: true

  - name: NCC 2-Week Team Continuity
    type: rotation_continuity
    groups: [NCC_JR, NCC_SR, CCM]
    block_size: 2
    choices:
      - [NCC1, Swing]
      - [NCC2, Swing]
    allow_none: true
    strength: hard
    active: true

  - name: Half-Year Balance
    type: windowed_balance
    groups: [NCC_JR, NCC_SR]
    shifts: [MICU]
    window_a: [0, 26]
    window_b: [26, 52]
    max_difference: 4
    strength: soft
    active: true

  - name: Swing Coverage Target
    type: coverage_target
    groups: [NCC_JR, NCC_SR, STROKE, CCM, NH]
    shifts: [Swing]
    max_uncovered_weeks: 2
    strength: soft
    active: true

night_call:
  - group: STROKE
    total_nights: 236
    friday_nights: 32
  # ...

weekend_call:
  - group: STROKE
    ncc_total: 22
    stroke_total: 50
  # ...

holiday_dates:
  - 2026-07-03
  # ...
```

## App Layout

The single-column layout is replaced with a sidebar + main panel layout.

### Top Bar
Config file selector, draft status indicator ("Draft: 3 changes"), and Publish/Discard buttons. Always visible.

### Left Sidebar
Navigation organized into two sections:

**CONFIG**: Fellows, Shifts (click to edit in main panel)

**RULES** (by group): NCC Jr, NCC Sr, Stroke, NH, CCM, Program (standing rules). Clicking a group shows that group's rules in the main panel.

**ANNUAL**: Night, Weekend, Vacations, Holidays (click to edit in main panel)

**Generate button** at the bottom of the sidebar.

### Main Panel
Shows the selected group's rules organized in logical clusters:

- **Coverage Totals**: Compact table of Shift Total rules. Inline editing — click a number to change the count, click a relation to toggle. Each row has feasibility pips.
- **Constraints**: Checklist of other rule types (Max Consecutive, Block Rotation, etc.) with one-line summaries. Expanding a row opens the inline edit form.
- **Orientation/Windowed**: Windowed Shift Totals and Windowed Balance rules.
- **Feasibility summary**: Aggregate status at the bottom ("14/14 pass standalone, all pairwise pass").
- **"+ Add Rule"** button opens the palette picker for the selected group.

### Schedule View
Clicking Generate replaces the main panel with the schedule view (progress bar, streaming SSE results, tabbed weekly/weekend/night tables). If UNSAT or unsatisfying, the back arrow returns to the editor with all state intact.

## Feasibility Checking

### Two-Pip System

Each active annual rule displays two feasibility indicators:

- **Pip 1 (solo)**: Feasible with standing rules alone. Encode standing rules + this rule, solve feasibility (~1s).
- **Pip 2 (pairwise)**: Feasible in all pairwise combinations with other active annual rules. For each other active annual rule Q, encode standing + this rule + Q, solve feasibility. Green = all pairs SAT, red = at least one pair UNSAT.

### When It Runs

- On initial config load (background sweep)
- When any feasibility-affecting input changes: rule toggled, rule parameters edited, fellow added/removed, shift list changed
- Debounced: waits 500ms after last edit before starting probes

### Performance

- Pip 1: N probes for N active annual rules (~30 probes, ~30s)
- Pip 2: Run pairwise only for rules that pass Pip 1. Up to N*(N-1)/2 probes, but can limit concurrency to ~5 simultaneous probes.
- Pips display as gray (unchecked) -> green/red as results arrive. Spinner on the feasibility summary line while probes are running.

### Backend

New endpoint: `POST /api/feasibility/check`
- Accepts: full config + subset of rules to probe
- Returns: SAT/UNSAT
- Frontend fires probes via `Promise.all` with concurrency limiting

## Draft/Publish Persistence

### File Layout

- `config/annual/my-2025-2026.yaml` — published config
- `config/annual/.drafts/my-2025-2026.draft.yaml` — auto-saved working copy

### Backend Endpoints

- `GET /api/config/annual/<file>` — returns published config (existing)
- `GET /api/config/annual/<file>/draft` — returns draft if exists, else published
- `PUT /api/config/annual/<file>/draft` — saves draft
- `POST /api/config/annual/<file>/publish` — copies draft to published, deletes draft
- `DELETE /api/config/annual/<file>/draft` — discards draft

### Frontend Behavior

- On load: fetch draft (falls back to published)
- Auto-save draft every 5 seconds if changes exist (debounced)
- "Publish" button enabled only when draft differs from published
- "Discard" reverts to published state

## Architecture: Solver Integration (Approach C — Incremental)

The palette schema is the source of truth for both YAML configs and the UI. The solver is refactored incrementally to accept the generic palette format.

### Phase 1: Schema + Translation
Define the palette schema. Add a translation layer in `solver_bridge.py` that converts palette-format rules into the current `SemanticConstraint` objects. Validate by confirming the solver produces equivalent results with the translated rules.

### Phase 2: Solver Refactor
Refactor solver encoders one at a time to accept palette-format rules directly, starting with the straightforward ones (shift_total, max_consecutive, block_rotation) and working toward complex decompositions (ncc_coverage split into 5 Staffing Per Week rules). Remove the translation layer as each encoder is converted.

### Phase 3: UI
Build the React rule editor against the palette schema. The editor reads/writes palette-format YAML through the draft/publish API. Standing rules displayed in a locked "Program Rules" section.

The transition period is short: confirm that the new palette schema produces the same feasible/optimal results as the old config before building the UI on top of it.

## Deferred Future Work

### Database Backend
The current system uses YAML files on disk for persistence. In a deployed multi-user setting, this should be replaced with a database (PostgreSQL or similar) to support concurrent access, audit logging, version history, and role-based permissions. The palette schema and draft/publish workflow are designed to be storage-agnostic — the API endpoints abstract the persistence layer, so swapping YAML for a database requires changes only in the backend, not in the UI or solver.

### Additional Rule Types
The 8-type palette covers all current constraints. If a creative fellowship director invents a new scheduling pattern that can't be expressed through the existing types, a new palette type can be added by: (1) defining its parameters, (2) adding an encoder in the solver, (3) adding a form component in the React UI. The palette is designed to be extended without restructuring.
