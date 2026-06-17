# NF Model — Background Rotations + Workbook Visualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the NF model's NCC fellows a real weekly background-service schedule (MICU/Elec/Vac/Anaesthesia/SICU/NS/Stroke/Telestroke quotas + a 2-CCM block coverage pool), then render the whole schedule through the familiar multi-sheet workbook (weekly per-fellow tab + day-granular call + NF tabs).

**Architecture:** Part A is mostly CONFIG (per-group `shift_total` quotas incl. a `window:` for MICU orientation, `block_rotation` for Anaesthesia/NS/CCM) plus two small ENCODER additions: a general `block_offset` parameter on the block rule (so CCM's first 4-week block can be "long" to absorb the 53-week remainder), and a soft per-CCM-block 6-8 NF-day target. Part B reuses `write_schedule_workbook`, adding a day-granular "Call Detail" sheet and an NF-aware output entry point, retiring the ad-hoc `experiments/render_nf_schedule.py`.

**Tech Stack:** Python 3.12 (`.venv`), `OpbBuilder` pseudo-Boolean, RoundingSat, openpyxl (workbook), pytest. Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest`. Heavy/full-year solves: prefer Slurm, but the NF full-year model solves in <1s so local probing is fine.

**Spec:** `docs/superpowers/specs/2026-06-17-nf-background-rotations-and-workbook-viz-design.md`

## Global Constraints

- **Config is the only activation channel.** No CLI flags / `--variant`. New behavior is config-driven or flag-gated behind `config.call_tier_day_granular`.
- **wb7 path stays byte-equivalent.** Every encoder change is either NF-config-only (new shifts/rules) or, for the shared block encoder, defaults to the EXACT current behavior when the new `block_offset` param is absent (offset 0 == today). wb7 has 608 passing tests as the regression gate at start.
- **Build order: Part A (rotations) fully before Part B (viz).** A produces the schedule B displays.
- **Two generic CCM** (`CCM Generic 1`, `CCM Generic 2`) — full quotas are ~3 fellow-weeks infeasible with one CCM.
- **Hard/soft:** MICU totals + orientation window, Anaesthesia block, SICU, NS block, Stroke, Telestroke, Vac=3 are HARD; Elec is a HARD floor (`at_least 8`) + SOFT target (12 JR / 9 SR); CCM per-block 6-8 NF-day count is SOFT.
- After each task that changes the solved model, re-probe full-year SAT (`PYTHONPATH=src .venv/bin/python experiments/probe_nf_model.py --timeout 180`). On UNSAT, STOP and report — relax the pre-authorized lever (JR MICU 16→14) only with controller sign-off, never weaken silently.

---

## File Structure

**Part A — rotations:**
- `config/annual/ncc-nf-model.yaml` — add `CCM Generic 2` to fellow_groups; expand weekly `shifts` to add `Anaesthesia, Stroke, Telestroke/Clinic`; add the per-group rotation `rules` (shift_total + block_rotation) and the CCM block rule.
- `src/parafrost_scheduler/schedule_encoder.py` — `_encode_all_or_none_block`: add a `block_offset` param (long first block); plus a small `_encode_ccm_block_nf_count` soft helper (6-8 NF days per selected CCM block), called from the call-tier block.
- `src/scheduler/palette_rules.py` — `_convert_block_rotation`: thread the optional `block_offset` (and a `nf_days_per_block` for the CCM soft target) from YAML into params.
- `tests/test_nf_rotations.py` (new) — quota + window + block-offset + CCM-block tests.

**Part B — workbook viz:**
- `src/parafrost_scheduler/workbook.py` — add `_build_call_detail_sheet` (day-granular NCC1/NCC2/NF holders) and an NF-aware `write_nf_workbook(...)` (or extend `write_schedule_workbook` with an `nf_mode`); simplify the NF weekly tab to one weekday cell per fellow.
- `src/parafrost_scheduler/experiment.py` — `solution_to_parsed` already feeds Tab 1; add a small path so `_write_outputs`/the NF output entry produces the NF workbook from `sol.call_assignments_by_day`.
- `tests/test_nf_workbook.py` (new) — structural assertions (sheets present; a known fellow's MICU weeks in Tab 1; day holders in Tab 2).
- DELETE `experiments/render_nf_schedule.py` (retired) and its `output_nf_*` artifacts are git-ignored already.

**Untouched:** wb7 configs; the Phase 1/2 call-tier/run/rest code; `write_schedule_workbook`'s wb7 behavior (the NF path is additive).

---

### Task A1: NCC_JR / NCC_SR background rotation quotas + palette + 2nd CCM (config only)

**Files:**
- Modify: `config/annual/ncc-nf-model.yaml`
- Test: `tests/test_nf_rotations.py` (new)

**Interfaces:**
- Consumes: existing `shift_total` rule type (`{type, groups, shifts, relation, count, strength, optional window:[s,e]}`) and `block_rotation` (`{type, groups, shifts, block_size, strength}`), both routing through the weekly `xs` layer (verified: `_encode_shift_total`, `_encode_all_or_none_block`).
- Produces: an NF config whose assembled `ScheduleSolverConfig` carries these constraints + the expanded weekly palette + 6 fellows (3 JR, 2 SR) + 2 CCM. Later tasks (CCM block, viz) rely on `Anaesthesia/Stroke/Telestroke/Clinic` being weekly shifts and `CCM Generic 2` existing.

This task is config-only (no encoder change) — the quota rule types already exist. It deliberately does NOT add the CCM block rule (Task A3) so the JR/SR quotas can be tested in isolation; with 2 CCM unconstrained the model stays feasible.

- [ ] **Step 1: Write the failing test** — `tests/test_nf_rotations.py`:

```python
"""Part-A tests: NCC_JR/NCC_SR background rotation quotas for the NF model."""
from __future__ import annotations

from pathlib import Path
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb, decode_solution
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

_REPO = Path(__file__).resolve().parent.parent
_ROUNDINGSAT = _REPO / "vendor/roundingsat/build/roundingsat"
_ANNUAL = _REPO / "config/annual/ncc-nf-model.yaml"
_STANDING = _REPO / "config/standing/ncc-nf-model.yaml"


def _assemble():
    res = assemble_config(None, annual_path=_ANNUAL, standing_path=_STANDING, verbose=False)
    return res[0] if isinstance(res, tuple) else res


def test_palette_has_rotation_shifts_and_two_ccm():
    cfg = _assemble()
    for s in ("Anaesthesia", "Stroke", "Telestroke/Clinic", "MICU", "SICU", "NS", "Elec", "Vac", "NCC"):
        assert s in cfg.shifts, s
    ccm = cfg.fellow_groups.get("CCM", [])
    assert len(ccm) == 2, ccm
    jr = cfg.fellow_groups.get("NCC_JR", [])
    sr = cfg.fellow_groups.get("NCC_SR", [])
    assert len(jr) == 3 and len(sr) == 2


def test_jr_micu_quota_constraint_present():
    """A NCC_JR MICU shift_total (exactly 16) must be among the assembled constraints."""
    cfg = _assemble()
    micu_totals = [c for c in cfg.constraints
                   if c.kind == "shift_total"
                   and c.shifts and "MICU" in c.shifts.shifts
                   and c.params.get("count") in (14, 16)]
    assert micu_totals, "expected an NCC_JR MICU shift_total rule"


def test_full_year_with_rotations_is_sat():
    if not _ROUNDINGSAT.exists():
        import pytest; pytest.skip("RoundingSat not built")
    cfg = _assemble()
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    res = RoundingSatRunner(_ROUNDINGSAT).solve(opb, timeout=180)
    assert res.satisfiable, "NF model with JR/SR rotations + 2 CCM must be feasible"
```

- [ ] **Step 2: Run, verify FAIL** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_rotations.py -v`. Expected: `test_palette_has_rotation_shifts_and_two_ccm` fails (Anaesthesia/Stroke/Telestroke not in palette, only 1 CCM), `test_jr_micu_quota_constraint_present` fails (no MICU rule yet).

- [ ] **Step 3: Edit `config/annual/ncc-nf-model.yaml`.** (a) Add `CCM Generic 2` to the CCM group. (b) Expand `shifts` to `[NCC, MICU, NS, SICU, Elec, Vac, Anaesthesia, Stroke, Telestroke/Clinic]`. (c) Add the rotation rules under `rules:` (mirror the wb7 shapes verified in the spec). Use these exact rules:

```yaml
rules:
  # --- NCC_JR background rotations ---
  - {type: shift_total, name: "JR MICU total", groups: [NCC_JR], shifts: [MICU], relation: exactly, count: 16, strength: hard}
  - {type: shift_total, name: "JR MICU orientation", groups: [NCC_JR], shifts: [MICU], relation: exactly, count: 4, window: [0, 4], strength: hard}
  - {type: block_rotation, name: "JR Anaesthesia 4wk block", groups: [NCC_JR], shifts: [Anaesthesia], block_size: 4, strength: hard}
  - {type: shift_total, name: "JR Anaesthesia total", groups: [NCC_JR], shifts: [Anaesthesia], relation: exactly, count: 4, strength: hard}
  - {type: shift_total, name: "JR SICU total", groups: [NCC_JR], shifts: [SICU], relation: exactly, count: 4, strength: hard}
  - {type: shift_total, name: "JR Vacation", groups: [NCC_JR], shifts: [Vac], relation: exactly, count: 3, strength: hard}
  - {type: shift_total, name: "JR Elective floor", groups: [NCC_JR], shifts: [Elec], relation: at_least, count: 8, strength: hard}
  - {type: shift_total, name: "JR Elective target", groups: [NCC_JR], shifts: [Elec], relation: exactly, count: 12, strength: soft}
  # --- NCC_SR background rotations ---
  - {type: shift_total, name: "SR MICU total", groups: [NCC_SR], shifts: [MICU], relation: exactly, count: 8, strength: hard}
  - {type: block_rotation, name: "SR NS 2wk block", groups: [NCC_SR], shifts: [NS], block_size: 2, strength: hard}
  - {type: shift_total, name: "SR NS total", groups: [NCC_SR], shifts: [NS], relation: exactly, count: 6, strength: hard}
  - {type: shift_total, name: "SR Stroke total", groups: [NCC_SR], shifts: [Stroke], relation: exactly, count: 2, strength: hard}
  - {type: shift_total, name: "SR Telestroke total", groups: [NCC_SR], shifts: [Telestroke/Clinic], relation: exactly, count: 2, strength: hard}
  - {type: shift_total, name: "SR Vacation", groups: [NCC_SR], shifts: [Vac], relation: exactly, count: 3, strength: hard}
  - {type: shift_total, name: "SR Elective floor", groups: [NCC_SR], shifts: [Elec], relation: at_least, count: 8, strength: hard}
  - {type: shift_total, name: "SR Elective target", groups: [NCC_SR], shifts: [Elec], relation: exactly, count: 9, strength: soft}
```

VERIFY before finalizing: (1) the YAML rule shape matches what `palette_rule_to_constraints`/`_convert_*` expect — `window` is read by `_encode_shift_total` (confirm it reads `constraint.weeks` or a `window` param; the spec found wb7 uses `window: [s,e]` — check `_convert_shift_total` maps it to `constraint.weeks` and that `_encode_shift_total` honors `constraint.weeks`). If the converter expects `window` under a different key, match it. (2) `block_rotation` requires the shift be in the palette (it is now). (3) `Telestroke/Clinic` as a shift name with a slash is acceptable in YAML (quote it if needed: `"Telestroke/Clinic"`).

- [ ] **Step 4: Run, verify PASS** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_rotations.py -v`. All 3 pass. NOTE `test_full_year_with_rotations_is_sat` is the feasibility gate; if it's UNSAT, STOP and report (the JR/SR quotas alone, with 2 unconstrained CCM, should be feasible per the capacity math — UNSAT here signals a rule-shape bug or a tighter-than-expected interaction).

- [ ] **Step 5: Full regression** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/ -q`. wb7 untouched (only the NF config changed). Expect prior count + 3 new.

- [ ] **Step 6: Commit**

```bash
git add config/annual/ncc-nf-model.yaml tests/test_nf_rotations.py
git commit -m "feat(nf): NCC_JR/NCC_SR background rotation quotas + palette + 2nd CCM"
```

---

### Task A2: `block_offset` parameter on the block rule (long first block)

**Files:**
- Modify: `src/parafrost_scheduler/schedule_encoder.py` (`_encode_all_or_none_block`)
- Modify: `src/scheduler/palette_rules.py` (`_convert_block_rotation`)
- Test: `tests/test_nf_rotations.py`

**Interfaces:**
- Consumes: `_encode_all_or_none_block` currently tiles `for block_start in range(0, num_weeks, block_size)` (verified, schedule_encoder.py:1376).
- Produces: an optional `block_offset` int param on `block_rotation` rules → `constraint.params["block_offset"]`. When set to `k>0`, the FIRST block spans weeks `[0, block_size+k)` (long by `k`), then `block_size`-week blocks tile from `block_size+k`. Default (absent/0) = today's behavior EXACTLY (so wb7 byte-equivalent). Task A3's CCM rule uses this.

The CCM coverage needs 13 four-week blocks over 53 weeks; 53 = 5 + 4×12, so the first block is 5 weeks (`block_offset=1`), then 4-week blocks. This is a general, reusable block-rule parameter, not NF-specific.

- [ ] **Step 1: Write the failing test** (append to `tests/test_nf_rotations.py`):

```python
from parafrost_scheduler.schedule_types import ScheduleSolverConfig
# reuse the minimal-config fixture pattern from tests/_nf_helpers (build) for a tiny block test
from _nf_helpers import make_nf_config, build, runner_or_skip
from scheduler.semantic_constraints import (
    SemanticConstraint, ConstraintLifecycle, ConstraintStrength, FellowSelector, ShiftSet)


def _block_rule(block_size, offset, strength=ConstraintStrength.HARD):
    params = {"name": "blk", "block_size": block_size}
    if offset:
        params["block_offset"] = offset
    return SemanticConstraint(
        kind="all_or_none_block", lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=strength, fellows=FellowSelector.by_groups("NCC_JR"),
        shifts=ShiftSet("blk", ("MICU",)), params=params)


def test_block_offset_long_first_block_grid():
    """With block_size=4, offset=1: a fellow on MICU in week 0 must be on it weeks
    0..4 (5-week first block), and a fellow on MICU in week 5 must be on weeks 5..8.
    Pinning MICU in weeks 0 and 5 then forbidding week 4 (inside block 0) is UNSAT."""
    # 9-week horizon (num_days=63), block grid: [0..4],[5..8]
    cfg = make_nf_config(num_days=63, shifts=("MICU", "NCC", "Elec", "Vac"),
                         constraints=[_block_rule(4, 1)])
    opb, vm = build(cfg)
    f = 3  # a JR
    micu = vm.shifts.index("MICU")
    opb.add_unit(vm.xs[f][0][micu])     # MICU week 0 -> block 0 = weeks 0..4 all MICU
    opb.add_unit(-vm.xs[f][4][micu])    # but forbid week 4 -> contradicts the 5-wk block
    res = runner_or_skip().solve(opb, timeout=30)
    assert not res.satisfiable
```

NOTE: verify `make_nf_config` accepts a `constraints=` kwarg (it does) and that fellow index 3 is a NCC_JR in the fixture's default groups (C1,S1,S2,J1,J2,J3 → index 3 = J1, NCC_JR ✓). Confirm the 9-week horizon: num_days=63, start_dow per fixture (start_dow=0 → 9 weeks exactly). Adjust if start_dow differs.

- [ ] **Step 2: Run, verify FAIL** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_rotations.py -k block_offset -v`. Pre-impl: the offset is ignored, blocks tile [0..3],[4..7],[8..] — week 0 and week 4 are in DIFFERENT blocks, so pinning week0-MICU + forbidding week4-MICU is SAT (no 5-week block). Test fails (expected UNSAT, got SAT).

- [ ] **Step 3: Implement the offset** in `_encode_all_or_none_block` (schedule_encoder.py:1359). Replace the block-grid loop:

```python
    block_size = constraint.params.get("block_size", 4)
    block_offset = constraint.params.get("block_offset", 0)
    target_shifts = list(constraint.shifts.shifts) if constraint.shifts else []

    def _block_starts():
        """Yield (start, end) blocks. With block_offset=k, the FIRST block is
        block_size+k weeks long, then block_size-week blocks tile after it.
        offset 0 == the original range(0, num_weeks, block_size) grid."""
        if num_weeks <= 0:
            return
        first_len = block_size + block_offset
        first_end = min(first_len, num_weeks)
        yield 0, first_end
        s = first_end
        while s < num_weeks:
            yield s, min(s + block_size, num_weeks)
            s += block_size

    for shift_name in target_shifts:
        si = shift_idx.get(shift_name)
        if si is None:
            continue
        for f in fellow_indices:
            for block_start, block_end in _block_starts():
                block_len = block_end - block_start
                block_vars = [xs[f][w][si] for w in range(block_start, block_end) if xs[f][w][si] != 0]
                # ... (rest of the existing body UNCHANGED: the not block_vars/len<block_len
                # guard, the sel var, the is_soft/hard PB clauses)
```
Keep the entire existing block-body (selector + soft/hard clauses) verbatim; only the iteration source changed from `range(0,num_weeks,block_size)` to `_block_starts()`. When `block_offset==0`, `_block_starts()` yields exactly `[0,bs),[bs,2bs),…` — identical to the old `range`, so wb7 is byte-equivalent.

- [ ] **Step 4: Thread the param in `_convert_block_rotation`** (palette_rules.py): add `block_offset` to params when present in the rule:
```python
        params={
            "name": name,
            "block_size": rule["block_size"],
            **({"block_offset": rule["block_offset"]} if "block_offset" in rule else {}),
        },
```

- [ ] **Step 5: Run, verify PASS** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_rotations.py -k block_offset -v`.

- [ ] **Step 6: wb7 byte-equivalence check** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/ -q`. The block-grid change must NOT alter any wb7/weekend/backup test (they use no `block_offset`, so the `range`-equivalent path runs). Expect prior count + 1 new, zero regressions. Spot-run `tests/test_dispatch_strength_no_isolated_week.py` and any block_rotation test.

- [ ] **Step 7: Commit**

```bash
git add src/parafrost_scheduler/schedule_encoder.py src/scheduler/palette_rules.py tests/test_nf_rotations.py
git commit -m "feat(nf): block_offset param on block rule (long first block); offset 0 == today"
```

---

### Task A3: CCM 4-week NCC block model + soft 6-8 NF-days-per-block

**Files:**
- Modify: `config/annual/ncc-nf-model.yaml` (add the CCM block rule)
- Modify: `src/parafrost_scheduler/schedule_encoder.py` (new `_encode_ccm_block_nf_count` soft helper, called from the call-tier block)
- Modify: `src/scheduler/palette_rules.py` (`_convert_block_rotation`: thread optional `nf_days_per_block` min/max into params for the soft target)
- Test: `tests/test_nf_rotations.py`

**Interfaces:**
- Consumes: Task A2's `block_offset` (CCM uses `block_size: 4, block_offset: 1` over the generic `NCC` weekly label → 13 blocks: [0-4],[5-8],…,[49-52]); the day-granular `call[d][f]["NF"]` vars (Phase 1); `config.call_tier_day_granular`.
- Produces: CCM `NCC` weeks come in aligned 4-week (first 5-week) blocks; a SOFT penalty when a CCM fellow's selected block has <6 or >8 NF days.

The block STRUCTURE is config (a `block_rotation` over `NCC` for the CCM group, reusing Task A2's offset). The soft NF-day-COUNT target is a new encoder helper because it spans the day-granular `call` vars within each weekly block grid — not expressible as a weekly `shift_total`.

- [ ] **Step 1: Add the CCM block rule to `config/annual/ncc-nf-model.yaml`** `rules:`:

```yaml
  # --- CCM generic coverage: 4-week NCC blocks (first block long: 53 = 5 + 4*12) ---
  - {type: block_rotation, name: "CCM NCC 4wk blocks", groups: [CCM], shifts: [NCC], block_size: 4, block_offset: 1, strength: hard, nf_days_per_block: [6, 8]}
```
The `block_rotation` over `NCC` makes each CCM `NCC` stretch a full aligned block (all-or-none per block). `block_offset: 1` gives the long first block. `nf_days_per_block: [6,8]` is read by Task A3's soft helper (and ignored by the block encoder itself).

- [ ] **Step 2: Write the failing tests** (append to `tests/test_nf_rotations.py`):

```python
def test_ccm_ncc_comes_in_aligned_blocks():
    """A CCM fellow on NCC in week 0 must be NCC for the whole first (5-week) block."""
    cfg = _assemble()
    if not _ROUNDINGSAT.exists():
        import pytest; pytest.skip("RoundingSat not built")
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    # pin a CCM fellow to NCC week 0; assert the block_rotation forces weeks 0..4 NCC
    ccm = cfg.fellow_groups["CCM"][0]
    f = vm.fellow_names.index(ccm)
    ncc = vm.shifts.index("NCC")
    opb.add_unit(vm.xs[f][0][ncc])
    opb.add_unit(-vm.xs[f][3][ncc])     # forbid week 3 (inside first 5-wk block) -> UNSAT
    res = RoundingSatRunner(_ROUNDINGSAT).solve(opb, timeout=120)
    assert not res.satisfiable


def test_ccm_block_nf_count_soft_penalty_registered():
    """The CCM per-block NF-day soft target registers soft-violation vars (so the
    objective can penalize <6 or >8 NF days in a selected CCM block)."""
    cfg = _assemble()
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    # soft_violations is a list of (var, weight); the CCM-NF-count helper appends some.
    # Assert at least one soft violation exists tagged at the CCM-NF weight (use a
    # sentinel weight set in config, or just assert the helper added vars by comparing
    # soft-violation count with/without the helper — simplest: assert > 0 here and rely
    # on the dedicated unit test below for the precise behavior).
    assert len(vm.soft_violations) > 0
```

The first test is the real block guard. For the second (soft NF-count), prefer a PRECISE unit test on the helper over the vague full-model count: build a minimal config with one CCM fellow, one block, pin exactly 5 NF days in the block, assert a penalty var for that block is forced (use the dispatch-style: a sentinel weight, assert `has_soft_weight`). Write that minimal-config version as the primary guard; verify the helper's emitted (var,weight) like the Phase-1 dispatch tests did. (Mirror `tests/_dispatch_helpers.has_soft_weight` if usable, or inspect `vm.soft_violations`.)

- [ ] **Step 3: Run, verify FAIL** — block test fails (no CCM block rule yet → CCM NCC not block-constrained); NF-count test fails (no soft penalty for block NF count). `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_rotations.py -k "ccm" -v`

- [ ] **Step 4: Thread `nf_days_per_block` in `_convert_block_rotation`** (palette_rules.py): add it to params when present (alongside the Task A2 `block_offset`):
```python
            **({"nf_days_per_block": rule["nf_days_per_block"]} if "nf_days_per_block" in rule else {}),
```

- [ ] **Step 5: Implement `_encode_ccm_block_nf_count`** in schedule_encoder.py. It reads the CCM block_rotation constraint(s) over `NCC` that carry `nf_days_per_block`, reconstructs the same block grid (block_size + block_offset, via a shared helper with `_block_starts` from Task A2 — extract `_block_starts(num_weeks, block_size, block_offset)` to a module function so both reuse it), and for each CCM fellow × block emits a SOFT band on the count of NF call-days in that block's weeks: penalize if `Σ_d∈block nf[d][f]` is <6 or >8. Use the soft-band idiom (a low/high slack var into `soft_violations` at a chosen weight, e.g. a new `config`-level weight or reuse `weekly_soft_weight`). Only emit for the days/weeks where the CCM fellow is actually on an NCC block (gate by the block's `NCC` weekly var, or — simpler and adequate for a soft nudge — emit unconditionally per block since a non-selected block has ~0 NF days and would always penalize; to AVOID penalizing empty blocks, gate the band on the block being selected: only penalize the >8 side always, and the <6 side only when the block has >=1 NF day). Specify the exact gating in the helper; the key correctness property: an UNSELECTED CCM block (0 NF days) must NOT be penalized. Call the helper from the `if config.call_tier_day_granular:` block after the run/rest helpers. Build `call`/`nf` from `call[d][f]["NF"]`.

VERIFY the gating empirically: a CCM fellow with NO NCC block in the year should incur ZERO CCM-NF-count penalty (else every unselected block penalizes and the objective is meaningless). The cleanest gate: let `blk_active` = OR of the block's NCC weekly vars for that fellow; penalize `nf_count < 6` only via `nf_count + 6*(1-blk_active) + slack_lo >= 6` and `nf_count - slack_hi <= 8`. Implement and unit-test both sides.

- [ ] **Step 6: Run, verify PASS** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_rotations.py -k "ccm" -v`.

- [ ] **Step 7: Full-year SAT re-probe** — `PYTHONPATH=src .venv/bin/python experiments/probe_nf_model.py --timeout 180`. Expect `RESULT SAT`. This is the full model with ALL rotations + CCM blocks. If UNSAT: STOP, report; relax JR MICU 16→14 only with controller sign-off.

- [ ] **Step 8: Full regression** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/ -q`, zero wb7 regressions (CCM helper is flag-gated).

- [ ] **Step 9: Commit**

```bash
git add config/annual/ncc-nf-model.yaml src/parafrost_scheduler/schedule_encoder.py src/scheduler/palette_rules.py tests/test_nf_rotations.py
git commit -m "feat(nf): CCM 4-week NCC blocks (long first) + soft 6-8 NF-days/block"
```

---

### Task B1: `write_nf_workbook` — weekly tab + day-granular Call Detail tab

**Files:**
- Modify: `src/parafrost_scheduler/workbook.py` (add `_build_nf_weekly_sheet`, `_build_call_detail_sheet`, `write_nf_workbook`)
- Test: `tests/test_nf_workbook.py` (new)

**Interfaces:**
- Consumes: a solved `FullScheduleSolution` (Phase-1 `decode_solution`) with `.weekly_assignments` (dict fellow→list[str], the generic `NCC`/MICU/Elec/… weekly labels), `.call_assignments_by_day` (list[dict role→fellow] per day), `.backup_solution`; the config's `fellow_groups`, `night_config.horizon_start_date`, `start_dow`. Reuses `_service_color` (workbook.py:114) for cell fills.
- Produces: `write_nf_workbook(sol, config, output_path)` writing an .xlsx with sheets: "Fellow Schedule" (weekly), "Call Detail" (day-granular), "Backup" (if present).

A SEPARATE function from `write_schedule_workbook` (not an overload) — the NF model's sheet shapes differ (no weekend-role/per-night sub-columns; a day-granular call sheet instead). Keeps the wb7 writer untouched.

- [ ] **Step 1: Write the failing test** — `tests/test_nf_workbook.py`:

```python
"""Part-B tests: NF-model workbook rendering."""
from __future__ import annotations

from pathlib import Path
import openpyxl
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb, decode_solution
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.workbook import write_nf_workbook

_REPO = Path(__file__).resolve().parent.parent
_ROUNDINGSAT = _REPO / "vendor/roundingsat/build/roundingsat"


def _solve_nf():
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-model.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = RoundingSatRunner(_ROUNDINGSAT).solve(opb, timeout=180)
    assert r.satisfiable
    return cfg, decode_solution(r.assignment, vm)


def test_nf_workbook_has_expected_sheets(tmp_path):
    if not _ROUNDINGSAT.exists():
        import pytest; pytest.skip("RoundingSat not built")
    cfg, sol = _solve_nf()
    out = tmp_path / "nf.xlsx"
    write_nf_workbook(sol, cfg, out)
    wb = openpyxl.load_workbook(out)
    assert "Fellow Schedule" in wb.sheetnames
    assert "Call Detail" in wb.sheetnames


def test_weekly_tab_shows_a_fellow_rotation(tmp_path):
    if not _ROUNDINGSAT.exists():
        import pytest; pytest.skip("RoundingSat not built")
    cfg, sol = _solve_nf()
    out = tmp_path / "nf.xlsx"
    write_nf_workbook(sol, cfg, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Fellow Schedule"]
    # Every JR fellow does MICU in weeks 0-3 (orientation). Find a JR column and
    # confirm a MICU cell appears in the first 4 week-rows.
    jr = cfg.fellow_groups["NCC_JR"][0]
    # header row holds fellow names; locate jr's column, scan its first 4 data rows
    cells = [c.value for row in ws.iter_rows() for c in row]
    assert "MICU" in cells, "weekly tab must show MICU rotations"


def test_call_detail_tab_shows_day_holders(tmp_path):
    if not _ROUNDINGSAT.exists():
        import pytest; pytest.skip("RoundingSat not built")
    cfg, sol = _solve_nf()
    out = tmp_path / "nf.xlsx"
    write_nf_workbook(sol, cfg, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Call Detail"]
    rows = list(ws.iter_rows(values_only=True))
    # header + 365 day rows; a header naming the call roles
    assert any("NF" in str(c) for c in rows[0])
    assert len(rows) >= 366
```

- [ ] **Step 2: Run, verify FAIL** — `ImportError: cannot import name 'write_nf_workbook'`. `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_workbook.py -v`

- [ ] **Step 3: Implement the three functions** in `src/parafrost_scheduler/workbook.py`:

```python
def _build_nf_weekly_sheet(ws, weekly_assignments, fellow_order, num_weeks):
    """Tab 1: rows=weeks, columns=fellows; each cell = the fellow's weekly label
    (NCC/MICU/Elec/Vac/Anaesthesia/...), colored by _service_color. One cell per
    fellow per week (no weekend/night sub-columns — the NF model's legacy layers
    are empty)."""
    ws.cell(row=1, column=1, value="Week")
    for fi, fellow in enumerate(fellow_order):
        ws.cell(row=1, column=2 + fi, value=fellow)
    for w in range(num_weeks):
        ws.cell(row=2 + w, column=1, value=w)
        for fi, fellow in enumerate(fellow_order):
            val = weekly_assignments[fellow][w]
            cell = ws.cell(row=2 + w, column=2 + fi, value=val)
            color = _service_color(val)
            if color:
                cell.fill = PatternFill(start_color=color, end_color=color, fill_type="solid")


def _build_call_detail_sheet(ws, call_assignments_by_day, horizon_start, start_dow):
    """Tab 2: rows=days; columns = Date, DOW, NCC1, NCC2, NF holders that day."""
    from datetime import timedelta
    dow_names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    ws.append(["Date", "DOW", "NCC1", "NCC2", "NF"])
    for d, holders in enumerate(call_assignments_by_day):
        date = horizon_start + timedelta(days=d)
        dow = dow_names[(start_dow + d) % 7]
        ws.append([date.isoformat(), dow,
                   holders.get("NCC1", ""), holders.get("NCC2", ""), holders.get("NF", "")])


def write_nf_workbook(sol, config, output_path) -> None:
    """Write the NF-model workbook: weekly per-fellow tab + day-granular Call Detail
    (+ Backup if present). Distinct from write_schedule_workbook (wb7)."""
    wb = openpyxl.Workbook()
    fellow_order = list(sol.weekly_assignments.keys())
    num_weeks = len(next(iter(sol.weekly_assignments.values())))
    ws1 = wb.active
    ws1.title = "Fellow Schedule"
    _build_nf_weekly_sheet(ws1, sol.weekly_assignments, fellow_order, num_weeks)
    ws2 = wb.create_sheet(title="Call Detail")
    _build_call_detail_sheet(ws2, sol.call_assignments_by_day,
                             config.night_config.horizon_start_date, config.start_dow)
    if getattr(sol, "backup_solution", None) is not None:
        ws3 = wb.create_sheet(title="Backup")
        _build_backup_coverage_sheet(ws3, _nf_parsed(sol, fellow_order), sol.backup_solution)
    wb.save(output_path)
```

VERIFY: `PatternFill` and `openpyxl` are already imported at the top of workbook.py (they are — the existing sheets use them). `_build_backup_coverage_sheet` takes a `parsed` arg; either build a minimal parsed via the existing `solution_to_parsed` (import from experiment.py) or write a tiny `_nf_parsed` helper, OR drop the backup sheet from B1 and add it in B2 if it complicates — the two REQUIRED sheets are Fellow Schedule + Call Detail. Use judgment; keep B1 focused on those two. Confirm `horizon_start_date` is a `date` on `config.night_config`.

- [ ] **Step 4: Run, verify PASS** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_workbook.py -v`.

- [ ] **Step 5: Full regression** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/ -q`. `write_schedule_workbook` (wb7) untouched; only new functions added.

- [ ] **Step 6: Commit**

```bash
git add src/parafrost_scheduler/workbook.py tests/test_nf_workbook.py
git commit -m "feat(nf): write_nf_workbook — weekly per-fellow tab + day-granular Call Detail"
```

---

### Task B2: Wire NF workbook into the output path; retire the ad-hoc renderer

**Files:**
- Modify: `experiments/render_nf_schedule.py` → replace its body with a thin call to `write_nf_workbook` (or delete it and add a small `experiments/render_nf_workbook.py`); keep one runnable entry point that solves the NF config and writes the .xlsx.
- Test: covered by B1's tests; add one CLI smoke check.

**Interfaces:**
- Consumes: `write_nf_workbook(sol, config, output_path)` (Task B1); the existing solve path (`assemble_config` → `build_full_schedule_opb` → `decode_solution`) already in `render_nf_schedule.py`.
- Produces: a single command that emits `output_nf_model.xlsx` for inspection.

- [ ] **Step 1: Replace `experiments/render_nf_schedule.py`** so it solves the NF config and calls `write_nf_workbook(sol, config, Path(args.prefix + ".xlsx"))`, dropping the CSV/ASCII writers (`_write_call_ledger`, `_write_week_grid`, `_write_nf_runs`). Keep the `--annual/--standing/--timeout/--prefix` args and the `_solve` helper. The whole point: ONE inspectable .xlsx, not three ad-hoc text files.

- [ ] **Step 2: Smoke-run it** — `PYTHONPATH=src .venv/bin/python experiments/render_nf_schedule.py --timeout 180 --prefix output_nf_model`. Confirm it prints success and `output_nf_model.xlsx` exists with the expected sheets (`PYTHONPATH=src .venv/bin/python -c "import openpyxl; print(openpyxl.load_workbook('output_nf_model.xlsx').sheetnames)"`).

- [ ] **Step 3: Open the weekly tab and sanity-check** the schedule by reading a few cells (e.g. confirm a JR fellow shows MICU in weeks 0-3, Anaesthesia in a 4-week block, ~16 MICU weeks total). Report a short summary + the file path to the user for inspection.

- [ ] **Step 4: Full regression** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/ -q`.

- [ ] **Step 5: Commit**

```bash
git add experiments/render_nf_schedule.py
git commit -m "refactor(nf): render NF schedule via write_nf_workbook (retire CSV/ASCII renderer)"
```

---

### Task B3: Final review + produce the inspectable schedule

**Files:** none (review + artifact).

- [ ] **Step 1: Whole-implementation review** over the rotations + workbook commit range: feasibility (full-year SAT with all rotations + CCM blocks), wb7 byte-equivalence (block_offset defaults; new workbook fns additive), CCM-block-NF-count gating correctness (unselected blocks not penalized), test non-vacuity, workbook sheet correctness. Address findings.
- [ ] **Step 2: Verify the rendered schedule** meets the quotas: write a quick check that, in the solved schedule, each JR has ≈16 MICU / 4 Anaesthesia (one block) / 3 Vac / ≥8 Elec weeks and MICU in weeks 0-3; each SR has 8 MICU / 6 NS / 2 Stroke / 2 Telestroke / 3 Vac; CCM NCC in 4-week blocks. Report the per-fellow quota table to the user with `output_nf_model.xlsx`.

---

## Self-review

**Spec coverage:**
- NCC_JR quotas (MICU+orientation, Anaesthesia block, SICU, Vac, Elec floor+target) → Task A1. ✓
- NCC_SR quotas (MICU, NS block, Stroke, Telestroke, Vac, Elec) → Task A1. ✓
- Expanded weekly palette + 2nd CCM → Task A1. ✓
- CCM 4-week NCC blocks, first block long → Task A2 (block_offset) + A3 (CCM rule). ✓
- Soft 6-8 NF-days per CCM block → Task A3. ✓
- Capacity (2 CCM) → Task A1 fellow_groups; feasibility re-probed A1/A3. ✓
- Workbook Tab 1 weekly (one cell/fellow), Tab 2 day-granular call → Task B1. ✓
- NF output entry point + retire render_nf_schedule.py → Task B2. ✓
- Tab 3 "NF Runs" — FOLDED into Tab 2 (Call Detail shows the NF holder per day, runs visible by scanning) per the spec's "may fold into Tab 2"; not a separate sheet. Documented here so it's an intentional choice, not a gap.
- Hard/soft split → A1/A3 strengths. ✓

**Placeholder scan:** none — each step has concrete YAML/code/commands. (Task A3 Step 5's helper gating is described precisely with the blk_active gate; the implementer writes the PB clauses following the stated property + verifies the unselected-block-no-penalty invariant.)

**Type/name consistency:** `block_offset` (param), `nf_days_per_block` (param), `_block_starts(num_weeks, block_size, block_offset)` (shared helper, extracted in A2 reused in A3), `_encode_ccm_block_nf_count`, `write_nf_workbook(sol, config, output_path)`, `_build_nf_weekly_sheet`, `_build_call_detail_sheet` — consistent across tasks. `call_assignments_by_day`, `weekly_assignments`, `horizon_start_date` match the Phase-1 `FullScheduleSolution`/config.

**Known intentional choices:** Tab 3 folded into Tab 2; backup sheet optional in B1 (kept simple). Elec = hard floor + soft target (two rules). MICU orientation is per-fellow hard ⇒ no JR call weeks 0-3 (intended).
