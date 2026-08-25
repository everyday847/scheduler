# Lexicographic Soft-Penalty Tiers — Design

**Status:** designed, NOT implemented. Build only if deviation-scaled penalties
(per-occurrence weights, already landed) fail to produce satisfactory schedules
on their own. The whole point is to keep this an *optional* gate that can be
turned off by collapsing every soft constraint into one tier.

## Problem

Today every soft constraint contributes `(var, weight)` to a single
`soft_violations` list, and the solver minimizes one weighted sum. The only
existing structure is `soft_weekly_count`, a single boundary splitting WEEKLY
penalties from WEEKEND/NIGHT/CALL penalties (used by `soft_penalty_breakdown`
and the streaming UI).

A single weighted sum lets a large pile of low-weight penalties trade against a
single high-weight one — e.g. many weight-10 weekend-night preferences can
outweigh a weight-100 coverage gap. Lexicographic tiers prevent that: minimize
the top tier to its floor, freeze it, then minimize the next tier, etc.

The risk the user flagged: strict freezing can force *bad* trades — sacrificing a
little of a higher tier to win a lot of a lower tier is sometimes the humane
choice, and strict lexicographic ordering forbids it. So the design uses
**slack-freeze** and must be **trivially alterable or abolishable**.

## The soft-constraint families (tiers)

From the full encoder audit (see the soft-constraint catalog). Four families,
top (most important) first:

1. **Coverage** — "someone must do it." In the live v3 config this reduces to
   just **Swing** coverage (`coverage_target`, A6). Note: `minimize_uncovered`
   (A7) is dead in v3; `ncc_stroke_oversight` (A12) is intentionally NOT pushed
   hard here (with many fellows on a fixed weekly schedule, forcing it to zero
   distorts the schedule with outsized consequences). The weekend-prerequisite
   soft fallback (B7) is more important than A12.
2. **Workload targets** — per-fellow fair distribution: shift totals
   (elective/vacation/SICU/NS), weekend NCC/Stroke totals, night totals, night
   multisets, per-fellow call totals. (A2, A5, A16, B1/B2, C1/C2, C3.)
3. **Structural / rotation quality** — max-consecutive, block shape, half-year
   balance, first-block stroke. (A8–A11, A13, A14.)
4. **Call-quality preferences** — weekend spacing, role-match, pre-vacation
   weekend, clinic/stroke night criteria, weekend-night linking. (B3–B6, C5/C6,
   C9/C10/C11.) Lowest stakes.

Currently-hard constraints (per-night/role coverage, night spacing, dual-stroke
caps, weekend ±1 band, hard night criteria anaesthesia/friday-NCC1/sunday) stay
HARD, above all tiers. The user leans toward making MORE things hard, not fewer.

## Core primitive: tag each soft violation with a layer key

`soft_violations` entries become `(var, weight, layer_key)` where `layer_key` is
a stable string naming the soft-constraint family, e.g.:

- `coverage.swing`, `coverage.weekend_prereq`, `coverage.ncc_oversight`
- `workload.shift_total`, `workload.weekend_ncc`, `workload.weekend_stroke`,
  `workload.night_total`, `workload.night_friday`, `workload.night_multiset`
- `structural.max_consecutive`, `structural.block_shape`,
  `structural.half_year_balance`, `structural.first_block_stroke`
- `callpref.weekend_spacing`, `callpref.weekend_match`,
  `callpref.prevacation`, `callpref.night_clinic`, `callpref.night_stroke`,
  `callpref.weekend_night_friday`, `callpref.weekend_night_saturday`,
  `callpref.weekend_night_sunday`

Every site that does `soft_violations.append(...)` or calls
`_add_cardinality_constraint(...)` passes its layer key. This is a mechanical but
wide change (~30 sites). The existing `soft_weekly_count` boundary is then
derivable from layer keys and can be retired.

## Tier configuration (single source of truth)

One ordered list in the standing YAML (or a dedicated `tiers:` block):

```yaml
soft_tiers:
  - name: coverage
    epsilon: 0          # strict for the top tier
    # ncc_oversight (A12) is soft and lives here, but with many fixed-schedule
    # fellows its floor may be > 0; that is acceptable — do NOT force it hard.
    layers: [coverage.swing, coverage.weekend_prereq, coverage.ncc_oversight]
  - name: workload
    epsilon: 200        # allow small regressions to help lower tiers
    layers: [workload.shift_total, workload.weekend_ncc, workload.weekend_stroke,
             workload.night_total, workload.night_friday, workload.night_multiset]
  - name: structural
    epsilon: 100
    layers: [structural.max_consecutive, structural.block_shape,
             structural.half_year_balance, structural.first_block_stroke]
  - name: callpref
    epsilon: 0          # bottom tier, nothing below it to protect
    layers: [callpref.weekend_spacing, callpref.weekend_match, callpref.prevacation,
             callpref.night_clinic, callpref.night_stroke,
             callpref.weekend_night_friday, callpref.weekend_night_saturday,
             callpref.weekend_night_sunday]
```

**Abolish the tier system:** put every layer in ONE tier. The optimizer then
minimizes a single weighted sum — exactly today's behavior. This is the default
until tiers prove necessary.

## Lint rule (tiers live in exactly one place)

A test/CI check that builds an OPB for a representative config, collects the set
of `layer_key`s actually emitted, and asserts:

1. Every emitted layer key appears in the `soft_tiers` config **exactly once**
   (no missing, no duplicate-across-tiers).
2. Every layer key in the config is actually emitted (no dead config entries) —
   or is explicitly allow-listed as "may be absent for this config."

This makes the tier list the single authority: add a new soft constraint and the
lint fails until you place its layer key in a tier.

## Solve loop (slack-freeze)

Sits on `optimize_stream` / native RoundingSat optimization. For tiers
`T_1..T_n` (top to bottom):

```
bounds = []  # accumulated hard upper bounds on higher tiers
for i, tier in enumerate(tiers):
    objective_i = sum(weight * var for (var, weight, key) in soft_violations
                      if key in tier.layers)
    # minimize objective_i subject to all previously-frozen bounds
    floor_i = roundingsat.optimize(objective_i, subject_to=bounds, time_limit=...)
    # slack-freeze: bound this tier at floor + epsilon, then move down
    bounds.append(objective_i <= floor_i + tier.epsilon)
final = minimize(objective_n, subject_to=bounds)  # or just take the last incumbent
```

Each tier is a full native-optimization pass; the accumulated `bounds` are hard
`weighted_sum_at_most` constraints added to the same formula (RoundingSat keeps
the formula warm between calls if we reuse the builder). `epsilon=0` gives strict
lexicographic for that tier; `epsilon>0` lets lower tiers buy small regressions.

**Cost:** n tiers ≈ n sequential optimization passes. With the current ~30-min
budgets that's expensive; mitigations: (a) short per-tier budgets for upper
tiers that converge fast, long budget for the bottom; (b) seed each pass with the
previous incumbent. This cost is the main reason to confirm scaled penalties are
insufficient before building this.

## Decision: implement later, only if needed

Deviation-scaled per-occurrence penalties (Friday=40, Sat/Sun=10, weekend/night
totals scaled, etc.) are already in. First run real workbooks and inspect whether
the single weighted sum already yields acceptable schedules. If a specific bad
trade shows up (e.g. coverage sacrificed for call preferences), build the tier
system and place the two offending families in different tiers. Until then, the
tier config stays collapsed to one tier (= off).
