# Call Schedule Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use `- [ ]` syntax for tracking.

**Goal:** Make weekend and night call constraints (spacing, blocking, penalties, eligibility) configurable via YAML rules and editable in the React UI.

**Architecture:** Add `night_rules:` and `weekend_rules:` sections to YAML configs alongside existing `rules:`. Decode them into config fields in `solver_bridge.py` and feed them into the weekend/night solvers. Extend the existing RulePalette/RuleCard/RuleEditor UI components for the 7 new rule types.

**Tech Stack:** Python (Z3 solver), YAML, React/TypeScript

**Spec reference:** `docs/superpowers/specs/2026-06-01-call-schedule-rules-design.md`

---

## File Structure Map

### Python backend (modified files)

| File | Responsibility |
|------|----------------|
| `src/scheduler/call_schedule_common.py` | Parameterize blocking, holiday, sunday-following helpers to accept config |
| `src/scheduler/night_call_solver.py` | `NightSolverConfig` gains new fields; solver reads config instead of hardcoded values |
| `src/scheduler/night_call_solver_policy.py` | Use config-driven penalty weights instead of hardcoded defaults |
| `src/scheduler/weekend_call_solver.py` | `WeekendSolverConfig` gains new fields; solver reads config instead of hardcoded values |
| `src/scheduler/solver_bridge.py` | Populate new config fields from `night_rules:` / `weekend_rules:` sections |
| `src/scheduler/palette_rules.py` | Parse `night_rules:` and `weekend_rules:` YAML sections into `PaletteRule` objects |

### React frontend (modified files)

| File | Responsibility |
|------|----------------|
| `src/web/src/types.ts` | Add new rule type values to `PaletteRule['type']` union |
| `src/web/src/components/RulePalette.tsx` | Add 7 new rule type entries to palette |
| `src/web/src/components/RuleEditor.tsx` | Add type-specific editor fields for each new type |
| `src/web/src/components/RuleCard.tsx` | Add `ruleSummary()` cases for new types |
| `src/web/src/App.tsx` | Add sidebar nav items + render sections for night/weekend rules |

### YAML configs (modified files)

| File | Responsibility |
|------|----------------|
| `config/standing/stanford-fellowship-v2.yaml` | Add `night_rules:` and `weekend_rules:` with program-level defaults |
| `config/annual/my-2025-2026-v2.yaml` | Add `night_rules:` and `weekend_rules:` with annual defaults |

### Tests (new/modified files)

| File | Responsibility |
|------|----------------|
| `tests/test_palette_rules.py` (new) | Test parsing of new rule sections |
| `tests/test_solver_bridge.py` (extend) | Test new config field population |
| `tests/test_night_call_solver.py` (extend) | Test solver with new config fields |
| `tests/test_weekend_call_solver.py` (extend) | Test solver with new config fields |

---

### Task 1: Add new rule types to `night_call_solver.py` — `NightSolverConfig` fields

**Files:**
- Modify: `src/scheduler/night_call_solver.py` (add new fields to dataclass, add defaults)

- [ ] **Step 1: Add new fields to `NightSolverConfig`**

Add these fields to the `NightSolverConfig` dataclass:

```python
@dataclass
class NightSolverConfig:
    # Existing fields...
    
    # New fields (populated from night_rules: section)
    spacing_max_nights: int = 1
    spacing_window_days: int = 3
    blocking_exact_services: tuple[str, ...] = ("Vacation", "NS SCVMC", "AAN", "Elective/NCS 2026")
    blocking_substring_services: tuple[str, ...] = ("SICU", "MSICU")
    holiday_allowed_services: tuple[str, ...] = ("NCC1", "NCC2", "Stroke")
    penalty_weights: dict[str, int] = None  # field(default_factory=lambda: {...})
    sunday_preferred_services: tuple[str, ...] = (
        "Elec", "Telestroke/Clinic", "Clinic/Elective",
        "SCVMC Rehab", "NIR", "ISC", "Vac"
    )
```

Use `field(default_factory=...)` for `penalty_weights` with defaults:
`{"anaesthesia": 1, "clinic": 1, "stroke": 5, "friday_weekend_ncc1": 1, "sunday_following": 1}`

- [ ] **Step 2: Replace hardcoded constants in solver methods**

In `_add_night_objectives`, replace hardcoded weight constants with `config.penalty_weights[...]`.

In the spacing constraint section, replace hardcoded `1` and `3` with `config.spacing_max_nights` and `config.spacing_window_days`.

In the holiday eligibility section, replace hardcoded `{NCC1, NCC2, Stroke}` with `set(config.holiday_allowed_services)`.

In the blocking section, replace `NIGHT_BLOCKING_EXACT_VALUES` with `set(config.blocking_exact_services)` and `NIGHT_BLOCKING_SUBSTRINGS` with `config.blocking_substring_services`.

- [ ] **Step 3: Update `night_call_solver_policy.py` to use config-driven weights**

Replace `NightPolicyWeights` hardcoded defaults with values from `config.penalty_weights`. The policy solver's 5 criteria (anaesthesia, clinic, stroke, friday_weekend_ncc1, sunday_following) map directly to `config.penalty_weights` keys. If a key is missing or weight is 0, that criterion is disabled (no penalty).

- [ ] **Step 4: Update callers for call_schedule_common.py change (see Task 3)**

After Task 3 parameterizes the common helpers (`is_preferred_sunday_following_service`, `is_night_blocked`, etc.), update `night_call_solver.py` and `night_call_solver_policy.py` callers to pass config fields.

- [ ] **Step 5: Update tests**

Extend `test_night_call_solver.py`: add a test that constructs a `NightSolverConfig` with non-default spacing and verifies the solver respects it. Add a test that changes `penalty_weights` and confirms solver behavior shifts (or at minimum, that the config is applied without error).

- [ ] **Step 6: Run existing tests to confirm no regression**

Run: `cd src/scheduler && python -m pytest tests/test_night_call_solver.py -v`

- [ ] **Step 7: Commit**

---

### Task 2: Add new fields to `weekend_call_solver.py` — `WeekendSolverConfig`

**Files:**
- Modify: `src/scheduler/weekend_call_solver.py`

- [ ] **Step 1: Add new fields to `WeekendSolverConfig`**

```python
@dataclass
class WeekendSolverConfig:
    # Existing fields...
    
    # New fields (populated from weekend_rules: section)
    spacing_max_consecutive: int = 1
    spacing_max_in_window: int = 2
    spacing_window_weeks: int = 4
    blocking_exact_services: tuple[str, ...] = ("Vacation", "Elective/NCS 2026")
    blocking_substring_services: tuple[str, ...] = ("SICU", "MSICU")
    stroke_eligible_groups: tuple[str, ...] = ()  # populated from weekend_call budget + weekend_rules in bridge
    stroke_cohort_min: int = 11
    stroke_cohort_max: int = 13
    stroke_cohort_total: int = 50
```

- [ ] **Step 2: Replace hardcoded constants in solver**

Remove `DEFAULT_STROKE_COHORT` tuple (hardcoded names). Replace the stroke cohort constraint logic: instead of checking `config.stroke_cohort` against hardcoded names, derive the cohort from `fellow_groups` filtered by `config.stroke_eligible_groups`.

Replace spacing constants with config fields. Replace `WEEKEND_BLOCKING_EXACT_VALUES` and `WEEKEND_BLOCKING_SUBSTRINGS` references with config fields.

- [ ] **Step 3: Update tests**

Extend `test_weekend_call_solver.py`: verify spacing config is applied, verify stroke eligibility is derived from `stroke_eligible_groups` not hardcoded names.

- [ ] **Step 4: Commit**

---

### Task 3: Parameterize `call_schedule_common.py` helpers

**Files:**
- Modify: `src/scheduler/call_schedule_common.py`

The helpers `is_night_blocked`, `is_weekend_blocked`, `is_night_holiday_eligible`, and `is_preferred_sunday_following_service` currently use module-level hardcoded constants. They need to accept config parameters.

(`is_anaesthesia_service` and `is_clinic_service` are substring checks that remain hardcoded — they aren't in the spec's configurable scope.)

- [ ] **Step 1: Parameterize night blocking helpers**

```python
# Before (module-level constants):
NIGHT_BLOCKING_EXACT_VALUES = {"Vacation", "NS SCVMC", "AAN", "Elective/NCS 2026", ""}
NIGHT_BLOCKING_SUBSTRINGS = ("SICU", "MSICU")

def is_night_blocked(weekday_service: str | None, ...) -> bool:
    if not weekday_service or weekday_service in NIGHT_BLOCKING_EXACT_VALUES:
        return True
    for sub in NIGHT_BLOCKING_SUBSTRINGS:
        if sub in weekday_service:
            return True
    return False

# After (accept config):
def is_night_blocked(weekday_service: str | None, *,
                     exact_services: frozenset[str] | None = None,
                     substring_services: tuple[str, ...] | None = None) -> bool:
    exact = exact_services if exact_services is not else _DEFAULT_NIGHT_EXACT
    sub = substring_services if substring_services is not None else _DEFAULT_NIGHT_SUB
    if not weekday_service or weekday_service in exact:
        return True
    for s in sub:
        if s in weekday_service:
            return True
    return False
```

Keep the module-level `_DEFAULT_NIGHT_EXACT` / `_DEFAULT_NIGHT_SUB` constants (renamed with underscore prefix) as fallbacks when callers don't pass config.

- [ ] **Step 2: Parameterize `is_night_holiday_eligible`**

```python
def is_night_holiday_eligible(weekday_service: str | None, *,
                              allowed_services: frozenset[str] | None = None) -> bool:
    allowed = allowed_services if allowed_services is not None else _DEFAULT_HOLIDAY_ALLOWED
    return weekday_service in allowed
```

- [ ] **Step 3: Parameterize `is_preferred_sunday_following_service`**

```python
def is_preferred_sunday_following_service(service: str, *,
                                          preferred: frozenset[str] | None = None) -> bool:
    if preferred is None:
        preferred = _DEFAULT_SUNDAY_PREFERRED
    return service in preferred
```

- [ ] **Step 4: Parameterize `is_weekend_blocked` and `weekend_roles_for_fellow`**

Same pattern: accept `exact_services` and `substring_services` keyword args, fall back to module-level defaults.

- [ ] **Step 5: Update callers in all solver files**

Update `night_call_solver.py`, `night_call_solver_policy.py`, `weekend_call_solver.py` to pass config fields when calling these helpers. The other solver variants (`night_call_solver_bool_matrix.py`, `night_call_solver_relaxed.py`, `new_approach/`) import these helpers — fixing `call_schedule_common.py` with defaults means they continue to work without changes (they use the module-level default behavior).

- [ ] **Step 6: Update existing tests**

Ensure tests that call `is_night_blocked`, `is_weekend_blocked`, etc. continue to pass. No test changes needed if defaults are preserved.

- [ ] **Step 7: Run tests**

Run: `cd src/scheduler && python -m pytest tests/ -v`
Expected: all existing tests pass.

- [ ] **Step 8: Commit**

### Task 4: Update `solver_bridge.py` to read rules

**Files:**

- [ ] **Step 1: Add helper to extract night rules from request**

Add a helper function (or inline in `_build_night_config`) that reads `request.get("night_rules", [])` and maps each rule to config fields:

```python
def _apply_night_rules(config, night_rules):
    for rule in night_rules:
        if not rule.get("active", True):
            continue
        kind = rule["type"]
        if kind == "night_spacing":
            config.spacing_max_nights = rule.get("max_nights", 1)
            config.spacing_window_days = rule.get("window_days", 3)
        elif kind == "night_blocked_services":
            config.blocking_exact_services = tuple(rule.get("exact_services", []))
            config.blocking_substring_services = tuple(rule.get("substring_services", []))
        elif kind == "night_holiday_eligibility":
            config.holiday_allowed_services = tuple(rule.get("allowed_services", []))
        elif kind == "night_penalties":
            config.penalty_weights = rule.get("weights", config.penalty_weights)
        elif kind == "night_sunday_following":
            config.sunday_preferred_services = tuple(rule.get("preferred_services", []))
    
    # If night_penalties rule exists but is inactive, zero all weights.
    # (A missing rule means "use defaults". An inactive rule means "no penalties".)
    has_active = any(r.get("active", True) and r["type"] == "night_penalties" for r in night_rules)
    has_inactive = any(not r.get("active", True) and r["type"] == "night_penalties" for r in night_rules)
    if has_inactive and not has_active:
        config.penalty_weights = {k: 0 for k in config.penalty_weights}
```

- [ ] **Step 2: Add helper to extract weekend rules**

Similarly for `_apply_weekend_rules`:

```python
def _apply_weekend_rules(config, weekend_rules, fellow_groups, weekend_call_budget):
    # Derive default stroke_eligible_groups from budget groups with stroke_total > 0
    budget_stroke_groups = [
        entry["group"] for entry in weekend_call_budget
        if entry.get("stroke_total", 0) > 0
    ]
    config.stroke_eligible_groups = tuple(budget_stroke_groups)
    
    for rule in weekend_rules:
        if not rule.get("active", True):
            continue
        kind = rule["type"]
        if kind == "weekend_spacing":
            config.spacing_max_consecutive = rule.get("max_consecutive", 1)
            config.spacing_max_in_window = rule.get("max_in_window", 2)
            config.spacing_window_weeks = rule.get("window_weeks", 4)
        elif kind == "weekend_blocked_services":
            config.blocking_exact_services = tuple(rule.get("exact_services", []))
            config.blocking_substring_services = tuple(rule.get("substring_services", []))
        elif kind == "weekend_stroke_eligibility":
            eligible = set(rule.get("eligible_groups", []))
            config.stroke_eligible_groups = tuple(eligible)
            config.stroke_cohort_min = rule.get("min_per_fellow", 11)
            config.stroke_cohort_max = rule.get("max_per_fellow", 13)
            config.stroke_cohort_total = rule.get("total", 50)
```

- [ ] **Step 3: Wire helpers into `_build_night_config` and `_build_weekend_config`**

In `_build_night_config`, after building the base config from `night_call` section, call `_apply_night_rules(config, request.get("night_rules", []))`.

Similarly in `_build_weekend_config` (passing the `weekend_call` budget list as well).

- [ ] **Step 4: Add Python-side validation**

In both `_apply_night_rules` and `_apply_weekend_rules`, add bounds checks after applying all rules:

```python
# In _apply_night_rules:
config.spacing_max_nights = max(0, min(1, config.spacing_max_nights))    # 0 or 1
config.spacing_window_days = max(1, min(7, config.spacing_window_days))  # 1-7

# In _apply_weekend_rules:
config.spacing_max_consecutive = max(0, min(4, config.spacing_max_consecutive))  # 0-4
config.spacing_max_in_window = max(1, min(4, config.spacing_max_in_window))      # 1-4
config.spacing_window_weeks = max(2, min(13, config.spacing_window_weeks))       # 2-13
config.stroke_cohort_min = max(0, min(52, config.stroke_cohort_min))
config.stroke_cohort_max = max(0, min(52, config.stroke_cohort_max))
config.stroke_cohort_total = max(0, min(365, config.stroke_cohort_total))
```

- [ ] **Step 5: Extend bridge tests**

Add tests in `test_solver_bridge.py` that:
- Create a mock request with `night_rules` and verify config fields are set correctly
- Create a mock request with `weekend_rules` and verify config fields are set correctly
- Verify `active: false` rules are skipped
- Verify missing rule sections leave defaults intact

- [ ] **Step 6: Commit**

---

### Task 5: Update `palette_rules.py` to parse new sections

**Files:**
- Modify: `src/scheduler/palette_rules.py`

- [ ] **Step 1: Extend parse function or add new entry point**

The existing `parse_palette_rules()` parses the `rules:` section. Add a new function or extend it with optional `section_key` parameter:

```python
def parse_call_rules(raw_rules: list[dict]) -> list[PaletteRule]:
    """Parse night_rules or weekend_rules sections into PaletteRule objects."""
    result = []
    for entry in raw_rules:
        rule = PaletteRule(
            name=entry.get("name", ""),
            type=entry.get("type", ""),
            groups=entry.get("groups", []),
            strength=entry.get("strength", "hard"),
            active=entry.get("active", True),
            params={k: v for k, v in entry.items() if k not in ("name", "type", "groups", "strength", "active")}
        )
        result.append(rule)
    return result
```

This mirrors how `parse_palette_rules` works — it just reads a different section key.

- [ ] **Step 2: Wire the parse into `solver_bridge.py`'s `build_solver_config_from_request`**

In `solver_bridge.py`, the function `build_solver_config_from_request` already calls `parse_palette_rules(request.get("rules", []))`. Add two parallel calls:

```python
palette_rules = parse_palette_rules(request.get("rules", []))
night_rules = parse_call_rules(request.get("night_rules", []))
weekend_rules = parse_call_rules(request.get("weekend_rules", []))
```

These `PaletteRule` lists flow to the frontend via the `ScheduleSolverConfig` or the draft config JSON response (whichever path the existing weekly rules use). Ensure both night_rules and weekend_rules are included in the JSON sent to the `/api/config/annual/{name}/draft` GET response.

- [ ] **Step 3: Write parser tests**

In `tests/test_palette_rules.py` (new file):

```python
def test_parse_night_spacing_rule():
    raw = [{"name": "Night spacing", "type": "night_spacing", "groups": ["STROKE"],
            "max_nights": 1, "window_days": 3, "strength": "hard", "active": True}]
    rules = parse_call_rules(raw)
    assert len(rules) == 1
    assert rules[0].type == "night_spacing"
    assert rules[0].params["max_nights"] == 1
```

- [ ] **Step 4: Commit**

---

### Task 6: Update YAML configs with new sections

**Files:**
- Modify: `config/standing/stanford-fellowship-v2.yaml`
- Modify: `config/annual/my-2025-2026-v2.yaml`

- [ ] **Step 1: Add `night_rules:` and `weekend_rules:` to standing YAML**

In `config/standing/stanford-fellowship-v2.yaml`, add sections with program-level invariants:

```yaml
night_rules:
  - name: Night spacing
    type: night_spacing
    groups: [STROKE, NCC_JR, NCC_SR, NH]
    max_nights: 1
    window_days: 3
    strength: hard
    active: true

  - name: Night blocked services
    type: night_blocked_services
    groups: [STROKE, NCC_JR, NCC_SR, NH, CCM]
    exact_services: [Vacation, "NS SCVMC", "AAN"]
    substring_services: [SICU, MSICU]
    strength: hard
    active: true

  - name: Holiday eligibility
    type: night_holiday_eligibility
    groups: [STROKE, NCC_JR, NCC_SR, NH]
    allowed_services: [NCC1, NCC2, Stroke]
    strength: hard
    active: true

weekend_rules:
  - name: Weekend spacing
    type: weekend_spacing
    groups: [STROKE, NCC_JR, NCC_SR, NH, CCM]
    max_consecutive: 1
    max_in_window: 2
    window_weeks: 4
    strength: hard
    active: true

  - name: Weekend blocked services
    type: weekend_blocked_services
    groups: [STROKE, NCC_JR, NCC_SR, NH, CCM]
    exact_services: [Vacation]
    substring_services: [SICU, MSICU]
    strength: hard
    active: true
```

- [ ] **Step 2: Add `night_rules:` and `weekend_rules:` to annual YAML**

In `config/annual/my-2025-2026-v2.yaml`, add sections with year-specific values:

```yaml
night_rules:
  - name: Night spacing
    type: night_spacing
    groups: [STROKE, NCC_JR, NCC_SR, NH]
    max_nights: 1
    window_days: 3
    strength: hard
    active: true

  - name: Night blocked services
    type: night_blocked_services
    groups: [STROKE, NCC_JR, NCC_SR, NH, CCM]
    exact_services: [Vacation, "NS SCVMC", "AAN", "Elective/NCS 2026"]
    substring_services: [SICU, MSICU]
    strength: hard
    active: true

  - name: Holiday eligibility
    type: night_holiday_eligibility
    groups: [STROKE, NCC_JR, NCC_SR, NH]
    allowed_services: [NCC1, NCC2, Stroke]
    strength: hard
    active: true

  - name: Night soft penalties
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

weekend_rules:
  - name: Weekend spacing
    type: weekend_spacing
    groups: [STROKE, NCC_JR, NCC_SR, NH, CCM]
    max_consecutive: 1
    max_in_window: 2
    window_weeks: 4
    strength: hard
    active: true

  - name: Weekend blocked services
    type: weekend_blocked_services
    groups: [STROKE, NCC_JR, NCC_SR, NH, CCM]
    exact_services: [Vacation, "Elective/NCS 2026"]
    substring_services: [SICU, MSICU]
    strength: hard
    active: true

  - name: Weekend Stroke eligibility
    type: weekend_stroke_eligibility
    groups: [STROKE]
    eligible_groups: [STROKE]
    min_per_fellow: 11
    max_per_fellow: 13
    total: 50
    strength: hard
    active: true
```

Season the `night_call:` and `weekend_call:` budget sections remain unchanged.

- [ ] **Step 3: Validate YAML parses correctly**

Run: `cd src/scheduler && python -c "import yaml; yaml.safe_load(open('../../config/annual/my-2025-2026-v2.yaml'))"`

- [ ] **Step 4: Commit**

---

### Task 7: Add new rule types to React TypeScript types

**Files:**
- Modify: `src/web/src/types.ts`

- [ ] **Step 1: Add new type values to `PaletteRule` union**

Find the `type` field in the `PaletteRule` interface (or wherever rule types are defined as a union) and add:

```typescript
export type PaletteRuleType =
  // Existing types...
  | 'night_spacing'
  | 'night_blocked_services'
  | 'night_holiday_eligibility'
  | 'night_penalties'
  | 'night_sunday_following'
  | 'weekend_spacing'
  | 'weekend_blocked_services'
  | 'weekend_stroke_eligibility';
```

- [ ] **Step 2: Add interfaces for new rule params (optional but recommended)**

```typescript
export interface NightSpacingParams {
  max_nights?: number;
  window_days?: number;
}

export interface NightBlockedServicesParams {
  exact_services?: string[];
  substring_services?: string[];
}

export interface NightHolidayEligibilityParams {
  allowed_services?: string[];
}

export interface NightPenaltiesParams {
  weights?: {
    anaesthesia: number;
    clinic: number;
    stroke: number;
    friday_weekend_ncc1: number;
    sunday_following: number;
  };
}

export interface NightSundayFollowingParams {
  preferred_services?: string[];
}

export interface WeekendSpacingParams {
  max_consecutive?: number;
  max_in_window?: number;
  window_weeks?: number;
}

export interface WeekendBlockedServicesParams {
  exact_services?: string[];
  substring_services?: string[];
}

export interface WeekendStrokeEligibilityParams {
  eligible_groups?: string[];
  min_per_fellow?: number;
  max_per_fellow?: number;
  total?: number;
}
```

- [ ] **Step 3: Commit**

---

### Task 8: Update `RulePalette.tsx` with new rule types

**Files:**
- Modify: `src/web/src/components/RulePalette.tsx`

- [ ] **Step 1: Add new rule type entries to the palette**

In the rules list (where the 8 existing types are defined), add entries for the new types:

```typescript
// Night rules
{ type: 'night_spacing', label: 'Night spacing', description: 'Limit how often a fellow can be on night call' },
{ type: 'night_blocked_services', label: 'Night blocked services', description: 'Set which weekday services block night call' },
{ type: 'night_holiday_eligibility', label: 'Holiday eligibility', description: 'Set which services allow night call on holidays' },
{ type: 'night_penalties', label: 'Night soft penalties', description: 'Set soft penalty weights for night call patterns' },
{ type: 'night_sunday_following', label: 'Sunday following preference', description: 'Set preferred services after Sunday night call' },

// Weekend rules
{ type: 'weekend_spacing', label: 'Weekend spacing', description: 'Limit how often a fellow can be on weekend call' },
{ type: 'weekend_blocked_services', label: 'Weekend blocked services', description: 'Set which weekday services block weekend call' },
{ type: 'weekend_stroke_eligibility', label: 'Weekend Stroke eligibility', description: 'Set which groups cover Weekend Stroke' },
```

- [ ] **Step 2: Optionally add category grouping**

Add visual headers to separate "Night Rules" from "Weekend Rules" in the palette menu.

- [ ] **Step 3: Run TypeScript compilation check**

Run: `cd src/web && npx tsc --noEmit`

- [ ] **Step 4: Commit**

---

### Task 9: Update `RuleEditor.tsx` with type-specific editor fields

**Files:**
- Modify: `src/web/src/components/RuleEditor.tsx`

- [ ] **Step 1: Add render cases in `renderTypeFields`**

In the `renderTypeFields` function (which switches on `rule.type`), add cases for each new type:

```typescript
case 'night_spacing':
  return (
    <>
      <label>Max nights in window</label>
      <input type="number" min={0} max={1} value={rule.params.max_nights ?? 1}
             onChange={e => updateParam('max_nights', parseInt(e.target.value))} />
      <label>Window (days)</label>
      <input type="number" min={1} max={7} value={rule.params.window_days ?? 3}
             onChange={e => updateParam('window_days', parseInt(e.target.value))} />
    </>
  );

case 'night_blocked_services':
  return (
    <>
      <label>Exact service matches (blocked)</label>
      <ShiftPicker selected={rule.params.exact_services ?? []}
                   onChange={v => updateParam('exact_services', v)}
                   shifts={availableShifts} />
      <label>Substring matches (blocked)</label>
      <TagInput value={rule.params.substring_services ?? []}
                onChange={v => updateParam('substring_services', v)} />
    </>
  );

case 'night_holiday_eligibility':
  return (
    <label>Allowed services on holidays</label>
    <ShiftPicker selected={rule.params.allowed_services ?? []}
                 onChange={v => updateParam('allowed_services', v)}
                 shifts={availableShifts} />
  );

case 'night_penalties':
  return (
    <>
      <label>Anaesthesia weight</label>
      <input type="number" min={0} max={100} value={rule.params.weights?.anaesthesia ?? 1}
             onChange={e => updateParam('weights', {...rule.params.weights, anaesthesia: parseInt(e.target.value)})} />
      <label>Clinic weight</label>
      <input type="number" min={0} max={100} value={rule.params.weights?.clinic ?? 1}
             onChange={e => updateParam('weights', {...rule.params.weights, clinic: parseInt(e.target.value)})} />
      <label>Stroke weight</label>
      <input type="number" min={0} max={100} value={rule.params.weights?.stroke ?? 5}
             onChange={e => updateParam('weights', {...rule.params.weights, stroke: parseInt(e.target.value)})} />
      <label>Friday/weekend NCC1 conflict weight</label>
      <input type="number" min={0} max={100} value={rule.params.weights?.friday_weekend_ncc1 ?? 1}
             onChange={e => updateParam('weights', {...rule.params.weights, friday_weekend_ncc1: parseInt(e.target.value)})} />
      <label>Sunday following conflict weight</label>
      <input type="number" min={0} max={100} value={rule.params.weights?.sunday_following ?? 1}
             onChange={e => updateParam('weights', {...rule.params.weights, sunday_following: parseInt(e.target.value)})} />
    </>
  );

case 'night_sunday_following':
  return (
    <label>Preferred services after Sunday night</label>
    <ShiftPicker selected={rule.params.preferred_services ?? []}
                 onChange={v => updateParam('preferred_services', v)}
                 shifts={availableShifts} />
  );

case 'weekend_spacing':
  return (
    <>
      <label>Max consecutive weekends</label>
      <input type="number" min={0} max={4} value={rule.params.max_consecutive ?? 1}
             onChange={e => updateParam('max_consecutive', parseInt(e.target.value))} />
      <label>Max weekends in window</label>
      <input type="number" min={1} max={4} value={rule.params.max_in_window ?? 2}
             onChange={e => updateParam('max_in_window', parseInt(e.target.value))} />
      <label>Window (weeks)</label>
      <input type="number" min={2} max={13} value={rule.params.window_weeks ?? 4}
             onChange={e => updateParam('window_weeks', parseInt(e.target.value))} />
    </>
  );

case 'weekend_blocked_services':
  return (
    <>
      <label>Exact service matches (blocked)</label>
      <ShiftPicker selected={rule.params.exact_services ?? []}
                   onChange={v => updateParam('exact_services', v)}
                   shifts={availableShifts} />
      <label>Substring matches (blocked)</label>
      <TagInput value={rule.params.substring_services ?? []}
                onChange={v => updateParam('substring_services', v)} />
    </>
  );

case 'weekend_stroke_eligibility':
  return (
    <>
      <label>Eligible groups</label>
      <GroupPicker selected={rule.params.eligible_groups ?? []}
                   onChange={v => updateParam('eligible_groups', v)}
                   groups={allGroups} />
      <label>Min per fellow</label>
      <input type="number" min={0} max={52} value={rule.params.min_per_fellow ?? 11}
             onChange={e => updateParam('min_per_fellow', parseInt(e.target.value))} />
      <label>Max per fellow</label>
      <input type="number" min={0} max={52} value={rule.params.max_per_fellow ?? 13}
             onChange={e => updateParam('max_per_fellow', parseInt(e.target.value))} />
      <label>Total cohort assignments</label>
      <input type="number" min={0} max={365} value={rule.params.total ?? 50}
             onChange={e => updateParam('total', parseInt(e.target.value))} />
    </>
  );
```

- [ ] **Step 2: Run TypeScript check**

Run: `cd src/web && npx tsc --noEmit`

- [ ] **Step 3: Commit**

---

### Task 10: Update `RuleCard.tsx` summaries for new types

**Files:**
- Modify: `src/web/src/components/RuleCard.tsx` (or wherever `ruleSummary()` is defined)

- [ ] **Step 1: Add summary cases**

In `ruleSummary()`, add cases:

```typescript
case 'night_spacing':
  return `Max ${p.max_nights ?? 1} night(s) per ${p.window_days ?? 3} days`;
case 'night_blocked_services':
  return `Blocked by: ${(p.exact_services ?? []).join(', ')}`;
case 'night_holiday_eligibility':
  return `Holidays: only ${(p.allowed_services ?? []).join(', ')}`;
case 'night_penalties':
  return `Weights: anaesthesia=${p.weights?.anaesthesia ?? 1}, clinic=${p.weights?.clinic ?? 1}, stroke=${p.weights?.stroke ?? 5}`;
case 'night_sunday_following':
  return `Preferred after Sunday night: ${(p.preferred_services ?? []).slice(0, 3).join(', ')}...`;
case 'weekend_spacing':
  return `Max ${p.max_consecutive ?? 1} consecutive, ${p.max_in_window ?? 2} in ${p.window_weeks ?? 4} weeks`;
case 'weekend_blocked_services':
  return `Blocked by: ${(p.exact_services ?? []).join(', ')}`;
case 'weekend_stroke_eligibility':
  return `Eligible: ${(p.eligible_groups ?? []).join(', ')}, ${p.min_per_fellow ?? 11}-${p.max_per_fellow ?? 13} each`;
```

- [ ] **Step 2: Commit**

---

### Task 11: Update `App.tsx` with sidebar + rule sections

**Files:**
- Modify: `src/web/src/App.tsx`

- [ ] **Step 1: Add sidebar nav items**

In the sidebar (alongside the existing "Program" and group-specific sections), add:

```typescript
{ id: 'night-rules', label: 'Night Rules', icon: '🌙' },
{ id: 'weekend-rules', label: 'Weekend Rules', icon: '📅' },
```

Position them under the "Annual" section heading alongside existing call scheduling entries.

- [ ] **Step 2: Add rule list rendering in main content**

In `renderMainContent`, add conditions for the new sections:

```typescript
if (activeSection === 'night-rules') {
  const nightRules = paletteRules.filter(r => isNightRuleType(r.type));
  return <RuleSection rules={nightRules} group="night" onAdd={...} onEdit={...} />;
}

if (activeSection === 'weekend-rules') {
  const weekendRules = paletteRules.filter(r => isWeekendRuleType(r.type));
  return <RuleSection rules={weekendRules} group="weekend" onAdd={...} onEdit={...} />;
}
```

Where `isNightRuleType` and `isWeekendRuleType` are helper functions that check the rule type prefix or match against a set.

- [ ] **Step 3: Wire up createDefaultRule for new types**

Add cases in `createDefaultRule` (or equivalent) to produce sensible defaults for the new types.

- [ ] **Step 4: Run TypeScript check and build**

Run: `cd src/web && npx tsc --noEmit && npm run build`

- [ ] **Step 5: Commit**

---

### Task 12: Integration testing

- [ ] **Step 1: Run all existing tests to confirm no regression**

```bash
cd src/scheduler && python -m pytest tests/ -v
```

- [ ] **Step 2: Run new parser tests**

```bash
cd src/scheduler && python -m pytest tests/test_palette_rules.py -v
```

- [ ] **Step 3: Run new bridge tests**

```bash
cd src/scheduler && python -m pytest tests/test_solver_bridge.py -v
```

- [ ] **Step 4: Verify frontend builds**

```bash
cd src/web && npm run build
```

- [ ] **Step 5: Manual end-to-end smoke test**

Start the Flask server (`cd src/scheduler && streamlit run app.py` or `python web_app.py`), verify that:
- Night Rules and Weekend Rules sections appear in the UI
- Rules can be added, edited, and toggled
- Budget tables (Night Call, Weekend Call) remain functional
- The night/weekend solvers still produce valid schedules
