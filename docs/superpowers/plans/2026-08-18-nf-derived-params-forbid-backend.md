# NF Derived-Parameter Resolver + Forbid-Coverage Rule (Backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a config-level NF derived-parameter resolver (semantic inputs → concrete NF bands at load) and verify the whole-week forbid-coverage rule on the NF model — the backend half of the combined spec.

**Architecture:** A new pure module `nf_param_resolver.py` expands an optional `nf_parameters:` config block into the concrete `nf_nf_day_band` / `nf_service_day_band` dicts the encoders already consume, using fixed formulas (`service = weeks × density`, `nf = service × fraction ± tol`). It is wired into `build_solver_config_from_request` (the single seam both the disk `assemble_config` path and the web-request path pass through) just before `_solver_options_kwargs`. Explicit raw bands always override derived ones; no `nf_parameters` = byte-identical to today. The forbid rule needs no new solver code — it reuses the existing windowed `staffing_per_week`/`at_most 0` pattern — so it gets a converter-level test plus a Slurm feasibility probe.

**Tech Stack:** Python 3, RoundingSat (via `RoundingSatRunner`), pytest, PyYAML. Solves run on Slurm (`sbatch -A prescient1 -p defq`); full-roster builds must NOT run on the edit node (OOM).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-17-nf-forbid-coverage-derived-params-webapp-design.md`.
- Branch: `partial-import`.
- Derivation representation = fixed named schema + resolver formulas in Python (no expression language).
- `nf_parameters` is resolved into `solver_options` before config build; it is NOT a `ScheduleSolverConfig` field.
- Explicit raw bands in `solver_options` ALWAYS override derived ones.
- No `nf_parameters` block ⇒ resolution is a no-op ⇒ byte-identical config assembly.
- OPB idiom: positive coefficients + negated literals only; never negative coefficients in `at_most` (dropped silently). N/A here (no new encoder) but keep in mind for the probe.
- Run fast/fixture tests locally: `PYTHONPATH=src:tests .venv/bin/python -m pytest <files> -q`. Never run the full-roster `assemble_config` build tests locally (OOM); route full solves to Slurm.
- End NF commits with the Co-Authored-By trailer used across the branch.

---

### Task 1: Resolver core — derive bands from explicit inputs

**Files:**
- Create: `src/scheduler/nf_param_resolver.py`
- Test: `tests/test_nf_param_resolver.py`

**Interfaces:**
- Produces: `resolve_nf_parameters(nf_parameters: dict | None, solver_options: dict | None, fellow_groups: dict, rules: list) -> dict` — returns a NEW solver_options dict with `nf_nf_day_band` and `nf_service_day_band` filled in for each group in `nf_parameters` (derived, only where not already explicit). No-op copy when `nf_parameters` is falsy.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_nf_param_resolver.py
from scheduler.nf_param_resolver import resolve_nf_parameters

FG = {"NCC_JR": ["JR1", "JR2"], "CCM": ["C1"]}

def test_derives_service_and_nf_band_from_explicit_inputs():
    # weeks 12-14, density 5.0-5.5, fraction 1/3, tol 1
    #   service = [ceil(12*5.0), floor(14*5.5)] = [60, 77]
    #   nf      = [round(60/3)-1, round(77/3)+1] = [19, 27]
    nf_params = {"NCC_JR": {"ncc_weeks": [12, 14], "density": [5.0, 5.5],
                            "nf_fraction": 1/3, "nf_tolerance": 1}}
    out = resolve_nf_parameters(nf_params, {}, FG, [])
    assert out["nf_service_day_band"]["NCC_JR"] == [60, 77]
    assert out["nf_nf_day_band"]["NCC_JR"] == [19, 27]

def test_no_params_is_noop_copy():
    opts = {"nf_week_off_cap": 3}
    out = resolve_nf_parameters(None, opts, FG, [])
    assert out == opts and out is not opts

def test_explicit_band_overrides_derived():
    nf_params = {"NCC_JR": {"ncc_weeks": [12, 14], "density": [5.0, 5.5]}}
    opts = {"nf_nf_day_band": {"NCC_JR": [22, 27]}}
    out = resolve_nf_parameters(nf_params, opts, FG, [])
    assert out["nf_nf_day_band"]["NCC_JR"] == [22, 27]  # unchanged
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_param_resolver.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scheduler.nf_param_resolver'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/scheduler/nf_param_resolver.py
"""Resolve NF derived-parameter inputs (nf_parameters) into concrete solver_options bands.

The NF fairness bands are a central value +/- tolerance derived from semantic inputs:
  service_band = ncc_weeks * density
  nf_day_band  = service_band * nf_fraction (+/- nf_tolerance)
Changing an input (e.g. ncc_weeks) moves the dependent bands automatically. Explicit raw
bands in solver_options always override the derived ones (back-compat / manual control).
"""
from __future__ import annotations

import math

_DEFAULT_FRACTION = 1.0 / 3.0


def _as_pair(v):
    if isinstance(v, (int, float)):
        return (float(v), float(v))
    return (float(v[0]), float(v[1]))


def resolve_nf_parameters(nf_parameters, solver_options, fellow_groups, rules):
    opts = dict(solver_options or {})
    if not nf_parameters:
        return opts
    nf_band = dict(opts.get("nf_nf_day_band") or {})
    svc_band = dict(opts.get("nf_service_day_band") or {})
    for group, params in nf_parameters.items():
        weeks = params.get("ncc_weeks")
        w_lo, w_hi = int(weeks[0]), int(weeks[1])
        d_lo, d_hi = _as_pair(params["density"])
        frac = float(params.get("nf_fraction", _DEFAULT_FRACTION))
        tol = int(params.get("nf_tolerance", 0))
        s_lo = math.ceil(w_lo * d_lo)
        s_hi = math.floor(w_hi * d_hi)
        n_lo = max(0, round(s_lo * frac) - tol)
        n_hi = round(s_hi * frac) + tol
        svc_band.setdefault(group, [s_lo, s_hi])
        nf_band.setdefault(group, [n_lo, n_hi])
    if nf_band:
        opts["nf_nf_day_band"] = nf_band
    if svc_band:
        opts["nf_service_day_band"] = svc_band
    return opts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_param_resolver.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/scheduler/nf_param_resolver.py tests/test_nf_param_resolver.py
git commit -m "feat(nf): resolver core — derive NF/service bands from semantic inputs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Read NCC-week band from existing rules

**Files:**
- Modify: `src/scheduler/nf_param_resolver.py`
- Test: `tests/test_nf_param_resolver.py`

**Interfaces:**
- Produces: `_ncc_weeks_from_rules(group, rules) -> tuple[int,int] | None` (module-internal); `resolve_nf_parameters` uses it when `ncc_weeks` is omitted.

- [ ] **Step 1: Write the failing test**

```python
def test_ncc_weeks_sourced_from_rules_when_omitted():
    rules = [
        {"type": "shift_total", "name": "JR NCC week floor", "groups": ["NCC_JR"],
         "shifts": ["NCC"], "relation": "at_least", "count": 12},
        {"type": "shift_total", "name": "JR NCC week ceil", "groups": ["NCC_JR"],
         "shifts": ["NCC"], "relation": "at_most", "count": 14},
    ]
    nf_params = {"NCC_JR": {"density": [5.0, 5.5]}}  # no ncc_weeks
    out = resolve_nf_parameters(nf_params, {}, FG, rules)
    assert out["nf_service_day_band"]["NCC_JR"] == [60, 77]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_param_resolver.py::test_ncc_weeks_sourced_from_rules_when_omitted -q`
Expected: FAIL — `TypeError`/`KeyError` (weeks is None → `weeks[0]`).

- [ ] **Step 3: Write minimal implementation**

Add the helper and use it in `resolve_nf_parameters` (replace the `weeks = params.get("ncc_weeks")` line):

```python
def _ncc_weeks_from_rules(group, rules):
    lo = hi = None
    for r in rules or []:
        if r.get("type") != "shift_total":
            continue
        if group not in (r.get("groups") or []):
            continue
        if "NCC" not in (r.get("shifts") or []):
            continue
        rel, cnt = r.get("relation"), r.get("count")
        if rel == "at_least" and cnt is not None:
            lo = int(cnt)
        elif rel == "at_most" and cnt is not None:
            hi = int(cnt)
    return (lo, hi) if lo is not None and hi is not None else None
```

In `resolve_nf_parameters`, change:

```python
        weeks = params.get("ncc_weeks")
        if weeks is None:
            weeks = _ncc_weeks_from_rules(group, rules)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_param_resolver.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/scheduler/nf_param_resolver.py tests/test_nf_param_resolver.py
git commit -m "feat(nf): resolver reads NCC-week band from existing rules when omitted

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Validation — fail loudly on bad inputs

**Files:**
- Modify: `src/scheduler/nf_param_resolver.py`
- Test: `tests/test_nf_param_resolver.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

def test_unknown_group_raises():
    with pytest.raises(ValueError, match="unknown group"):
        resolve_nf_parameters({"NOPE": {"density": [5, 5]}}, {}, FG, [])

def test_missing_weeks_raises():
    with pytest.raises(ValueError, match="ncc_weeks"):
        resolve_nf_parameters({"NCC_JR": {"density": [5, 5]}}, {}, FG, [])  # no rules, no explicit

def test_bad_fraction_raises():
    with pytest.raises(ValueError, match="nf_fraction"):
        resolve_nf_parameters(
            {"NCC_JR": {"ncc_weeks": [12, 14], "density": [5, 5], "nf_fraction": 1.5}},
            {}, FG, [])

def test_lo_gt_hi_raises():
    with pytest.raises(ValueError, match="lo > hi|empty band"):
        resolve_nf_parameters(
            {"NCC_JR": {"ncc_weeks": [14, 12], "density": [5, 5]}}, {}, FG, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_param_resolver.py -k "raises" -q`
Expected: FAIL (no validation yet).

- [ ] **Step 3: Write minimal implementation**

In `resolve_nf_parameters`, inside the loop, add validation:

```python
        if group not in fellow_groups:
            raise ValueError(f"nf_parameters names unknown group {group!r}.")
        weeks = params.get("ncc_weeks")
        if weeks is None:
            weeks = _ncc_weeks_from_rules(group, rules)
        if weeks is None:
            raise ValueError(
                f"nf_parameters[{group!r}] needs ncc_weeks (explicit or via NCC-week rules).")
        if "density" not in params:
            raise ValueError(f"nf_parameters[{group!r}] needs density.")
        # ... after computing frac/tol/w_/d_ :
        if not (0.0 < frac < 1.0):
            raise ValueError(f"nf_parameters[{group!r}] nf_fraction must be in (0,1).")
        if tol < 0:
            raise ValueError(f"nf_parameters[{group!r}] nf_tolerance must be >= 0.")
        if w_lo > w_hi or d_lo > d_hi:
            raise ValueError(f"nf_parameters[{group!r}] has lo > hi.")
        # ... after computing n_lo/n_hi :
        if n_lo > n_hi:
            raise ValueError(f"nf_parameters[{group!r}] resolves to empty band.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_param_resolver.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/scheduler/nf_param_resolver.py tests/test_nf_param_resolver.py
git commit -m "feat(nf): resolver validates nf_parameters (loud failures)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Wire the resolver into build_solver_config_from_request

**Files:**
- Modify: `src/scheduler/solver_bridge.py:288` (the `opt_kwargs = _solver_options_kwargs(...)` line)
- Test: `tests/test_nf_param_resolver_wiring.py`

**Interfaces:**
- Consumes: `resolve_nf_parameters` (Task 1–3); `raw_request` fields `nf_parameters`, `solver_options`, `fellow_groups` (line 145), `rules` (`annual_rules_list`, line 163).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_nf_param_resolver_wiring.py
from pathlib import Path
from scheduler.solver_bridge import build_solver_config_from_request

_STANDING = Path("config/standing/ncc-nf-model.yaml")

def _min_request(**extra):
    req = {
        "fellow_groups": {"NCC_JR": ["JR1", "JR2", "JR3"]},
        "shifts": ["NCC", "MICU", "Elec", "Vac"],
        "num_weeks": 8,
        "solver_options": {"call_tier_day_granular": True},
        "rules": [
            {"type": "shift_total", "groups": ["NCC_JR"], "shifts": ["NCC"],
             "relation": "at_least", "count": 12, "name": "f"},
            {"type": "shift_total", "groups": ["NCC_JR"], "shifts": ["NCC"],
             "relation": "at_most", "count": 14, "name": "c"},
        ],
    }
    req.update(extra)
    return req

def test_nf_parameters_resolved_into_config_band():
    req = _min_request(nf_parameters={"NCC_JR": {"density": [5.0, 5.5]}})
    cfg = build_solver_config_from_request(req, standing_path=_STANDING)
    assert cfg.nf_nf_day_band["NCC_JR"] == [20, 26]  # svc [60,77] * 1/3, tol 0

def test_no_nf_parameters_leaves_bands_untouched():
    req = _min_request(solver_options={"call_tier_day_granular": True,
                                       "nf_nf_day_band": {"NCC_JR": [22, 27]}})
    cfg = build_solver_config_from_request(req, standing_path=_STANDING)
    assert cfg.nf_nf_day_band["NCC_JR"] == [22, 27]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_param_resolver_wiring.py -q`
Expected: FAIL — first test: `cfg.nf_nf_day_band` is None/missing (resolver not wired).

- [ ] **Step 3: Write minimal implementation**

In `src/scheduler/solver_bridge.py`, add the import near the top with the other `from scheduler...` imports:

```python
from scheduler.nf_param_resolver import resolve_nf_parameters
```

Replace line 288:

```python
    opt_kwargs = _solver_options_kwargs(raw_request.get("solver_options") or {})
```

with:

```python
    _resolved_opts = resolve_nf_parameters(
        raw_request.get("nf_parameters"),
        raw_request.get("solver_options") or {},
        fellow_groups,
        annual_rules_list,
    )
    opt_kwargs = _solver_options_kwargs(_resolved_opts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_param_resolver_wiring.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/scheduler/solver_bridge.py tests/test_nf_param_resolver_wiring.py
git commit -m "feat(nf): wire nf_parameters resolver into build_solver_config_from_request

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Forbid-coverage rule — converter-level test

**Files:**
- Test: `tests/test_forbid_coverage_rule.py`

**Interfaces:**
- Consumes: `palette_rules.palette_rule_to_constraints` (existing). Confirms the windowed `staffing_per_week`/`at_most 0` shape converts to a windowed zero-cap `SemanticConstraint` (no new code — this guards the pattern the webapp will emit).

- [ ] **Step 1: Write the failing test** (it should PASS immediately if the pattern is valid — this is a guard, not RED→GREEN; if it fails, the pattern is wrong and must be fixed before the webapp relies on it)

```python
# tests/test_forbid_coverage_rule.py
from scheduler.palette_rules import palette_rule_to_constraints

def test_windowed_forbid_converts_to_zero_cap_windowed_constraint():
    rule = {
        "type": "staffing_per_week", "name": "No CCM coverage (holiday)",
        "groups": ["CCM"], "shifts": ["NCC"],
        "relation": "at_most", "count": 0, "window": [25, 27], "strength": "hard",
    }
    cons = palette_rule_to_constraints(rule, {"CCM": ["C1", "C2"]})
    assert cons, "rule must produce at least one constraint"
    c = cons[0]
    # windowed forbid: at_most 0 over weeks 25..26
    assert getattr(c.weeks, "start", None) == 25
    assert getattr(c.weeks, "end", None) == 27
```

- [ ] **Step 2: Run the test**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_forbid_coverage_rule.py -q`
Expected: PASS. If it FAILS, inspect `palette_rules.py` `_convert_staffing_per_week` / `WeekSpan` handling and adjust the assertion to the real attribute names (`weeks.start/end` vs `.lo/.hi`) — do NOT change production code unless the pattern genuinely does not carry the window; report if so.

- [ ] **Step 3: Commit**

```bash
git add tests/test_forbid_coverage_rule.py
git commit -m "test(nf): guard windowed forbid-coverage rule conversion

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Slurm feasibility probe — forbid on the production NF config

**Files:**
- Create: `config/annual/ncc-nf-4jr-forbidprobe.yaml` (production config + one forbid rule)

**Interfaces:** none (verification task; no code). Produces a workbook + `evaluate_nf` result to confirm the whole-week forbid holds on the real roster and does not silently UNSAT or leak (weekend-Elec hole).

- [ ] **Step 1: Build the probe config**

```bash
cd /cv/scratch/u/watkina6/scheduler
PYTHONPATH=src .venv/bin/python -c "
import yaml
c=yaml.safe_load(open('config/annual/ncc-nf-model.yaml'))
c['rules'].append({'type':'staffing_per_week','name':'No CCM coverage wk25-26',
  'groups':['CCM'],'shifts':['NCC'],'relation':'at_most','count':0,
  'window':[25,27],'strength':'hard','active':True})
yaml.safe_dump(c, open('config/annual/ncc-nf-4jr-forbidprobe.yaml','w'), sort_keys=False)
print('wrote probe config')
"
```

- [ ] **Step 2: Launch the Slurm feasibility probe (`--mode sat`, no subprocess timeout)**

```bash
sbatch -A prescient1 -p defq -n1 -t 8:00:00 -J nf_forbidprobe \
  -o results_slurm/forbidprobe_%j.out -e results_slurm/forbidprobe_%j.err \
  --wrap "PYTHONPATH=src .venv/bin/python -u experiments/nf_solve_production.py \
   --annual config/annual/ncc-nf-4jr-forbidprobe.yaml \
   --standing config/standing/ncc-nf-model.yaml --mode sat \
   --prefix results_slurm/out_forbidprobe"
```

- [ ] **Step 3: On completion, verify SAT + evaluator + no leak**

Poll `squeue -u watkina6` / `sacct`. When COMPLETED, check:
- `grep -E "SAT in|NF evaluate" results_slurm/forbidprobe_*.out` → expect `SAT` and `NF evaluate OK`.
- Confirm CCM holds NO call in weeks 25–26 in `results_slurm/out_forbidprobe_initial.xlsx`:

```bash
PYTHONPATH=src .venv/bin/python -c "
import openpyxl
wb=openpyxl.load_workbook('results_slurm/out_forbidprobe_initial.xlsx'); ws=wb['Call Detail']
bad=[]
w=-1
for r in range(2,ws.max_row+1):
    if ws.cell(r,1).value: w+=1
    if ws.cell(r,2).value in ('NCC1','NCC2','NF') and w in (25,26):
        for c in range(3,10):
            v=ws.cell(r,c).value
            if v and str(v).startswith('CCM'): bad.append((w,ws.cell(r,2).value,v))
print('LEAK' if bad else 'clean forbid', bad[:5])
"
```

Expected: `clean forbid []`. If `LEAK`, the weekend-Elec hole is real → record for the deferred day-layer reinforcement (out of scope for this plan; flag to the user).

- [ ] **Step 4: Commit the probe config**

```bash
git add config/annual/ncc-nf-4jr-forbidprobe.yaml
git commit -m "chore(nf): forbid-coverage feasibility probe config

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Derived-parameter model (spec §2): Tasks 1–4 (formulas, weeks-from-rules, validation, wiring, override + no-op regression). ✓
- Forbid-coverage rule (spec §3): Task 5 (converter guard) + Task 6 (feasibility probe incl. weekend-Elec leak check). ✓ Editor affordance is Plan 2 (webapp).
- Testing/verification (spec §6): resolver units, byte-identical regression (Task 4 test 2), forbid probe. ✓
- Webapp (spec §4, §5): intentionally deferred to Plan 2. Noted.

**Placeholder scan:** No TBD/TODO; every code step has concrete code. ✓

**Type consistency:** `resolve_nf_parameters(nf_parameters, solver_options, fellow_groups, rules)` signature identical across Tasks 1–4; `_ncc_weeks_from_rules(group, rules)` consistent. Band values are `[lo, hi]` lists throughout (matches existing `nf_nf_day_band` dict format the encoders consume). ✓

**Note for implementer:** Task 5's assertion may need the real `WeekSpan` attribute names (`.start/.end` vs `.lo/.hi`); the step says how to adapt without touching production code.
