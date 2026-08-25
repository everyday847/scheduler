# NH courtesy penalties + NCC weekend-alignment penalty + post-process swap

Date: 2026-06-06
Branch: `partial-import`
Solver: RoundingSat (PB), unified entry point `schedule.py`

## Motivation

1. **NH courtesy weeks.** We only manage part of the NH fellows' time. When an NH
   fellow is on AAN (conference) or ABPN (board exam / elective) for a week, their
   primary fellowship may be relying on that week being relatively light. We would
   rather not assign them call that week, but it is not forbidden — so a soft
   penalty, not a hard block.

2. **NCC weekend alignment.** It is preferable that a fellow on weekday NCC1 also
   takes the Weekend NCC1 role (and likewise NCC2↔NCC2). The optimizer does not
   currently care which of the two NCC weekend roles a fellow gets, because every
   other constraint treats the two roles interchangeably. A small penalty nudges
   the optimizer toward alignment; a deterministic post-processing swap cleans up
   any residual misalignment in the final workbook.

## Scope decisions (from brainstorming)

- All three penalties are **folded into the default config**, each behind a weight
  field that can be set to 0 to deactivate (like any other constraint).
- AAN/ABPN penalties apply to **NH-group fellows only** (documented: their primary
  fellowship relies on a light week). Gate: NH-group membership AND pinned to
  AAN/ABPN that week.
- The swap is **guarded**: only performed when it strictly improves NCC weekday↔
  weekend alignment AND does not introduce a `friday_weekend_ncc1` hard violation.
- AAN/ABPN penalty is **per-occurrence** (each night + each weekend role = 100).

## Part A — NH AAN-week / ABPN-week call penalties

### Where
New function `_encode_nh_courtesy_weeks(...)` in
`new_approach/src/parafrost_scheduler/schedule_solver.py`, called from
`_encode_night_constraints` next to the existing `_encode_nhs_week_nights` /
`_encode_pre_aan_forbid` calls (so it has `xn`, `xs`, `wr`, `shift_idx`,
`soft_violations` in scope).

### Config
Two new fields on `ScheduleSolverConfig`:

```python
nh_aan_week_call_penalty: int = 100   # per night + per weekend role, AAN week, NH fellow
nh_abpn_week_call_penalty: int = 100  # per night + per weekend role, ABPN week, NH fellow
```

Setting either to 0 deactivates that family (encoder skips when weight == 0).

### Semantics (per-occurrence)
For each NH-group fellow `f` and each week `w`:
- Let `aan_var = xs[f][w][shift_idx["AAN"]]` (0 if forbidden/absent). Because NH
  AAN weeks are injected as **hard pins**, this is effectively a constant 1 on the
  pinned week and 0 elsewhere — but we gate on the var for generality/correctness,
  exactly like `_encode_pre_aan_forbid`.
- For each night `d` in week `w` where `xn[d][f] != 0`: add a soft AND-indicator
  `pen = aan_var AND xn[d][f]`, weight `nh_aan_week_call_penalty`.
- For each weekend role_idx in (NCC1, NCC2, Stroke) where `f in wr[w][role_idx]`:
  add a soft AND-indicator `pen = aan_var AND wr[w][role_idx][f]`, same weight.
- Identical block for ABPN using `shift_idx["ABPN"]` and
  `nh_abpn_week_call_penalty`.

AND-indicator encoding mirrors the established NHS pattern
(`pen >= a + b - 1`, `pen <= a`, `pen <= b`) and pushes `(pen, weight)` onto
`soft_violations`.

NH group membership: `config.fellow_groups.get("NH", [])` → indices in
`fellow_names`. Skip fellows not in NH.

### Interaction notes
- AAN already hard-blocks Mon–Thu nights via `NIGHT_BLOCKED_SHIFTS`, so the AAN
  night penalty effectively bites only Fri/Sat/Sun nights + weekend roles. That is
  fine and intended (the courtesy penalty is a superset nudge; the hard block
  still owns Mon–Thu).
- ABPN is night-blocked only under the `abpn_block` variant. Under default, the
  ABPN penalty bites all 7 nights + weekend roles.
- The pre-AAN encoder (`_encode_pre_aan_forbid`) hard-forbids the AAN fellow's
  *preceding* weekend + Fri/Sat/Sun nights. This new penalty covers the AAN week
  *itself*, a disjoint target — no double-encoding.

## Part B — NCC1↔NCC2 weekend-misalignment penalty (weight 10)

### Where
New function `_encode_ncc_weekend_alignment(...)` in `schedule_solver.py`, called
from `_encode_weekend_constraints` (has `wr`, `xs`, `shift_idx`,
`soft_violations`).

### Config
```python
ncc_weekend_misalign_penalty: int = 10  # 0 disables
```

### Semantics
For each fellow `f`, week `w`:
- `s_ncc1 = shift_idx["NCC1"]`, `s_ncc2 = shift_idx["NCC2"]`.
- If `xs[f][w][s_ncc1] != 0` and `f in wr[w][_ROLE_NCC2]`: soft AND-indicator
  `pen = xs[f][w][s_ncc1] AND wr[w][_ROLE_NCC2][f]`, weight 10. (Weekday NCC1 but
  Weekend NCC2.)
- If `xs[f][w][s_ncc2] != 0` and `f in wr[w][_ROLE_NCC1]`: soft AND-indicator
  weight 10. (Weekday NCC2 but Weekend NCC1.)

This is the only signal in the model that distinguishes the two NCC weekend roles
by which weekday-NCC the holder is on.

## Part C — Post-processing guarded swap → `_workbook_swapped.xlsx`

### Where
New pure-Python helper
`swap_ncc_weekend_roles(parsed, weekend_solution, night_solution)` returning a new
`WeekendScheduleSolution`. Place it in
`new_approach/src/parafrost_scheduler/experiment.py` (alongside the other output
helpers `solution_to_parsed` / `write_csv`); called from
`schedule.py::_write_outputs`, which already has `sol.night_solution` in scope.

### Algorithm
Operate on `weekend_solution.assignments_by_week` (list of
`{role: fellow_name}`), using `parsed.week_rows[w].weekday_assignments`
(`fellow → weekday shift`) for alignment and a per-week Friday-night holder lookup
from the night solution.

For each week `w`:
1. `a = assignments["Weekend NCC1"]`, `b = assignments["Weekend NCC2"]`.
   Skip if either is empty/missing.
2. `wd_a = weekday_assignments[a]`, `wd_b = weekday_assignments[b]`.
3. Compute current alignment score: `+1` if `wd_a == "NCC1"`, `+1` if
   `wd_b == "NCC2"`. Compute swapped alignment: `+1` if `wd_b == "NCC1"`, `+1` if
   `wd_a == "NCC2"`.
4. Swap only if `swapped > current` (strict improvement).
5. **Guard:** after a tentative swap, fellow `b` would hold Weekend NCC1. If `b`
   is on that week's Friday night (`night_solution.assignments_by_week[w]
   ["Night Fri"] == b`), the swap would create a `friday_weekend_ncc1` HARD
   violation → **do not swap** this week. (Symmetric: original NCC1 holder `a`
   moving off NCC1 cannot *create* the violation; only the incoming NCC1 holder
   can. We only need to check the incoming one.)
6. Otherwise swap: `assignments["Weekend NCC1"], assignments["Weekend NCC2"] =
   b, a`.

The swap touches only the two NCC weekend roles; Weekend Stroke is untouched.
Because every other constraint treats NCC1/NCC2 weekend roles interchangeably and
the only score-affecting differences are the two Friday rules (`friday_weekend_
ncc1` HARD, `weekend_night_friday` SOFT-40 for NCC2/Stroke), the guard in step 5
is necessary and sufficient to guarantee the swapped schedule never scores worse.

Note on the SOFT-40 rule: it penalizes Friday-night + Weekend **NCC2** (not NCC1).
A swap that moves a Friday-night fellow off NCC2 onto NCC1 trades a soft-40
penalty for a potential hard violation — already blocked by step 5. A swap that
moves a Friday-night fellow onto NCC2 (from NCC1) is impossible because the
original NCC1 holder on Friday night would already be a hard violation that the
solver avoided. So no soft regression is possible either.

### Output wiring
`schedule.py::_write_outputs` writes BOTH workbooks:
- `{prefix}_workbook.xlsx` — as-solved (unchanged behavior).
- `{prefix}_workbook_swapped.xlsx` — after `swap_ncc_weekend_roles`.

CSV remains single (as-solved). If optimization is good enough, Part B drives the
misalignment to zero and the swap is a no-op (the two workbooks are identical) —
that is the success signal.

## Part D — Third heavy optimization

Run the **default config** (now carrying Parts A + B) on wb6 with a distinct
prefix, parallel to the two running jobs:

```
sbatch -J wb6-courtesy -o results_slurm/wb6_courtesy_%j.out \
  --time=06:30:00 run.slurm optimize \
  --workbook new_approach/workbook_partial_input6.xlsx \
  --out-prefix output_v3_wb6_courtesy
```

No SAT re-check required: Parts A+B add only soft constraints, so feasibility is
unchanged from the already-SAT default. Distinct prefix → no collision with
`output_v3_wb6` (15342836) or `output_v3_wb6_abpnblock` (15414076), which run
committed old code and are unaffected.

## Testing

TDD, per project norm. Constraint-presence tests inspect `opb._constraints`
strings (no solve); the swap is pure Python and unit-tested directly.

- `test_nh_aan_week_penalty_present`: build OPB for a config where an NH fellow is
  pinned to AAN week `w`; assert a soft violation with weight 100 exists linking
  that fellow's AAN var to a night/weekend var in week `w`. Assert NO penalty for
  a non-NH fellow on AAN, and none with weight set to 0.
- `test_nh_abpn_week_penalty_present`: analogous for ABPN.
- `test_ncc_weekend_misalign_penalty_present`: assert weight-10 soft links exist
  for the NCC1-weekday/NCC2-weekend and NCC2-weekday/NCC1-weekend cross-cases;
  none when aligned; none at weight 0.
- `test_swap_aligns_when_beneficial`: construct a weekend+weekday+night solution
  with a misaligned NCC pair and no Friday-night conflict → assert swap aligns.
- `test_swap_skipped_when_friday_hard_violation`: misaligned pair but the incoming
  NCC1 fellow is on Friday night → assert swap is NOT performed.
- `test_swap_noop_when_already_aligned`: aligned pair → unchanged.
- `test_swap_noop_when_no_strict_improvement`: swap would not improve alignment →
  unchanged.

Feasibility / score sanity goes to Slurm (the courtesy run itself). Verify the
existing suite still passes: `PYTHONPATH=src:new_approach/src python -m pytest
tests/ new_approach/tests/ -q`.

## Out of scope (YAGNI)

- No CLI flags for the weights (they live in the config dataclass; flip via
  `dataclasses.replace` or a variant if ever needed).
- No change to the CSV format.
- No swap for Weekend Stroke (only the NCC1/NCC2 pair is interchangeable).
- No re-running / re-scoring inside the swap (the single Friday guard is provably
  sufficient).
