# NF Model — Phase 2 (NF Runs + Rest) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make NF assignments come in hard 4–6 consecutive-day runs, with a symmetric ≥2 fully-off-day rest bracketing every run — turning Phase 1's unbounded NF stretches (154-day blobs) into realistic night-float rotations, while keeping the full-year model SAT and the wb7 path untouched.

**Architecture:** Additions on the existing day-granular NF call variable, PLUS one revision to the Phase-1 weekly link. For each fellow `f`, `nf[d] = call[d][f]["NF"]`; a "run" is a maximal block of consecutive `nf=1` days. Phase 2, all gated behind `call_tier_day_granular`:
- **(Task 0 — weekly-label revision):** the weekly roster gets a single GENERIC `NCC` shift; the per-role NCC1/NCC2/NF split lives ONLY in the day layer (`CALL_ROLES`). The Phase-1 per-role link (`xs[f][w][role] ⇔ call days of that role`) is REPLACED by a generic one: `xs[f][w]["NCC"] = 1 ⇔ fellow has ANY call day in week w`. This is what makes a blank day *inside* a call week legitimately "off" (the per-role weekly label was what blocked it). It deliberately allows blank weekday slots within an NCC week — breaking the weekday "fully assigned" invariant, exactly as weekends have always allowed fully-off fellows.
- **(Task 1) min run length 4** — a run START forces the next 3 days NF.
- **(Task 1) max run length 6** — no 7-day all-NF window.
- **(Task 2) symmetric ≥2 fully-off rest** — a run END forces the next 2 days OFF for that fellow, and a run START forces the prior 2 days OFF, where **OFF day** = (fellow holds NO call role that day) AND (fellow's weekly slot is `NCC` or unlabeled — i.e. NOT a working background rotation like MICU/NS/SICU/Elec/Vac). Since blank days inside an NCC week now count as off, this is feasible.

**Tech Stack:** Python 3.12 (`.venv`), `OpbBuilder` pseudo-Boolean, RoundingSat, pytest. Run: `PYTHONPATH=src:tests .venv/bin/python -m pytest`. wb7 regression gate: full suite green (602 at Phase-2 start).

**Spec:** `docs/superpowers/specs/2026-06-16-nf-night-float-model-design.md` (NF run + rest section).
**Builds on:** Phase 1 (`docs/superpowers/plans/2026-06-16-nf-model-phase1-foundation.md`) — the call tier, coverage, weekly link, gating, and `experiments/render_nf_schedule.py` already exist.

**Phasing:** Phase 2 of 3. Phase 3 = call-block continuity penalties + service-day quota bands + objective.

---

## Design invariants (read before implementing)

- **Config is the only activation channel.** All Phase-2 constraints are emitted only when `config.call_tier_day_granular` is True (they live in the existing call-tier block of `build_full_schedule_opb`, or in helpers called only from there). wb7 path byte-equivalent.
- **NF coverage is exactly-one-per-day (Phase 1).** So at the horizon edges, the run/rest rules must handle truncation: a run that would start <4 days before the horizon end, or rest that runs past the horizon, is clamped — encode only the windows fully inside `[0, num_days)`. A run touching day 0 has no "prior" rest to enforce (nothing before the horizon); likewise a run ending at the last day has no "after" rest. Do NOT force phantom days outside the horizon.
- **"Fully off" predicate.** For fellow `f`, day `d`: `off[d][f]` is true iff `f` holds NO call role on day `d` AND `f`'s background weekly rotation for `week(d)` is off/unassigned. In Phase 1 the background is empty (no rotation rules), so `off` reduces to "no call role that day" — but encode the general predicate so Phase 3 rotations don't silently break rest. Elec and Vac are WORKING for rest purposes (NOT off) — only a genuinely empty/unassigned weekly slot counts as off. (See spec: "Only true off/unassigned days serve as the 2-day buffer.")
- **Symmetric rest gates NF<->any other working state, both directions.** Concretely: the 2 buffer days around a run must be `off` (the predicate above). Same-type consecutive NF days within a run are not "switches." A non-NF→non-NF transition never involves NF and is not gated.
- **Feasibility risk.** These are hard constraints on a tight roster (5–6 fellows, exactly-one NF/day). After each constraint is added, re-probe the full-year model for SAT; if a constraint makes it UNSAT, STOP and report (it may need the optional CCM Generic 2, or a spec revisit) rather than forcing it.

---

## File Structure

**Modified (all behind the flag):**
- `src/parafrost_scheduler/schedule_encoder.py` — new helper `_encode_nf_runs_and_rest(opb, call, xs, shift_idx, config, fellow_names, num_days, start_dow)` (or a few small helpers), called from the `if config.call_tier_day_granular:` block in `build_full_schedule_opb` after `_encode_call_weekly_link`. A `_nf_off_indicator(...)` helper builds the per-(day,fellow) "fully off" var.

**Tests:**
- `tests/test_nf_runs_rest.py` — new file (keep Phase-2 tests separate from `test_nf_call_tier_foundation.py`). Uses `tests/_nf_helpers.py` fixtures.

**No production-config change** (the ncc-nf-model configs already activate the tier). No wb7 change.

---

### Task 0: Generic `NCC` weekly label (replaces per-role weekly link)

**Files:**
- Modify: `config/annual/ncc-nf-model.yaml` (weekly `shifts`: drop NCC1/NCC2/NF, add `NCC`)
- Modify: `src/parafrost_scheduler/schedule_encoder.py` (`_encode_call_weekly_link` — rewrite to the generic label)
- Test: `tests/test_nf_call_tier_foundation.py` (update the per-role link test → generic NCC label) and a new generic-label assertion

**Why:** The day layer owns the per-role split (CALL_ROLES = NCC1/NCC2/NF on `call[d][f][role]`); the WEEKLY roster only needs to say "this fellow is on call service this week" via one `NCC` shift. This lets a blank day inside a call week be a legitimate rest day (Task 2 depends on it). NCC1/NCC2/NF are NO LONGER weekly shifts.

- [ ] **Step 1: Update the config palette.** In `config/annual/ncc-nf-model.yaml`, change the weekly `shifts` from `[NCC1, NCC2, NF, MICU, NS, SICU, Elec, Vac]` to `[NCC, MICU, NS, SICU, Elec, Vac]`. (NCC1/NCC2/NF remain day-layer call roles via `CALL_ROLES` in code — they are not weekly shifts.)

- [ ] **Step 2: Write/adjust the failing test.** In `tests/test_nf_call_tier_foundation.py`, the existing `test_weekly_label_reflects_call_day` asserts the weekly `NF` shift label is set when a fellow has an NF call day. REPLACE its intent: a fellow with ANY call day in a week must have the weekly `NCC` label set; a fellow with NO call day that week must NOT. Concretely (using `make_nf_config` whose `shifts` must now include `NCC` and exclude NCC1/NCC2/NF — update the fixture default in `_nf_helpers.py` shifts to `("NCC","MICU","Elec","Vac")` or similar, keeping a non-call shift for tests):

```python
def test_weekly_ncc_label_reflects_any_call_day():
    cfg = make_nf_config(num_days=14)
    opb, vm = build(cfg)
    f = 1
    opb.add_unit(vm.call[0][f]["NF"])     # f has an NF call day in week 0
    res = runner_or_skip().solve(opb, timeout=30)
    assert res.satisfiable
    ncc_si = vm.shifts.index("NCC")
    assert res.assignment.get(vm.xs[f][0][ncc_si], False), \
        "weekly NCC label must be set when the fellow has any call day that week"
```

Also update `test_forbidden_weekly_call_role_forbids_that_call_at_day_tier` if it referenced NF as a weekly shift — under the generic model a forbidden CALL role can't be expressed as a weekly `zero_shifts` on NF (NF isn't weekly anymore). Either retarget it to forbid the `NCC` weekly shift (which should forbid ALL call that week for that fellow) or remove/replace it; use judgment and keep it meaningful (forbidding weekly `NCC` for a fellow in all weeks ⇒ that fellow holds no call any day ⇒ the model must still be SAT with other fellows covering). Document the change.

- [ ] **Step 3: Run, verify FAIL** — the new NCC-label test fails (link still emits per-role labels, and `NCC` may not be a shift yet / `NF` weekly shift gone). `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_call_tier_foundation.py -k "ncc_label or weekly" -v`

- [ ] **Step 4: Rewrite `_encode_call_weekly_link`** (schedule_encoder.py ~166) to the generic label:

```python
def _encode_call_weekly_link(opb, call, xs, shift_idx, fellow_names,
                             num_days, start_dow, num_weeks):
    """Link the GENERIC weekly 'NCC' label to the day-granular call tier so the
    weekly calendar reads like a Swing-style schedule:
        xs[f][w]['NCC'] == 1  iff  the fellow has ANY call day (NCC1/NCC2/NF) in
        week w.
    The per-role split lives only in the day layer (CALL_ROLES). A blank day inside
    an NCC week is therefore a legitimate non-working day (Phase-2 rest depends on
    this). If the palette has no 'NCC' shift (non-NF-model configs never call this),
    do nothing."""
    si = shift_idx.get("NCC")
    if si is None:
        return
    opb.add_comment("NF model: weekly NCC label <=> any day-granular call that week")
    num_fellows = len(fellow_names)
    days_in_week: dict[int, list[int]] = {}
    for d in range(num_days):
        days_in_week.setdefault(_day_to_week(d, start_dow), []).append(d)
    for f in range(num_fellows):
        for w, days in days_in_week.items():
            wk = xs[f][w][si]
            if wk == 0:
                # 'NCC' forbidden for this fellow/week -> forbid ALL call that week.
                for d in days:
                    for role in CALL_ROLES:
                        opb.add_unit(-call[d][f][role])
                continue
            # any call day (any role) => weekly NCC label
            day_call_vars = [call[d][f][role] for d in days for role in CALL_ROLES]
            for cv in day_call_vars:
                opb.weighted_sum_at_least([(-cv, 1), (wk, 1)], 1)
            # weekly NCC label => at least one call day that week
            opb.weighted_sum_at_least([(-wk, 1)] + [(cv, 1) for cv in day_call_vars], 1)
```

- [ ] **Step 5: Update `_nf_helpers.make_nf_config` default shifts** to include `NCC` and exclude NCC1/NCC2/NF from the WEEKLY list (they're day roles). E.g. `shifts=("NCC", "MICU", "Elec", "Vac")`. Verify all existing NF tests that reference weekly shift names still make sense (some asserted `vm.shifts.index("NF")` — those must change to `"NCC"` or be removed). Grep the test file for `.index("NF")` / `.index("NCC1")` and fix.

- [ ] **Step 6: Run, verify PASS** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_call_tier_foundation.py -v` (all green).

- [ ] **Step 7: Full-year SAT + decode check** — `PYTHONPATH=src .venv/bin/python experiments/probe_nf_model.py --timeout 120` → `RESULT SAT`. The decode (`call_assignments_by_day`) is unaffected (day layer unchanged). The week grid in the renderer will now show `bg=NCC` for call weeks.

- [ ] **Step 8: Full regression** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/ -q`, zero wb7 regressions (the link change is flag-gated; the `if si is None: return` guard means non-NF configs without an `NCC` shift are untouched).

- [ ] **Step 9: Commit**

```bash
git add src/parafrost_scheduler/schedule_encoder.py config/annual/ncc-nf-model.yaml tests/_nf_helpers.py tests/test_nf_call_tier_foundation.py
git commit -m "feat(nf): generic weekly NCC label (per-role split is day-layer only)"
```

---

### Task 1: NF run length — min 4, max 6 (hard)

**Files:**
- Modify: `src/parafrost_scheduler/schedule_encoder.py` (new helper `_encode_nf_run_length`, called from the call-tier block after `_encode_call_weekend_backup_exclusion` at ~line 446)
- Test: `tests/test_nf_runs_rest.py` (new)

For each fellow `f`, with `nf[d] = call[d][f]["NF"]`:
- **Min 4 (no run shorter than 4):** a run START at day `d` is `nf[d] ∧ ¬nf[d-1]` (or `d==0`). On a start, the next 3 days must be NF: for offsets 1..3 with `d+off < num_days`, emit `(nf[d] ∧ ¬nf[d-1]) ⇒ nf[d+off]`. As a PB clause: `¬nf[d] + nf[d-1] + nf[d+off] ≥ 1` (with the `nf[d-1]` term dropped when `d==0`). EDGE: if a start at `d` cannot fit 4 days before the horizon end (`d+3 ≥ num_days`), forbid starting a run there — but since coverage forces exactly one NF/day, the cleaner handling is: only emit the forcing clauses for offsets that land in-horizon, AND additionally forbid a run from starting in the last 3 days (`d > num_days-4`) UNLESS it's a continuation — i.e. for `d in [num_days-3, num_days-1]`, emit `nf[d] ⇒ nf[d-1]` (can't start a fresh short run at the tail). Implement the tail rule as: for `d >= num_days-3` and `d >= 1`, `¬nf[d] + nf[d-1] ≥ 1` is too strong (forces continuation always). INSTEAD keep it simple and correct: the min-4 forcing clause `¬nf[d] + nf[d-1] + nf[d+off] ≥ 1` for off in 1..3 where `d+off < num_days`; at the tail where `d+off` would exceed the horizon, the missing clause means a run starting there isn't forced to length 4 — acceptable for Phase 2 (the final partial run may be short). Document this tail allowance in a comment. (Rationale: the year-boundary is artificial; forcing a phantom run past Jun 30 is wrong. A short tail run is the lesser evil and matches how the night layer handled horizon edges.)
- **Max 6 (no run longer than 6):** for every 7-day window fully in-horizon (`d` in `0..num_days-7`), `at_most_k([nf[d..d+6]], 6)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_nf_runs_rest.py`:

```python
"""Phase-2 tests: NF runs are 4-6 consecutive days with a 2-day fully-off rest."""
from __future__ import annotations

from _nf_helpers import make_nf_config, build, runner_or_skip


def _nf_holder(assignment, vm, d):
    for f in range(vm.num_fellows):
        if assignment.get(vm.call[d][f]["NF"], False):
            return f
    return None


def _nf_runs(assignment, vm):
    """List of (fellow, start_day, length) for maximal NF runs in the solution."""
    runs, cur, start = [], None, None
    for d in range(vm.num_days):
        h = _nf_holder(assignment, vm, d)
        if h != cur:
            if cur is not None:
                runs.append((cur, start, d - start))
            cur, start = h, d
    if cur is not None:
        runs.append((cur, start, vm.num_days - start))
    return runs


def test_max_run_is_six():
    cfg = make_nf_config(num_days=28)
    opb, vm = build(cfg)
    res = runner_or_skip().solve(opb, timeout=60)
    assert res.satisfiable
    runs = _nf_runs(res.assignment, vm)
    assert all(length <= 6 for _, _, length in runs), runs


def test_min_run_is_four_except_horizon_tail():
    cfg = make_nf_config(num_days=28)
    opb, vm = build(cfg)
    res = runner_or_skip().solve(opb, timeout=60)
    assert res.satisfiable
    runs = _nf_runs(res.assignment, vm)
    # every run that ends before the last 3 days must be >= 4 long
    for fellow, start, length in runs:
        if start + length < vm.num_days - 2:   # not the truncated tail run
            assert length >= 4, (fellow, start, length, runs)


def test_pinned_three_day_run_is_unsat():
    """A length-exactly-3 NF run (NF at d, d+1, d+2; not d-1, not d+3) is forbidden."""
    cfg = make_nf_config(num_days=28)
    opb, vm = build(cfg)
    f = 1   # an SR fellow (eligible, not week-0 restricted)
    # force a clean isolated 3-run for f at days 7..9, blocked on both sides
    for d in (7, 8, 9):
        opb.add_unit(vm.call[d][f]["NF"])
    opb.add_unit(-vm.call[6][f]["NF"])
    opb.add_unit(-vm.call[10][f]["NF"])
    res = runner_or_skip().solve(opb, timeout=60)
    assert not res.satisfiable
```

VERIFY before finalizing: confirm `vm.num_days` exists on ScheduleVarMap (it does). Confirm fellow index 1 is a non-week-0-restricted fellow in the fixture (fellows are C1,S1,S2,J1,J2,J3 — index 1 = S1, NCC_SR, fine). Confirm days 7..9 are mid-horizon (yes for num_days=28). If the 3-run pin is UNSAT for an unrelated reason (e.g. coverage can't reassign), pick days where it's cleanly isolable; the test must fail PRE-implementation (run length unconstrained → SAT) and pass POST.

- [ ] **Step 2: Run, verify FAIL** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_runs_rest.py -v`. Pre-implementation: `test_max_run_is_six` / `test_min_run_is_four...` may pass trivially if the solver happens to pick short runs, but `test_pinned_three_day_run_is_unsat` MUST fail (currently SAT — no run-length rule). If the max/min tests pass vacuously pre-impl, that's fine; the pinned test is the real red→green guard.

- [ ] **Step 3: Implement `_encode_nf_run_length`** in `schedule_encoder.py` (near the other `_encode_call_*` helpers):

```python
def _encode_nf_run_length(opb, call, fellow_names, num_days):
    """HARD: every NF run is 4-6 consecutive days (NF model). nf[d]=call[d][f]['NF'].
    Min 4: a run start forces the next 3 days NF (clauses only for in-horizon days;
    a run starting in the last 3 days may be a short truncated tail run — the
    year boundary is artificial, so we don't force a phantom run past it).
    Max 6: no 7-day all-NF window."""
    opb.add_comment("NF model: NF runs are 4-6 consecutive days")
    num_fellows = len(fellow_names)
    for f in range(num_fellows):
        nf = [call[d][f]["NF"] for d in range(num_days)]
        # Min 4: (nf[d] AND NOT nf[d-1]) => nf[d+off], off in 1..3, in-horizon.
        for d in range(num_days):
            for off in (1, 2, 3):
                if d + off >= num_days:
                    continue
                terms = [(-nf[d], 1), (nf[d + off], 1)]
                if d - 1 >= 0:
                    terms.append((nf[d - 1], 1))
                opb.weighted_sum_at_least(terms, 1)
        # Max 6: no 7 consecutive NF days.
        for d in range(num_days - 6):
            opb.at_most_k([nf[d + o] for o in range(7)], 6)
```

Call from the call-tier block (after `_encode_call_weekend_backup_exclusion(...)`):
```python
        _encode_nf_run_length(opb, call, fellow_names, num_days)
```

- [ ] **Step 4: Run, verify PASS** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_runs_rest.py -v` (all 3).

- [ ] **Step 5: Full-year SAT re-probe** — `PYTHONPATH=src .venv/bin/python experiments/probe_nf_model.py --timeout 180`. Expect `RESULT SAT`. If UNSAT, STOP and report (run-length on the lean roster may need CCM Generic 2 — do not force).

- [ ] **Step 6: Full regression** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/ -q`. Expect prior count + 3 new, zero wb7 regressions.

- [ ] **Step 7: Commit**

```bash
git add src/parafrost_scheduler/schedule_encoder.py tests/test_nf_runs_rest.py
git commit -m "feat(nf): NF runs are hard 4-6 consecutive days"
```

---

### Task 2: Symmetric ≥2 fully-off rest around every NF run (hard)

**Files:**
- Modify: `src/parafrost_scheduler/schedule_encoder.py` (helpers `_nf_off_indicator` + `_encode_nf_rest`, called from the call-tier block after `_encode_nf_run_length`)
- Test: `tests/test_nf_runs_rest.py`

**The OFF-day indicator.** For fellow `f`, day `d`, build a var `off[d][f]` that is true iff: (i) `f` holds NO call role on day `d` (`¬NCC1 ∧ ¬NCC2 ∧ ¬NF` for that day), AND (ii) `f`'s weekly slot for `week(d)` is NOT a working background rotation. Working background = the weekly shifts that are neither `NCC` nor unlabeled: in the model palette that's `{MICU, NS, SICU, Elec, Vac}` (everything except `NCC`). So condition (ii) = `xs[f][w][s]=0` for every `s ∈ working-background-indices`. NOTE: `NCC` weekly label does NOT disqualify off (a blank day inside an NCC week is off); only a genuine working rotation does. Build `off[d][f]` as a conjunction indicator:
- `off ⇒ ¬call_role` for each of the day's 3 call vars, `off ⇒ ¬xs[f][w][bg]` for each working-background shift bg, AND the reverse (`off` is true when all those are false). Standard AND-of-negations: mint `off`; for each literal `L` in the set {call roles that day, working-bg vars that week}, emit `¬off + ¬L ≥ 1` (off ⇒ ¬L); and emit `off + Σ L ≥ 1` (if all L are false, off must be true). This pins `off` exactly. (Mirror the conjunction-indicator idiom already used elsewhere, e.g. the `on_backup` OR-indicator in `_encode_backup_constraints` — but this is an AND-of-negations.)

**The rest constraints (hard, symmetric).** With `nf[d]=call[d][f]["NF"]`:
- **After a run:** a run END at `d` is `nf[d] ∧ ¬nf[d+1]`. Then `d+1` and `d+2` must be OFF: for `k in (1,2)` with `d+k < num_days`, emit `(nf[d] ∧ ¬nf[d+1]) ⇒ off[d+k]` = `¬nf[d] + nf[d+1] + off[d+k] ≥ 1`. (At the horizon tail where `d+k ≥ num_days`, drop the clause — no phantom rest past the year.)
- **Before a run:** a run START at `d` is `nf[d] ∧ ¬nf[d-1]` (only meaningful for `d ≥ 1`). Then `d-1` and `d-2` must be OFF: for `k in (1,2)` with `d-k ≥ 0`, emit `(nf[d] ∧ ¬nf[d-1]) ⇒ off[d-k]` = `¬nf[d] + nf[d-1] + off[d-k] ≥ 1`. (At the horizon head, drop clauses for `d-k < 0`.)

This gates NF↔any working state both directions: the 2 days adjacent to a run can be neither call (off excludes call) nor a working background rotation (off excludes those) — only blank/NCC-week days. Same-type NF-internal days aren't run boundaries so aren't gated.

- [ ] **Step 1: Write failing tests** (append to `tests/test_nf_runs_rest.py`):

```python
def _holds_any_call(assignment, vm, d, f):
    return any(assignment.get(vm.call[d][f][r], False) for r in ("NCC1", "NCC2", "NF"))


def test_rest_after_run_two_days_no_call():
    cfg = make_nf_config(num_days=28)
    opb, vm = build(cfg)
    res = runner_or_skip().solve(opb, timeout=90)
    assert res.satisfiable
    a = res.assignment
    runs = _nf_runs(a, vm)
    for fellow, start, length in runs:
        end = start + length - 1
        # the 2 days after a (non-tail) run end must have NO call for that fellow
        for k in (1, 2):
            d = end + k
            if d < vm.num_days and start + length < vm.num_days - 2:
                assert not _holds_any_call(a, vm, d, fellow), (fellow, end, d)


def test_pinned_run_then_immediate_call_is_unsat():
    """NF run ending at d, then the SAME fellow on a call role at d+1 -> UNSAT
    (rest requires >=2 off days first)."""
    cfg = make_nf_config(num_days=28)
    opb, vm = build(cfg)
    f = 1
    # force an NF run days 7..11 (length 5, valid), not NF at 6 and 12
    for d in range(7, 12):
        opb.add_unit(vm.call[d][f]["NF"])
    opb.add_unit(-vm.call[6][f]["NF"])
    opb.add_unit(-vm.call[12][f]["NF"])
    # then try to put f on NCC1 call at day 12 (immediately after run end at 11)
    opb.add_unit(vm.call[12][f]["NCC1"])
    res = runner_or_skip().solve(opb, timeout=90)
    assert not res.satisfiable
```

Verify the pin scenario is cleanly isolable (f=1 = S1; days 7..12 mid-horizon). The second test is the real red→green guard (pre-impl: SAT; post: UNSAT). Adjust day indices if needed so the run-length rule from Task 1 doesn't itself make the *setup* UNSAT for an unintended reason (a 5-day run 7..11 is valid; day-12 NCC1 is the only thing that should force UNSAT, via rest).

- [ ] **Step 2: Run, verify FAIL** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_runs_rest.py -k "rest_after or run_then_immediate" -v` (the pinned-immediate-call test must fail = currently SAT).

- [ ] **Step 3: Implement `_nf_off_indicator` + `_encode_nf_rest`.** Add `off` indicator construction and the rest clauses per the design above. `working-background-indices` = `[shift_idx[s] for s in config.shifts if s != "NCC" and s in shift_idx]`. Build `off[d][f]` lazily (only for days referenced by rest clauses, or for all (d,f) — all is simpler and the model is small). Call `_encode_nf_rest(opb, call, xs, shift_idx, config, fellow_names, num_days, start_dow)` from the call-tier block after `_encode_nf_run_length`.

- [ ] **Step 4: Run, verify PASS** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_nf_runs_rest.py -v` (all).

- [ ] **Step 5: Full-year SAT re-probe** — `PYTHONPATH=src .venv/bin/python experiments/probe_nf_model.py --timeout 300`. Expect `RESULT SAT`. **This is the riskiest constraint for feasibility** (rest forces 2 off days around each run on a lean roster). If UNSAT: STOP, report, and we'll consider CCM Generic 2 (the spec's tight-slack escape hatch) or a spec discussion — do NOT weaken the rule silently.

- [ ] **Step 6: Full regression** — `PYTHONPATH=src:tests .venv/bin/python -m pytest tests/ -q`, zero wb7 regressions.

- [ ] **Step 7: Commit**

```bash
git add src/parafrost_scheduler/schedule_encoder.py tests/test_nf_runs_rest.py
git commit -m "feat(nf): symmetric >=2 fully-off rest around every NF run"
```

---

### Task 3: Re-render the schedule + Phase-2 verification

**Files:** none (uses existing `experiments/render_nf_schedule.py`).

- [ ] **Step 1: Regenerate the inspectable schedule** — `PYTHONPATH=src .venv/bin/python experiments/render_nf_schedule.py --timeout 300 --prefix output_nf_phase2`. Produces `output_nf_phase2_{call_ledger.csv,week_grid.txt,nf_runs.txt}`.

- [ ] **Step 2: Eyeball the NF runs file** — confirm every run is 4–6 days (except possibly one truncated tail run at the horizon end), and that consecutive runs by the same/different fellows are separated by ≥2 off days. Confirm the week grid now shows `bg=NCC` for call weeks and blank strips for rest days. Report a short summary (run-length distribution, any tail exception) to the user with the file paths.

- [ ] **Step 3: Final Phase-2 review** — dispatch a whole-implementation reviewer over the Phase-2 commit range (the generic-NCC link, run length, rest) checking: wb7 byte-equivalence (all changes flag-gated), correctness of the off-indicator (it must be pinned exactly — a common bug is a half-defined AND indicator that lets `off` float true), horizon-edge handling (no phantom clauses), and test non-vacuity (the pinned UNSAT tests genuinely bite). Address findings, then mark Phase 2 complete.

---

## Self-review

**Spec coverage (Phase 2 scope):**
- NF runs hard 4–6 consecutive days → Task 1. ✓
- Symmetric ≥2 fully-off rest, both directions → Task 2. ✓
- "Fully off" = no call AND not a working background rotation (NCC-week/blank counts as off; Elec/Vac are working) → Task 2 off-indicator, enabled by Task 0's generic NCC label. ✓
- Weekly calendar stays readable (generic NCC label) → Task 0. ✓
- **Deferred to Phase 3 (correctly):** call-block continuity penalties, isolated-fragment penalties, per-fellow service-day quota bands, the objective. NOT Phase-2 gaps.

**Horizon edges:** run-length and rest clauses are emitted only for in-horizon windows; a truncated tail run is explicitly allowed (no phantom days past Jun 30). Documented in helpers.

**Feasibility:** every hard constraint is followed by a full-year SAT re-probe; UNSAT halts for a CCM-pool / spec decision rather than silent weakening.

**wb7 safety:** all new constraints live in the `if config.call_tier_day_granular:` block or helpers guarded by `if shift_idx.get("NCC") is None: return`; the flag-off path is unchanged.

**Type/name consistency:** `_encode_nf_run_length`, `_nf_off_indicator`, `_encode_nf_rest`, `off[d][f]`, generic `"NCC"` shift, `CALL_ROLES` day-layer roles — consistent across tasks.
