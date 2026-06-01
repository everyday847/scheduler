# Call Schedule Rules — Design Spec

Date: 2026-06-01

Status: Draft

## Problem

Weekly fellowship assignment rules are surfaced in the YAML configs and editable in the React UI via an 8-type palette rule system. Weekend and night call constraints, however, are hardcoded in Python solvers (`weekend_call_solver.py`, `night_call_solver.py`, `night_call_solver_policy.py`) — they cannot be configured through YAML or edited in the UI.

Specific constraints currently hardcoded:
- **Weekend**: spacing rules (no consecutive weekends, max 2 in 4 weeks), blocked services (which weekday services preclude weekend call), stroke cohort eligibility + min/max/totals
- **Night**: spacing rules (max 1 night in 3 consecutive days), blocked services (which weekday services preclude night call), holiday eligibility scope (which services allow night call on holidays), soft penalty weights (anaesthesia, clinic, stroke, friday_weekend_ncc1, sunday_following), sunday-following preferred services

The per-group budget totals (`total_nights`, `friday_nights`, `ncc_total`, `stroke_total`) are already in the YAML (`night_call:` / `weekend_call:` sections) and have basic budget-bar UI, but are not part of the rule system.

## Approach

**Approach 1 (chosen)**: Add dedicated `night_rules:` and `weekend_rules:` YAML sections alongside the existing `rules:` (weekly) section. The existing `night_call:` and `weekend_call:` budget tables remain unchanged. The Python solvers read from these rule sections to populate config fields instead of using hardcoded values. The React UI extends the existing `RulePalette`/`RuleCard`/`RuleEditor` components to support the new rule types.

## YAML Schema

### New sections in annual config:

```yaml
# Existing — unchanged
night_call:
  - group: STROKE
    total_nights: 236
    friday_nights: 32
  - group: NCC_SR
    total_nights: 58
    friday_nights: 8
  - group: NCC_JR
    total_nights: 40
    friday_nights: 6
  - group: NH
    total_nights: 30
    friday_nights: 6

weekend_call:
  - group: STROKE
    ncc_total: 22
    stroke_total: 50
  - group: NCC_SR
    ncc_total: 30
    stroke_total: 2
  - group: NCC_JR
    ncc_total: 24
    stroke_total: 0
  - group: NH
    ncc_total: 6
    stroke_total: 0
  - group: CCM
    ncc_total: 22
    stroke_total: 0

# New — night rules
night_rules:

  - name: Night spacing
    description: "Max 1 night in any 3-consecutive-day window"
    type: night_spacing
    groups: [STROKE, NCC_JR, NCC_SR, NH]
    max_nights: 1
    window_days: 3
    strength: hard
    active: true

  - name: Night blocked services
    description: "Weekday services that preclude night call"
    type: night_blocked_services
    groups: [STROKE, NCC_JR, NCC_SR, NH, CCM]
    exact_services: [Vacation, "NS SCVMC", "AAN", "Elective/NCS 2026"]
    substring_services: [SICU, MSICU]
    strength: hard
    active: true

  - name: Holiday eligibility
    description: "Only NCC1/NCC2/Stroke fellows can do night call on holidays"
    type: night_holiday_eligibility
    groups: [STROKE, NCC_JR, NCC_SR, NH]
    allowed_services: [NCC1, NCC2, Stroke]
    strength: hard
    active: true

  - name: Night soft penalties
    description: "Weights for undesirable night call patterns"
    type: night_penalties
    groups: [STROKE, NCC_JR, NCC_SR, NH]
    weights:
      anaesthesia: 1
      clinic: 1
      stroke: 5
      friday_weekend_ncc1: 1
      sunday_following: 1
    strength: soft
    active: true

  - name: Sunday following preference
    description: "Preferred weekday service after Sunday night call"
    type: night_sunday_following
    groups: [STROKE, NCC_JR, NCC_SR, NH]
    preferred_services:
      - Elec
      - Telestroke/Clinic
      - Clinic/Elective
      - SCVMC Rehab
      - NIR
      - ISC
      - Vac
    strength: soft
    active: true

# New — weekend rules
weekend_rules:

  - name: Weekend spacing
    description: "No consecutive weekends, max 2 in any 4-week window"
    type: weekend_spacing
    groups: [STROKE, NCC_JR, NCC_SR, NH, CCM]
    max_consecutive: 1
    max_in_window: 2
    window_weeks: 4
    strength: hard
    active: true

  - name: Weekend blocked services
    description: "Weekday services that preclude weekend call"
    type: weekend_blocked_services
    groups: [STROKE, NCC_JR, NCC_SR, NH, CCM]
    exact_services: [Vacation, "Elective/NCS 2026"]
    substring_services: [SICU, MSICU]
    strength: hard
    active: true

  - name: Weekend Stroke eligibility
    description: "Which fellow groups and counts for Weekend Stroke role"
    type: weekend_stroke_eligibility
    groups: [STROKE]        # constraint governs STROKE group members
    eligible_groups: [STROKE]   # only STROKE fellows can staff Weekend Stroke
    min_per_fellow: 11
    max_per_fellow: 13
    total: 50
    strength: hard
    active: true
```

### Standing YAML extension:

The `standing/stanford-fellowship-v2.yaml` can also carry `night_rules:` / `weekend_rules:` sections for program-level invariants that rarely change (e.g., blocked service definitions). Standing rules appear read-only in the UI (toggles only), same as existing weekly standing rules.

## Temporal Definitions

- **Weekend**: Saturday and Sunday. A "weekend call" occupies both days. There are 52 weekend slots per year for each of the 3 roles (NCC1, NCC2, Stroke).
- **Night**: Monday through Sunday. Each night is a single-day assignment. There are 365 night slots per year.
- **Spacing**: Night spacing operates on **calendar days** (not business days). A spacing rule of `max_nights: 1, window_days: 3` means if a fellow is assigned to night N, they cannot be assigned to nights N+1 or N+2.

## Rule Type Vocabulary

All rules share `{name, type, groups, strength, active}`. The `groups` field acts as a **filter** — it specifies which fellow groups the constraint governs. For `weekend_stroke_eligibility`, `groups` filters *which groups are governed by this rule*, while `eligible_groups` specifies *which groups may staff the Weekend Stroke role*.

### `night_rules:` types

| Type | Strength | Fields | Purpose |
|------|----------|--------|---------|
| `night_spacing` | hard | `max_nights`, `window_days` | How close together night calls can be |
| `night_blocked_services` | hard | `exact_services`, `substring_services` | Which weekday services block night call |
| `night_holiday_eligibility` | hard | `allowed_services` | Which services permit night call on holidays |
| `night_penalties` | soft | `weights` dict with 5 keys | Weights for undesirable patterns |
| `night_sunday_following` | soft | `preferred_services` | Easy-service preference after Sunday night; weight from `night_penalties` |

### `weekend_rules:` types

| Type | Strength | Fields | Purpose |
|------|----------|--------|---------|
| `weekend_spacing` | hard | `max_consecutive`, `max_in_window`, `window_weeks` | Weekend call frequency limits |
| `weekend_blocked_services` | hard | `exact_services`, `substring_services` | Which weekday services block weekend call |
| `weekend_stroke_eligibility` | hard | `eligible_groups`, `min_per_fellow`, `max_per_fellow`, `total` | Who covers Weekend Stroke |

## Python Solver Changes

### `NightSolverConfig` new fields

| Field | Type | Source | Default |
|-------|------|--------|---------|
| `spacing_max_nights` | int | `night_spacing.max_nights` | 1 |
| `spacing_window_days` | int | `night_spacing.window_days` | 3 |
| `blocking_exact_services` | list[str] | `night_blocked_services.exact_services` | `[Vacation, "NS SCVMC", "AAN", "Elective/NCS 2026"]` |
| `blocking_substring_services` | list[str] | `night_blocked_services.substring_services` | `[SICU, MSICU]` |
| `holiday_allowed_services` | list[str] | `night_holiday_eligibility.allowed_services` | `[NCC1, NCC2, Stroke]` |
| `penalty_weights` | dict[str, int] | `night_penalties.weights` | `{anaesthesia:1, clinic:1, stroke:5, friday_weekend_ncc1:1, sunday_following:1}` |
| `sunday_preferred_services` | list[str] | `night_sunday_following.preferred_services` | current hardcoded list |

**Coupling note**: `night_sunday_following.preferred_services` is only meaningful when `night_penalties` is active AND `night_penalties.weights.sunday_following > 0`. If either condition is false, the preferred-services list is ignored (all services are equally preferred for Sunday following).

### `WeekendSolverConfig` new fields

| Field | Type | Source | Default |
|-------|------|--------|---------|
| `spacing_max_consecutive` | int | `weekend_spacing.max_consecutive` | 1 |
| `spacing_max_in_window` | int | `weekend_spacing.max_in_window` | 2 |
| `spacing_window_weeks` | int | `weekend_spacing.window_weeks` | 4 |
| `blocking_exact_services` | list[str] | `weekend_blocked_services.exact_services` | `[Vacation, "Elective/NCS 2026"]` |
| `blocking_substring_services` | list[str] | `weekend_blocked_services.substring_services` | `[SICU, MSICU]` |
| `stroke_eligible_groups` | list[str] | `weekend_stroke_eligibility.eligible_groups` | derived from `groups` field (all groups the rule governs) |

**Note**: `groups` on `weekend_stroke_eligibility` is a metadata/UI categorization field — it determines which fellow group section this rule appears under in the UI. Only `eligible_groups` drives solver behavior. The default `eligible_groups` when no rule is present is all groups whose `weekend_call` budget has `stroke_total > 0`.
| `stroke_cohort_min` | int | `weekend_stroke_eligibility.min_per_fellow` | 11 |
| `stroke_cohort_max` | int | `weekend_stroke_eligibility.max_per_fellow` | 13 |
| `stroke_cohort_total` | int | `weekend_stroke_eligibility.total` | 50 |

### Bridge changes (`solver_bridge.py`)

Behavior of `active: false`: the rule is silently skipped — no constraint is added, no default is substituted. Fallback to hardcoded defaults only occurs when the entire `night_rules:` / `weekend_rules:` section is absent from the YAML.

`_build_night_config()` and `_build_weekend_config()` will each gain a second pass:
1. First pass: parse `night_call` / `weekend_call` budget sections (unchanged)
2. Second pass: iterate `night_rules` / `weekend_rules` lists, map each rule to config fields
3. Missing sections or rules: fall back to hardcoded defaults (backward compatible)

The existing `DEFAULT_HOLIDAY_DATES`, `DEFAULT_EXACT_NCC_TOTALS`, etc. remain as fallback constants but are overridden by any matching rule.

### Solver changes

The weekend and night solvers will use the new config fields instead of hardcoded constants:
- `call_schedule_common.py`: `WEEKEND_BLOCKING_EXACT_VALUES`, `NIGHT_BLOCKING_EXACT_VALUES`, `is_preferred_sunday_following_service`, `is_night_holiday_eligible` become config-driven
- `weekend_call_solver.py`: `DEFAULT_STROKE_COHORT`, spacing constants become config fields
- `night_call_solver.py`: soft objective weights, holiday allowed services become config fields
- `night_call_solver_policy.py`: `NightPolicyWeights` defaults become config-driven

## React UI Changes

### New sidebar sections

Two new sidebar items under "Annual" section:
- **Weekend Rules** — scrolls to `weekend-rules` section
- **Night Rules** — scrolls to `night-rules` section

Plus the existing **Weekend Call** and **Night Call** budget sections remain for the `weekend_call` / `night_call` tables.

### New palette rule types

`RulePalette` gets 7 new entries (grouped visually):

**Night rules:**
1. `night_spacing` — "Limit how often a fellow can be on night call"
2. `night_blocked_services` — "Set which weekday services block night call"
3. `night_holiday_eligibility` — "Set which services allow night call on holidays"
4. `night_penalties` — "Set soft penalty weights for night call patterns"
5. `night_sunday_following` — "Set preferred services after Sunday night call"

**Weekend rules:**
6. `weekend_spacing` — "Limit how often a fellow can be on weekend call"
7. `weekend_blocked_services` — "Set which weekday services block weekend call"
8. `weekend_stroke_eligibility` — "Set which groups cover Weekend Stroke"

### Type-specific editor fields

`RuleEditor.renderTypeFields()` adds cases for each new type:

- `night_spacing`: max_nights (number input), window_days (number input)
- `night_blocked_services`: exact_services (chip picker using existing shifts list), substring_services (tag input — arbitrary substrings, not limited to known shifts)
- `night_holiday_eligibility`: allowed_services (chip picker)
- `night_penalties`: 5 number inputs (anaesthesia, clinic, stroke, friday_weekend_ncc1, sunday_following)
- `night_sunday_following`: preferred_services (chip picker)
- `weekend_spacing`: max_consecutive, max_in_window, window_weeks (number inputs)
- `weekend_blocked_services`: exact_services (chip picker), substring_services (tag input)
- `weekend_stroke_eligibility`: eligible_groups (checkboxes for fellow groups), min_per_fellow, max_per_fellow, total (number inputs)

### Card summaries

`ruleSummary()` adds readable one-line summaries, e.g.:
- `night_spacing`: "Max 1 night per 3 days"
- `night_blocked_services`: "Blocked by: Vacation, NS SCVMC..."
- `night_penalties`: "Weights: anaesthesia=1, clinic=1, stroke=5"
- `weekend_spacing`: "No consecutive weekends"

### Data flow

No API changes needed — the existing `GET /api/config/annual/{name}/draft` returns the full config with new sections, and `PUT /api/config/annual/{name}/draft` saves the full config back. The `PaletteRule` TypeScript type gains the new `type` values in its `type` union.

## Validation (frontend and backend)

Both the React UI and the Python parser validate rule inputs:

### Bounds checks

| Field | Valid range |
|-------|-------------|
| `max_nights` | 0 or 1 |
| `window_days` | 1–7 |
| `max_consecutive` | 0–4 |
| `max_in_window` | 1–4 |
| `window_weeks` | 2–13 |
| `min_per_fellow` | 0–52 |
| `max_per_fellow` | 0–52 |
| `total` | 0–365 |
| Weights | 0–100 (0 disables the criterion) |

### Type checks
- `exact_services` and `substring_services` must be arrays of non-empty strings
- `preferred_services` and `allowed_services` must be arrays of known shift names (chip picker enforces this)
- `eligible_groups` must be known fellow group names (checkbox enforces this)
- `weights` must be an object with all 5 keys present, each a non-negative integer

### Edge cases
- `active: false` rules are silently skipped by both parser and solver
- Missing rule sections (no `night_rules:` key) fall back to hardcoded defaults
- Empty rule list (`night_rules: []`) adds no constraints — solver uses minimal defaults
- Duplicate rule names: last one wins (parser overwrites), frontend warns on save

## Testing Strategy

Per repo conventions, add focused `pytest` tests before changing solver logic:

### Parser tests (`test_palette_rules.py` or `test_call_rules.py`)
- Parse `night_rules:` and `weekend_rules:` sections into `PaletteRule` objects
- Verify each rule type produces correct `params` dict
- Verify `active: false` rules are present but inactive
- Verify missing sections produce empty lists

### Bridge tests (`test_solver_bridge.py`)
- `_build_night_config()` correctly maps each rule type to config fields
- `_build_weekend_config()` correctly maps each rule type to config fields
- Missing sections produce hardcoded fallback configs
- `active: false` rules are skipped, defaults NOT substituted

### Solver integration tests (existing solver tests)
- Update `test_weekend_call_solver.py` and `test_night_call_solver.py` to pass rule-derived configs
- Verify spacing, blocking, penalty parameters work identically to previous hardcoded values
- Test that changing a rule parameter (e.g., `max_nights: 2`) produces different solver behavior

### Frontend validation tests
- TypeScript type checks: new rule types compile without errors
- `ruleSummary()` renders correct text for each new type
- `RuleEditor.renderTypeFields()` renders correct controls for each new type

## Migration Plan

1. Add `night_rules:` and `weekend_rules:` sections to `my-2025-2026-v2.yaml` with the current hardcoded defaults as explicit rules
2. Add corresponding sections to `stanford-fellowship-v2.yaml` for standing invariants (blocked services, spacing)
3. Update `palette_rules.py` to parse the new sections
4. Add new fields to `NightSolverConfig` / `WeekendSolverConfig`
5. Update `solver_bridge.py` to populate new fields from rules
6. Replace hardcoded constants in solvers with config field references
7. Add new rule types to React frontend (`types.ts`, `RulePalette.tsx`, `RuleEditor.tsx`, `App.tsx`)

## Future Considerations

- **Staged hardening**: The `night_call_solver_policy.py` currently hardcodes 5 staged hardening specs (all-soft → hard-sunday → ... → all-hard). A future enhancement could allow users to configure the hardening order as a rule parameter.
- **Per-group penalties**: Currently soft penalties apply uniformly to all groups. Future work could allow per-group weight overrides.
- **Budget-to-rule migration**: If desired, the `night_call:` and `weekend_call:` budget sections could eventually become rule types (`night_total`, `weekend_total`) with group-level distribution semantics.
