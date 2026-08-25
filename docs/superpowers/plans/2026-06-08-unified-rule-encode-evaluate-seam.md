# Unified Rule encode/evaluate seam (implements ADR-0005, generalized)

## Problem

ADR-0005 decided each **Criterion** lives in ONE place exposing both its *encode* (compile to pseudo-Boolean) and *evaluate* (run against a concrete **Schedule**) manifestations, pinned by a contract test so the two cannot drift. The code never caught up:

- The 5 night criteria (Anaesthesia, Clinic, Stroke, Friday-Weekend-NCC1, Sunday-following) are implemented **twice, in two packages**: encode in `parafrost_scheduler/schedule_encoder.py:2274` (`_encode_night_policy_criteria`), evaluate in `scheduler/night_policy_types.py:149` (`criteria_for_assignment`). The evaluator's own comments say it "mirrors the encoder."
- **No contract test exists.** `tests/test_night_policy_stroke_criterion.py` / `_clinic_criterion.py` exercise the evaluator alone — they never call the encoder.
- Latent drift is already visible: stroke shift-match uses substring in the evaluator (`"Stroke" in svc and "Telestroke" not in svc`) vs exact shift-index in the encoder; anaesthesia/sunday-following use predicate helpers on one side, `schedule_types.py` frozensets on the other.
- The **weekend** soft criteria (NCC/Stroke alignment, mismatch, prevacation) live inline in `_encode_weekend_constraints` with **no evaluate path at all** — the workbook cannot even display them.
- Per the grilling: `evaluate()` against an arbitrary **Schedule** is a missing *capability*, not just a drift guard. An **Imported** fellow's frozen weeks are skipped by encoding (ADR-0002), so nothing validates them today; a hand-edit that overloads a fellow, or an externally-supplied schedule fed back in, has no checker.

## Design (settled in grilling)

**One object, two readings.** A **Constraint** and a **Criterion** are the same object differentiated by **Strength** (see CONTEXT.md, updated this session). Every rule exposes:
- `encode(sink, vars, params)` — emits pseudo-Boolean form to a **Constraint Sink**.
- `evaluate(schedule, params) -> value` — runs against a concrete **Schedule**.

**Strength decides what an evaluated violation means:** hard ⇒ Schedule invalid; soft ⇒ Schedule penalized. This yields a `validate(schedule)` driver that is the evaluate-side mirror of `build_full_schedule_opb`: walk the registry, partition violations by Strength.

**Declarative archetype, not an expression language.** "Everything configurable" = identify the right abstractions and extract them into a fixed, code-defined **Rule Shape** vocabulary (night-before-gating-service, role-holder-on-night, following-week-service, weekend-alignment, count-band, block-rotation, …). Config *selects and parameterizes* a shape (dow filter, gating week-offset, **Shift** set, **Weekend Role**, exemption, weight). A rule fitting an existing shape is pure config; a genuinely new shape is one new code location carrying encode + evaluate + a contract test. This honors ADR-0003 (declarative archetype) and ADR-0005 (no open expression language). Nothing rule-shaped may remain hardcoded inline in the encoder.

**The seam breaks the import cycle.** Rule objects + the shape vocabulary live in a dependency-free home that exposes a `ConstraintSink` Protocol. The Protocol surface, derived from `OpbBuilder` in `opb_encoder.py`: `new_var()`, `add_unit(lit)`, `at_most_k(lits, k)`, `at_least_k(lits, k)`, `weighted_sum_at_most(weighted_lits, bound)`, `weighted_sum_at_least(weighted_lits, bound)`, and `soft(lit, weight)`. `OpbBuilder` satisfies the emit methods directly but has NO `soft` (soft penalties are an external `soft_violations` list today), so an adapter `OpbConstraintSink(opb, soft_violations)` delegates the emit methods and owns `soft` (appends `(lit, weight)`). `encode()` writes to the sink, never to `OpbBuilder` directly, so the rules home imports nothing from the solver package. The encoder injects the adapter as the sink and becomes a **generic interpreter** over the registry.

**Naming decisions (settled this session):**
- New rules home = **`schedule_rules`** (Rule Shape vocabulary + `ConstraintSink` Protocol). Created in Slice 0 — on the critical path.
- Package rename: `scheduler` → **`schedule_model`** (domain model, evaluation, web), `parafrost_scheduler` → **`schedule_solver`** (encode→PB, roundingsat, decode, optimizer, workbook). Still deferred to a post-seam follow-up; the `OpbConstraintSink` adapter lives in the solver package under whichever name.

## Scope decisions

- **Boundary:** the registry holds *all* Constraints (structural + criteria), because `evaluate()` against arbitrary schedules is a real capability (validate Imported/hand-edited/exogenous schedules; hard-reject constraint violations, penalize criteria violations). Very few rules are true **Solver Invariants** (likely only "at most one Shift per week"); the rest are long-term **Standing Rules**.
- **Order:** tracer-bullet. Prove the full seam end-to-end on the 5 night criteria first (they alone have both manifestations live + an evaluator to pin against), then widen. Do NOT big-bang all ~24 kinds.

## Slices

### Slice 0 — ConstraintSink Protocol + rules home (tracer skeleton)
- Create the dependency-free rules home (module/package) with a `ConstraintSink` Protocol covering exactly the emit operations the night shapes need: `new_var`, `add_unit`/`forbid`, `at_most_k`, `weighted_sum_at_least`, `weighted_sum_at_most`, and a `soft(lit, weight)` sink for the soft-violation list. Mirror the `OpbBuilder` method names so `OpbBuilder` satisfies the Protocol with an adapter (or directly).
- Define the `Rule` base shape: `encode(sink, vars, params)` + `evaluate(schedule, params)`.
- **Verify:** `OpbBuilder` (or a thin adapter) type-checks as a `ConstraintSink`. No import of the solver package from the rules home (grep the import graph).

### Slice 1 — Stroke criterion as the first co-located Rule (tracer bullet)
Stroke is the richest of the 5 (two rules: night-before-stroke-workday + weekend-Stroke-Saturday; dual-stroke exemption; the prior false-red bug). Proving it proves the pattern.
- Implement `StrokeCriterion` (or `NightBeforeGatingService` + `WeekendRoleHolderOnNight` shapes parameterized for Stroke) with both manifestations, lifting logic out of `_encode_night_policy_criteria` (the stroke blocks, lines ~2355–2402) and `criteria_for_assignment` (the stroke block, lines ~200–233).
- Resolve the substring-vs-index divergence: pick ONE shift-match (exact `"Stroke"` shift, exempting Telestroke) used by both manifestations.
- Encoder calls the new rule via the sink for the stroke portion; evaluator's stroke branch delegates to the rule's `evaluate`.
- **Contract test (the load-bearing artifact):** build a small `ScheduleSolverConfig`, encode via the rule, solve, decode to a concrete Schedule; independently run the rule's `evaluate` on that Schedule; assert the solver's stroke penalty count == the evaluated stroke violation count. Include the dual-stroke and weekend-NCC1-not-penalized cases the existing tests cover.
- **Verify:** existing `test_night_policy_stroke_criterion.py` still passes (evaluate side unchanged in behavior); new contract test passes; a full solve on the real config produces identical stroke counts to `main` (regression).

### Slice 2 — Remaining 4 night criteria through the same seam
- Port Anaesthesia, Clinic, Friday-Weekend-NCC1, Sunday-following into rules. Fold the `schedule_types.py` frozenset / predicate-helper divergence into single per-rule definitions.
- `_encode_night_policy_criteria` becomes a loop over the night-criterion registry writing to the sink; `criteria_for_assignment` becomes a loop calling each rule's `evaluate`. Both lose their hand-written per-criterion bodies.
- Contract test parameterized across all 5 criteria.
- **Verify:** full solve stroke/clinic/anaesthesia/friday/sunday counts identical to `main`; workbook colorer output unchanged on a reference schedule.

### Slice 3 — Weekend soft criteria gain an evaluate path + display
Classification (from exploration): weekend soft criteria split into **cell-localizable** ("the weekend itself is at fault") vs **aggregate/spacing** (no single guilty cell).
- **Port + display (cell-localizable):**
  - NCC weekday/weekend role misalignment — `_encode_ncc_weekend_alignment` (1422–1462); guilty cell = week `w`, the Weekend NCC1/NCC2 column.
  - Stroke weekday/weekend role misalignment — `_encode_stroke_weekend_alignment` (1464–1500); guilty cell = week `w`, Weekend Stroke column.
  - Shared weekend-role/weekday mismatch — `_encode_weekend_mismatch_penalty` (1865–1903); guilty cell = week `w`, the mismatching Weekend Role column.
  - Pre-vacation weekend call — `_encode_prevacation_weekend_penalty` (1739–1780); guilty cell = week `w-1`, whichever Weekend Role the fellow holds before the vacation week `w`.
  - NOTE: the three alignment/mismatch criteria overlap (Stroke-align *stacks on* the shared mismatch). Consolidate into ONE `weekend-role/weekday-service alignment` Rule Shape parameterized per role, not three separate rules.

**Slice 3 RESOLUTION (2026-06-09): consolidation deferred — it is solve-changing, not neutral.** Reading the actual encoder, the three "alignment" functions are NOT one shape: they carry deliberately-stacking weights (shared `_encode_weekend_mismatch_penalty` w=`weekend_mismatch_weight`=20 on all three roles; `_encode_ncc_weekend_alignment` NCC1↔NCC2 cross w=`ncc_weekend_misalign_penalty`=10; `_encode_stroke_weekend_alignment` w=`stroke_weekend_misalign_penalty`=40 stacking ON TOP of the mismatch's Stroke case). Merging them changes the objective and fails the byte-neutral gate. So Slice 3 scoped to the user's actual ask — *visible weekend faults* — and ported the two genuinely cell-localizable, display-worthy criteria:
- **`WeekendRoleMismatchCriterion`** (`schedule_rules/criteria/weekend_mismatch.py`) — replaces `_encode_weekend_mismatch_penalty`'s body; encode byte-neutral (incl. the `weekday_var==0` always-mismatch unit-penalty branch). evaluate net-new.
- **`PrevacationWeekendCriterion`** (`schedule_rules/criteria/prevacation_weekend.py`) — replaces `_encode_prevacation_weekend_penalty`'s body; encode byte-neutral. evaluate net-new.
- LEFT as encoder-only solver gradient (NOT faults; not painted): `_encode_ncc_weekend_alignment` (NCC1↔NCC2 is a preference among *valid* assignments) and `_encode_stroke_weekend_alignment` (its fault is already the mismatch flag; the extra weight is just a stronger pull). Consolidating the three into one parameterized shape with explicit per-role weights is a separate, solve-affecting refactor — out of this slice.
- **Display:** `weekend_criteria_for_role(parsed, week, role, fellow, weekend_solution=...)` in `night_policy_types.py` is the weekend analogue of `criteria_for_assignment`; the workbook colors the Weekend cell red (matching the night-cell red-font convention) when either weekend fault fires. Verified by a real-artifact test (`tests/test_weekend_workbook_display.py`) that generates the actual `.xlsx` and inspects cell fonts.
- Encoder relocation verified multiset-neutral (49594 vars / 152209 constraints). Full suite 448 passed (+13 over Slice 2: 9 weekend contract + 4 display). Red-green verified: corrupting the mismatch geometry fails contract AND display tests together.
- **Port penalty-only (aggregate/spacing — NO display, no guilty cell):** buffered-consecutive-weekends (`_encode_buffered_consecutive_pair`, 1350–1414), weekend-total band (`_weekend_total_band`, 1570–1587). (at-most-2-in-4 is hard, not soft — leave as-is.)
- Evaluate side is net-new (the agent confirmed NO weekend evaluate path exists today — encode-only). Wire the cell-localizable ones into the workbook colorer alongside the night criteria.
- Contract test per weekend criterion.
- **Verify:** solve weekend penalties identical to `main`; new evaluate path agrees with encoder via contract test; workbook colors the four cell-localizable weekend violations on a reference schedule.

### Rule Shape archetype taxonomy (mapped 2026-06-09)
The 24 handler kinds are INSTANCES of ~9 generic archetypes (a kind like `jr_ncc_before_swing` is just "N weeks of X before any Y, for fellows F"; `scvmc_second_half` is "shift X in weeks W for fellows F"). Archetypes, with the kinds they subsume:
1. **WindowedCountBand** — count of shift-set S in window/block W for fellows F satisfies a relation (=, ≤, ≥, ∈[lo,hi]). Largest cluster: `shift_total`, `staffing_per_week`, `nir_one_week_per_half`, `scvmc_second_half`, `stroke_shift_coverage`, `block_shift_count`, `fourth_block_two_micu` (lo=hi count-exact), service_profile totals/window_totals/active_blocks (active_blocks = conditional band).
2. **WindowedBalance** — |count_a − count_b| ≤ max_diff across two windows. `windowed_balance`, `comparable_half_year`.
3. **CoverageWeeks** — # weeks where shift S has ≥1 fellow ≥ floor (uncovered minimized/bounded). `minimize_uncovered`, `coverage_target`, `ncc_coverage` (swing part).
4. **FullAssignment** — each fellow exactly one shift/week (the core **Solver Invariant**; the Aditya-overload case).
5. **PinForbid** — fellow(s) on shift in week W (pin) / never on shift-set S (forbid). `specific_assignment`, `zero_shifts`, `isc` (pin+forbid composite), `stroke_no_block_one_ncc` (forbid), prereq's first-N-weeks forbid.
6. **Prerequisite** — N weeks of prereq shift-set X before any week of target Y, fellows F. `jr_ncc_before_swing`, `prerequisite`.
7. **Supervision** — ADR-0004 (already a named archetype). `ncc_stroke_oversight`.
8. **MaxConsecutive** — no >K consecutive weeks on shift-set S, fellows F. `max_consecutive`.
9. **BlockSetChoice / AllOrNoneBlock** — block-structure shapes. `block_shift_set_choice`, `all_or_none_block`.
Also (from Slice 3, corrected): the weekend alignment functions (`weekend_mismatch`, `ncc_weekend_alignment`, `stroke_weekend_alignment`) are three INSTANCES of one **WeekendRoleAlignment** archetype (params: role-set, matching-shift, weight) — not three types, and not one merged constraint.

### Slice 4 — Structural Constraints become evaluable + `validate(schedule)` driver
**Scope decision (2026-06-09):** the capability goal is "check an Imported/hand-edited/exogenous Schedule against the hard rules." `evaluate()` is archetype-independent (compute a rule's violation on a concrete schedule); delivering the capability does NOT require byte-neutral relocation of all 24 encoders. So Slice 4:
- Builds the `validate(schedule) -> ValidationResult` driver (walk the active constraints, evaluate each, partition by Strength: hard ⇒ invalid, soft ⇒ penalty total).
- Implements `evaluate()` for the archetypes a bad import/hand-edit realistically breaks, dispatched by kind, starting with **FullAssignment** (the at-most/exactly-one-shift Solver Invariant — Aditya), **PinForbid** (`specific_assignment`/`zero_shifts`), and **WindowedCountBand** (`shift_total`/`staffing_per_week` — over/under service counts). These cover the realistic validation surface.
- evaluate() for each archetype is co-located with a stub/handle for its encode (full byte-neutral encode relocation of every archetype is its own follow-on; not required for the capability and explicitly deferred to keep this slice shippable).
- The bespoke-geometry kinds map to archetypes 6–9 and are added incrementally; documented as covered-by-archetype, evaluate-pending.
ORIGINAL Slice 4 text follows:
- Give the structural kinds (`full_assignment`, `ncc_coverage`, `max_consecutive`, `block_rotation`, `all_or_none_block`, `specific_assignment`, `shift_total`, …) an `evaluate(schedule)` manifestation (encode already exists via the `handlers` dict).
- Build `validate(schedule) -> ValidationResult` that walks the registry, evaluates each rule, partitions by Strength (hard violations ⇒ invalid; soft ⇒ penalty total). This is the entry point for checking Imported/hand-edited/exogenous schedules.
- Identify the true **Solver Invariants** (likely just at-most-one-shift-per-week) and confirm they too are evaluable (the Aditya-overload case).
- **Verify:** feed a known-good solver output through `validate` ⇒ zero hard violations, penalty total == solver objective; feed a hand-broken schedule (two shifts one week) ⇒ flagged invalid.

### Slice 5 — Fail-fast on unknown kind (DONE 2026-06-09)
**Shipped (the correctness hole — silent rule loss):**
- Weekly dispatch (`_encode_weekly_rules`): unknown `constraint.kind` now RAISES `ValueError` naming the kind + listing known kinds (was: stderr warn + `continue`, which silently dropped a typo'd rule from the Schedule). Removed the now-dead `import sys`.
- Call-rule dispatch (`_encode_call_rules`): unknown `rule["type"]` now RAISES, via an explicit `_KNOWN_TYPES` check placed BEFORE the `fellow not in fellow_names` early-continue (so a fellow-specific typo can't be masked). `active:False` rules still skipped first, by design.
- **Verified:** both fail-fast paths multiset-neutral on the production config (49594 vars / 152209 constraints, == pristine); full suite 468 passed; red-green confirmed for both guards.

**Scope decision (2026-06-09): dispatch UNIFICATION deferred to its own slice.** The two dispatch systems use different data models — weekly uses typed `SemanticConstraint` + a handler dict; call_rules uses raw dicts + an if/elif chain on `rule["type"]`. Collapsing them means migrating call_rules to `SemanticConstraint` (touching web_app, palette, solver_bridge) and byte-neutral relocation of ~8 call-rule types — a large refactor with real regression surface. The fail-fast (the actual correctness win) is done on BOTH paths; unification is tracked below.

**Still inline / hardcoded (NOT yet excised — folded into the archetype work):** `_encode_dual_stroke_helena` (tiered penalty constants `DUAL_STROKE_*`), the wk26/27 toggle (`STROKE_WK2627_*`). These are bespoke encoder-only rules; the dow filters that WERE inline already moved into the criteria objects (Slices 1–2). Excising the remaining constants is best done WHEN building their archetypes (the "everything configurable" goal), not as standalone constant-shuffling.

## Remaining work (post-Slice-5, tracked for future sessions)
- **Build the archetypes as parameterized Rule Shapes** (the "everything configurable" goal). The 24 kinds + 3 weekend-alignment instances reduce to ~9 archetypes (taxonomy above). Build each archetype ONCE as a co-located Rule (encode+evaluate), re-express the kinds as configured instances, excise the remaining inline constants (`_encode_dual_stroke_helena`, wk26/27) as part of their archetype. This is the larger architectural payoff the archetype reframing unlocked; each archetype is its own byte-neutral relocation slice (multiset gate).
- **Dispatch unification** — migrate `call_rules` (raw dicts) into the `SemanticConstraint` model + one handler registry, collapsing the two dispatch systems. Deferred from Slice 5 (large; touches web_app, palette, solver_bridge). Fail-fast already done on both paths.
- **Deterministic encoder iteration** — fix the set-iteration nondeterminism in service-profile/coverage `_on_indicator` so OPB output is byte-stable (would let byte-diff replace the multiset gate). Latent.

## Out of scope (tracked separately)
- Package rename (`scheduler`→`schedule_model`, `parafrost_scheduler`→`schedule_solver`) — mechanical, large diff, do after the seam work lands.
- Candidate #2 (Shift Attributes as data on the Shift, ADR-0003) — adjacent; the `Rule Shape` discipline here is the same select-and-parameterize pattern and should make it easier, but it's its own deepening.
- Candidate #4 (orchestrator seam completion, ADR-0006).

## Regression discipline
The right gate for an encoding-relocation slice is the **canonicalized OPB constraint multiset**, NOT byte-identical text and NOT criterion counts:
- **Byte-identical OPB is unachievable** — two pristine builds (no changes) already differ from each other. There is pre-existing build nondeterminism (set-iteration order in service-profile/coverage `_on_indicator` OR-clauses) that reorders ~1900 lines and shuffles terms within lines. (Worth fixing as its own task: deterministic iteration in those encoders. Latent, out of scope here.)
- **Criterion counts from a time-budgeted solve are unreproducible** — RoundingSat has no deterministic budget; identical 120s runs gave 259/276/234. A criterion-count fingerprint is only stable if solved to PROVEN optimality (hours, on Slurm).
- **The gate that works:** build the default-config OPB (`assemble_config(Path('workbook_partial_input6.xlsx'))` → `build_full_schedule_opb(config, objective=True)`), canonicalize each constraint line (sort the `coeff var` terms within the line; keep op+rhs), and compare the `collections.Counter` of canonical lines against a pristine build. Equal multiset ⇒ semantically neutral relocation. Slice 1 verified this way: changed build's multiset == pristine build's multiset, num_vars=49594, num_constraints=152209.
- The per-rule **contract test** (pin a concrete schedule → encode → solve → forced-soft-indicator count == `evaluate()` count, plus a HARD⇒UNSAT case) is the permanent ADR-0005 guard. Red-green verified for Stroke: corrupting the shared `gating_terms` fails BOTH the contract test and the legacy evaluator test — one geometry, both manifestations.

### Interpreter note
Canonical interpreter is **`.venv/bin/python` (3.12, all deps)**; run tests as `PYTHONPATH=src .venv/bin/python -m pytest tests/`. Bare `python` is 3.9 (the suite happens to pass there but it is NOT the project interpreter). Full-suite green baseline under `.venv`: **415 passed** (was 408 pre-Slice-0; +7 Slice-0 sink tests; Slice 1 added contract tests and the count stays 415 after the clinic/etc. remain untouched).
