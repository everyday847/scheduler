# NF Model — Phase 1 (Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a new no-workbook NCC schedule model with a day-granular call tier (NCC1/NCC2/NF) that builds, solves, and produces a correctly-covered full-year schedule — activated entirely by new config files, leaving the wb7 path untouched.

**Architecture:** A new `ScheduleSolverConfig.call_tier_day_granular` flag (default `False`, set only by the new config) makes `build_full_schedule_opb` emit a **day-granular call layer** `call[d][f][role]` for roles NCC1/NCC2/NF, replacing the legacy per-night `xn[d][f]` layer for this model. The existing **weekly background roster** (`xs[f][w][shift]`, Swing removed) is retained and *gates* call availability: a fellow on a non-call background rotation (MICU/NS/Vac/etc.) that week is unavailable for call those days. Hard coverage: weekdays NCC1+NCC2+NF=1 each, weekends NCC1+NF=1. This phase delivers feasibility-of-coverage only; NF run-length/rest (Phase 2) and continuity/quotas (Phase 3) are follow-on plans.

**Tech Stack:** Python 3.12 (`.venv`), pseudo-Boolean encoding via `OpbBuilder`, RoundingSat solver (`vendor/roundingsat/build/roundingsat`), pytest. Run tests: `PYTHONPATH=src:tests python -m pytest`. Full wb7 regression suite must stay green (587 tests).

**Spec:** `docs/superpowers/specs/2026-06-16-nf-night-float-model-design.md`

**Phasing:** This is Phase 1 of 3. Phase 2 = NF runs (4-6 days) + symmetric 2-day rest. Phase 3 = call-block continuity penalties + service-day quota bands + objective. Each phase is independently shippable and testable.

---

## Design invariants (read before implementing)

- **Config is the only activation channel.** The day-granular call tier turns on iff `config.call_tier_day_granular` is `True`, which is set only from the new annual config. No CLI flag, no `--variant`. (See memory `feedback_no_dual_activation_channels`.)
- **wb7 path is sacrosanct.** When `call_tier_day_granular` is `False` (every existing config), `build_full_schedule_opb` must emit byte-identical output to today. Every new code path is behind the flag. The 587-test suite is the regression gate.
- **Soft rules never emit hard sub-constraints** (memory `feedback_soft_never_hard`). Phase 1 has no soft rules; coverage is hard.
- **Day vs week granularity:** background roster `xs[f][w][shift]` is week-granular (unchanged). The new call layer `call[d][f][role]` is day-granular: `d ∈ [0, num_days)`, `role ∈ {NCC1, NCC2, NF}`.

---

## File Structure

**New files:**
- `config/standing/ncc-nf-model.yaml` — standing config for the new model: shift palette (Swing removed, NF added), shift attributes, the structural rules shared across years. Carries the activation marker `call_tier_day_granular: true` under `solver_options:`.
- `config/annual/ncc-nf-model.yaml` — annual config: `fellow_groups` (3 NCC_JR, 2 NCC_SR, 1 CCM; parameterized for a 2nd CCM), `shifts`, `horizon_start`, and an empty/minimal `rules` set for Phase 1. Points at the new standing file conceptually (assembled via `--standing`).
- `tests/test_nf_call_tier_foundation.py` — unit/dispatch tests for the call-tier vars, coverage, and seam (mirrors `tests/_dispatch_helpers.py` style).
- `tests/_nf_helpers.py` — a small fixture builder for a minimal day-granular-call config (parallel to `_dispatch_helpers.make_dispatch_config`, but with `call_tier_day_granular=True` and a short horizon).

**Modified files (all changes gated behind `call_tier_day_granular`):**
- `src/parafrost_scheduler/schedule_types.py` — add `call_tier_day_granular: bool = False` field to `ScheduleSolverConfig`; add `CALL_ROLES = ("NCC1", "NCC2", "NF")` constant and a `call` field on `ScheduleVarMap`.
- `src/parafrost_scheduler/schedule_encoder.py` — in `build_full_schedule_opb`: when the flag is set, build `call[d][f][role]` vars, emit hard coverage + the background→call seam, and SKIP the legacy `xn` night layer; extend `decode_solution` to surface the call tier.
- `src/scheduler/solver_bridge.py` — thread `call_tier_day_granular` from `solver_options` into the config (it already unpacks `solver_options`; confirm the key passes through).
- `src/scheduler/call_schedule_common.py` — add the new model's weekend role set if/where decode needs it (NCC1+NF), without disturbing the wb7 `WEEKEND_ROLES`.

**Explicitly NOT touched in Phase 1:** NF run-length and rest (Phase 2); continuity penalties and quota bands (Phase 3); the web UI; the wb7 configs.

---

### Task 1: Add the `call_tier_day_granular` config flag (activation channel)

**Files:**
- Modify: `src/parafrost_scheduler/schedule_types.py` (`ScheduleSolverConfig`, ~line 199 — add field near the other variant flags)
- Modify: `src/scheduler/solver_bridge.py:36-42` (`_SOLVER_OPTION_BOOL_KEYS`)
- Test: `tests/test_nf_call_tier_foundation.py`

This task only adds the flag and proves it threads from a `solver_options` block through to the config. No encoder behavior yet.

- [ ] **Step 1: Write the failing test**

Create `tests/test_nf_call_tier_foundation.py`:

```python
"""Phase-1 foundation tests for the NF model's day-granular call tier.

The whole tier activates iff config.call_tier_day_granular is True, set only from
a new config's solver_options. Default-off keeps the wb7 path byte-identical.
"""
from __future__ import annotations

from scheduler.solver_bridge import build_solver_config_from_request


def _minimal_request(**solver_options):
    """A tiny no-workbook request: 2 fellows, 3 shifts, no rules."""
    return {
        "fellow_groups": {"NCC_JR": ["A", "B"]},
        "shifts": ["NCC1", "NCC2", "Elec"],
        "standing_rules": [],  # bypass disk standing config
        "horizon_start": "2026-07-01",
        "num_weeks": 2,
        "solver_options": dict(solver_options),
    }


def test_flag_defaults_false_when_absent():
    cfg = build_solver_config_from_request(_minimal_request())
    assert cfg.call_tier_day_granular is False


def test_flag_threads_from_solver_options():
    cfg = build_solver_config_from_request(
        _minimal_request(call_tier_day_granular=True))
    assert cfg.call_tier_day_granular is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_call_tier_foundation.py -v`
Expected: FAIL — `test_flag_threads_from_solver_options` raises `ValueError: Unknown solver_options key(s): call_tier_day_granular` (and/or `AttributeError: ... has no attribute 'call_tier_day_granular'`).

- [ ] **Step 3: Add the dataclass field**

In `src/parafrost_scheduler/schedule_types.py`, inside `ScheduleSolverConfig` near the other variant flags (after `abpn_night_block: bool = True`, ~line 203), add:

```python
    # --- NF model (no-workbook, day-granular call tier) ---
    # When True, build_full_schedule_opb emits a day-granular call layer
    # call[d][f][role] for roles NCC1/NCC2/NF and SKIPS the legacy per-night xn
    # layer. Default False => the wb7 path is byte-identical. Set only by the new
    # ncc-nf-model config's solver_options block (config is the only activation
    # channel — see feedback_no_dual_activation_channels).
    call_tier_day_granular: bool = False
```

- [ ] **Step 4: Register the solver_options key**

In `src/scheduler/solver_bridge.py`, add to `_SOLVER_OPTION_BOOL_KEYS` (line 36-42):

```python
_SOLVER_OPTION_BOOL_KEYS = (
    "weekend_consecutive_hard",
    "nh_aan_week_call_hard",
    "abpn_night_block",
    "weekend_night_saturday_hard",
    "weekend_night_sunday_hard",
    "call_tier_day_granular",
)
```

(No other change needed: `_solver_options_kwargs` already maps every bool key through to a constructor kwarg, and the `known` set is derived from this tuple, so the key is both accepted and threaded.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_call_tier_foundation.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Run the full suite to confirm wb7 is untouched**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/ -q`
Expected: 589 passed (587 prior + 2 new).

- [ ] **Step 7: Commit**

```bash
git add src/parafrost_scheduler/schedule_types.py src/scheduler/solver_bridge.py tests/test_nf_call_tier_foundation.py
git commit -m "feat(nf): add call_tier_day_granular config flag (activation channel, default off)"
```

---

### Task 2: Build the day-granular call vars `call[d][f][role]`

**Files:**
- Modify: `src/parafrost_scheduler/schedule_types.py` (add `CALL_ROLES` constant near `_WEEKEND_ROLE_NAMES` ~line 65; add `call` field to `ScheduleVarMap` ~line 290)
- Modify: `src/parafrost_scheduler/schedule_encoder.py` (in `build_full_schedule_opb`, after the night-var section ~line 319; and the `ScheduleVarMap(...)` construction ~line 356)
- Test: `tests/_nf_helpers.py` (new fixture), `tests/test_nf_call_tier_foundation.py`

Build a day-granular variable for each (day, fellow, role) when the flag is on. No constraints yet — just the vars and their presence in the var_map, so later tasks (coverage, seam) and decode can reference them.

- [ ] **Step 1: Add the `_nf_helpers` fixture builder**

Create `tests/_nf_helpers.py`:

```python
"""Fixtures for the NF model's day-granular call tier (Phase 1+).

Parallel to tests/_dispatch_helpers.make_dispatch_config, but turns the call tier
ON (call_tier_day_granular=True) and uses a short horizon so solves are fast.
"""
from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

from parafrost_scheduler.schedule_encoder import build_full_schedule_opb
from parafrost_scheduler.schedule_types import ScheduleSolverConfig
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig

ROUNDINGSAT = Path(__file__).resolve().parent.parent / "vendor/roundingsat/build/roundingsat"


def make_nf_config(
    *,
    fellows=("J1", "J2", "J3", "S1", "S2", "C1"),
    shifts=("NCC1", "NCC2", "NF", "MICU", "Elec", "Vac"),
    num_days: int = 14,
    start_dow: int = 0,
    constraints=(),
    **overrides,
) -> ScheduleSolverConfig:
    """A minimal day-granular-call config (call_tier_day_granular ON)."""
    night_config = NightSolverConfig(
        total_nights={}, friday_nights={},
        total_night_multisets=(), friday_night_multisets=(),
        ccm_fellows=frozenset(), holiday_dates=(),
        horizon_start_date=date(2026, 7, 1))
    weekend_config = WeekendSolverConfig(
        ncc_totals={}, stroke_totals={}, stroke_cohort=(), stroke_cohort_total=None,
        ccm_fellows=frozenset(),
        always_stroke_eligible=frozenset(fellows),
        telestroke_stroke_eligible=frozenset(), stroke_only_eligible=frozenset())
    kwargs = dict(
        fellow_groups={"NCC_JR": list(fellows)},
        shifts=list(shifts),
        constraints=list(constraints),
        night_config=night_config,
        weekend_config=weekend_config,
        start_dow=start_dow,
        num_days=num_days,
        call_tier_day_granular=True,
    )
    kwargs.update(overrides)
    return ScheduleSolverConfig(**kwargs)


def build(config, *, objective=False):
    return build_full_schedule_opb(config, objective=objective)


def runner_or_skip():
    import pytest
    if not ROUNDINGSAT.exists():
        pytest.skip("RoundingSat binary not built")
    return RoundingSatRunner(ROUNDINGSAT)
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_nf_call_tier_foundation.py`:

```python
from _nf_helpers import make_nf_config, build
from parafrost_scheduler.schedule_types import CALL_ROLES


def test_call_vars_exist_per_day_fellow_role_when_flag_on():
    cfg = make_nf_config(num_days=14)
    opb, vm = build(cfg)
    # vm.call[d][f] is a dict role-name -> var id, one entry per CALL_ROLE.
    assert len(vm.call) == 14                       # one per day
    assert len(vm.call[0]) == vm.num_fellows        # one per fellow
    assert set(vm.call[0][0].keys()) == set(CALL_ROLES)
    # vars are real, positive, distinct
    sample = vm.call[0][0]["NCC1"]
    assert isinstance(sample, int) and sample > 0


def test_call_vars_absent_when_flag_off():
    cfg = make_nf_config(num_days=14, call_tier_day_granular=False)
    opb, vm = build(cfg)
    assert vm.call == []   # no day-granular call layer on the wb7-style path
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_call_tier_foundation.py -k call_vars -v`
Expected: FAIL — `ImportError: cannot import name 'CALL_ROLES'` (and `ScheduleVarMap` has no `call`).

- [ ] **Step 4: Add the `CALL_ROLES` constant and the var_map field**

In `src/parafrost_scheduler/schedule_types.py`, after `_WEEKEND_ROLE_NAMES = (...)` (~line 65):

```python
# Day-granular call roles for the NF model (config.call_tier_day_granular). NF
# replaces Swing AND the legacy per-night layer; these are the per-day call roles.
CALL_ROLES = ("NCC1", "NCC2", "NF")
```

In `ScheduleVarMap` (~line 290), add a field after `xn`:

```python
    # Day-granular call layer (NF model only): call[d][f] is a dict
    # role-name -> var id for each CALL_ROLE. Empty list when the flag is off.
    call: list[list[dict[str, int]]] = field(default_factory=list)
```

- [ ] **Step 5: Build the vars in `build_full_schedule_opb`**

In `src/parafrost_scheduler/schedule_encoder.py`, immediately after the night-var block (after line 319, before "7. Night constraints"), add:

```python
    # -------------------------------------------------------------------
    # 6b. Day-granular call vars: call[d][f][role]  (NF model only)
    # -------------------------------------------------------------------
    call: list[list[dict[str, int]]] = []
    if config.call_tier_day_granular:
        opb.add_comment("NF model: day-granular call variables (NCC1/NCC2/NF)")
        for d in range(num_days):
            call.append([])
            for f in range(num_fellows):
                call[d].append({role: opb.new_var() for role in CALL_ROLES})
```

Add `CALL_ROLES` to the existing `from parafrost_scheduler.schedule_types import (...)` block at the top of the file (it already imports `_WEEKEND_ROLE_NAMES` etc.).

In the `ScheduleVarMap(...)` construction (~line 356), pass the new field:

```python
        xn=xn,
        call=call,
        soft_violations=soft_violations,
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_call_tier_foundation.py -k call_vars -v`
Expected: PASS (both `test_call_vars_*`).

- [ ] **Step 7: Run the full suite (wb7 regression)**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/ -q`
Expected: 591 passed (589 + 2 new). The `call=[]` default means flag-off builds are unchanged.

- [ ] **Step 8: Commit**

```bash
git add src/parafrost_scheduler/schedule_types.py src/parafrost_scheduler/schedule_encoder.py tests/_nf_helpers.py tests/test_nf_call_tier_foundation.py
git commit -m "feat(nf): day-granular call[d][f][role] vars behind the flag"
```

---

### Task 3: Hard coverage + one-call-role-per-day-per-fellow

**Files:**
- Modify: `src/parafrost_scheduler/schedule_encoder.py` (new helper `_encode_call_tier_coverage`, called from `build_full_schedule_opb` when the flag is on)
- Test: `tests/test_nf_call_tier_foundation.py`

Coverage rules (all HARD):
- Each WEEKDAY (dow 0-4): exactly one fellow on NCC1, exactly one on NCC2, exactly one on NF.
- Each WEEKEND day (dow 5-6): exactly one fellow on NCC1, exactly one on NF; **NCC2 forbidden** (no fellow may hold weekend NCC2).
- Each (day, fellow): at most one call role (you can't be NCC1 and NF the same day).

`_day_of_week(d, start_dow)` returns Mon=0..Sun=6; weekend = dow ∈ {5, 6}.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_nf_call_tier_foundation.py`:

```python
from _nf_helpers import runner_or_skip
from parafrost_scheduler.schedule_types import day_of_week


def _role_holders(assignment, vm, d, role):
    return [f for f in range(vm.num_fellows)
            if assignment.get(vm.call[d][f][role], False)]


def test_coverage_is_satisfiable_and_exactly_one_each():
    cfg = make_nf_config(num_days=14)   # 2 weeks, 6 fellows
    opb, vm = build(cfg)
    res = runner_or_skip().solve(opb, timeout=30)
    assert res.satisfiable
    a = res.assignment
    for d in range(14):
        dow = day_of_week(d, vm.start_dow)
        assert len(_role_holders(a, vm, d, "NCC1")) == 1
        assert len(_role_holders(a, vm, d, "NF")) == 1
        if dow in (5, 6):   # weekend: no NCC2
            assert len(_role_holders(a, vm, d, "NCC2")) == 0
        else:               # weekday: exactly one NCC2
            assert len(_role_holders(a, vm, d, "NCC2")) == 1


def test_one_call_role_per_fellow_per_day():
    cfg = make_nf_config(num_days=14)
    opb, vm = build(cfg)
    res = runner_or_skip().solve(opb, timeout=30)
    assert res.satisfiable
    a = res.assignment
    for d in range(14):
        for f in range(vm.num_fellows):
            held = [r for r in ("NCC1", "NCC2", "NF") if a.get(vm.call[d][f][r], False)]
            assert len(held) <= 1
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_call_tier_foundation.py -k coverage -v`
Expected: FAIL — coverage unconstrained, so `exactly one` assertions fail (0 or >1 holders).

- [ ] **Step 3: Implement `_encode_call_tier_coverage`**

In `src/parafrost_scheduler/schedule_encoder.py`, add the helper (near the other `_encode_*` functions):

```python
def _encode_call_tier_coverage(opb, call, config, fellow_names, num_days, start_dow):
    """HARD coverage for the day-granular call tier (NF model).

    Weekdays: exactly one fellow each on NCC1, NCC2, NF.
    Weekends: exactly one each on NCC1, NF; NCC2 forbidden.
    Per (day, fellow): at most one call role.
    """
    opb.add_comment("NF model: call-tier hard coverage (NCC1/NCC2/NF)")
    num_fellows = len(fellow_names)
    for d in range(num_days):
        dow = _day_of_week(d, start_dow)
        weekend = dow in (5, 6)
        # Coverage per role.
        for role in CALL_ROLES:
            holders = [call[d][f][role] for f in range(num_fellows)]
            if role == "NCC2" and weekend:
                for v in holders:           # NCC2 forbidden on weekends
                    opb.add_unit(-v)
            else:
                opb.exactly_one(holders)
        # At most one call role per fellow per day.
        for f in range(num_fellows):
            opb.at_most_k([call[d][f][r] for r in CALL_ROLES], 1)
```

Call it from `build_full_schedule_opb` right after the call vars are built (inside the `if config.call_tier_day_granular:` region from Task 2):

```python
        _encode_call_tier_coverage(
            opb, call, config, fellow_names, num_days, start_dow)
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_call_tier_foundation.py -k coverage -v`
Expected: PASS (both). NOTE: with 6 fellows and weekday demand of 3 roles/day this small 14-day config is comfortably satisfiable.

- [ ] **Step 5: Full suite**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/ -q`
Expected: 593 passed.

- [ ] **Step 6: Commit**

```bash
git add src/parafrost_scheduler/schedule_encoder.py tests/test_nf_call_tier_foundation.py
git commit -m "feat(nf): hard call-tier coverage (wkday NCC1+NCC2+NF, wkend NCC1+NF)"
```

---

### Task 4: Background→call seam (a blocking weekly rotation closes call those days)

**Files:**
- Modify: `src/parafrost_scheduler/schedule_encoder.py` (`_encode_call_tier_coverage` or a new `_encode_call_background_seam`)
- Test: `tests/test_nf_call_tier_foundation.py`

The seam: a fellow whose WEEKLY background rotation that week is a **call-blocking** shift (MICU, NS, Vac, SICU, etc. — any weekly shift that is NOT itself a call role or a free state) cannot hold any call role on that week's days. This is how "MICU blocks the weekends" falls out.

Encoding (hard): for each blocking weekly shift `s`, day `d` in week `w=week(d)`, fellow `f`, role `r`:
`call[d][f][r] ⇒ ¬xs[f][w][s]`  i.e. `~call[d][f][r] + ~xs[f][w][s] >= 1`.

"Blocking" = every weekly shift in the model's palette EXCEPT the free/call-compatible states. For Phase 1 the call-compatible weekly states are `{"NCC1", "NCC2", "NF", "Elec", ""}` (off). Everything else (`MICU, NS, Vac, SICU, ...`) blocks. (This list is the model's, and lives in the encoder keyed off the config's shift palette; Phase 2/3 refine which states are "free" vs "working" for the rest rule — Phase 1 only needs the block set.)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_nf_call_tier_foundation.py`:

```python
def _shift_idx(vm, name):
    return vm.shifts.index(name)


def test_micu_week_blocks_call_for_that_fellow():
    cfg = make_nf_config(num_days=14)
    opb, vm = build(cfg)
    # Pin fellow 0 to MICU in week 0 (force the weekly background var true).
    f, w, micu = 0, 0, _shift_idx(vm, "MICU")
    opb.add_unit(vm.xs[f][w][micu])
    res = runner_or_skip().solve(opb, timeout=30)
    assert res.satisfiable          # other fellows still cover call
    a = res.assignment
    # Fellow 0 holds NO call role on any day of week 0 (days 0..6).
    for d in range(7):
        for r in ("NCC1", "NCC2", "NF"):
            assert not a.get(vm.call[d][f][r], False)


def test_non_blocking_week_allows_call():
    cfg = make_nf_config(num_days=14)
    opb, vm = build(cfg)
    # Pin fellow 0 to Elec in week 0 — Elec does NOT block call.
    f, w, elec = 0, 0, _shift_idx(vm, "Elec")
    opb.add_unit(vm.xs[f][w][elec])
    # Also pin fellow 0 to NF on day 0 — must remain satisfiable.
    opb.add_unit(vm.call[0][f]["NF"])
    res = runner_or_skip().solve(opb, timeout=30)
    assert res.satisfiable
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_call_tier_foundation.py -k "micu_week or non_blocking" -v`
Expected: `test_micu_week_blocks_call_for_that_fellow` FAILS (fellow 0 can still take call on a MICU week — seam not yet enforced).

- [ ] **Step 3: Implement the seam**

In `src/parafrost_scheduler/schedule_encoder.py`, add a module-level constant near the other NF constants:

```python
# Weekly background states that DO NOT block the day-granular call tier (NF model).
# Everything else in the palette (MICU, NS, Vac, SICU, ...) blocks call those days.
_NF_CALL_COMPATIBLE_WEEKLY = frozenset({"NCC1", "NCC2", "NF", "Elec", ""})
```

Add the seam helper and call it from inside the flag region (after coverage):

```python
def _encode_call_background_seam(opb, call, xs, config, fellow_names, shift_idx,
                                 num_days, start_dow):
    """A blocking weekly background rotation closes the call tier for that fellow
    on that week's days (e.g. a MICU week blocks the weekends)."""
    opb.add_comment("NF model: background-week gates call availability")
    num_fellows = len(fellow_names)
    blocking_indices = [shift_idx[s] for s in config.shifts
                        if s not in _NF_CALL_COMPATIBLE_WEEKLY and s in shift_idx]
    for d in range(num_days):
        w = _day_to_week(d, start_dow)
        for f in range(num_fellows):
            for s in blocking_indices:
                bg = xs[f][w][s]
                if bg == 0:
                    continue
                for r in CALL_ROLES:
                    # call[d][f][r] => ~xs[f][w][s]
                    opb.weighted_sum_at_least([(-call[d][f][r], 1), (-bg, 1)], 1)
```

Call site (in `build_full_schedule_opb`, right after `_encode_call_tier_coverage(...)`):

```python
        _encode_call_background_seam(
            opb, call, xs, config, fellow_names, shift_idx, num_days, start_dow)
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_call_tier_foundation.py -k "micu_week or non_blocking" -v`
Expected: PASS (both).

- [ ] **Step 5: Full suite**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/ -q`
Expected: 595 passed.

- [ ] **Step 6: Commit**

```bash
git add src/parafrost_scheduler/schedule_encoder.py tests/test_nf_call_tier_foundation.py
git commit -m "feat(nf): background-week->call seam (MICU/NS/Vac block call those days)"
```

---

### Task 5: Weekly↔day link + skip the legacy night layer

**Files:**
- Modify: `src/parafrost_scheduler/schedule_encoder.py` (`build_full_schedule_opb`: gate the legacy night layer off; add `_encode_call_weekly_link`)
- Test: `tests/test_nf_call_tier_foundation.py`

Two concerns:

**(a) Skip the legacy night layer for this model.** When `call_tier_day_granular` is on, the `xn[d][f]` night layer must NOT be built/constrained (NF replaces it). Guard the night-var build and the two night encode calls behind `if not config.call_tier_day_granular:`. With no `xn` vars, `decode_solution`'s night loop yields all-`""` (harmless) — Task 5 step (c) repoints decode at the call tier instead.

**(b) Weekly↔day link (keeps the calendar apples-to-apples).** The weekly `xs` roster must reflect call involvement so the week reads like a Swing-style calendar. Decision (from the spec's "NF is a weekly rotation value like Swing", generalized to all call roles): **the weekly `xs[f][w][role]` for a call role is the OR of that fellow's day-level call vars of that role in week w.** Formally, for role ∈ {NCC1, NCC2, NF} and week w:
`xs[f][w][role] = 1  ⇔  ∃ day d in week w with call[d][f][role] = 1`.
This is two implications per (f, w, role):
- forward: each `call[d][f][role] ⇒ xs[f][w][role]`  (`~call[d][f][role] + xs[f][w][role] >= 1`)
- back: `xs[f][w][role] ⇒ OR_d call[d][f][role]`  (`~xs[f][w][role] + Σ_d call[d][f][role] >= 1`)

Combined with the existing at-most-one-shift-per-week (line 207), a fellow whose week mixes (say) NCC1 and NCC2 days would need both weekly labels true — which at-most-one forbids. **Phase 1 accepts this as a known limitation:** the link makes a week "pure" in its call role (all call days in a week share one role). True multi-role-within-a-week tours are a Phase 3 continuity concern; for Phase 1 the link keeps the weekly display coherent and the model still SAT (a fellow's call days within a week take one role). Document this in a code comment.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_nf_call_tier_foundation.py`:

```python
def test_legacy_night_layer_absent_under_flag():
    cfg = make_nf_config(num_days=14)
    opb, vm = build(cfg)
    # No xn vars allocated for the NF model (NF replaces the night layer).
    assert all(all(v == 0 for v in day) for day in vm.xn) or vm.xn == []


def test_weekly_label_reflects_call_day():
    cfg = make_nf_config(num_days=14)
    opb, vm = build(cfg)
    f, d = 0, 0
    # Force fellow 0 onto NF day 0; the week-0 weekly NF label must follow.
    opb.add_unit(vm.call[d][f]["NF"])
    res = runner_or_skip().solve(opb, timeout=30)
    assert res.satisfiable
    nf_si = vm.shifts.index("NF")
    assert res.assignment.get(vm.xs[f][0][nf_si], False), \
        "weekly NF label must be set when the fellow has an NF call day that week"


def test_decode_surfaces_call_assignments():
    from parafrost_scheduler.schedule_encoder import decode_solution
    cfg = make_nf_config(num_days=14)
    opb, vm = build(cfg)
    res = runner_or_skip().solve(opb, timeout=30)
    assert res.satisfiable
    sol = decode_solution(res.assignment, vm)
    # The call tier surfaces per-day NCC1/NCC2/NF holders (see Step 4 for shape).
    assert len(sol.call_assignments_by_day) == 14
    day0 = sol.call_assignments_by_day[0]
    assert day0["NCC1"] != "" and day0["NF"] != ""
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_call_tier_foundation.py -k "legacy_night or weekly_label or decode_surfaces" -v`
Expected: FAIL — `xn` still built; weekly label not linked; `FullScheduleSolution` has no `call_assignments_by_day`.

- [ ] **Step 3: Gate the legacy night layer**

In `src/parafrost_scheduler/schedule_encoder.py`, wrap the night-var build (lines ~310-319) and the two night encode calls (lines ~324-331) so they only run when the flag is off:

```python
    # 6. Night variables: xn[d][f] — legacy per-night layer (wb7 model).
    #    Skipped for the NF model, where the day-granular call tier's NF role
    #    IS the night coverage.
    xn: list[list[int]] = []
    if not config.call_tier_day_granular:
        opb.add_comment("Night assignment variables")
        _diag_disable_nights = os.environ.get("SCHED_DIAG_DISABLE_NIGHTS") == "1"
        for d in range(num_days):
            xn.append([])
            for f in range(num_fellows):
                if _diag_disable_nights or fellow_names[f] in config.night_config.ccm_fellows:
                    xn[d].append(0)
                else:
                    xn[d].append(opb.new_var())
        _encode_night_constraints(
            opb, xn, xs, wr, config, fellow_names, shift_idx, soft_violations,
        )
        _encode_night_layer_rules(
            opb, xn, xs, wr, config, fellow_mapping, fellow_names, shift_idx, soft_violations,
        )
```

(Keep the call-tier var build + `_encode_call_tier_coverage` + `_encode_call_background_seam` from Tasks 2-4 in the `if config.call_tier_day_granular:` branch right after. `decode_solution`'s night loop already guards `var != 0`, so an empty `xn` yields all-`""` nights — harmless.)

- [ ] **Step 4: Implement the weekly↔day link**

Add the helper and call it inside the flag region:

```python
def _encode_call_weekly_link(opb, call, xs, shift_idx, fellow_names,
                             num_days, start_dow, num_weeks):
    """Link the weekly roster label to the day-granular call tier so the weekly
    calendar stays readable (apples-to-apples with Swing-style schedules):
        xs[f][w][role] == 1  iff  the fellow has a day of that call role in week w.
    NOTE (Phase 1 limitation): combined with at-most-one-shift-per-week, this makes
    a fellow's call days within a week share ONE role. Multi-role-within-week tours
    are a Phase 3 continuity concern.
    """
    opb.add_comment("NF model: weekly label <=> day-granular call (per role)")
    num_fellows = len(fellow_names)
    # Bucket day indices by week.
    days_in_week: dict[int, list[int]] = {}
    for d in range(num_days):
        days_in_week.setdefault(_day_to_week(d, start_dow), []).append(d)
    for role in CALL_ROLES:
        si = shift_idx.get(role)
        if si is None:
            continue
        for f in range(num_fellows):
            for w, days in days_in_week.items():
                wk_var = xs[f][w][si]
                if wk_var == 0:
                    continue
                day_vars = [call[d][f][role] for d in days]
                # forward: each call day => weekly label
                for cv in day_vars:
                    opb.weighted_sum_at_least([(-cv, 1), (wk_var, 1)], 1)
                # back: weekly label => some call day
                opb.weighted_sum_at_least([(-wk_var, 1)] + [(cv, 1) for cv in day_vars], 1)
```

Call site (in the flag region, after the seam):

```python
        _encode_call_weekly_link(
            opb, call, xs, shift_idx, fellow_names, num_days, start_dow, num_weeks)
```

Requires NCC1/NCC2/NF to be in the model's `shifts` palette (they are — Task 7's config lists them).

- [ ] **Step 5: Surface the call tier in decode**

In `src/parafrost_scheduler/schedule_types.py`, add a field to `FullScheduleSolution`:

```python
    # NF model: per-day call holders, call_assignments_by_day[d] is a dict
    # role-name -> fellow name (or "" if none / role absent that day). Empty list
    # for the wb7 model.
    call_assignments_by_day: list[dict[str, str]] = field(default_factory=list)
```

In `decode_solution` (`src/parafrost_scheduler/schedule_encoder.py` ~line 3726, after the night loop), add:

```python
    # Day-granular call tier (NF model). Empty when var_map.call is [].
    call_by_day: list[dict[str, str]] = []
    for d in range(len(var_map.call)):
        day_holders: dict[str, str] = {}
        for role in CALL_ROLES:
            day_holders[role] = ""
            for f in range(num_fellows):
                var = var_map.call[d][f].get(role, 0)
                if var != 0 and assignment.get(var, False):
                    day_holders[role] = fellow_names[f]
                    break
        call_by_day.append(day_holders)
```

And pass it to the `FullScheduleSolution(...)` return:

```python
        backup_solution=BackupScheduleSolution(assignments_by_week=backup_by_week),
        call_assignments_by_day=call_by_day,
    )
```

- [ ] **Step 6: Run to verify pass**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_call_tier_foundation.py -k "legacy_night or weekly_label or decode_surfaces" -v`
Expected: PASS (all three).

- [ ] **Step 7: Full suite**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/ -q`
Expected: 598 passed (595 + 3 new). The legacy-night gating must NOT change wb7 output (flag off → night layer built exactly as before).

- [ ] **Step 8: Commit**

```bash
git add src/parafrost_scheduler/schedule_encoder.py src/parafrost_scheduler/schedule_types.py tests/test_nf_call_tier_foundation.py
git commit -m "feat(nf): weekly<->day call link + decode; skip legacy night layer under flag"
```

---

### Task 6: New standing + annual config files (the activation)

**Files:**
- Create: `config/standing/ncc-nf-model.yaml`
- Create: `config/annual/ncc-nf-model.yaml`
- Test: `tests/test_nf_call_tier_foundation.py`

The new model is activated by these files (no flag bolted onto wb7 configs). Phase 1 keeps the rule sets minimal — coverage/seam/link are encoder-driven, not YAML-rule-driven.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_nf_call_tier_foundation.py`:

```python
from pathlib import Path
import yaml
from parafrost_scheduler.experiment import assemble_config

_REPO = Path(__file__).resolve().parent.parent


def test_nf_model_configs_assemble_with_flag_on():
    cfg, _ = assemble_config(
        None,
        annual_path=_REPO / "config/annual/ncc-nf-model.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml",
        verbose=False)
    assert cfg.call_tier_day_granular is True
    # Swing is gone; NF and the call roles are present.
    assert "Swing" not in cfg.shifts
    assert {"NCC1", "NCC2", "NF"} <= set(cfg.shifts)
    # Fellow pool: 3 JR + 2 SR + 1 CCM = 6.
    all_fellows = [f for g in cfg.fellow_groups.values() for f in g]
    assert len(all_fellows) == 6
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_call_tier_foundation.py -k configs_assemble -v`
Expected: FAIL — config files do not exist (`FileNotFoundError`).

- [ ] **Step 3: Create the standing config**

Create `config/standing/ncc-nf-model.yaml`:

```yaml
# Standing config — NF MODEL (no-workbook, day-granular call tier).
# Distinct from stanford-fellowship-v3.yaml: Swing is replaced by NF, and the
# day-granular call tier is activated here (config is the only activation channel).

# Shift attributes: which weekly background rotations block call / weekends.
# (Call-tier coverage + the background->call seam are encoder-driven; this palette
# only needs the background-rotation behaviors.)
shift_palette:
  SICU: [night_blocked_all_week, weekend_blocked]
  MICU: [night_blocked_all_week, weekend_blocked]
  NS: [night_blocked_all_week, weekend_blocked]
  Vac: [night_blocked_all_week, weekend_blocked]

# Activation: turn the day-granular call tier ON for this model.
solver_options:
  call_tier_day_granular: true

# Phase 1: no structural weekly rules yet (coverage/seam are encoder-driven).
rules: []
night_rules: []
weekend_rules: []
```

- [ ] **Step 4: Create the annual config**

Create `config/annual/ncc-nf-model.yaml`:

```yaml
# Annual config — NF MODEL. No workbook; fellows listed explicitly.
# CCM pool is parameterized: add "CCM Generic 2" to the CCM group if 1-CCM is too
# tight (see spec supply/demand). Phase 1 ships with 1 CCM.

solver_options:
  call_tier_day_granular: true

fellow_groups:
  NCC_JR:
    - JR1
    - JR2
    - JR3
  NCC_SR:
    - SR1
    - SR2
  CCM:
    - CCM Generic 1

shifts:
  - NCC1
  - NCC2
  - NF
  - MICU
  - NS
  - SICU
  - Elec
  - Vac

horizon_start: '2026-07-01'
num_weeks: 53

rules: []
```

NOTE: `assemble_config(None, ...)` is the no-workbook path (sets `locked_assignments={}`). The `solver_options` block appears in BOTH files; the assembler merges them — keeping it in the annual file makes the activation explicit at the year level too.

- [ ] **Step 5: Run to verify pass**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_call_tier_foundation.py -k configs_assemble -v`
Expected: PASS.

- [ ] **Step 6: Full suite**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/ -q`
Expected: 599 passed.

- [ ] **Step 7: Commit**

```bash
git add config/standing/ncc-nf-model.yaml config/annual/ncc-nf-model.yaml tests/test_nf_call_tier_foundation.py
git commit -m "feat(nf): new standing + annual config files (activate day-granular call tier)"
```

---

### Task 7: Full-year feasibility probe (1 CCM, then 2 if needed)

**Files:**
- Create: `experiments/probe_nf_model.py`
- Create: `experiments/probe_nf_model.slurm`
- (No production code change; this is the integration gate for Phase 1.)

This task is the Phase-1 acceptance gate: does the full 53-week model with hard coverage + seam + link actually SOLVE? Per the no-login-node rule, run on Slurm. The spec flags 1-CCM as tight (~2.6% slack); if UNSAT/timeout, add CCM Generic 2 and re-probe.

- [ ] **Step 1: Write the probe script**

Create `experiments/probe_nf_model.py`:

```python
"""Phase-1 feasibility probe for the NF model (full year). objective=False +
solve() => first model / proven UNSAT (the correct feasibility method)."""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

REPO = Path(__file__).resolve().parent.parent
ROUNDINGSAT = REPO / "vendor/roundingsat/build/roundingsat"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--annual", default=str(REPO / "config/annual/ncc-nf-model.yaml"))
    ap.add_argument("--standing", default=str(REPO / "config/standing/ncc-nf-model.yaml"))
    ap.add_argument("--timeout", type=float, default=3600.0)
    args = ap.parse_args()

    config, _ = assemble_config(None, annual_path=Path(args.annual),
                                standing_path=Path(args.standing), verbose=True)
    opb, _ = build_full_schedule_opb(config, objective=False)
    runner = RoundingSatRunner(ROUNDINGSAT)
    print(f"[nf-probe] constraints={opb.num_constraints} timeout={args.timeout}s", flush=True)
    t0 = time.perf_counter()
    try:
        res = runner.solve(opb, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(f"RESULT UNKNOWN(timeout) elapsed={time.perf_counter()-t0:.1f}s", flush=True)
        return 0
    verdict = "SAT" if res.satisfiable else "UNSAT"
    print(f"RESULT {verdict} elapsed={time.perf_counter()-t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write the Slurm wrapper**

Create `experiments/probe_nf_model.slurm`:

```bash
#!/bin/bash
#SBATCH -A prescient1
#SBATCH -p defq
#SBATCH -n 1
#SBATCH -J nfprobe
#SBATCH -o /cv/scratch/u/watkina6/scheduler/results_slurm/nfprobe_%j.out
#SBATCH -e /cv/scratch/u/watkina6/scheduler/results_slurm/nfprobe_%j.err
#SBATCH --time=02:30:00
set -euo pipefail
REPO=/cv/scratch/u/watkina6/scheduler
cd "$REPO"
export PYTHONPATH="src:new_approach/src"
export PYTHONUNBUFFERED=1
echo "Host: $(hostname)"; echo "Start: $(date)"; echo "---"
$REPO/.venv/bin/python -u experiments/probe_nf_model.py "$@"
echo "---"; echo "End: $(date)"
```

- [ ] **Step 3: Local smoke (build only, tiny timeout)**

Run: `PYTHONPATH=src .venv/bin/python experiments/probe_nf_model.py --timeout 5`
Expected: prints `[nf-probe] constraints=<N>` then `RESULT UNKNOWN(timeout)` (or SAT if it solves in 5s). Confirms the full-year model BUILDS without error. (Do NOT run a long solve on the login node.)

- [ ] **Step 4: Submit the full probe to Slurm**

Run: `sbatch experiments/probe_nf_model.slurm --timeout 7200`
Then poll: `grep -h RESULT results_slurm/nfprobe_*.out` and `squeue -u watkina6 --name=nfprobe`.
Expected: `RESULT SAT`. If `UNSAT` or `UNKNOWN(timeout)`: add `"CCM Generic 2"` to the CCM group in `config/annual/ncc-nf-model.yaml` and re-submit (the spec predicts 1-CCM is tight). Record the verdict.

- [ ] **Step 5: Commit the probe harness**

```bash
git add experiments/probe_nf_model.py experiments/probe_nf_model.slurm
git commit -m "experiments: NF model full-year feasibility probe (Slurm)"
```

---

## Self-review

**Spec coverage (Phase 1 scope):**
- No-workbook model, new configs → Task 6. ✓
- Day-granular call tier NCC1/NCC2/NF → Tasks 2-3. ✓
- Weekday 3-cover / weekend NCC1+NF (no weekend NCC2) → Task 3. ✓
- Background-week gates call (MICU blocks weekends) → Task 4. ✓
- Week stays readable / NF as weekly rotation value → Task 5 link. ✓
- NF replaces the night layer → Task 5 gating. ✓
- Full-year feasibility, CCM-pool parameterized → Task 7. ✓
- **Deferred (correctly, to later phases):** NF run length 4-6 + 2-day rest (Phase 2); call-block continuity + isolated-fragment penalties + service-day quotas + objective (Phase 3). These are NOT Phase-1 gaps.

**Placeholder scan:** none — every step has concrete code/commands.

**Type/name consistency:** `CALL_ROLES` (schedule_types), `call` field on `ScheduleVarMap` and `var_map.call`, `call_assignments_by_day` on `FullScheduleSolution`, `_encode_call_tier_coverage` / `_encode_call_background_seam` / `_encode_call_weekly_link`, `_NF_CALL_COMPATIBLE_WEEKLY`, `make_nf_config`/`build`/`runner_or_skip` in `_nf_helpers` — used consistently across tasks. Test counts assume the suite starts at 587 and increments per task (589/591/593/595/598/599) — adjust if the live baseline differs.

**Known Phase-1 limitations (documented in-code):** the weekly↔day link makes a fellow's call days within a week share one role (multi-role-within-week tours are Phase 3). Acceptable for a readable, feasible foundation.
