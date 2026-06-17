# NF Density / Elec / Continuity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote the validated density/Elec/continuity levers from the `experiments/nf_optimize.py` prototype into the production encoder + annual config — config-driven, flag-gated, test-guarded — so the NF model produces dense NCC blocks, real elective weeks, and contiguous NCC1, while keeping wb7 byte-equivalent.

**Architecture:** Three new encoder functions gated under `config.call_tier_day_granular`, plus a `nf_service` knob set in `ScheduleSolverConfig` (threaded via `solver_options`). The objective gains soft concentration terms. The hard Elec floor + soft target ride through ordinary config `shift_total` rules. The Slurm sweep (already running) picks the numeric operating point; this plan implements the mechanisms and leaves the chosen scalars as config values.

**Tech Stack:** Python, RoundingSat (pseudo-Boolean OPB), pytest, openpyxl. Solves run on Slurm `defq -A prescient1` (single-CPU).

## Global Constraints

- wb7 production path MUST stay byte-equivalent: every change is config-only or gated behind `config.call_tier_day_granular`. Verify via OPB multiset diff as in prior NF phases.
- Every new HARD constraint gets a red→green guard test: neutering the constraint must flip a specific test to failing (project's VACUOUS-test history, memory `feedback_test_strength_dispatch_gap`).
- Test/run command: `PYTHONPATH=src:tests .venv/bin/python -m pytest`.
- Feasibility probe shape: `build_full_schedule_opb(cfg, objective=False)` + `runner.solve()`. Quality runs: `runner.optimize(opb, time_limit=...)`.
- RoundingSat is single-threaded; heavy solves go to Slurm (`experiments/nf_optimize.slurm`, `experiments/nf_sweep_slurm.sh`).
- Encoder functions for the NF model live in `src/parafrost_scheduler/schedule_encoder.py` and are called from the `if config.call_tier_day_granular:` block (~line 800).
- New `solver_options` keys must be registered in `src/scheduler/solver_bridge.py` (`_SOLVER_OPTION_BOOL_KEYS` for bools, or the `known` set + explicit handling for scalars/enums) or config loading raises ValueError.

## Already shipped this session (NOT in this plan)

Committed at `0e41fc3`, do not re-implement:
- Asymmetric NF rest (1 before / 2 after) in `_encode_nf_rest` + tests.
- MICU 4-week `block_rotation` for NCC_JR/NCC_SR in `config/annual/ncc-nf-model.yaml`.
- Workbook: week-grid Call Detail, fellow coloring, block borders, NCC/Elec service colors + tests.
- The `experiments/nf_optimize.py` prototype (levers as ad-hoc OPB injections) — this plan promotes its logic into the encoder.

---

### Task 1: `nf_service` config knobs

Add the scalar/bool config fields the new encoders read, threaded through `solver_options`. No encoder behavior yet — just plumbing, so later tasks read config instead of hardcoding.

**Files:**
- Modify: `src/parafrost_scheduler/schedule_types.py` (ScheduleSolverConfig, ~line 234)
- Modify: `src/scheduler/solver_bridge.py` (`_SOLVER_OPTION_BOOL_KEYS` ~line 36, `_solver_options_kwargs` ~line 46)
- Test: `tests/test_nf_service_config.py` (create)

**Interfaces:**
- Produces: `ScheduleSolverConfig` fields:
  - `nf_max_consecutive_off: int = 0` (0 = OFF; N = forbid >N consecutive off days)
  - `nf_ncc1_continuity: str = "off"` (`"off"` | `"weekday"` | `"fullweek"`)
  - `nf_ncc_week_penalty: int = 0` (0 = OFF; soft weight per JR/SR NCC week)
  - `nf_ccm_block_penalty: int = 0` (0 = OFF; soft weight per active CCM 4-wk NCC block)
  - `nf_service_day_band: dict | None = None` (None = use hardcoded 75/85,125/135; else `{"NCC_JR":[lo,hi],"NCC_SR":[lo,hi]}`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_nf_service_config.py
from scheduler.solver_bridge import _solver_options_kwargs
import pytest


def test_nf_service_options_parse():
    opts = {
        "call_tier_day_granular": True,
        "nf_max_consecutive_off": 2,
        "nf_ncc1_continuity": "weekday",
        "nf_ncc_week_penalty": 10,
        "nf_ccm_block_penalty": 200,
    }
    kw = _solver_options_kwargs(opts)
    assert kw["nf_max_consecutive_off"] == 2
    assert kw["nf_ncc1_continuity"] == "weekday"
    assert kw["nf_ncc_week_penalty"] == 10
    assert kw["nf_ccm_block_penalty"] == 200


def test_nf_ncc1_continuity_bad_value_raises():
    with pytest.raises(ValueError):
        _solver_options_kwargs({"nf_ncc1_continuity": "sometimes"})


def test_nf_service_defaults_off():
    from parafrost_scheduler.schedule_types import ScheduleSolverConfig
    c = ScheduleSolverConfig()
    assert c.nf_max_consecutive_off == 0
    assert c.nf_ncc1_continuity == "off"
    assert c.nf_ncc_week_penalty == 0
    assert c.nf_ccm_block_penalty == 0
    assert c.nf_service_day_band is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_service_config.py -v`
Expected: FAIL (AttributeError / KeyError — fields and parsing don't exist).

- [ ] **Step 3: Add the dataclass fields**

In `src/parafrost_scheduler/schedule_types.py`, after `ncc_weekend_misalign_penalty` (~line 234), add:

```python
    # --- NF model density / continuity / concentration knobs (all default OFF so
    # the wb7 path and existing NF behavior are unchanged). Set via solver_options. ---
    # Forbid more than N consecutive fully-off days (0 = off). Forces dense NCC blocks.
    nf_max_consecutive_off: int = 0
    # NCC1 contiguity: "off" | "weekday" (constant Mon-Fri) | "fullweek" (all 7 days).
    nf_ncc1_continuity: str = "off"
    # Soft weight per JR/SR NCC-labeled week (0 = off) — concentrates call, frees Elec.
    nf_ncc_week_penalty: int = 0
    # Soft weight per ACTIVE CCM 4-week NCC block (0 = off) — concentrates CCM service.
    nf_ccm_block_penalty: int = 0
    # Per-group NCC service-DAY band override {"NCC_JR":[lo,hi],"NCC_SR":[lo,hi]}.
    # None => the historical hardcoded 75/85 (JR), 125/135 (SR).
    nf_service_day_band: dict | None = None
```

- [ ] **Step 4: Register the keys in solver_bridge**

In `src/scheduler/solver_bridge.py`, add the int/bool keys to `_SOLVER_OPTION_BOOL_KEYS` is wrong (those are bools); instead extend `_solver_options_kwargs`. Add to the `known` set (~line 56) and add explicit handling:

```python
    known = set(_SOLVER_OPTION_BOOL_KEYS) | {
        "night_hard_criteria", "dual_stroke_helena", "stroke_wk2627_toggle",
        "nf_max_consecutive_off", "nf_ncc1_continuity", "nf_ncc_week_penalty",
        "nf_ccm_block_penalty", "nf_service_day_band",
    }
```

Then before `return kwargs` (~line 85):

```python
    for int_key in ("nf_max_consecutive_off", "nf_ncc_week_penalty",
                    "nf_ccm_block_penalty"):
        if int_key in opts:
            kwargs[int_key] = int(opts[int_key])
    if "nf_ncc1_continuity" in opts:
        val = opts["nf_ncc1_continuity"]
        if val not in ("off", "weekday", "fullweek"):
            raise ValueError(
                f"nf_ncc1_continuity must be 'off'|'weekday'|'fullweek', got {val!r}.")
        kwargs["nf_ncc1_continuity"] = val
    if "nf_service_day_band" in opts:
        kwargs["nf_service_day_band"] = opts["nf_service_day_band"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_service_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/parafrost_scheduler/schedule_types.py src/scheduler/solver_bridge.py tests/test_nf_service_config.py
git commit -m "feat(nf): nf_service config knobs (max-off, ncc1-continuity, penalties, day-band)"
```

---

### Task 2: `_encode_nf_max_consecutive_off` (hard density lever) — ✅ DONE, but SUPERSEDED by Task 8

> **STATUS (2026-06-17):** Implemented and committed (`cd85ac7`), but its semantics were
> superseded by the user's corrected intent. The user clarified the rule is about MAXIMUM rest
> (≤2 off days/week), not capping consecutive-off-runs. **Task 8 (Rule A)** is the real rule.
> Leave this code in place — it is gated off by default (`nf_max_consecutive_off=0`), so it is
> harmless and stays unused. The production config (Task 6) does NOT set `nf_max_consecutive_off`.
> Do not re-implement; do not wire it on.

Forbid more than `config.nf_max_consecutive_off` consecutive fully-off days for JR/SR fellows. Local per-window clause (tractable). Reuses the existing `_build_off_indicator`.

**Files:**
- Modify: `src/parafrost_scheduler/schedule_encoder.py` (new fn near `_encode_nf_rest` ~line 360; call site in the gated block ~line 814)
- Test: `tests/test_nf_max_off.py` (create)

**Interfaces:**
- Consumes: `_build_off_indicator(opb, call, xs, shift_idx, config, f, d, start_dow) -> int` (existing), `config.nf_max_consecutive_off`, `config.fellow_groups`.
- Produces: `_encode_nf_max_consecutive_off(opb, call, xs, shift_idx, config, fellow_names, num_days, start_dow) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_nf_max_off.py
from pathlib import Path
import pytest
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

_REPO = Path(__file__).resolve().parent.parent
_RS = _REPO / "vendor/roundingsat/build/roundingsat"


def _runner_or_skip():
    if not _RS.exists():
        pytest.skip("RoundingSat not built")
    return RoundingSatRunner(_RS)


def _cfg(max_off):
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-model.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    object.__setattr__(cfg, "nf_max_consecutive_off", max_off)
    return cfg


def test_max_off_off_by_default_changes_nothing():
    # max_off=0 => constraint emits nothing => still SAT fast
    cfg = _cfg(0)
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = _runner_or_skip().solve(opb, timeout=180)
    assert r.satisfiable


def test_max_off_2_is_satisfiable_and_bounds_off_runs():
    cfg = _cfg(2)
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = _runner_or_skip().solve(opb, timeout=240)
    assert r.satisfiable
    a = r.assignment
    from parafrost_scheduler.schedule_encoder import CALL_ROLES, _build_off_indicator
    # No JR/SR fellow has 3 consecutive days with no call role AND no working bg week.
    # Proxy check: no 3 consecutive days where the fellow holds no call role while the
    # week's generic NCC label is on (i.e. inside call-land). Full off-indicator is
    # internal; assert the weaker, observable property: every JR/SR has <= max_off+? —
    # here just assert solve succeeded and at least one fellow has a call day, proving
    # the constraint did not trivially force everyone off.
    jr = cfg.fellow_groups["NCC_JR"][0]
    fi = vm.fellow_names.index(jr)
    has_call = any(a.get(vm.call[d][fi][r2], False)
                   for d in range(vm.num_days) for r2 in CALL_ROLES)
    assert has_call
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_max_off.py -v`
Expected: FAIL — `nf_max_consecutive_off` not consumed yet (constraint absent; but tests may PASS trivially since they only assert SAT). To make this a genuine red→green, the binding test is Step 3's mutation guard below; write it now too:

```python
def test_max_off_forbids_long_off_stretch():
    """RED->GREEN guard: pin a JR fellow OFF for max_off+1 consecutive days inside a
    non-working stretch; with the constraint active this is UNSAT, proving it bites."""
    cfg = _cfg(2)
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    from parafrost_scheduler.schedule_encoder import CALL_ROLES
    f = vm.fellow_names.index(cfg.fellow_groups["NCC_JR"][0])
    # Force days 5,6,7 to carry NO call role for this fellow, and force the week's
    # NCC label on (so those are "off inside call-land"); 3 consecutive off > max_off=2.
    # Forcing off: pin all call roles 0 on days 5..7.
    for d in (5, 6, 7):
        for r2 in CALL_ROLES:
            opb.add_unit(-vm.call[d][f][r2])
    # Force the containing week's NCC weekly label on so the days count as call-land
    # (NCC weekly var == any call day that week; we instead pin a call day on day 8
    # to relabel the week NCC while leaving 5..7 off -> 3 off days inside an NCC week).
    opb.add_unit(vm.call[8][f]["NCC1"])
    r = _runner_or_skip().solve(opb, timeout=180)
    assert not r.satisfiable
```

(If day indices 5–8 fall in different weeks for the model's `start_dow`, adjust to three consecutive days plus a 4th call day all within one week; the encoder's off-indicator counts a day off when the fellow has no call role that day AND no working background rotation that week. Verify the chosen week is not a forced MICU/Vac week for that fellow.)

- [ ] **Step 3: Implement the encoder function**

In `schedule_encoder.py`, after `_encode_nf_rest` (~line 360):

```python
def _encode_nf_max_consecutive_off(opb, call, xs, shift_idx, config,
                                   fellow_names, num_days, start_dow):
    """HARD: no JR/SR fellow is fully OFF for more than config.nf_max_consecutive_off
    consecutive days. "off" via _build_off_indicator (no call role that day AND not on
    a working background rotation that week; Elec/Vac/MICU count as WORKING). A local
    per-window clause: in each window of (max_off+1) days, >=1 day is NOT off. This
    forces dense NCC blocks without punishing genuine elective/vacation weeks. No-op
    when nf_max_consecutive_off == 0."""
    k = config.nf_max_consecutive_off
    if k <= 0:
        return
    opb.add_comment(f"NF model: no more than {k} consecutive off days (JR/SR)")
    jr = set(config.fellow_groups.get("NCC_JR", []))
    sr = set(config.fellow_groups.get("NCC_SR", []))
    win = k + 1
    for f, name in enumerate(fellow_names):
        if name not in jr and name not in sr:
            continue
        off = [_build_off_indicator(opb, call, xs, shift_idx, config, f, d, start_dow)
               for d in range(num_days)]
        for d in range(num_days - win + 1):
            opb.weighted_sum_at_least([(-off[d + o], 1) for o in range(win)], 1)
```

- [ ] **Step 4: Wire the call site**

In the `if config.call_tier_day_granular:` block, after `_encode_nf_rest(...)` (~line 814):

```python
        _encode_nf_max_consecutive_off(
            opb, call, xs, shift_idx, config, fellow_names, num_days, start_dow)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_max_off.py -v`
Expected: PASS (incl. `test_max_off_forbids_long_off_stretch` UNSAT guard).

- [ ] **Step 6: Verify wb7 byte-equivalence**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/ -k "wb7 or byte or equiv" -q`
Expected: PASS (no change — flag default 0, wb7 has no call tier).

- [ ] **Step 7: Commit**

```bash
git add src/parafrost_scheduler/schedule_encoder.py tests/test_nf_max_off.py
git commit -m "feat(nf): _encode_nf_max_consecutive_off hard density lever (config-gated)"
```

---

### Task 3: `_encode_nf_ncc1_continuity` (NCC1 contiguous blocks)

Force NCC1 constant within a week per fellow — either all 7 days (`fullweek`) or the 5 weekdays (`weekday`, weekend NCC1 free). Hard. No-op when `nf_ncc1_continuity == "off"`.

**Files:**
- Modify: `src/parafrost_scheduler/schedule_encoder.py` (new fn ~line 360; call site ~line 814)
- Test: `tests/test_nf_ncc1_continuity.py` (create)

**Interfaces:**
- Consumes: `config.nf_ncc1_continuity`, `_day_to_week`, `day_of_week` (imported as `_day_of_week`), `vm.call`.
- Produces: `_encode_nf_ncc1_continuity(opb, call, config, fellow_names, num_days, start_dow) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_nf_ncc1_continuity.py
from pathlib import Path
import pytest
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb, decode_solution, _day_to_week, _day_of_week)
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

_REPO = Path(__file__).resolve().parent.parent
_RS = _REPO / "vendor/roundingsat/build/roundingsat"


def _runner_or_skip():
    if not _RS.exists():
        pytest.skip("RoundingSat not built")
    return RoundingSatRunner(_RS)


def _cfg(mode):
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-model.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    object.__setattr__(cfg, "nf_ncc1_continuity", mode)
    return cfg


def test_fullweek_makes_ncc1_week_constant():
    cfg = _cfg("fullweek")
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = _runner_or_skip().solve(opb, timeout=240)
    assert r.satisfiable
    sol = decode_solution(r.assignment, vm)
    # For each week, the set of NCC1 holders across its days is <= 1 fellow.
    days_in_week = {}
    for d in range(vm.num_days):
        days_in_week.setdefault(_day_to_week(d, vm.start_dow), []).append(d)
    for w, days in days_in_week.items():
        holders = {sol.call_assignments_by_day[d]["NCC1"]
                   for d in days if sol.call_assignments_by_day[d]["NCC1"]}
        assert len(holders) <= 1, (w, holders)


def test_weekday_allows_weekend_ncc1_to_differ():
    cfg = _cfg("weekday")
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = _runner_or_skip().solve(opb, timeout=240)
    assert r.satisfiable
    sol = decode_solution(r.assignment, vm)
    # Weekday (Mon-Fri) NCC1 holders within a week are <= 1 fellow; weekend may differ.
    days_in_week = {}
    for d in range(vm.num_days):
        days_in_week.setdefault(_day_to_week(d, vm.start_dow), []).append(d)
    for w, days in days_in_week.items():
        wkday = {sol.call_assignments_by_day[d]["NCC1"]
                 for d in days
                 if _day_of_week(d, vm.start_dow) < 5 and sol.call_assignments_by_day[d]["NCC1"]}
        assert len(wkday) <= 1, (w, wkday)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_ncc1_continuity.py -v`
Expected: FAIL — without the constraint, NCC1 holders vary within a week (`len(holders) > 1`).

- [ ] **Step 3: Implement the encoder function**

In `schedule_encoder.py` after the max-off fn:

```python
def _encode_nf_ncc1_continuity(opb, call, config, fellow_names, num_days, start_dow):
    """HARD: NCC1 is constant across consecutive days within a week, per fellow.
    mode "fullweek" => all 7 days; "weekday" => Mon-Fri only (weekend NCC1 free, which
    permits a weekday NCC1 block, weekend off, then a Monday NF run). With the hard
    coverage (exactly 1 NCC1 holder/day) this makes NCC1 a clean weekly block; NCC2
    stays day-granular and absorbs NF-boundary fragmentation. No-op when mode=="off"."""
    mode = config.nf_ncc1_continuity
    if mode not in ("weekday", "fullweek"):
        return
    opb.add_comment(f"NF model: NCC1 continuity ({mode})")
    days_in_week: dict[int, list[int]] = {}
    for d in range(num_days):
        days_in_week.setdefault(_day_to_week(d, start_dow), []).append(d)
    for f in range(len(fellow_names)):
        for w, days in days_in_week.items():
            days = sorted(days)
            if mode == "weekday":
                days = [d for d in days if _day_of_week(d, start_dow) < 5]
            for a, b in zip(days, days[1:]):
                if b - a != 1:          # only chain truly consecutive calendar days
                    continue
                va, vb = call[a][f]["NCC1"], call[b][f]["NCC1"]
                opb.weighted_sum_at_least([(-va, 1), (vb, 1)], 1)  # va => vb
                opb.weighted_sum_at_least([(-vb, 1), (va, 1)], 1)  # vb => va
```

- [ ] **Step 4: Wire the call site**

After the max-off call (~line 815):

```python
        _encode_nf_ncc1_continuity(
            opb, call, config, fellow_names, num_days, start_dow)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_ncc1_continuity.py -v`
Expected: PASS (both modes).

- [ ] **Step 6: Commit**

```bash
git add src/parafrost_scheduler/schedule_encoder.py tests/test_nf_ncc1_continuity.py
git commit -m "feat(nf): _encode_nf_ncc1_continuity (weekday/fullweek NCC1 blocks, config-gated)"
```

---

### Task 4: Config-drive the service-day band

Replace the hardcoded 75/85, 125/135 in `_encode_nf_service_day_band` with `config.nf_service_day_band` when present (fall back to the historical constants). Discharges the long-standing TODO.

**Files:**
- Modify: `src/parafrost_scheduler/schedule_encoder.py` (`_encode_nf_service_day_band` ~line 366)
- Test: `tests/test_nf_service_band.py` (create)

**Interfaces:**
- Consumes: `config.nf_service_day_band: dict | None`.
- Produces: no signature change (reads from `config`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_nf_service_band.py
from pathlib import Path
import pytest
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb, decode_solution
from parafrost_scheduler.schedule_encoder import CALL_ROLES
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

_REPO = Path(__file__).resolve().parent.parent
_RS = _REPO / "vendor/roundingsat/build/roundingsat"


def _runner_or_skip():
    if not _RS.exists():
        pytest.skip("RoundingSat not built")
    return RoundingSatRunner(_RS)


def _cfg(band):
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-model.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    object.__setattr__(cfg, "nf_service_day_band", band)
    return cfg


def test_tighter_band_caps_service_days():
    # Lower the JR hi to 70 (below the historical 85) and confirm every JR <= 70.
    cfg = _cfg({"NCC_JR": [60, 70], "NCC_SR": [125, 135]})
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = _runner_or_skip().solve(opb, timeout=240)
    assert r.satisfiable
    sol = decode_solution(r.assignment, vm)
    counts = {n: 0 for n in vm.fellow_names}
    for d in range(vm.num_days):
        for role in CALL_ROLES:
            who = sol.call_assignments_by_day[d][role]
            if who:
                counts[who] += 1
    for jr in cfg.fellow_groups["NCC_JR"]:
        assert 60 <= counts[jr] <= 70, (jr, counts[jr])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_service_band.py -v`
Expected: FAIL — band ignored; JR service days land in the old 75–85 range (>70).

- [ ] **Step 3: Implement the override**

In `_encode_nf_service_day_band`, replace the hardcoded branch:

```python
    jr_names = set(config.fellow_groups.get("NCC_JR", []))
    sr_names = set(config.fellow_groups.get("NCC_SR", []))
    band = config.nf_service_day_band or {"NCC_JR": [75, 85], "NCC_SR": [125, 135]}
    jr_lo, jr_hi = band.get("NCC_JR", [75, 85])
    sr_lo, sr_hi = band.get("NCC_SR", [125, 135])

    opb.add_comment("NF model: per-fellow NCC service-day band")
    for f, name in enumerate(fellow_names):
        if name in jr_names:
            lo, hi = jr_lo, jr_hi
        elif name in sr_names:
            lo, hi = sr_lo, sr_hi
        else:
            continue
        if num_days < lo:
            continue
        daily = [call[d][f][role] for d in range(num_days) for role in CALL_ROLES]
        opb.weighted_sum_at_least([(v, 1) for v in daily], lo)
        opb.weighted_sum_at_most([(v, 1) for v in daily], hi)
```

Remove the now-obsolete `TODO: expose lo/hi ...` comment in the docstring.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_service_band.py -v`
Expected: PASS.

- [ ] **Step 5: Run the existing band/NF tests (no regression at default)**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/ -k "nf" -q`
Expected: PASS — default `None` reproduces the historical 75/85,125/135.

- [ ] **Step 6: Commit**

```bash
git add src/parafrost_scheduler/schedule_encoder.py tests/test_nf_service_band.py
git commit -m "feat(nf): config-drive NCC service-day band (nf_service_day_band override)"
```

---

### Task 5: Soft concentration objective (NCC-week + CCM-block penalties)

Add the soft objective terms that the prototype injected — per-JR/SR-NCC-week penalty and per-active-CCM-block penalty — into `build_full_schedule_opb`'s `soft_violations`, so `optimize()` concentrates call and frees Elec. Gated by the two weight knobs (0 = off).

**Files:**
- Modify: `src/parafrost_scheduler/schedule_encoder.py` (new fn ~line 420; call site in gated block, before `weekly_soft_count` is consumed — append to `soft_violations`)
- Test: `tests/test_nf_concentration_obj.py` (create)

**Interfaces:**
- Consumes: `config.nf_ncc_week_penalty`, `config.nf_ccm_block_penalty`, `_block_starts_grid`, `shift_idx["NCC"]`, `xs`, `soft_violations` list.
- Produces: `_encode_nf_concentration_objective(opb, xs, shift_idx, config, fellow_names, num_weeks, soft_violations) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_nf_concentration_obj.py
from pathlib import Path
import pytest
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb

_REPO = Path(__file__).resolve().parent.parent


def _cfg(ncc_w, ccm_w):
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-model.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    object.__setattr__(cfg, "nf_ncc_week_penalty", ncc_w)
    object.__setattr__(cfg, "nf_ccm_block_penalty", ccm_w)
    return cfg


def test_penalties_register_soft_violations():
    cfg = _cfg(10, 200)
    opb, vm = build_full_schedule_opb(cfg, objective=True)
    # The objective must carry penalty terms with weights 10 and 200.
    weights = {w for _, w in vm.soft_violations}
    assert 10 in weights
    assert 200 in weights
    assert opb.has_objective


def test_penalties_off_by_default_add_nothing():
    cfg = _cfg(0, 0)
    opb, vm = build_full_schedule_opb(cfg, objective=True)
    weights = {w for _, w in vm.soft_violations}
    # No penalty terms at weight 10/200 from this feature when both are 0.
    # (Other framework soft weights may exist; assert our specific markers absent.)
    assert 200 not in weights
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_concentration_obj.py -v`
Expected: FAIL — no terms at weight 200 (feature absent).

- [ ] **Step 3: Implement the encoder function**

In `schedule_encoder.py` near `_encode_ccm_block_nf_count`:

```python
def _encode_nf_concentration_objective(opb, xs, shift_idx, config, fellow_names,
                                       num_weeks, soft_violations):
    """SOFT concentration terms (NF model). Two independent knobs (0 = off):
      nf_ncc_week_penalty: per JR/SR NCC-labeled week -> minimize NCC weeks, which frees
        weeks to label Elec (the soft pressure toward dense, fewer call weeks).
      nf_ccm_block_penalty: per ACTIVE CCM 4-week NCC block (blk_active = OR of the block's
        NCC week vars) -> minimize the number of CCM blocks used, concentrating CCM service.
    Appended to soft_violations so build's set_objective(soft_violations) minimizes them."""
    ncc_si = shift_idx.get("NCC")
    if ncc_si is None:
        return
    jr = set(config.fellow_groups.get("NCC_JR", []))
    sr = set(config.fellow_groups.get("NCC_SR", []))
    ccm = set(config.fellow_groups.get("CCM", []))

    w_ncc = config.nf_ncc_week_penalty
    if w_ncc > 0:
        opb.add_comment("NF model: soft penalty per JR/SR NCC week (concentrate -> Elec)")
        for f, name in enumerate(fellow_names):
            if name not in jr and name not in sr:
                continue
            for w in range(num_weeks):
                wk = xs[f][w][ncc_si]
                if wk != 0:
                    soft_violations.append((wk, w_ncc))

    w_ccm = config.nf_ccm_block_penalty
    if w_ccm > 0:
        opb.add_comment("NF model: soft penalty per active CCM 4-wk NCC block")
        for f, name in enumerate(fellow_names):
            if name not in ccm:
                continue
            for bs, be in _block_starts_grid(num_weeks, 4, 1):
                bvars = [xs[f][w][ncc_si] for w in range(bs, be) if xs[f][w][ncc_si] != 0]
                if not bvars:
                    continue
                blk = opb.new_var()
                for bv in bvars:                                   # bv => blk
                    opb.weighted_sum_at_least([(-bv, 1), (blk, 1)], 1)
                opb.weighted_sum_at_least([(-blk, 1)] + [(bv, 1) for bv in bvars], 1)  # blk => some bv
                soft_violations.append((blk, w_ccm))
```

- [ ] **Step 4: Wire the call site**

In the gated call block, after `_encode_ccm_block_nf_count(...)`:

```python
        _encode_nf_concentration_objective(
            opb, xs, shift_idx, config, fellow_names, num_weeks, soft_violations)
```

Note: this must run BEFORE the `if objective and soft_violations: opb.set_objective(...)` at ~line 835 (it already does — the gated block precedes it). The CCM-block aux vars and clauses are emitted regardless of `objective`; only the objective terms matter when optimizing. (Aux vars unused in a feasibility solve are harmless.)

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_concentration_obj.py -v`
Expected: PASS.

- [ ] **Step 6: Verify wb7 byte-equivalence**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/ -k "wb7 or byte or equiv" -q`
Expected: PASS (gated; default weights 0 emit nothing).

- [ ] **Step 7: Commit**

```bash
git add src/parafrost_scheduler/schedule_encoder.py tests/test_nf_concentration_obj.py
git commit -m "feat(nf): soft concentration objective (NCC-week + CCM-block penalties)"
```

---

### Task 6: Wire chosen operating point into config + retire the prototype injections

Set the Slurm-sweep-chosen scalars in `config/annual/ncc-nf-model.yaml` `solver_options`, add the hard Elec floor + soft Elec target rules, and update `experiments/nf_optimize.py` to read the levers from config (delete its ad-hoc OPB injections, so the experiment and production agree).

**Files:**
- Modify: `config/annual/ncc-nf-model.yaml`
- Modify: `experiments/nf_optimize.py`
- Test: `tests/test_nf_model_integration.py` (create — one full-model SAT + property gate)

**Interfaces:**
- Consumes: all of Tasks 1–5.
- Produces: a production config that, solved via `optimize()`, yields dense NCC + real Elec.

> **NOTE:** The exact numbers below (`ELEC_FLOOR`, `NCC1_MODE`, `NCC_W`, `CCM_W`, band) are placeholders to be replaced by the Slurm sweep's chosen operating point (collected by the scheduled wake-up). Use the winning combo from `results_slurm/sweep_*.out`. If the sweep is not yet collected when this task runs, STOP and collect it first.

- [ ] **Step 1: Set solver_options + Elec rules in the annual config**

In `config/annual/ncc-nf-model.yaml`, extend `solver_options` (currently just `call_tier_day_granular: true`):

```yaml
solver_options:
  call_tier_day_granular: true
  nf_max_consecutive_off: 2
  nf_ncc1_continuity: weekday        # or fullweek — per sweep winner
  nf_ncc_week_penalty: 10
  nf_ccm_block_penalty: 200          # per sweep winner
  # nf_service_day_band:             # uncomment to override 75/85,125/135
  #   NCC_JR: [75, 85]
  #   NCC_SR: [125, 135]
```

And add the Elec floor + soft target rules (floor level = sweep-chosen tractable ceiling):

```yaml
  # --- Elec: un-forbid + guarantee a minimum + pull toward the target ---
  - {type: shift_total, name: "JR Elec floor", groups: [NCC_JR], shifts: [Elec], relation: at_least, count: 3, strength: hard}
  - {type: shift_total, name: "SR Elec floor", groups: [NCC_SR], shifts: [Elec], relation: at_least, count: 3, strength: hard}
  - {type: shift_total, name: "JR Elec target", groups: [NCC_JR], shifts: [Elec], relation: at_least, count: 9, strength: soft}
  - {type: shift_total, name: "SR Elec target", groups: [NCC_SR], shifts: [Elec], relation: at_least, count: 9, strength: soft}
```

- [ ] **Step 2: Confirm Elec is no longer force-forbidden**

Run:
```bash
PYTHONPATH=src .venv/bin/python -c "
import yaml
from scheduler.palette_derivations import derive_forbidden_shifts
cfg = yaml.safe_load(open('config/annual/ncc-nf-model.yaml'))
for c in derive_forbidden_shifts(cfg['rules'], cfg['shifts'], cfg['fellow_groups']):
    print(c.params['name'], '->', c.params['zero_shifts'])
"
```
Expected: neither forbidden list contains `Elec`.

- [ ] **Step 3: Simplify the prototype to read config**

In `experiments/nf_optimize.py`, DELETE the inline max-off, NCC1-continuity, NCC-week-penalty, and CCM-block-penalty injections (now in the encoder). The script should just `assemble_config`, `build_full_schedule_opb(cfg, objective=True)`, then `runner.optimize(...)` and report — relying on config. Keep the `--time-limit` and `--prefix` args and the per-fellow report. Keep `_annual_with_elec` only if still used for ad-hoc floor overrides; otherwise remove it.

- [ ] **Step 4: Write the integration test**

```python
# tests/test_nf_model_integration.py
from pathlib import Path
import pytest
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb, decode_solution, _day_to_week, CALL_ROLES)
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

_REPO = Path(__file__).resolve().parent.parent
_RS = _REPO / "vendor/roundingsat/build/roundingsat"


def test_production_nf_config_solves_and_has_elec():
    if not _RS.exists():
        pytest.skip("RoundingSat not built")
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-model.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = RoundingSatRunner(_RS).solve(opb, timeout=300)
    assert r.satisfiable
    sol = decode_solution(r.assignment, vm)
    # Every JR/SR fellow has at least the hard Elec floor (>=3) elective weeks.
    for grp in ("NCC_JR", "NCC_SR"):
        for name in cfg.fellow_groups[grp]:
            elec = sum(1 for l in sol.weekly_assignments[name] if l == "Elec")
            assert elec >= 3, (name, elec)
```

- [ ] **Step 5: Run the integration test**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_model_integration.py -v`
Expected: PASS (feasibility + Elec floor met). If it TIMES OUT, the chosen hard Elec floor is too high for a plain `solve()` — lower the floor per the sweep, or mark the test to use `optimize()` with a longer Slurm-scale limit (document why).

- [ ] **Step 6: Full NF suite + byte-equivalence**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/ -k "nf or wb7 or byte" -q`
Expected: PASS.

- [ ] **Step 7: Render the production workbook for inspection**

Run:
```bash
sbatch experiments/nf_optimize.slurm --time-limit 3600 --prefix output_nf_production
```
Expected: a Slurm job id; on completion `output_nf_production.xlsx` exists with dense NCC blocks, real Elec, contiguous NCC1, colored/bordered call grid.

- [ ] **Step 8: Commit**

```bash
git add config/annual/ncc-nf-model.yaml experiments/nf_optimize.py tests/test_nf_model_integration.py
git commit -m "feat(nf): wire chosen density/Elec/continuity operating point into config"
```

---

### Task 7: CCM concentration decision (deferred — gated on workbook inspection)

**This task is BLOCKED** on the user inspecting the freshly-optimized workbook (Task 6 Step 7) and choosing the coverage-relaxation lever (per the spec §6: soften a weekday slot / trim JR-SR quotas / accept CCM-full). Do NOT implement until the user decides. When unblocked, the likely change is one of:
- Make weekend or 3rd-weekday NCC2 coverage soft on some weeks (frees CCM weeks), OR
- Reduce a JR/SR background quota (more JR/SR call-availability), OR
- Accept both CCM at 53/53 and close this task with no code change.

- [ ] **Step 1:** Present the rendered workbook + the §6 tradeoff numbers to the user; capture the chosen lever.
- [ ] **Step 2:** Implement the chosen relaxation as a config change (or a small gated encoder tweak), with a red→green guard test if it adds a constraint.
- [ ] **Step 3:** Re-render and confirm CCM concentration improved without breaking coverage; commit.

---

## CCM / rest model (added 2026-06-17 after the workbook audit)

The shipped CCM service was audited and found under-constrained: blocks ranged 14–27 service
days, one CCM worked 21 consecutive days, and NF runs bridged 4-week block boundaries. (Rest
rules themselves were fully compliant — these are MISSING constraints, not violations.) The user
specified three corrected rules. Feasibility was proven on Slurm (`experiments/nf_ccm_rules_probe.py`,
jobs 16479775-781): A=SAT 542s, AB=SAT 1025s, AC=SAT 1045s, **ABC=SAT 558s**. B-alone and C-alone
time out, but all three together solve in ~9 min — Rule A's tight per-week off-cap prunes the
search. We always ship all three together, so the solo timeouts are irrelevant.

**Solve location:** these constraints push solve time to ~9 min; production solves run on Slurm
(`experiments/nf_optimize.slurm`), NOT locally. Local feasibility checks of a single rule will
time out — that is expected and not a failure.

### Task 8: Rule A — per-week off-day cap (HARD; supersedes Task 2)

A week has AT MOST 2 days off — EXCEPT the one forced case where the week contains a full NF run
whose entire mandatory rest (1 day before + 2 after) falls in that same week (e.g. `Mon off /
Tue–Fri NF / Sat–Sun off` = 3 off, all mandatory). You never get "NF rest PLUS up to 2
discretionary". "off" via `_build_off_indicator` (Elec/Vac/MICU weeks are working, not off, so
those weeks are unaffected). Applies to ALL call fellows.

**Files:**
- Modify: `src/parafrost_scheduler/schedule_types.py` (add `nf_week_off_cap: int = 0` field, default 0 = OFF)
- Modify: `src/scheduler/solver_bridge.py` (register `nf_week_off_cap` int key — add to `known` set and the int-coercion loop from Task 1)
- Modify: `src/parafrost_scheduler/schedule_encoder.py` (new fn `_encode_nf_week_off_cap`; call site in the gated block after `_encode_nf_rest`)
- Test: `tests/test_nf_week_off_cap.py` (create)

**Interfaces:**
- Consumes: `config.nf_week_off_cap: int` (0=off; the cap, normally 2), `_build_off_indicator`, `_day_to_week`, `vm.call[d][f]["NF"]`.
- Produces: `_encode_nf_week_off_cap(opb, call, xs, shift_idx, config, fellow_names, num_days, start_dow) -> None`.

- [ ] **Step 1: Add the config knob**

In `schedule_types.py`, after the Task-1 NF fields:
```python
    # Max OFF days per week (0 = off). At most this many off days/week, EXCEPT a week
    # holding a full NF run's 3 mandatory rest days may have 3 (all forced). Normally 2.
    nf_week_off_cap: int = 0
```
In `solver_bridge.py`, add `"nf_week_off_cap"` to the `known` set and to the int-coercion loop alongside the Task-1 int keys.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_nf_week_off_cap.py
from pathlib import Path
import pytest
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb, decode_solution, _day_to_week, CALL_ROLES)
from parafrost_scheduler.schedule_types import day_of_week

_REPO = Path(__file__).resolve().parent.parent
_RS = _REPO / "vendor/roundingsat/build/roundingsat"


def _runner_or_skip():
    from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
    if not _RS.exists():
        pytest.skip("RoundingSat not built")
    return RoundingSatRunner(_RS)


def _cfg(cap):
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-model.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    object.__setattr__(cfg, "nf_week_off_cap", cap)
    return cfg


def _off_days_per_week(sol, vm, name):
    """Per week, count days the fellow holds NO call role AND is on no working bg
    rotation (i.e. the week's weekly label is NCC or blank). Returns {week: off_count}."""
    # A fellow is 'off' on day d iff no call role that day AND the week is not a working
    # background week. We approximate working-bg by: weekly label not in {'', 'NCC'}.
    labels = sol.weekly_assignments[name]
    per = {}
    days_in_week = {}
    for d in range(vm.num_days):
        days_in_week.setdefault(_day_to_week(d, vm.start_dow), []).append(d)
    for w, days in days_in_week.items():
        if labels[w] not in ("", "NCC"):
            continue  # working bg week: its days are 'working', not off
        off = 0
        for d in days:
            if not any(sol.call_assignments_by_day[d][r] == name for r in CALL_ROLES):
                off += 1
        per[w] = off
    return per


def test_week_off_cap_2_holds_except_forced_nf_rest_weeks(tmp_path):
    cfg = _cfg(2)
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = _runner_or_skip().solve(opb, timeout=1200)
    assert r.satisfiable
    sol = decode_solution(r.assignment, vm)
    # For every fellow and every NCC/blank week, off-days <= 3, and <= 2 unless the week
    # contains a full in-week NF run (>=3 NF days that week, the forced-rest case).
    for name in vm.fellow_names:
        labels = sol.weekly_assignments[name]
        per = _off_days_per_week(sol, vm, name)
        days_in_week = {}
        for d in range(vm.num_days):
            days_in_week.setdefault(_day_to_week(d, vm.start_dow), []).append(d)
        for w, off in per.items():
            assert off <= 3, (name, w, off)
            if off == 3:
                nf_days = sum(1 for d in days_in_week[w]
                              if sol.call_assignments_by_day[d]["NF"] == name)
                assert nf_days >= 3, (name, w, "3 off but no in-week NF run")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_week_off_cap.py -v`
Expected: FAIL — without the encoder, weeks with >2 (and ==3-without-NF) off days appear.

NOTE: this test SOLVES (5–20 min) and the solve must run on Slurm or with a generous local
timeout. To avoid burning a long solve on a vacuous constraint, ALSO do the build-only vacuity
check in Step 4a below before trusting any solve.

- [ ] **Step 4: Implement the encoder**

In `schedule_encoder.py`, near `_encode_nf_rest`. Port the verified probe logic from
`experiments/nf_ccm_rules_probe.py` (Rule A block), adapted to consume `config.nf_week_off_cap`:

```python
def _encode_nf_week_off_cap(opb, call, xs, shift_idx, config,
                            fellow_names, num_days, start_dow):
    """HARD: at most config.nf_week_off_cap (normally 2) off days per week, EXCEPT a week
    holding a full NF run's 3 mandatory rest days may have cap+1 (all forced). No-op at 0.
    A 'mandatory rest day' = an off day that is the 1 day before a run start, or one of the
    2 days after a run end. off via _build_off_indicator (Elec/Vac/MICU weeks unaffected)."""
    cap = config.nf_week_off_cap
    if cap <= 0:
        return
    opb.add_comment(f"NF model: <= {cap} off days/week (exempt full in-week NF rest)")
    days_in_week = {}
    for d in range(num_days):
        days_in_week.setdefault(_day_to_week(d, start_dow), []).append(d)

    def and_upper(lits):
        v = opb.new_var()
        for lit in lits:
            opb.weighted_sum_at_least([(-v, 1), (lit, 1)], 1)   # v => lit
        return v

    for f in range(len(fellow_names)):
        nf = [call[d][f]["NF"] for d in range(num_days)]
        off = [_build_off_indicator(opb, call, xs, shift_idx, config, f, d, start_dow)
               for d in range(num_days)]
        rest = [None] * num_days
        for d in range(num_days):
            terms = []
            if d + 1 < num_days:
                terms.append(and_upper([off[d], nf[d + 1]]))            # 1 before a start
            if d - 1 >= 0:
                terms.append(and_upper([off[d], nf[d - 1]]))            # 1st after an end
            if d - 2 >= 0:
                nnf = opb.new_var()
                opb.weighted_sum_at_least([(-nnf, 1), (-nf[d - 1], 1)], 1)  # nnf => ~nf[d-1]
                terms.append(and_upper([off[d], nnf, nf[d - 2]]))       # 2nd after an end
            rv = opb.new_var()
            if terms:
                opb.weighted_sum_at_least([(t, 1) for t in terms] + [(-rv, 1)], 0)  # rv <= sum(terms)
            else:
                opb.add_unit(-rv)
            rest[d] = rv
        for w, days in days_in_week.items():
            offs = [off[d] for d in days]
            rests = [rest[d] for d in days]
            extra = opb.new_var()
            # extra can be 1 only with >= cap+1 mandatory rest days that week.
            # MUST be (extra, -(cap+1)) — neg weight on the POSITIVE literal — giving
            # sum(rests) - (cap+1)*extra >= 0. Do NOT write (-extra, cap+1): that emits
            # +(cap+1)*~extra = +(cap+1)*(1-extra), which is VACUOUS (extra unconstrained,
            # solver sets it 1 and the cap never binds). This exact bug shipped in the probe.
            opb.weighted_sum_at_least([(r, 1) for r in rests] + [(extra, -(cap + 1))], 0)
            # sum(off) <= cap + extra
            opb.weighted_sum_at_most([(o, 1) for o in offs] + [(extra, -1)], cap)
```

Call site (gated block, after `_encode_nf_rest`):
```python
        _encode_nf_week_off_cap(
            opb, call, xs, shift_idx, config, fellow_names, num_days, start_dow)
```

- [ ] **Step 4a: Build-only vacuity check (NO solve — seconds)**

Before any solve, confirm the `extra` relaxation actually binds. Build the OPB with
`nf_week_off_cap=2` and print the two constraints emitted for one fellow+week:
```python
# the lower-bound constraint MUST read "... -3 x<extra> >= 0" (negative coeff on the
# POSITIVE extra literal). If it instead reads "+3 ~x<extra> >= 0", the cap is VACUOUS
# (the bug). The upper-bound must read "... -1 x<extra> <= 2".
```
Inspect `opb._constraints` for the week's two lines (see `experiments/nf_ccm_rules_probe.py`
and the recovered check in this task's history). Only proceed to the solve once the signs
are correct. This catches the vacuous-constraint class without a 10-minute solve.

- [ ] **Step 5: Run test to verify it passes (ON SLURM)**

The solve takes ~5–20 min; run it on Slurm, not locally. Submit:
`sbatch -A prescient1 -p defq -n1 --time=01:00:00 --wrap "cd $(pwd) && PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_week_off_cap.py -v"`
then read the job's output file. Expected: PASS.

- [ ] **Step 6: wb7 byte-equivalence**

Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/ -k "wb7 or byte or equiv" -q`
Expected: PASS (no-op at default cap 0).

- [ ] **Step 7: Commit**

```bash
git add src/parafrost_scheduler/schedule_types.py src/scheduler/solver_bridge.py src/parafrost_scheduler/schedule_encoder.py tests/test_nf_week_off_cap.py
git commit -m "feat(nf): Rule A — per-week off-day cap (<=2, exempt full in-week NF rest)"
```

### Task 9: Rule B — max consecutive call days (HARD)

No fellow holds a CALL role (NCC1/NCC2/NF) on more than `config.nf_max_consecutive_call_days`
consecutive days (normally 14 — two full weeks). A background-rotation or off day breaks the
streak. NOT "not-off" (that wrongly counts MICU/Elec weeks and is instant-UNSAT). All call fellows.

**Files:**
- Modify: `src/parafrost_scheduler/schedule_types.py` (add `nf_max_consecutive_call_days: int = 0`)
- Modify: `src/scheduler/solver_bridge.py` (register the int key)
- Modify: `src/parafrost_scheduler/schedule_encoder.py` (new fn `_encode_nf_max_consecutive_call_days`; call site in gated block)
- Test: `tests/test_nf_max_call_days.py` (create)

**Interfaces:**
- Consumes: `config.nf_max_consecutive_call_days: int`, `vm.call[d][f][role]`.
- Produces: `_encode_nf_max_consecutive_call_days(opb, call, fellow_names, num_days, config) -> None`.

- [ ] **Step 1: Config knob** — add `nf_max_consecutive_call_days: int = 0` (schedule_types.py) and register it in solver_bridge.py (known set + int loop).

- [ ] **Step 2: Write the failing test**

```python
# tests/test_nf_max_call_days.py
from pathlib import Path
import pytest
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb, decode_solution, CALL_ROLES)

_REPO = Path(__file__).resolve().parent.parent
_RS = _REPO / "vendor/roundingsat/build/roundingsat"


def _runner_or_skip():
    from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
    if not _RS.exists():
        pytest.skip("RoundingSat not built")
    return RoundingSatRunner(_RS)


def _cfg(cap):
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-model.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    object.__setattr__(cfg, "nf_max_consecutive_call_days", cap)
    return cfg


def test_no_fellow_exceeds_14_consecutive_call_days():
    cfg = _cfg(14)
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = _runner_or_skip().solve(opb, timeout=1200)
    assert r.satisfiable
    sol = decode_solution(r.assignment, vm)
    for name in vm.fellow_names:
        on = [1 if any(sol.call_assignments_by_day[d][role] == name for role in CALL_ROLES)
              else 0 for d in range(vm.num_days)]
        run = 0
        for v in on:
            run = run + 1 if v else 0
            assert run <= 14, (name, "consecutive call days exceeded 14")
```

- [ ] **Step 3: Run to verify it fails** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_max_call_days.py -v`; without the constraint some fellow exceeds 14 (the audit found 21).

- [ ] **Step 4: Implement the encoder**

```python
def _encode_nf_max_consecutive_call_days(opb, call, fellow_names, num_days, config):
    """HARD: no fellow holds a call role on more than config.nf_max_consecutive_call_days
    consecutive days. call-day = OR(NCC1,NCC2,NF) that day; a background/off day breaks the
    streak. Per-window clause: >=1 non-call day in every (cap+1)-day window. No-op at 0."""
    cap = config.nf_max_consecutive_call_days
    if cap <= 0:
        return
    opb.add_comment(f"NF model: <= {cap} consecutive call days")
    win = cap + 1
    for f in range(len(fellow_names)):
        cday = []
        for d in range(num_days):
            roles = [call[d][f][r] for r in CALL_ROLES]
            c = opb.new_var()
            for rv in roles:
                opb.weighted_sum_at_least([(-rv, 1), (c, 1)], 1)               # role => c
            opb.weighted_sum_at_least([(-c, 1)] + [(rv, 1) for rv in roles], 1)  # c => some role
            cday.append(c)
        for d in range(num_days - win + 1):
            opb.weighted_sum_at_least([(-cday[d + o], 1) for o in range(win)], 1)
```
Call site (gated block, after Task 8's call):
```python
        _encode_nf_max_consecutive_call_days(opb, call, fellow_names, num_days, config)
```

- [ ] **Step 5: Run to verify passes** — same command; expect PASS (solve may be minutes).
- [ ] **Step 6: wb7 byte-equivalence** — `-k "wb7 or byte or equiv"`; PASS (no-op at 0).
- [ ] **Step 7: Commit**
```bash
git add src/parafrost_scheduler/schedule_types.py src/scheduler/solver_bridge.py src/parafrost_scheduler/schedule_encoder.py tests/test_nf_max_call_days.py
git commit -m "feat(nf): Rule B — max consecutive call days (default 14)"
```

### Task 10: Rule C — CCM NF runs may not bridge a block boundary (HARD)

A CCM fellow's NF run must lie entirely within one 4-week NCC block (offset-1 grid). At each
block-start day `b` (first day of weeks 5, 9, 13, …), forbid `nf[b-1] AND nf[b]` for CCM fellows.

**Files:**
- Modify: `src/parafrost_scheduler/schedule_types.py` (add `nf_ccm_no_bridge_blocks: bool = False`)
- Modify: `src/scheduler/solver_bridge.py` (add to `_SOLVER_OPTION_BOOL_KEYS`)
- Modify: `src/parafrost_scheduler/schedule_encoder.py` (new fn `_encode_nf_ccm_no_block_bridge`; call site in gated block)
- Test: `tests/test_nf_ccm_no_bridge.py` (create)

**Interfaces:**
- Consumes: `config.nf_ccm_no_bridge_blocks: bool`, `config.fellow_groups["CCM"]`, `_day_to_week`, `vm.call[d][f]["NF"]`. Block grid = `_block_starts_grid(num_weeks, 4, 1)` (the boundaries are its block-start weeks except week 0).
- Produces: `_encode_nf_ccm_no_block_bridge(opb, call, config, fellow_names, num_days, start_dow, num_weeks) -> None`.

- [ ] **Step 1: Config knob** — add `nf_ccm_no_bridge_blocks: bool = False` (schedule_types.py) and add `"nf_ccm_no_bridge_blocks"` to `_SOLVER_OPTION_BOOL_KEYS` in solver_bridge.py.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_nf_ccm_no_bridge.py
from pathlib import Path
import pytest
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb, decode_solution, _day_to_week, _block_starts_grid)

_REPO = Path(__file__).resolve().parent.parent
_RS = _REPO / "vendor/roundingsat/build/roundingsat"


def _runner_or_skip():
    from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
    if not _RS.exists():
        pytest.skip("RoundingSat not built")
    return RoundingSatRunner(_RS)


def _cfg(flag):
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-model.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    object.__setattr__(cfg, "nf_ccm_no_bridge_blocks", flag)
    return cfg


def test_no_ccm_nf_run_bridges_a_block_boundary():
    cfg = _cfg(True)
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = _runner_or_skip().solve(opb, timeout=1200)
    assert r.satisfiable
    sol = decode_solution(r.assignment, vm)
    # boundary day = first day of each block start-week (except week 0)
    starts = [bs for bs, _ in _block_starts_grid(vm.num_weeks, 4, 1)][1:]
    bday = []
    for wk in starts:
        ds = [d for d in range(vm.num_days) if _day_to_week(d, vm.start_dow) == wk]
        if ds:
            bday.append(min(ds))
    for name in cfg.fellow_groups["CCM"]:
        for b in bday:
            if b - 1 >= 0:
                prev = sol.call_assignments_by_day[b - 1]["NF"] == name
                cur = sol.call_assignments_by_day[b]["NF"] == name
                assert not (prev and cur), (name, b, "NF run bridges block boundary")
```

- [ ] **Step 3: Run to verify it fails** — without the constraint the audit found bridging runs (e.g. CCM days 90–91, 202–203). Expect FAIL.

- [ ] **Step 4: Implement the encoder**

```python
def _encode_nf_ccm_no_block_bridge(opb, call, config, fellow_names, num_days,
                                   start_dow, num_weeks):
    """HARD: a CCM fellow's NF run may not bridge a 4-week NCC block boundary. At each
    block-start day b (first day of the block-start weeks from _block_starts_grid, except
    week 0), forbid nf[b-1] AND nf[b]. No-op when nf_ccm_no_bridge_blocks is False."""
    if not config.nf_ccm_no_bridge_blocks:
        return
    opb.add_comment("NF model: CCM NF runs do not bridge a 4-week block boundary")
    ccm = set(config.fellow_groups.get("CCM", []))
    starts = [bs for bs, _ in _block_starts_grid(num_weeks, 4, 1)][1:]
    days_in_week = {}
    for d in range(num_days):
        days_in_week.setdefault(_day_to_week(d, start_dow), []).append(d)
    for f, name in enumerate(fellow_names):
        if name not in ccm:
            continue
        for wk in starts:
            ds = days_in_week.get(wk, [])
            if not ds:
                continue
            b = min(ds)
            if b - 1 >= 0:
                opb.at_most_k([call[b - 1][f]["NF"], call[b][f]["NF"]], 1)
```
Call site (gated block, after Task 9's call):
```python
        _encode_nf_ccm_no_block_bridge(
            opb, call, config, fellow_names, num_days, start_dow, num_weeks)
```

- [ ] **Step 5: Run to verify passes** — same command; expect PASS.
- [ ] **Step 6: wb7 byte-equivalence** — `-k "wb7 or byte or equiv"`; PASS (no-op when flag False).
- [ ] **Step 7: Commit**
```bash
git add src/parafrost_scheduler/schedule_types.py src/scheduler/solver_bridge.py src/parafrost_scheduler/schedule_encoder.py tests/test_nf_ccm_no_bridge.py
git commit -m "feat(nf): Rule C — CCM NF runs do not bridge 4-week block boundaries"
```

> **Task 6 addendum:** when wiring the production config, set in `solver_options`:
> `nf_week_off_cap: 2`, `nf_max_consecutive_call_days: 14`, `nf_ccm_no_bridge_blocks: true`
> (and do NOT set `nf_max_consecutive_off`). The full ABC model solves in ~9 min on Slurm
> (proven SAT), so the integration test (Task 6 Step 5) and production render (Step 7) must
> run via `optimize()` on Slurm, not a local `solve()`.

---

## Self-Review notes

- **Spec coverage:** §1 un-forbid Elec → Task 6 rules + Task 6 Step 2 check. §2 MICU blocks → already shipped. §3 density → originally Task 2 (max-off), SUPERSEDED by Task 8 (Rule A per-week off-cap, the user's corrected intent). §4 NCC1 continuity → Task 3 (production uses `weekday` mode). §4.5 asymmetric rest → already shipped. §5 concentration objective → Task 5. §6 CCM → Task 7 (coverage relax, deferred to inspection) + the CCM/rest model Tasks 8–10. §7 workbook → already shipped. Day-band config-driving → Task 4.
- **CCM/rest model (Tasks 8–10):** Rule A (per-week off-cap, supersedes Task 2), Rule B (max-14-consecutive-call-days), Rule C (no CCM NF bridging block boundary). All proven feasible together on Slurm (ABC SAT 558s); each is gated and no-ops at its default. Production config (Task 6) sets `nf_week_off_cap: 2`, `nf_max_consecutive_call_days: 14`, `nf_ccm_no_bridge_blocks: true`.
- **Tractability:** the full model solves in ~9 min — production solves run on Slurm via `optimize()`, not local `solve()`. Single-rule local checks time out (expected). Task 6 Step 5/7 must use Slurm.
- **Gating:** Tasks 2,3,5,8,9,10 all no-op at their default knob values; encoder tasks include wb7 byte-equivalence steps.
