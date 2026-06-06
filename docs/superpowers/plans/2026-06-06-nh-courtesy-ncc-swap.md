# NH Courtesy Penalties + NCC Weekend-Alignment + Post-Process Swap — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three soft constraints (NH AAN-week call, NH ABPN-week call, NCC1↔NCC2 weekday/weekend misalignment) plus a guarded post-processing weekend-role swap that emits `_workbook_swapped.xlsx`, then launch a third heavy optimization on wb6.

**Architecture:** Two new encoder functions in `schedule_solver.py` push `(slack_var, weight)` pairs onto the shared `soft_violations` list using the established AND-indicator pattern. A pure-Python `swap_ncc_weekend_roles` helper in `experiment.py` rewrites the solved `WeekendScheduleSolution`. `schedule.py::_write_outputs` writes both the as-solved and swapped workbooks. Spec: `docs/superpowers/specs/2026-06-06-nh-courtesy-ncc-swap-design.md`.

**Tech Stack:** Python, RoundingSat pseudo-Boolean solver, pytest. Run from repo root `/cv/scratch/u/watkina6/scheduler` with `PYTHONPATH=src:new_approach/src`. venv at `.venv/bin/python`.

---

## File Structure

- **Modify** `new_approach/src/parafrost_scheduler/schedule_solver.py`
  - Add config fields `nh_aan_week_call_penalty`, `nh_abpn_week_call_penalty`, `ncc_weekend_misalign_penalty` to `ScheduleSolverConfig` (after the variant flags, ~line 274).
  - Add `_encode_nh_courtesy_weeks(...)` near `_encode_pre_aan_forbid` (~line 2153).
  - Call it from `_encode_night_constraints` after the pre-AAN call (~line 2253).
  - Add `_encode_ncc_weekend_alignment(...)` near the end of `_encode_weekend_constraints` (before its closing, ~line 1721) and call it inline there.
- **Modify** `new_approach/src/parafrost_scheduler/experiment.py`
  - Add `swap_ncc_weekend_roles(parsed, weekend_solution, night_solution)` next to `solution_to_parsed` / `write_csv` (~line 146).
- **Modify** `schedule.py`
  - In `_write_outputs` (line 80), also write `{prefix}_workbook_swapped.xlsx`.
- **Create** `tests/test_nh_courtesy_ncc_align.py` — presence tests for Parts A & B.
- **Create** `tests/test_ncc_weekend_swap.py` — unit tests for the Part C swap.

The build-config path (`build_solver_config_from_request`) constructs `ScheduleSolverConfig` with defaults for any field it doesn't set, so the three new fields default in without touching `experiment.py`'s config assembly.

---

## Task 1: Config fields for the three penalty weights

**Files:**
- Modify: `new_approach/src/parafrost_scheduler/schedule_solver.py:273-274`

- [ ] **Step 1: Add the three config fields**

In `ScheduleSolverConfig`, immediately after the `stroke_wk2627_toggle` field (line 274, just before `def __post_init__`), add:

```python
    # --- NH courtesy + NCC alignment soft penalties (set weight 0 to deactivate) ---
    # NH-group fellow on AAN/ABPN that week: per-occurrence soft penalty for each
    # night worked and each weekend role held (we only manage part of their time;
    # their primary fellowship may rely on a light AAN/ABPN week).
    nh_aan_week_call_penalty: int = 100
    nh_abpn_week_call_penalty: int = 100
    # Fellow on weekday NCC1 but Weekend NCC2 (or weekday NCC2 but Weekend NCC1):
    # soft nudge toward weekday/weekend NCC role alignment.
    ncc_weekend_misalign_penalty: int = 10
```

- [ ] **Step 2: Verify it imports and the field exists**

Run: `cd /cv/scratch/u/watkina6/scheduler && PYTHONPATH=src:new_approach/src .venv/bin/python -c "from parafrost_scheduler.schedule_solver import ScheduleSolverConfig as C; print(C.__dataclass_fields__['nh_aan_week_call_penalty'].default, C.__dataclass_fields__['nh_abpn_week_call_penalty'].default, C.__dataclass_fields__['ncc_weekend_misalign_penalty'].default)"`
Expected: `100 100 10`

- [ ] **Step 3: Commit**

```bash
cd /cv/scratch/u/watkina6/scheduler
git add new_approach/src/parafrost_scheduler/schedule_solver.py
git commit -m "feat: config weights for NH courtesy + NCC weekend-alignment penalties"
```

---

## Task 2: NH AAN/ABPN-week courtesy penalty encoder (Part A)

**Files:**
- Modify: `new_approach/src/parafrost_scheduler/schedule_solver.py` (add function ~line 2153, after `_encode_pre_aan_forbid`)
- Test: `tests/test_nh_courtesy_ncc_align.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_nh_courtesy_ncc_align.py`:

```python
"""Presence tests for NH AAN/ABPN courtesy penalties and NCC weekend alignment.

These inspect opb._constraints strings (no solve). They mirror the harness in
tests/test_nh_nhs_aan.py.
"""

from __future__ import annotations

from datetime import date

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.schedule_solver import (
    ScheduleSolverConfig,
    _ROLE_NCC1,
    _ROLE_NCC2,
    _ROLE_STROKE,
    _week_day,
    _encode_nh_courtesy_weeks,
    _encode_ncc_weekend_alignment,
)
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig


def _config(num_days=28, nh_aan=100, nh_abpn=100, ncc_align=10,
            fellow_groups=None, shifts=None):
    night_config = NightSolverConfig(
        total_nights={}, friday_nights={}, total_night_multisets=(),
        friday_night_multisets=(), ccm_fellows=frozenset(), holiday_dates=(),
        horizon_start_date=date(2026, 7, 1),
    )
    weekend_config = WeekendSolverConfig(
        ncc_totals={}, stroke_totals={}, stroke_cohort=(), stroke_cohort_total=None,
        ccm_fellows=frozenset(), always_stroke_eligible=frozenset(),
        telestroke_stroke_eligible=frozenset(), stroke_only_eligible=frozenset(),
        total_weekends={}, weekend_options=None, friday_weekend_options=None,
    )
    return ScheduleSolverConfig(
        fellow_groups=fellow_groups or {"NH": ["Jin"]},
        shifts=shifts or ["NCC1", "NCC2", "AAN", "ABPN", "Elec"], constraints=[],
        night_config=night_config, weekend_config=weekend_config,
        night_hard_criteria=frozenset(), start_dow=0, num_days=num_days,
        nh_aan_week_call_penalty=nh_aan, nh_abpn_week_call_penalty=nh_abpn,
        ncc_weekend_misalign_penalty=ncc_align,
    )


def _make_xs_xn_wr(opb, config, fellow_names):
    nf = len(fellow_names)
    nw = config.num_weeks
    ns = len(config.shifts)
    xs = [[[opb.new_var() for _ in range(ns)] for _ in range(nw)] for _ in range(nf)]
    xn = [[opb.new_var() for _ in range(nf)] for _ in range(config.num_days)]
    wr = [[{f: opb.new_var() for f in range(nf)} for _ in range(3)] for _ in range(nw)]
    return xs, xn, wr


def _links(opb, a, b):
    """Constraints mentioning both var a and var b (ignoring ~ negation)."""
    return [c for c in opb._constraints
            if f"x{a} " in c.replace("~", "") and f"x{b} " in c.replace("~", "")]


class TestNhCourtesyWeeks:
    def test_aan_week_night_and_weekend_penalized_for_nh(self):
        config = _config()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs, xn, wr = _make_xs_xn_wr(opb, config, ["Jin"])
        soft = []
        _encode_nh_courtesy_weeks(opb, xn, wr, xs, config, ["Jin"], shift_idx, soft)

        aan = xs[0][2][shift_idx["AAN"]]  # AAN in week 2
        # A night in week 2 (Fri, dow 4) must produce a soft AND-indicator linked to aan.
        d = _week_day(2, 4, config.start_dow)
        assert _links(opb, aan, xn[d][0]), "AAN-week night must be penalized for NH fellow"
        # A weekend role in week 2 must produce a soft AND-indicator linked to aan.
        assert _links(opb, aan, wr[2][_ROLE_NCC1][0]), "AAN-week weekend role must be penalized"
        # All AAN penalties weigh 100.
        assert soft, "AAN week must add soft penalties"
        assert all(w == 100 for _, w in soft), "AAN courtesy weight must be 100"

    def test_abpn_week_penalized_for_nh(self):
        config = _config()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs, xn, wr = _make_xs_xn_wr(opb, config, ["Jin"])
        soft = []
        _encode_nh_courtesy_weeks(opb, xn, wr, xs, config, ["Jin"], shift_idx, soft)
        abpn = xs[0][1][shift_idx["ABPN"]]  # ABPN in week 1
        d = _week_day(1, 2, config.start_dow)  # Wed night, week 1
        assert _links(opb, abpn, xn[d][0]), "ABPN-week night must be penalized for NH fellow"
        assert soft and all(w == 100 for _, w in soft)

    def test_non_nh_fellow_not_penalized(self):
        config = _config(fellow_groups={"NCC_SR": ["Bob"]})
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs, xn, wr = _make_xs_xn_wr(opb, config, ["Bob"])
        soft = []
        _encode_nh_courtesy_weeks(opb, xn, wr, xs, config, ["Bob"], shift_idx, soft)
        assert soft == [], "Non-NH fellow must not get AAN/ABPN courtesy penalties"

    def test_zero_weight_disables(self):
        config = _config(nh_aan=0, nh_abpn=0)
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs, xn, wr = _make_xs_xn_wr(opb, config, ["Jin"])
        soft = []
        _encode_nh_courtesy_weeks(opb, xn, wr, xs, config, ["Jin"], shift_idx, soft)
        assert soft == [], "Zero weights must add no penalties"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /cv/scratch/u/watkina6/scheduler && PYTHONPATH=src:new_approach/src .venv/bin/python -m pytest tests/test_nh_courtesy_ncc_align.py::TestNhCourtesyWeeks -q`
Expected: FAIL — `ImportError: cannot import name '_encode_nh_courtesy_weeks'`.

- [ ] **Step 3: Implement `_encode_nh_courtesy_weeks`**

In `new_approach/src/parafrost_scheduler/schedule_solver.py`, after `_encode_pre_aan_forbid` ends (line 2152, before `def _encode_night_constraints`), insert:

```python
def _encode_nh_courtesy_weeks(
    opb: OpbBuilder,
    xn: list[list[int]],
    wr: list[list[dict[int, int]]],
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
) -> None:
    """Courtesy penalty: an NH-group fellow on AAN (or ABPN) for a week should
    avoid call that week. We only manage part of the NH fellows' time, and their
    primary fellowship may rely on the AAN/ABPN week being light. Per-occurrence
    SOFT penalty (each night worked + each weekend role held that week). Gated on
    the AAN/ABPN weekly shift var, so only the pinned fellow/week is affected.
    """
    nh_indices = set()
    for name in config.fellow_groups.get("NH", []):
        if name in fellow_names:
            nh_indices.add(fellow_names.index(name))
    if not nh_indices:
        return
    num_weeks = config.num_weeks
    start_dow = config.start_dow
    num_days = config.num_days

    def _and_penalty(a: int, b: int, weight: int) -> None:
        # pen = a AND b: pen >= a + b - 1; pen <= a; pen <= b.
        pen = opb.new_var()
        opb.weighted_sum_at_most([(a, 1), (b, 1), (-pen, 1)], 2)
        opb.weighted_sum_at_least([(a, 1), (-pen, 1)], 1)
        opb.weighted_sum_at_least([(b, 1), (-pen, 1)], 1)
        soft_violations.append((pen, weight))

    for shift_name, weight in (("AAN", config.nh_aan_week_call_penalty),
                               ("ABPN", config.nh_abpn_week_call_penalty)):
        if weight == 0:
            continue
        si = shift_idx.get(shift_name)
        if si is None:
            continue
        for w in range(num_weeks):
            for f in nh_indices:
                gate = xs[f][w][si]
                if gate == 0:
                    continue
                # Each night the fellow works that week.
                for dow in range(7):
                    d = _week_day(w, dow, start_dow)
                    if 0 <= d < num_days and xn[d][f] != 0:
                        _and_penalty(gate, xn[d][f], weight)
                # Each weekend role the fellow holds that week.
                for role_idx in range(3):
                    if f in wr[w][role_idx]:
                        _and_penalty(gate, wr[w][role_idx][f], weight)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /cv/scratch/u/watkina6/scheduler && PYTHONPATH=src:new_approach/src .venv/bin/python -m pytest tests/test_nh_courtesy_ncc_align.py::TestNhCourtesyWeeks -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
cd /cv/scratch/u/watkina6/scheduler
git add new_approach/src/parafrost_scheduler/schedule_solver.py tests/test_nh_courtesy_ncc_align.py
git commit -m "feat: NH AAN/ABPN-week courtesy call penalty (soft, per-occurrence)"
```

---

## Task 3: Wire the courtesy encoder into the night-constraint build

**Files:**
- Modify: `new_approach/src/parafrost_scheduler/schedule_solver.py:2251-2253`

- [ ] **Step 1: Add the call site**

In `_encode_night_constraints`, immediately after the `_encode_pre_aan_forbid(...)` call (line 2253), add:

```python
    # NH courtesy: AAN/ABPN week call penalty (soft, per-occurrence).
    opb.add_comment("Night/Weekend: NH AAN/ABPN-week courtesy penalty")
    _encode_nh_courtesy_weeks(opb, xn, wr, xs, config, fellow_names, shift_idx, soft_violations)
```

(`_encode_night_constraints` already receives `wr` — confirmed at its signature, line 2159.)

- [ ] **Step 2: Verify a full OPB build still constructs (smoke import)**

Run: `cd /cv/scratch/u/watkina6/scheduler && PYTHONPATH=src:new_approach/src .venv/bin/python -c "import parafrost_scheduler.schedule_solver as s; print('ok', hasattr(s, '_encode_nh_courtesy_weeks'))"`
Expected: `ok True`

- [ ] **Step 3: Commit**

```bash
cd /cv/scratch/u/watkina6/scheduler
git add new_approach/src/parafrost_scheduler/schedule_solver.py
git commit -m "feat: call NH courtesy-week penalty from night-constraint build"
```

---

## Task 4: NCC1↔NCC2 weekday/weekend misalignment penalty (Part B)

**Files:**
- Modify: `new_approach/src/parafrost_scheduler/schedule_solver.py` (add function ~line 1681, before `_encode_weekend_constraints`; call it inside that function ~line 1721)
- Test: `tests/test_nh_courtesy_ncc_align.py` (add a class)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_nh_courtesy_ncc_align.py`:

```python
class TestNccWeekendAlignment:
    def test_misalignment_penalized_weight_10(self):
        config = _config()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs, xn, wr = _make_xs_xn_wr(opb, config, ["Jin"])
        soft = []
        _encode_ncc_weekend_alignment(opb, wr, xs, config, ["Jin"], shift_idx, soft)
        # Weekday NCC1 (week 0) crossed with Weekend NCC2 (week 0) must be penalized.
        wd_ncc1 = xs[0][0][shift_idx["NCC1"]]
        we_ncc2 = wr[0][_ROLE_NCC2][0]
        assert _links(opb, wd_ncc1, we_ncc2), "weekday-NCC1/weekend-NCC2 must be penalized"
        # Weekday NCC2 crossed with Weekend NCC1 must be penalized.
        wd_ncc2 = xs[0][0][shift_idx["NCC2"]]
        we_ncc1 = wr[0][_ROLE_NCC1][0]
        assert _links(opb, wd_ncc2, we_ncc1), "weekday-NCC2/weekend-NCC1 must be penalized"
        assert soft and all(w == 10 for _, w in soft), "misalign weight must be 10"

    def test_aligned_not_penalized(self):
        config = _config()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs, xn, wr = _make_xs_xn_wr(opb, config, ["Jin"])
        soft = []
        _encode_ncc_weekend_alignment(opb, wr, xs, config, ["Jin"], shift_idx, soft)
        # The aligned pairing weekday-NCC1 + weekend-NCC1 must NOT be linked.
        wd_ncc1 = xs[0][0][shift_idx["NCC1"]]
        we_ncc1 = wr[0][_ROLE_NCC1][0]
        assert not _links(opb, wd_ncc1, we_ncc1), "aligned NCC1/NCC1 must not be penalized"

    def test_zero_weight_disables(self):
        config = _config(ncc_align=0)
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs, xn, wr = _make_xs_xn_wr(opb, config, ["Jin"])
        soft = []
        _encode_ncc_weekend_alignment(opb, wr, xs, config, ["Jin"], shift_idx, soft)
        assert soft == [], "Zero weight must add no penalties"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /cv/scratch/u/watkina6/scheduler && PYTHONPATH=src:new_approach/src .venv/bin/python -m pytest tests/test_nh_courtesy_ncc_align.py::TestNccWeekendAlignment -q`
Expected: FAIL — `ImportError: cannot import name '_encode_ncc_weekend_alignment'`.

- [ ] **Step 3: Implement `_encode_ncc_weekend_alignment`**

In `schedule_solver.py`, immediately before `def _encode_weekend_constraints` (line 1683), insert:

```python
def _encode_ncc_weekend_alignment(
    opb: OpbBuilder,
    wr: list[list[dict[int, int]]],
    xs: list[list[list[int]]],
    config: ScheduleSolverConfig,
    fellow_names: list[str],
    shift_idx: dict[str, int],
    soft_violations: list[tuple[int, int]],
) -> None:
    """SOFT nudge toward NCC weekday/weekend role alignment: a fellow on weekday
    NCC1 should take Weekend NCC1 (not NCC2), and vice versa. The two NCC weekend
    roles are interchangeable to every other constraint, so this is the only
    signal distinguishing them by the holder's weekday NCC service.
    """
    weight = config.ncc_weekend_misalign_penalty
    if weight == 0:
        return
    s_ncc1 = shift_idx.get("NCC1")
    s_ncc2 = shift_idx.get("NCC2")
    if s_ncc1 is None or s_ncc2 is None:
        return
    num_weeks = config.num_weeks

    def _and_penalty(a: int, b: int) -> None:
        pen = opb.new_var()
        opb.weighted_sum_at_most([(a, 1), (b, 1), (-pen, 1)], 2)
        opb.weighted_sum_at_least([(a, 1), (-pen, 1)], 1)
        opb.weighted_sum_at_least([(b, 1), (-pen, 1)], 1)
        soft_violations.append((pen, weight))

    for w in range(num_weeks):
        for f in range(len(fellow_names)):
            wd1 = xs[f][w][s_ncc1]
            wd2 = xs[f][w][s_ncc2]
            # Weekday NCC1 but Weekend NCC2.
            if wd1 != 0 and f in wr[w][_ROLE_NCC2]:
                _and_penalty(wd1, wr[w][_ROLE_NCC2][f])
            # Weekday NCC2 but Weekend NCC1.
            if wd2 != 0 and f in wr[w][_ROLE_NCC1]:
                _and_penalty(wd2, wr[w][_ROLE_NCC1][f])
```

- [ ] **Step 4: Add the call site inside `_encode_weekend_constraints`**

In `_encode_weekend_constraints`, after the all-different block (line 1721, after the `opb.at_most_k(vars_for_f, 1)` loop), add:

```python
    # NCC weekday/weekend role alignment (soft nudge).
    opb.add_comment("Weekend: NCC1/NCC2 weekday-weekend alignment (soft)")
    _encode_ncc_weekend_alignment(
        opb, wr, xs, config, fellow_names, shift_idx, soft_violations,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /cv/scratch/u/watkina6/scheduler && PYTHONPATH=src:new_approach/src .venv/bin/python -m pytest tests/test_nh_courtesy_ncc_align.py -q`
Expected: PASS (all 7 tests in the file).

- [ ] **Step 6: Commit**

```bash
cd /cv/scratch/u/watkina6/scheduler
git add new_approach/src/parafrost_scheduler/schedule_solver.py tests/test_nh_courtesy_ncc_align.py
git commit -m "feat: soft penalty for NCC weekday/weekend role misalignment"
```

---

## Task 5: Guarded NCC weekend-role swap helper (Part C)

**Files:**
- Modify: `new_approach/src/parafrost_scheduler/experiment.py` (add function ~line 146, after `solution_to_parsed`)
- Test: `tests/test_ncc_weekend_swap.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ncc_weekend_swap.py`:

```python
"""Unit tests for the post-processing NCC1<->NCC2 weekend-role swap.

The swap aligns weekday NCC role with weekend NCC role when it strictly improves
alignment, UNLESS the incoming Weekend-NCC1 fellow is on that week's Friday night
(which would create a friday_weekend_ncc1 HARD violation).
"""

from __future__ import annotations

from parafrost_scheduler.experiment import swap_ncc_weekend_roles
from scheduler.weekend_call_types import WeekendScheduleSolution
from scheduler.night_call_types import NightScheduleSolution
from scheduler.call_schedule_common import ParsedCallScheduleCsv, WeekRow


def _parsed(weekday_by_week):
    """weekday_by_week: list of {fellow: weekday_shift} dicts, one per week."""
    week_rows = [
        WeekRow(weekday_assignments=dict(wd), schedule_assignments={}, raw_row=[])
        for wd in weekday_by_week
    ]
    fellows = list(weekday_by_week[0].keys())
    return ParsedCallScheduleCsv(
        fellow_names=fellows, existing_schedule_columns=(),
        week_rows=week_rows, trailing_rows=[],
    )


def _weekend(assign_by_week):
    return WeekendScheduleSolution(assignments_by_week=[dict(a) for a in assign_by_week])


def _night(fri_by_week):
    """fri_by_week: list of fellow-name-on-Night-Fri (or '') per week."""
    weeks = []
    for fri in fri_by_week:
        wk = {role: "" for role in
              ("Night Mon", "Night Tue", "Night Wed", "Night Thu",
               "Night Fri", "Night Sat", "Night Sun")}
        wk["Night Fri"] = fri
        weeks.append(wk)
    return NightScheduleSolution(assignments_by_week=weeks)


def test_swap_aligns_when_beneficial():
    parsed = _parsed([{"A": "NCC2", "B": "NCC1"}])  # A does weekday NCC2, B weekday NCC1
    weekend = _weekend([{"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "C"}])
    night = _night([""])  # nobody on Friday night
    out = swap_ncc_weekend_roles(parsed, weekend, night)
    a = out.assignments_by_week[0]
    assert a["Weekend NCC1"] == "B" and a["Weekend NCC2"] == "A", "should swap to align"
    assert a["Weekend Stroke"] == "C", "stroke untouched"


def test_swap_skipped_when_friday_hard_violation():
    parsed = _parsed([{"A": "NCC2", "B": "NCC1"}])
    weekend = _weekend([{"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "C"}])
    night = _night(["B"])  # B (incoming Weekend NCC1) is on Friday night -> would be hard violation
    out = swap_ncc_weekend_roles(parsed, weekend, night)
    a = out.assignments_by_week[0]
    assert a["Weekend NCC1"] == "A" and a["Weekend NCC2"] == "B", "must NOT swap (hard guard)"


def test_noop_when_already_aligned():
    parsed = _parsed([{"A": "NCC1", "B": "NCC2"}])
    weekend = _weekend([{"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "C"}])
    night = _night([""])
    out = swap_ncc_weekend_roles(parsed, weekend, night)
    a = out.assignments_by_week[0]
    assert a["Weekend NCC1"] == "A" and a["Weekend NCC2"] == "B", "aligned -> unchanged"


def test_noop_when_no_strict_improvement():
    # Neither holder is on weekday NCC1/NCC2 -> swapping changes nothing useful.
    parsed = _parsed([{"A": "Elec", "B": "Stroke"}])
    weekend = _weekend([{"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "C"}])
    night = _night([""])
    out = swap_ncc_weekend_roles(parsed, weekend, night)
    a = out.assignments_by_week[0]
    assert a["Weekend NCC1"] == "A" and a["Weekend NCC2"] == "B", "no improvement -> unchanged"


def test_input_not_mutated():
    parsed = _parsed([{"A": "NCC2", "B": "NCC1"}])
    weekend = _weekend([{"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "C"}])
    night = _night([""])
    swap_ncc_weekend_roles(parsed, weekend, night)
    assert weekend.assignments_by_week[0]["Weekend NCC1"] == "A", "original must be untouched"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /cv/scratch/u/watkina6/scheduler && PYTHONPATH=src:new_approach/src .venv/bin/python -m pytest tests/test_ncc_weekend_swap.py -q`
Expected: FAIL — `ImportError: cannot import name 'swap_ncc_weekend_roles'`.

- [ ] **Step 3: Implement `swap_ncc_weekend_roles`**

In `new_approach/src/parafrost_scheduler/experiment.py`, after `solution_to_parsed` (ends ~line 145, before `def write_csv`), insert. First ensure the imports at the top of the file include the types — check the existing import block and add what's missing:

```python
def swap_ncc_weekend_roles(parsed, weekend_solution, night_solution):
    """Return a new WeekendScheduleSolution with NCC1/NCC2 weekend roles swapped
    per week WHEN the swap strictly improves weekday<->weekend NCC alignment and
    does NOT put the incoming Weekend-NCC1 fellow on that week's Friday night
    (a friday_weekend_ncc1 HARD violation). The two NCC weekend roles are
    interchangeable to every other constraint, so this Friday check is the only
    guard needed to guarantee the result never scores worse. Weekend Stroke is
    never touched. Pure function — does not mutate its inputs.
    """
    from scheduler.weekend_call_types import WeekendScheduleSolution

    new_weeks = []
    for w, assignments in enumerate(weekend_solution.assignments_by_week):
        a = assignments.get("Weekend NCC1", "")
        b = assignments.get("Weekend NCC2", "")
        new = dict(assignments)
        if a and b:
            wd = parsed.week_rows[w].weekday_assignments
            wd_a = wd.get(a, "")
            wd_b = wd.get(b, "")
            cur = (1 if wd_a == "NCC1" else 0) + (1 if wd_b == "NCC2" else 0)
            swp = (1 if wd_b == "NCC1" else 0) + (1 if wd_a == "NCC2" else 0)
            if swp > cur:
                fri = night_solution.assignments_by_week[w].get("Night Fri", "")
                # After swap, b would hold Weekend NCC1; forbid if b is on Friday night.
                if fri != b:
                    new["Weekend NCC1"], new["Weekend NCC2"] = b, a
        new_weeks.append(new)
    return WeekendScheduleSolution(assignments_by_week=new_weeks)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /cv/scratch/u/watkina6/scheduler && PYTHONPATH=src:new_approach/src .venv/bin/python -m pytest tests/test_ncc_weekend_swap.py -q`
Expected: PASS (5 tests). If `ParsedCallScheduleCsv` / `WeekRow` import paths in the test differ, fix the test imports to match the canonical locations (grep: `grep -rn "class WeekRow" src/scheduler/`).

- [ ] **Step 5: Commit**

```bash
cd /cv/scratch/u/watkina6/scheduler
git add new_approach/src/parafrost_scheduler/experiment.py tests/test_ncc_weekend_swap.py
git commit -m "feat: guarded NCC1/NCC2 weekend-role swap post-processor"
```

---

## Task 6: Emit `_workbook_swapped.xlsx` from the optimize output path

**Files:**
- Modify: `schedule.py:80-88`

- [ ] **Step 1: Update `_write_outputs` to write both workbooks**

Replace the body of `_write_outputs` (lines 80-88) with:

```python
def _write_outputs(sol, config, out_prefix: Path):
    write_csv(sol, out_prefix.with_suffix(".csv"))
    parsed = solution_to_parsed(sol, list(sol.weekly_assignments.keys()))
    write_schedule_workbook(
        parsed, sol.night_solution, sol.weekend_solution,
        out_prefix.parent / f"{out_prefix.name}_workbook.xlsx",
        hard_criteria=_COLOR_HARD,
        backup_solution=getattr(sol, "backup_solution", None),
        fellow_groups=config.fellow_groups)
    # Guarded NCC weekend-role swap -> second workbook (a no-op when the model
    # already aligns NCC weekday/weekend roles).
    swapped = swap_ncc_weekend_roles(parsed, sol.weekend_solution, sol.night_solution)
    write_schedule_workbook(
        parsed, sol.night_solution, swapped,
        out_prefix.parent / f"{out_prefix.name}_workbook_swapped.xlsx",
        hard_criteria=_COLOR_HARD,
        backup_solution=getattr(sol, "backup_solution", None),
        fellow_groups=config.fellow_groups)
```

- [ ] **Step 2: Add the import**

In `schedule.py`, update the experiment import (line 33-36) to include `swap_ncc_weekend_roles`:

```python
from parafrost_scheduler.experiment import (
    DEFAULT_ANNUAL, DEFAULT_STANDING, assemble_config,
    solution_to_parsed, swap_ncc_weekend_roles, write_csv,
)
```

- [ ] **Step 3: Verify schedule.py imports cleanly**

Run: `cd /cv/scratch/u/watkina6/scheduler && PYTHONPATH=src:new_approach/src .venv/bin/python -c "import importlib.util, pathlib; spec=importlib.util.spec_from_file_location('sched','schedule.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print('ok', hasattr(m,'_write_outputs'))"`
Expected: `ok True`

- [ ] **Step 4: Commit**

```bash
cd /cv/scratch/u/watkina6/scheduler
git add schedule.py
git commit -m "feat: write _workbook_swapped.xlsx alongside as-solved workbook"
```

---

## Task 7: Full regression + launch the third heavy optimization

**Files:** none (verification + run)

- [ ] **Step 1: Run the full test suite**

Run: `cd /cv/scratch/u/watkina6/scheduler && PYTHONPATH=src:new_approach/src .venv/bin/python -m pytest tests/ new_approach/tests/ -q`
Expected: All previously-passing tests still pass (handoff baseline: 413 passed, 9 skipped) PLUS the 12 new tests → ~425 passed, 9 skipped. If any pre-existing test fails, STOP and investigate (do not adjust the new code to mask an unrelated failure).

- [ ] **Step 2: Confirm SAT-equivalence quickly is unnecessary, but sanity-build the OPB**

The new constraints are soft-only, so feasibility is unchanged. Sanity-check that a full OPB build with the real config succeeds (no solve):

Run: `cd /cv/scratch/u/watkina6/scheduler && PYTHONPATH=src:new_approach/src .venv/bin/python -c "
from pathlib import Path
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_solver import build_full_schedule_opb
cfg,_ = assemble_config(Path('new_approach/workbook_partial_input6.xlsx'), verbose=False)
opb, vm = build_full_schedule_opb(cfg, objective=True)
print('built OPB; soft terms =', len(vm.soft_violations))
"`
Expected: prints a soft-term count (a few thousand); no exception.

- [ ] **Step 3: Launch the third heavy optimization on Slurm**

Run from repo root:

```bash
cd /cv/scratch/u/watkina6/scheduler && sbatch -J wb6-courtesy \
  -o results_slurm/wb6_courtesy_%j.out -A prescient1 -p defq -n 1 \
  --time=06:30:00 run.slurm optimize \
  --workbook new_approach/workbook_partial_input6.xlsx \
  --out-prefix output_v3_wb6_courtesy
```

Expected: `Submitted batch job <ID>`. Record the ID.

- [ ] **Step 4: Confirm the job started and is producing the expected log header**

Run: `squeue -u watkina6` (job appears, state R or PD), then once running:
`tail -5 results_slurm/wb6_courtesy_<ID>.out`
Expected: the `weekend_consecutive_hard=... night_hard_criteria=...` and `preview slices (returning, write-to-disk) = (8.0, 25.0, 90.0, 600.0, 1800.0)` header lines, then incumbents landing at the 600/1800s checkpoints.

- [ ] **Step 5: Final commit (none needed — code already committed in Tasks 1-6)**

This task produces no code changes. Report the launched job ID and the two existing jobs' status to the user.

---

## Self-Review Notes

- **Spec coverage:** Part A → Tasks 2-3; Part B → Task 4; Part C → Tasks 5-6; Part D → Task 7. ✔
- **Per-occurrence AAN/ABPN:** Task 2 emits one slack per night and per weekend role. ✔
- **NH-only gating:** Task 2 builds `nh_indices` from `fellow_groups["NH"]` and skips otherwise. ✔
- **Swap guard:** Task 5 checks only the incoming Weekend-NCC1 fellow against Night Fri (necessary+sufficient per spec). ✔
- **Deactivation:** all three weights short-circuit at 0 (Tasks 2, 4). ✔
- **Type consistency:** `_and_penalty` AND-indicator identical in Tasks 2 & 4; `swap_ncc_weekend_roles(parsed, weekend_solution, night_solution)` signature matches its call in Task 6. ✔
- **Import paths (verified):** `ParsedCallScheduleCsv` and `WeekRow` both live in `scheduler.call_schedule_common` (confirmed; this is also where `experiment.py:24` imports them). `WeekendScheduleSolution` is in `scheduler.weekend_call_types`; `NightScheduleSolution` is in `scheduler.night_call_types`.
```
