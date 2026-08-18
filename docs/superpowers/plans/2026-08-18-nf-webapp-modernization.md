# NF Webapp Modernization (Rules-Editing + solver_options-as-rules) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modernize the webapp so the 4-group NF config edits and solves in-app: reconcile the retired `call_rules` channel, expose the whole-week forbid-coverage rule, round-trip `solver_options` + `nf_parameters` as editable rule-like cards, add the missing palette types, and make the sidebar group-dynamic.

**Architecture:** The Flask backend (`src/scheduler/web_app.py`) already routes the request `rules:` list through the typed palette pipeline and (from the backend plan) accepts `nf_parameters`/`solver_options`. The React/TS frontend (`src/web/`) is where nearly all changes land: it currently emits a separate `call_rules` array the backend rejects, has no windowed-forbid affordance, and doesn't model `solver_options`/`nf_parameters`. Fixes route call-tier rules into `rules:`, add editor affordances, and let `...config` carry `solver_options`/`nf_parameters` through the existing draft/solve bodies.

**Tech Stack:** React 19 + TypeScript 4.9 (Create React App), Flask, pytest (backend), `tsc --noEmit` + react-scripts/jest (frontend). Python via `.venv`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-17-nf-forbid-coverage-derived-params-webapp-design.md`. Depends on the backend plan (`2026-08-18-nf-derived-params-forbid-backend.md`) already merged (the resolver + `nf_parameters` acceptance in `build_solver_config_from_request`).
- Branch: `partial-import`.
- The `call_rules` request field is retired; call-tier rule types (`per_fellow_shift_total`, `specific_night_assignment`, `blocked_night`, `specific_weekend_assignment`, `blocked_weekend`, `friday_call_assignment`, `group_night_requirement`, `weekend_stroke_prerequisite`, `weekend_ncc_prerequisite`, `dual_stroke_window`) must travel inside `rules:` (backend `_migrated_call_rule_to_constraint` handles them there).
- Groups are dynamic everywhere (no hardcoded group names in logic or types).
- Forbid-coverage rule = `staffing_per_week`, `relation: at_most`, `count: 0`, `window: [A,B]`, `shifts: ['NCC']` for NF configs, default `strength: hard`.
- solver_options are edited as rule-like cards in the same paradigm as other rules (per user: "these do reflect rules").
- Frontend type-check gate: `cd src/web && npx tsc --noEmit` must pass after every frontend task.
- Backend test gate: `PYTHONPATH=src:tests .venv/bin/python -m pytest <file> -q`.
- Per project rule: after backend/web changes, verify Flask starts (`python -m scheduler.web_app` boots without traceback); a CORS error in-app means the server crashed.
- End commits with the branch's Co-Authored-By trailer.

---

### Task 1: Backend guard — call-tier rules route through `rules:`, not `call_rules:`

**Files:**
- Test: `tests/test_call_rules_routing.py`

**Interfaces:**
- Consumes: `scheduler.solver_bridge.build_solver_config_from_request` (existing).
- Produces: a locked contract the frontend relies on — a `blocked_weekend`/etc. entry inside `rules:` builds without error; the same entry inside `call_rules:` raises.

- [ ] **Step 1: Write the test**

```python
# tests/test_call_rules_routing.py
from pathlib import Path
import pytest
from scheduler.solver_bridge import build_solver_config_from_request

_STANDING = Path("config/standing/ncc-nf-model.yaml")

def _req(**extra):
    r = {"fellow_groups": {"NCC_JR": ["JR1", "JR2", "JR3"], "CCM": ["C1"]},
         "shifts": ["NCC", "MICU", "Elec", "Vac"], "num_weeks": 8,
         "solver_options": {"call_tier_day_granular": True}, "rules": []}
    r.update(extra); return r

def test_blocked_weekend_in_rules_list_is_accepted():
    rule = {"type": "blocked_weekend", "name": "blk", "fellow": "JR1",
            "weeks": [3, 4], "active": True}
    cfg = build_solver_config_from_request(_req(rules=[rule]), standing_path=_STANDING)
    assert cfg is not None  # routed via _migrated_call_rule_to_constraint, no raise

def test_active_call_rules_channel_still_rejected():
    rule = {"type": "blocked_weekend", "name": "blk", "fellow": "JR1",
            "weeks": [3, 4], "active": True}
    with pytest.raises(ValueError, match="call_rules"):
        build_solver_config_from_request(_req(call_rules=[rule]), standing_path=_STANDING)
```

- [ ] **Step 2: Run it**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_call_rules_routing.py -q`
Expected: both PASS. If `test_blocked_weekend_in_rules_list_is_accepted` FAILS, the call-tier type is not actually handled inside `rules:` — inspect `_migrated_call_rule_to_constraint` (`solver_bridge.py:447`) and `_build_palette_constraints` (`~:333`); report BLOCKED with the error rather than changing production code speculatively (the frontend reconciliation depends on this contract).

- [ ] **Step 3: Commit**

```bash
git add tests/test_call_rules_routing.py
git commit -m "test(web): lock contract — call-tier rules route via rules: list

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Frontend — reconcile the retired `call_rules` channel

**Files:**
- Modify: `src/web/src/App.tsx` (saveDraft body `~:578-593`; buildRequest `~:675-689`)

**Interfaces:**
- Consumes: existing `paletteRules`, `callRules` state; the Task 1 backend contract.
- Produces: request/draft bodies that carry call-tier rules inside `rules:` and never send `call_rules`.

- [ ] **Step 1: Edit saveDraft** — replace the `rules`/`call_rules` lines in the `body` object (`~:580-583`):

```typescript
      rules: [...paletteRules, ...callRules],
      night_rules: nightRules,
      weekend_rules: weekendRules,
```
(delete the `call_rules: callRules,` line entirely.)

- [ ] **Step 2: Edit buildRequest** — remove `call_rules: callRules,` from the `req` object (`~:681`) and fold call rules into `req.rules` in BOTH branches (`~:684-689`):

```typescript
    if (paletteRules.length > 0) {
      req.rules = [...paletteRules, ...callRules];
      req.standing_rules = standingRules.filter((r) => r.active);
    } else {
      req.rules = [...callRules];
      req.standing_rules = standingRules.filter((r) => r.active);
    }
```

- [ ] **Step 3: Type-check**

Run: `cd src/web && npx tsc --noEmit`
Expected: no errors. (`PaletteRule[]` and `CallRule[]` differ; if tsc complains about the spread into `rules`, type `req.rules` as `any[]` at its assignment or cast `callRules as any[]` — the backend consumes plain dicts. Prefer `req.rules = [...paletteRules, ...(callRules as any[])];`.)

- [ ] **Step 4: Commit**

```bash
cd /cv/scratch/u/watkina6/scheduler
git add src/web/src/App.tsx
git commit -m "fix(web): route call rules through rules: (drop retired call_rules field)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Frontend — dynamic SidebarSection type

**Files:**
- Modify: `src/web/src/components/Sidebar.tsx:3-7`

- [ ] **Step 1: Replace the union** with a template-literal group form so any group renders type-safely:

```typescript
export type SidebarSection =
  | 'fellows' | 'shifts'
  | `rules-${string}`
  | 'rules-program'
  | 'night' | 'weekend' | 'vacations' | 'holidays'
  | 'night-rules' | 'weekend-rules' | 'call-rules';
```

- [ ] **Step 2: Type-check**

Run: `cd src/web && npx tsc --noEmit`
Expected: no errors. The existing `as SidebarSection` cast at the dynamic `rules-${g}` item (`Sidebar.tsx:36`) is now redundant but harmless; leave it or remove it — either type-checks.

- [ ] **Step 3: Commit**

```bash
cd /cv/scratch/u/watkina6/scheduler
git add src/web/src/components/Sidebar.tsx
git commit -m "refactor(web): make SidebarSection group-dynamic (rules-\${string})

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Frontend — forbid-coverage affordance (palette entry + window inputs)

**Files:**
- Modify: `src/web/src/components/RulePalette.tsx` (add a synthetic "No coverage" option + default)
- Modify: `src/web/src/components/RuleEditor.tsx:88-101` (render window inputs on demand)

**Interfaces:**
- Produces: a one-click "No coverage (group × weeks)" rule = `staffing_per_week` / `at_most` / `count 0` / seeded `window` / `shifts:['NCC']`, editable (incl. window) in RuleEditor.

- [ ] **Step 1: RulePalette — add a synthetic palette option.** At the top of `RulePalette.tsx`, add a synthetic id and include it in the weekly list rendering. Add to the `WEEKLY_TYPES` render a special button (simplest: extend `createDefaultRule` with a synthetic case keyed off a string the palette passes). Concretely, add this helper and a dedicated button:

In `createDefaultRule`, add a branch BEFORE the `switch` returns — but since `type` is a `PaletteRuleType`, instead add a separate exported factory:

```typescript
export function createForbidCoverageRule(group: string): PaletteRule {
  return {
    name: `${group}: No coverage (weeks)`,
    type: 'staffing_per_week', groups: [group], strength: 'hard', active: true,
    shifts: ['NCC'], relation: 'at_most', count: 0, window: [0, 1],
  };
}
```

Then in the `RulePalette` component's weekly render, add one extra button after the `types.map(...)` block (only for the weekly category):

```tsx
        {(!category || category === 'weekly') && (
          <button key="forbid-coverage" className="palette-option"
            onClick={() => onAdd(createForbidCoverageRule(group))}>
            <span className="palette-option-name">No Coverage (weeks)</span>
            <span className="palette-option-desc">Forbid a group from a shift in a week range</span>
          </button>
        )}
```

- [ ] **Step 2: RuleEditor — always allow a window for shift_total/staffing_per_week.** Replace the conditional window block (`RuleEditor.tsx:88-101`) so the user can add/edit a window even when none exists yet:

```tsx
          <WindowField
            window={'window' in rule ? rule.window : undefined}
            onChange={window => update({ window })} />
```

And add this component at the bottom of the file:

```tsx
function WindowField({ window, onChange }: {
  window?: [number, number]; onChange: (w: [number, number] | undefined) => void;
}) {
  const enabled = !!window;
  return (
    <div className="editor-field">
      <label className="chip-option">
        <input type="checkbox" checked={enabled}
          onChange={e => onChange(e.target.checked ? [0, 1] : undefined)} />
        <span>Restrict to week window</span>
      </label>
      {enabled && (
        <div className="editor-row">
          <label className="editor-field"><span>Window Start</span>
            <input type="number" min={0} value={window![0]}
              onChange={e => onChange([Number(e.target.value), window![1]])} /></label>
          <label className="editor-field"><span>Window End</span>
            <input type="number" min={0} value={window![1]}
              onChange={e => onChange([window![0], Number(e.target.value)])} /></label>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Type-check**

Run: `cd src/web && npx tsc --noEmit`
Expected: no errors. (`WindowField`'s `onChange` yields `[number,number] | undefined`; `update({ window })` accepts it since `window?` is optional on both rule types.)

- [ ] **Step 4: Manual smoke of the rule shape (jest)** — add `src/web/src/components/RulePalette.test.tsx`:

```tsx
import { createForbidCoverageRule } from './RulePalette';

test('forbid-coverage rule is a windowed at_most-0 staffing rule', () => {
  const r: any = createForbidCoverageRule('CCM');
  expect(r.type).toBe('staffing_per_week');
  expect(r.relation).toBe('at_most');
  expect(r.count).toBe(0);
  expect(r.shifts).toEqual(['NCC']);
  expect(r.window).toEqual([0, 1]);
  expect(r.groups).toEqual(['CCM']);
});
```

Run: `cd src/web && CI=true npx react-scripts test --watchAll=false RulePalette.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /cv/scratch/u/watkina6/scheduler
git add src/web/src/components/RulePalette.tsx src/web/src/components/RuleEditor.tsx src/web/src/components/RulePalette.test.tsx
git commit -m "feat(web): forbid-coverage palette entry + editable week window

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Frontend — add the missing palette rule types

**Files:**
- Modify: `src/web/src/types.ts` (`PaletteRuleType`, new interfaces, `PaletteRule` union, labels/descriptions, `BlockRotationRule`)
- Modify: `src/web/src/components/RulePalette.tsx` (`WEEKLY_TYPES` + defaults)
- Modify: `src/web/src/components/RuleEditor.tsx` (`renderTypeFields` branches)

- [ ] **Step 1: types.ts — extend the union and add fields.** Add `'no_isolated_week' | 'group_count_balance'` to `PaletteRuleType` (`:3-20`). Extend `BlockRotationRule` (`:61-65`):

```typescript
export interface BlockRotationRule extends PaletteRuleBase {
  type: 'block_rotation';
  shifts: string[];
  block_size: number;
  block_offset?: number;
  nf_days_per_block?: [number, number];
}
```

Add interfaces and union members:

```typescript
export interface NoIsolatedWeekRule extends PaletteRuleBase {
  type: 'no_isolated_week';
  shifts: string[];
}
export interface GroupCountBalanceRule extends PaletteRuleBase {
  type: 'group_count_balance';
  shifts: string[];
  max_difference: number;
  window?: [number, number];
}
```

Add both to the `PaletteRule` union (`:183-200`), and add entries to `PALETTE_TYPE_LABELS` + `PALETTE_TYPE_DESCRIPTIONS`:

```typescript
  no_isolated_week: 'No Isolated Week',
  group_count_balance: 'Group Count Balance',
```
```typescript
  no_isolated_week: 'Forbid lone 1-week stints of a shift',
  group_count_balance: 'Balance a shift count across fellows',
```

- [ ] **Step 2: RulePalette — register defaults.** Add both to `WEEKLY_TYPES` (`:4-7`) and to `createDefaultRule`:

```typescript
    case 'no_isolated_week':
      return { ...base, type, shifts: [] };
    case 'group_count_balance':
      return { ...base, type, shifts: [], max_difference: 4 };
```

- [ ] **Step 3: RuleEditor — add render branches** in `renderTypeFields`:

```tsx
    case 'no_isolated_week':
      return (
        <ShiftsField shifts={rule.shifts} allShifts={allShifts}
          onChange={shifts => update({ shifts })} />
      );
    case 'group_count_balance':
      return (
        <>
          <ShiftsField shifts={rule.shifts} allShifts={allShifts}
            onChange={shifts => update({ shifts })} />
          <label className="editor-field"><span>Max difference</span>
            <input type="number" min={0} value={rule.max_difference}
              onChange={e => update({ max_difference: Number(e.target.value) })} /></label>
        </>
      );
```

Also extend the `block_rotation` branch (`RuleEditor.tsx:118-129`) with optional `block_offset` and `nf_days_per_block` number inputs (follow the same `editor-field`/`input type="number"` pattern; `nf_days_per_block` is a `[lo,hi]` pair rendered like the window row).

- [ ] **Step 4: Type-check + existing test**

Run: `cd src/web && npx tsc --noEmit`
Expected: no errors (the `PaletteRule` union is exhaustively switched in `renderTypeFields` and `createDefaultRule` — adding cases keeps them exhaustive).

- [ ] **Step 5: Commit**

```bash
cd /cv/scratch/u/watkina6/scheduler
git add src/web/src/types.ts src/web/src/components/RulePalette.tsx src/web/src/components/RuleEditor.tsx
git commit -m "feat(web): model no_isolated_week, group_count_balance, block_rotation offset

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: solver_options + nf_parameters round-trip + editor cards

**Files:**
- Modify: `src/web/src/types.ts` (`AnnualConfig`)
- Modify: `src/web/src/App.tsx` (`emptyConfig` default; a new `activeSection === 'solver-options'` section; Sidebar item)
- Modify: `src/web/src/components/Sidebar.tsx` (add a "Solver Options" nav item)
- Create: `src/web/src/components/SolverOptionsEditor.tsx`
- Test: `tests/test_web_solver_options_roundtrip.py`

**Interfaces:**
- Consumes: backend `build_solver_config_from_request` (accepts `solver_options` + `nf_parameters`, from the backend plan).
- Produces: `solver_options`/`nf_parameters` carried on `AnnualConfig` and thus through `...config` in every draft/solve body; a card-based editor for them.

- [ ] **Step 1: types.ts — extend AnnualConfig** (`:209-219`):

```typescript
export interface NfParameters {
  [group: string]: {
    ncc_weeks?: [number, number];
    density?: [number, number];
    nf_fraction?: number;
    nf_tolerance?: number;
  };
}
export interface AnnualConfig {
  fellow_groups: Record<string, string[]>;
  shifts: string[];
  num_weeks: number;
  horizon_start?: string;
  rules: PaletteRule[];
  night_call: NightCallEntry[];
  weekend_call: WeekendCallEntry[];
  holiday_dates: string[];
  fellow_week_pairs: Record<string, string[]>;
  solver_options?: Record<string, any>;
  nf_parameters?: NfParameters;
}
```

- [ ] **Step 2: App.tsx — default + section.** Ensure `emptyConfig` includes `solver_options: {}` and `nf_parameters: {}` (search for the `emptyConfig` literal and add the two keys). `...annual` at load (`:247`) then carries any real values; `...config` in the bodies (`:579`, `:676`) already sends them back — no body change needed.

Add a render section (near the other `activeSection ===` blocks, e.g. after `call-rules`):

```tsx
        {activeSection === 'solver-options' && (
          <SolverOptionsEditor
            solverOptions={config.solver_options || {}}
            nfParameters={config.nf_parameters || {}}
            groups={Object.keys(config.fellow_groups)}
            onChange={(solver_options, nf_parameters) =>
              setConfig(prev => ({ ...prev, solver_options, nf_parameters }))}
          />
        )}
```

Import it at the top: `import { SolverOptionsEditor } from './components/SolverOptionsEditor';`

- [ ] **Step 3: Sidebar.tsx — nav item.** Add `'solver-options'` to the `SidebarSection` union and an item in the Annual section:

```tsx
        <SidebarItem id="solver-options" label="Solver Options" active={active} onNavigate={onNavigate} />
```

- [ ] **Step 4: Create SolverOptionsEditor.tsx** — card-based editor: one card per known scalar dial (typed input), and one card per group in `nf_parameters` showing inputs + a live-derived band preview.

```tsx
import React from 'react';
import { NfParameters } from '../types';

// Known scalar dials (label, key, kind). Extend as needed.
const SCALAR_DIALS: { key: string; label: string; kind: 'bool' | 'int' | 'text' }[] = [
  { key: 'call_tier_day_granular', label: 'Call tier (day-granular)', kind: 'bool' },
  { key: 'nf_week_off_cap', label: 'Week off cap', kind: 'int' },
  { key: 'nf_one_third_nf_weight', label: '1/3 balance weight', kind: 'int' },
  { key: 'nf_ncc1_continuity', label: 'NCC1 continuity', kind: 'text' },
  { key: 'nf_max_consecutive_call_days', label: 'Max consecutive call days', kind: 'int' },
];

function deriveNfBand(p: { ncc_weeks?: [number, number]; density?: [number, number]; nf_fraction?: number; nf_tolerance?: number }): string {
  if (!p.ncc_weeks || !p.density) return '(needs weeks + density)';
  const frac = p.nf_fraction ?? 1 / 3;
  const tol = p.nf_tolerance ?? 0;
  const sLo = Math.ceil(p.ncc_weeks[0] * p.density[0]);
  const sHi = Math.floor(p.ncc_weeks[1] * p.density[1]);
  const nLo = Math.max(0, Math.round(sLo * frac) - tol);
  const nHi = Math.round(sHi * frac) + tol;
  return `service [${sLo}, ${sHi}] → NF [${nLo}, ${nHi}]`;
}

type Props = {
  solverOptions: Record<string, any>;
  nfParameters: NfParameters;
  groups: string[];
  onChange: (solverOptions: Record<string, any>, nfParameters: NfParameters) => void;
};

export function SolverOptionsEditor({ solverOptions, nfParameters, groups, onChange }: Props) {
  const setDial = (k: string, v: any) => onChange({ ...solverOptions, [k]: v }, nfParameters);
  const setParam = (g: string, patch: any) =>
    onChange(solverOptions, { ...nfParameters, [g]: { ...nfParameters[g], ...patch } });
  return (
    <div className="rules-section">
      <h2>Solver Options</h2>
      <div className="rule-list">
        {SCALAR_DIALS.map(d => (
          <div key={d.key} className="rule-card">
            <span className="rule-card-title">{d.label}</span>
            {d.kind === 'bool' && (
              <input type="checkbox" checked={!!solverOptions[d.key]}
                onChange={e => setDial(d.key, e.target.checked)} />)}
            {d.kind === 'int' && (
              <input type="number" value={solverOptions[d.key] ?? ''}
                onChange={e => setDial(d.key, e.target.value === '' ? undefined : Number(e.target.value))} />)}
            {d.kind === 'text' && (
              <input type="text" value={solverOptions[d.key] ?? ''}
                onChange={e => setDial(d.key, e.target.value || undefined)} />)}
          </div>
        ))}
      </div>
      <h2>NF Parameters (derived bands)</h2>
      <div className="rule-list">
        {groups.map(g => {
          const p = nfParameters[g] || {};
          return (
            <div key={g} className="rule-card">
              <span className="rule-card-title">{g}</span>
              <label>weeks lo/hi
                <input type="number" value={p.ncc_weeks?.[0] ?? ''}
                  onChange={e => setParam(g, { ncc_weeks: [Number(e.target.value), p.ncc_weeks?.[1] ?? 0] })} />
                <input type="number" value={p.ncc_weeks?.[1] ?? ''}
                  onChange={e => setParam(g, { ncc_weeks: [p.ncc_weeks?.[0] ?? 0, Number(e.target.value)] })} />
              </label>
              <label>density lo/hi
                <input type="number" step="0.1" value={p.density?.[0] ?? ''}
                  onChange={e => setParam(g, { density: [Number(e.target.value), p.density?.[1] ?? 0] })} />
                <input type="number" step="0.1" value={p.density?.[1] ?? ''}
                  onChange={e => setParam(g, { density: [p.density?.[0] ?? 0, Number(e.target.value)] })} />
              </label>
              <label>tolerance
                <input type="number" value={p.nf_tolerance ?? ''}
                  onChange={e => setParam(g, { nf_tolerance: e.target.value === '' ? undefined : Number(e.target.value) })} />
              </label>
              <span className="rule-card-desc">{deriveNfBand(p)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Backend round-trip test** — confirm a request carrying `nf_parameters` (from the UI) resolves, and `solver_options` scalars pass through:

```python
# tests/test_web_solver_options_roundtrip.py
from pathlib import Path
from scheduler.solver_bridge import build_solver_config_from_request
_STANDING = Path("config/standing/ncc-nf-model.yaml")

def test_ui_nf_parameters_and_dials_roundtrip():
    req = {"fellow_groups": {"NCC_JR": ["JR1", "JR2", "JR3"]},
           "shifts": ["NCC", "MICU", "Elec", "Vac"], "num_weeks": 8,
           "solver_options": {"call_tier_day_granular": True, "nf_week_off_cap": 3},
           "nf_parameters": {"NCC_JR": {"ncc_weeks": [12, 14], "density": [5.0, 5.5]}},
           "rules": []}
    cfg = build_solver_config_from_request(req, standing_path=_STANDING)
    assert cfg.nf_week_off_cap == 3
    assert cfg.nf_nf_day_band["NCC_JR"] == [20, 26]
```

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_web_solver_options_roundtrip.py -q`
Expected: PASS. (The derived band matches the frontend `deriveNfBand` preview — keep the two formulas in sync; the backend resolver is authoritative.)

- [ ] **Step 6: Type-check**

Run: `cd src/web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
cd /cv/scratch/u/watkina6/scheduler
git add src/web/src/types.ts src/web/src/App.tsx src/web/src/components/Sidebar.tsx src/web/src/components/SolverOptionsEditor.tsx tests/test_web_solver_options_roundtrip.py
git commit -m "feat(web): edit solver_options + nf_parameters as cards; derived-band preview

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: End-to-end smoke — Flask boot + 4-group config load + solve

**Files:** none (verification task).

- [ ] **Step 1: Build the frontend (type-safe production build)**

Run: `cd src/web && CI=true npx react-scripts build 2>&1 | tail -20`
Expected: "Compiled successfully" (warnings OK; no errors).

- [ ] **Step 2: Boot Flask and hit the API** (per the "verify Flask starts" rule)

```bash
cd /cv/scratch/u/watkina6/scheduler
PYTHONPATH=src .venv/bin/python -m scheduler.web_app &
sleep 4
curl -s http://127.0.0.1:5000/api/configs | head -c 400 ; echo
curl -s http://127.0.0.1:5000/api/config/annual/ncc-nf-model.yaml | python3 -c "import sys,json; d=json.load(sys.stdin); print('groups:', list(d.get('fellow_groups',{})), '| has solver_options:', 'solver_options' in d)"
kill %1 2>/dev/null
```

Expected: `/api/configs` returns JSON listing configs (no traceback); the config load prints the 4 groups and `has solver_options: True`. A connection error / empty body means Flask crashed — read its stdout and fix before proceeding.

- [ ] **Step 3: Solve smoke via the stream endpoint** (adds one forbid rule through the API to prove the reconciled path):

```bash
cd /cv/scratch/u/watkina6/scheduler
PYTHONPATH=src .venv/bin/python -m scheduler.web_app &
sleep 4
PYTHONPATH=src .venv/bin/python - <<'PY'
import json, urllib.request
cfg = json.load(urllib.request.urlopen("http://127.0.0.1:5000/api/config/annual/ncc-nf-model.yaml"))
# emulate the UI: a call-tier rule + a forbid rule inside rules:
cfg.setdefault("rules", []).append({"type":"blocked_weekend","name":"smoke","fellow":
  cfg["fellow_groups"]["NCC_JR"][0],"weeks":[3],"active":True})
req = urllib.request.Request("http://127.0.0.1:5000/api/feasibility/check",
  data=json.dumps({**cfg, "rules": cfg["rules"]}).encode(), headers={"Content-Type":"application/json"})
try:
    print("feasibility endpoint status:", urllib.request.urlopen(req, timeout=120).status)
except Exception as e:
    print("ERROR (must not be a call_rules ValueError):", e)
PY
kill %1 2>/dev/null
```

Expected: a 200 status and NO `call_rules is no longer a supported channel` error — proving call-tier + forbid rules flow through `rules:`. (A full solve is slow; the feasibility endpoint is the fast proof the request is accepted. If feasibility is unavailable for NF, substitute a short `/api/schedule/stream` read and confirm the first SSE line is not an error.)

- [ ] **Step 4: Record the smoke result** in the progress ledger (no commit; verification only).

---

## Self-Review

**Spec coverage:**
- §4 reconcile retired channel → Task 1 (backend contract) + Task 2 (frontend). ✓
- §4 dynamic groups → Task 3. ✓
- §4 missing palette types → Task 5. ✓
- §3/§4 forbid-coverage editor affordance → Task 4. ✓
- §5 solver_options + nf_parameters as cards, round-trip → Task 6. ✓
- §6 webapp smoke (Flask boot, load, solve, no call_rules rejection) → Task 7. ✓
- Backend nf_parameters/forbid resolution → done in the backend plan (dependency noted in Global Constraints).

**Placeholder scan:** every code step has concrete code or an exact command; no TBD/TODO. ✓

**Type consistency:** `createForbidCoverageRule(group)` (Task 4) referenced in its own test (Task 4 step 4); `NfParameters`/`AnnualConfig.solver_options`/`nf_parameters` defined in Task 6 step 1 and consumed by `SolverOptionsEditor` (step 4) and App.tsx (step 2); `SidebarSection` gains `'solver-options'` (Task 6 step 3) matching the App.tsx section (step 2). The frontend `deriveNfBand` mirrors the backend resolver formula (service = ceil(w_lo·d_lo)..floor(w_hi·d_hi); nf = round(service·frac)±tol) — Task 6 step 5 pins the backend value `[20,26]` for weeks [12,14]/density [5.0,5.5]/tol 0, which the preview must match.

**Note for implementer:** frontend unit-testing is thin (CRA/jest); most frontend tasks gate on `tsc --noEmit` + the Task 7 smoke rather than component tests. That is intentional given the harness, not a skipped test.
