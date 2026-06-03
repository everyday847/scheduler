# Constraint Debugging Lessons

Hard-won lessons from debugging UNSAT results in the fellowship schedule solver.
These patterns recur; this document is the reference for future debugging sessions.

---

## 1. locked_fellow_indices Over-Inclusion

### What happened

All fellows appearing in `locked_assignments` were added to `locked_fellow_indices`.
This set controls which fellows have their per-fellow constraints skipped (any kind in
`PER_FELLOW_KINDS`). Fellows with only 1-4 specific-week assignments (STROKE, NH)
were treated the same as fellows whose entire schedule is externally managed (NCC 53/53,
CCM 8-45/53).

### Why it was confusing

The solver returned SAT, which looked correct. But it was only SAT because shift_total,
zero_shifts, max_consecutive, and block_rotation constraints were silently skipped for
those fellows. Their schedules were unconstrained in the weeks not pinned by locked
assignments, so the solver could put them anywhere -- violating rules that should have
applied.

### How to avoid it

Use a threshold to distinguish externally-managed fellows from internally-managed ones:

```python
# > num_weeks//10 (~5 weeks) means externally managed
locked_fellow_indices = {
    f for f, weeks in locked_assignments.items()
    if len(weeks) > num_weeks // 10
}
```

Fellows with sparse locks (STROKE 1-2 weeks, NH 4 weeks) stay below the threshold and
get full constraint enforcement. Fellows with dense locks (NCC 53/53, CCM 8-45/53)
exceed the threshold and have per-fellow constraints skipped since their schedule is
managed elsewhere.

---

## 2. derive_forbidden_shifts Budget Side Effects

### What happened

`derive_forbidden_shifts` computes the set of allowed shifts for each group by looking
at ACTIVE shift_total rules. Any shift NOT mentioned in any shift_total rule for that
group becomes HARD FORBIDDEN (encoded as zero_shifts). Deactivating a shift_total rule
removes the shifts it mentions from the budget, making them forbidden.

### Why it was confusing

Three manifestations:

1. **NH fellows could not take Vac** -- no NH rule mentioned "Vac" as a shift, so it
   was hard-forbidden. Fix: add `NH: Vacation Total at_least 3 soft`.

2. **"Disable rules one at a time" is unreliable** -- disabling a shift_total rule
   changes the budget, which changes forbidden shifts. The resulting SAT/UNSAT doesn't
   tell you whether the rule itself is the problem; it might be the budget change.

3. **Softening (hard to soft) is safe** -- `derive_forbidden_shifts` checks whether a
   rule is active, not whether it is hard or soft. Softening a rule keeps it active and
   preserves the budget. This makes softening the correct diagnostic action.

### How to avoid it

- Never debug by deactivating shift_total rules. Instead, soften them.
- When adding a new group or shift type, always add at least one shift_total rule that
  mentions it -- even if it is a trivial soft lower-bound -- so the shift stays in the
  budget.
- If you must deactivate a rule, verify the budget afterwards:
  ```python
  from scheduler.palette_derivations import derive_forbidden_shifts
  forbidden = derive_forbidden_shifts(rules, groups, shifts)
  # Check that expected shifts are NOT in the forbidden set
  ```

---

## 3. CCM NCC Block vs. Partial Workbook

### What happened

CCM fellows are imported from a workbook with partial assignments (8-45 out of 53
weeks). The `all_or_none_block` (block_rotation) constraint requires NCC/Swing in full
4-week blocks. If the workbook assigns 3 out of 4 weeks in a block, the remaining week
must be filled -- but the constraint prevents assigning NCC/Swing to a single week
without the full block.

### Why it was confusing

The constraint is logically correct (NCC/Swing should be in blocks). The workbook is
also correct (it only assigns the weeks it knows about). The conflict only surfaces when
these two interact: the workbook creates an implicit partial-block obligation that the
block constraint makes unsatisfiable.

### How to avoid it

CCM fellows exceed the locked_fellow_indices threshold (8-45 weeks > 5), so
block_rotation is skipped for them automatically. Their block structure is managed by
the external workbook, not by the solver.

If the threshold changes, ensure that any group with partial workbook imports and block
constraints is handled -- either by raising the threshold or by explicitly exempting the
group from block constraints.

---

## 4. at_most Constraints Cannot Cause UNSAT Alone

### What happened

During debugging, `at_most_k` constraints (NCC caps, Swing caps, "Only One NH on Core
ICU per week") were suspected as UNSAT causes.

### Why it was confusing

These constraints look restrictive. A cap of 1 feels like it could block assignment.
But `at_most_k` is a LIMITING constraint -- it says "no more than k" -- and setting
everything to 0 always satisfies it.

### How to avoid it

When diagnosing UNSAT, immediately classify constraints:

| Type | Examples | Can cause UNSAT alone? |
|------|----------|----------------------|
| LIMITING (at_most_k) | NCC cap, Swing cap, NH-on-Core cap | No |
| FORCING (at_least_k, exactly_k, full_assignment, specific_assignment) | shift_totals, coverage targets, locked weeks | Yes |

UNSAT always requires a FORCING constraint that cannot be satisfied given the limits.
The at_most constraints define the space; the at_least/exactly constraints must fit
within it. Look for the forcing constraint that overflows the space.

---

## 5. Soft Constraints Can Never Cause UNSAT

### What happened

Adding a constraint marked as "soft" appeared to cause UNSAT in some tests.

### Why it was confusing

If adding a soft constraint makes the problem UNSAT, intuition says the constraint is
the problem. But soft constraints by design cannot cause UNSAT.

### How to avoid it

The encoding of a soft constraint creates a violation indicator variable `v`. When
`v = 1`, the constraint is fully relaxed. Setting all `v = 1` makes all soft
constraints trivially satisfiable. Therefore:

- If adding a SOFT constraint causes UNSAT, the bug is elsewhere.
- Check `derive_forbidden_shifts` (pattern #2) -- adding the rule may have changed the
  budget.
- Check that the constraint is actually encoded as soft, not accidentally hard.
- Check that the constraint's presence doesn't trigger a code path that creates
  additional hard constraints.

---

## 6. NH shift_total "exactly" Constraints + Hard Vacations

### What happened

NH fellows have many hard "exactly N" shift_total rules:
- NCC exactly 2
- Telestroke exactly 2
- NHS exactly 1
- AAN exactly 1
- Elec/APBN at_least 1

Combined with hard vacation requests pinning specific weeks to Vac AND per-fellow
shift_total rules (e.g., Jinyuan exactly 2 Stroke, Sokena 4-6 Stroke), the scheduling
becomes over-determined. The "Only One NH on Core ICU per week" constraint further
restricts which weeks are available.

### Why it was confusing

Each constraint in isolation is clearly satisfiable. Each NH fellow needs ~13-15
specific weeks assigned across ~7 different shifts, with 53 weeks available. The
combinatorial interaction between hard vacation pins, exact shift counts, and the
one-NH-per-week cap creates an infeasible system that isn't obvious from inspecting
individual rules.

### How to avoid it

- NH shift_totals should be soft. The solver will optimize toward the targets without
  hard-failing when the combinatorial interactions make exact satisfaction impossible.
- When "exactly N" rules accumulate for a group and sum to most of the available weeks,
  treat that as a red flag -- the system is becoming over-determined.
- Test with hard vacations populated before declaring the constraint set feasible.
  Vacation pins are the final piece that often tips the system from SAT to UNSAT.

---

## 7. dual_stroke_window Encoder Bug (OPEN)

### What happened

The `_encode_dual_stroke_window` function creates at_most_k constraints limiting Stroke
assignments per week (1 supervisor + 1 non-supervisor inside the window, 1 total
outside). This causes UNSAT even when all Stroke coverage/total constraints are soft,
the window covers the entire year [0, 53], and each constraint individually is trivially
satisfiable.

### Why it was confusing

The encoder includes ALL fellows' Stroke variables, including locked NCC/CCM fellows
whose zero_shifts are skipped due to locked_fellow_indices. These variables are live but
effectively 0 (forced by at_most_one + pins). The at_most_k constraint should still be
satisfiable since setting variables to 0 is free (at_most is limiting, pattern #4).

The bug is confirmed but the root cause is unknown -- possibly an interaction with the
OPB encoding or variable ordering.

### Current status

Disabled. Needs separate investigation. When re-enabling, test with a minimal instance
(2 fellows, 4 weeks) to isolate the encoding issue from the combinatorial problem.

---

## 8. Debugging Methodology

### Unreliable approaches

- **"Disable rule X and check SAT"** -- unreliable when X is a shift_total rule.
  Disabling changes the budget (pattern #2), introducing a side effect that
  invalidates the test.

- **"Test pairs/subsets of constraints"** -- unreliable if you don't verify the test
  config matches your mental model. Budget side effects, locked_fellow_indices
  interactions, and encoder code paths can all differ from what you expect.

### Reliable approaches

- **"Soften rule X"** -- safe for diagnosis. Does not change the budget. Cannot
  introduce new UNSAT. If softening a rule resolves UNSAT, that rule (or its hard
  interaction with other rules) is the cause.

- **Classify constraints by type** -- immediately determine which constraints are
  FORCING vs LIMITING (pattern #4). Focus diagnosis on forcing constraints.

- **Check per_fellow_shift_total separately** -- rules in `call_rules` with kind
  `per_fellow_shift_total` are encoded by `_encode_call_rules`, NOT
  `_encode_weekly_rules`. They are NOT in `PER_FELLOW_KINDS` and are always active
  regardless of `locked_fellow_indices`. They can be a hidden source of forcing
  constraints.

- **Split "exactly" into "at_least" + "at_most"** -- when an "exactly N" constraint
  causes UNSAT, test "at_least N" and "at_most N" separately to find which direction
  fails:
  - If "at_most N" alone fails: the solver needs MORE than N (other constraints force
    assignments above the target -- check coverage/assignment constraints).
  - If "at_least N" alone fails: the solver can't reach N (other constraints prevent
    enough assignments -- check caps, forbidden shifts, vacation pins).

---

## Reference: PER_FELLOW_KINDS

```python
PER_FELLOW_KINDS = frozenset({
    "shift_total",
    "max_consecutive",
    "all_or_none_block",
    "block_shift_set_choice",
    "prerequisite",
    "windowed_balance",
    "service_profile",
    "nir_one_week_per_half",
    "scvmc_second_half",
    "stroke_no_block_one_ncc",
    "jr_ncc_before_swing",
    "comparable_half_year_distribution",
    "block_rotation",
    "rotation_continuity",
    "block_shift_count",
    "zero_shifts",
})
```

Constraints with kinds in this set are **SKIPPED** for fellows in
`locked_fellow_indices`.

Constraints NOT in this set are **ALWAYS enforced** regardless of locked status:
- `full_assignment`
- `staffing_per_week`
- `specific_assignment`
- `ncc_coverage`
- `coverage_target`
- `minimize_uncovered_shift_weeks`
- `stroke_shift_coverage`
- `ncc_stroke_oversight`
- `fourth_block_two_micu_fellows`
- `isc`

---

## Quick Diagnostic Flowchart

```
UNSAT encountered
    |
    v
1. Are all soft constraints actually soft? (check encoding)
    |
    v
2. Check derive_forbidden_shifts -- are expected shifts in the budget?
    |
    v
3. Check locked_fellow_indices -- are the right fellows being skipped?
    |
    v
4. Identify FORCING constraints (at_least, exactly, full_assignment, specific_assignment)
    |
    v
5. Soften forcing constraints one at a time (NOT disable)
    |
    v
6. When softening X resolves UNSAT: X interacts with the limits.
   Split "exactly" into at_least/at_most to find the failing direction.
    |
    v
7. Check per_fellow_shift_total rules in call_rules (always active, often forgotten)
```
