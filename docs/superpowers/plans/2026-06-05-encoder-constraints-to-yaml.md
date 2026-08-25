# Plan: Migrate encoder-hardcoded scheduling policy into YAML config

**Date:** 2026-06-05
**Status:** Draft / proposal (no code written yet)
**Scope:** `new_approach/src/parafrost_scheduler/schedule_solver.py`, `src/scheduler/solver_bridge.py`,
`src/scheduler/palette_rules.py`, `config/standing/stanford-fellowship-v3.yaml`,
`config/annual/my-2026-2027-v3.yaml`, the `night_call_types`/`weekend_call_types` config dataclasses.

---

## 1. Summary

A large slice of scheduling *policy* — which weekday services block night/weekend call,
which next-week services are "non-preferred" after a Sunday night, which rotations buffer a
consecutive-weekend pair, and several soft-penalty weights — is hardcoded as module-level
`frozenset` constants and inline integers in `schedule_solver.py` (the OPB encoder), invisible to
and untunable from YAML. Worse, **the YAML already contains rule types for most of this policy**
(`night_blocked_services`, `weekend_blocked_services`, `night_sunday_following`, `night_penalties`,
`night_holiday_eligibility`), and the bridge parses them into `NightSolverConfig` /
`WeekendSolverConfig` fields — **but the encoder never reads those fields**, so the YAML is dead
config and the constants silently win. This plan inventories every hardcoded item, explains the
lossy-`SHIFT_MAP` reason the constants exist, and lays out an incremental, SAT-verifiable migration
that first *removes the duplication* (wire the existing YAML through to the encoder on canonical
names) before adding any new schema.

---

## 2. Inventory

Line numbers verified against `schedule_solver.py` on branch `partial-import`, 2026-06-05.
"Category" is **policy** (a tunable business rule), **structural** (a correctness invariant of the
encoding), or **weight** (a soft-penalty coefficient).

### 2a. Curated shift-set constants (`schedule_solver.py` lines 66–97)

| Item | Lines | What it is | Category | Priority | Notes |
|---|---|---|---|---|---|
| `NIGHT_BLOCKED_SHIFTS` = {SICU, MICU, Vac, NS, SCVMC Rehab, NHS, AAN, RWC, NCS 2026} | 73–75 | Weekday services that block night call. Members NOT in `NIGHT_BLOCKED_ALL_WEEK` block only **Sun–Thu** (nights before a workday). Used at 1866, 1884. | policy | **High** | YAML `night_blocked_services` already exists but is **ignored by encoder** (see §4). |
| `NIGHT_BLOCKED_ALL_WEEK` = {SICU, MICU, Vac, NS} | 76 | Subset blocking **all 7 nights**. Used at 1865, 1878. | policy + structural | **High** | The Sun–Thu vs all-7 distinction is load-bearing (§6). |
| `WEEKEND_BLOCKED_SHIFTS` = {SICU, MICU, NS, Anaesthesia, Vac} | 91 | Weekday services that block weekend call roles. Used at 1700. | policy | **High** | YAML `weekend_blocked_services` exists but is **ignored by encoder** (§4). |
| `NON_PREFERRED_SUNDAY_FOLLOWING` = {Anaesthesia, Clinic/Elective, Telestroke/Clinic, Vac, NS, NIR, SICU, SCVMC Rehab} | 82–84 | Target set for the `sunday_following` soft criterion: penalize Sunday-night fellow if NEXT week's service is in this set. Used at 2160. | policy | **High** | This is the **complement** of YAML `night_sunday_following.preferred_services`; encoder uses the constant, not the YAML (§4). |
| `CONSECUTIVE_WEEKEND_BUFFER_SHIFTS` = {Vac, ISC, APBN, NHS, AAN, NCS 2026} | 95–97 | "Light" rotations that exempt a consecutive-weekend pair from penalty. Used at 1605. | policy | **High** | **No YAML representation at all** — brand-new policy, code-only. |
| `ANAESTHESIA_SHIFTS` = {Anaesthesia} | 77 | Triggers the anaesthesia night criterion (weekday nights). Used at 2033. | policy | Medium | Single-element; likely stable. |
| `CLINIC_SHIFTS` = {Clinic/Elective} | 80 | Triggers the clinic night criterion. Telestroke/Clinic deliberately excluded. Used at 2034. | policy | Medium | Subtle Telestroke exclusion is load-bearing (§6). |
| `STROKE_SHIFTS` = {Stroke} | 81 | Defined but the encoder actually uses `shift_idx.get("Stroke")` directly (2035), not this constant. | policy | Low | **Effectively dead** in the encoder — see §4. |
| `HOLIDAY_ELIGIBLE_SHIFTS` = {NCC1, NCC2, Stroke} | 85 | Only these services may work holiday nights. Used at 1894. | policy | Medium | YAML `night_holiday_eligibility.allowed_services` exists but is **ignored by encoder** (§4). |

### 2b. Hardcoded weights (`schedule_solver.py` lines 104–112)

| Item | Lines | Value | Category | Priority | Notes |
|---|---|---|---|---|---|
| `DEFAULT_WEEKLY_SOFT_WEIGHT` | 104 | 100 | weight | Medium | Used as the default for `weekly_soft_weight` and reused as night/weekend total weight. |
| `DEFAULT_WEEKEND_MISMATCH_WEIGHT` | 105 | 20 | weight | Low | weekend role-vs-service mismatch + consecutive-pair penalty. |
| `DEFAULT_SWING_UNCOVERED_WEIGHT` | 106 | 100 | weight | Low | |
| `DEFAULT_WEEKEND_NIGHT_FRIDAY_WEIGHT` | 110 | 40 | weight | Medium | Fri night holding a weekend role. |
| `DEFAULT_WEEKEND_NIGHT_SATURDAY_WEIGHT` | 111 | 10 | weight | Low | Sat-night role-preference nudge. |
| `DEFAULT_WEEKEND_NIGHT_SUNDAY_WEIGHT` | 112 | 10 | weight | Low | Sun-night role-preference nudge. |
| `NightPolicyWeights` (anaesthesia/clinic/stroke/sunday_following/friday_weekend_ncc1) | `night_policy_types.py` 39–58 | 1/1/5/1/40 | weight | **High to fix wiring** | These ARE config-shaped (`from_config(penalty_weights)`) and the encoder DOES read them via `config.night_weights` (line 2030). **But the bridge never builds `night_weights`** — see §4, this is dead YAML. |

### 2c. Hardcoded structural constraints (no clean YAML representation)

| Item | Lines | What it is | Category | Priority |
|---|---|---|---|---|
| Exactly-one-fellow-per-night | 1841–1858 | Hard night coverage (`exactly_one`). | structural | Low (keep hardcoded) |
| Exactly-one-per-weekend-role | 1476–1488 | Hard weekend coverage. | structural | Low (keep hardcoded) |
| Night spacing (≤1 per 3-day window) | 1967–1976 | **Config-driven** via `spacing_max_nights`/`spacing_window_days` from `night_spacing` YAML rule (bridge 430–432). Only the iteration/encoding is hardcoded. | structural (params config) | **Done** — verify, no work |
| Weekend "at most 2 in any 4 weeks" | 1620–1629 | Hard window cap, window=4 and k=2 hardcoded. | policy + structural | Medium |
| Buffered no-consecutive-weekends (`_encode_buffered_consecutive_pair`) + `weekend_consecutive_hard` flag | 1386–1455, 1588–1618 | Buffer logic + hard/soft flag (default soft). Flag is a dataclass field, NOT from YAML. | policy + structural | High (tied to buffer set) |
| Weekend eligibility tiers (always/telestroke/stroke_only) | 1731–1768 | Encoder reads `wk_config.*_eligible`; the bridge **does** populate `always_stroke_eligible`/`telestroke_stroke_eligible` from rules (`_build_weekend_config`, solver_bridge 395–409). `stroke_only_eligible` is always empty in the bridge path. | policy (partly config) | Medium |
| Sat-night-must-be-Weekend-NCC1/NCC2 (hard) | 2241–2268, flag 205 | Controlled by `weekend_night_saturday_hard` (default True) dataclass flag, not YAML. | policy | Medium |
| Sun-night-should-be-Weekend-Stroke (soft) | 2270–2300, flag 206 | Controlled by `weekend_night_sunday_hard` (default False) dataclass flag, not YAML. | policy | Medium |
| Weekend per-fellow total ±tolerance band | 1502–1551, field 218 | `weekend_total_tolerance` (default 1) dataclass field, not YAML. | policy | Medium |
| First-week NCC_JR/STROKE night restriction | 1904–1917 | Group names "NCC_JR"/"STROKE" hardcoded. | policy | Low |
| Holiday-eligible-services (encoder) | 1892–1902 | Uses `HOLIDAY_ELIGIBLE_SHIFTS` constant. | policy | Medium (see 2a) |

---

## 3. The SHIFT_MAP / lossy-canonicalization problem

`SHIFT_MAP` lives in the run scripts (e.g. `run_v3_optimize_wb2.py:33`):

```python
SHIFT_MAP = {
    'MSICU': 'MICU', 'Anesthesia': 'Anaesthesia', 'Vacation': 'Vac',
    'Elective': 'Elec', 'Elective/SICU': 'SICU', 'NS SCVMC': 'NS',
}
```

It is applied when importing a workbook (`run_v3_optimize_wb2.py:60`) to translate **raw** workbook
shift names into the **canonical** names the encoder reasons about. It is **lossy** in two ways:

1. **Many-to-one collapse:** `Elective` → `Elec` *and* `Elective/SICU` → `SICU`. After mapping, an
   `Elec` cell cannot be distinguished from a raw `Elective`, and a raw `Elective/SICU` is now
   indistinguishable from a raw `SICU`. Round-tripping canonical→raw is impossible.
2. **Rename:** `MSICU` → `MICU`, `Anesthesia` → `Anaesthesia`, `NS SCVMC` → `NS`, `Vacation` → `Vac`.

The code comment at `schedule_solver.py:66–72` is explicit: the constants are curated on canonical
names *precisely because* deriving them from the config's raw-named `night_blocked_services` rule
would over-block (e.g. mapping the raw substring rule `SICU` would also catch `Elective/SICU`-derived
`SICU`). The decoder-side helpers (`is_night_blocked`, `is_weekend_blocked`,
`is_preferred_sunday_following_service` in `night_call_types.py` / `weekend_call_types.py` /
`call_schedule_common.py`) operate on **raw** names with `exact_services` like `"NS SCVMC"`, `"MSICU"`,
`"Vacation"` — confirming the existing YAML rules are written in the **raw** vocabulary, while the
encoder needs the **canonical** vocabulary. **This vocabulary mismatch is the root cause of the
duplication.**

### Proposal: a canonical-shift vocabulary in config

- **Single source of truth for canonical names.** The annual config already lists `shifts:` (the
  canonical shift list passed to the solver). Treat that list as the authoritative canonical
  vocabulary. Any encoder-facing policy rule must reference only names in `shifts:`.
- **Move `SHIFT_MAP` into config**, not run scripts. Add an optional `shift_aliases:` mapping
  (raw → canonical) to the annual config so import normalization is declared once and is auditable.
  The lossy collapse stays explicit and reviewable.
- **Add a new rule *flavor* that is canonical-typed.** Rather than retrofit the raw-named
  `night_blocked_services` (which the decoder still needs in raw form for coloring imported CSVs),
  introduce encoder-facing rules whose `shifts:` are validated against the canonical `shifts:` list at
  load time (§4 schema). A name not in `shifts:` is a hard load-time error — no silent over/under-block.
- **Validation at load time.** In `solver_bridge`, after assembling the canonical `shifts` list,
  assert every shift referenced by an encoder-facing policy rule is in it. This catches typos
  (`"Vacation"` instead of `"Vac"`) that today silently no-op (the constants use `if s in shift_idx`,
  so an unknown name is simply dropped — see §6).

---

## 4. Duplication & dead config discovered (the key finding)

There are **two parallel policy systems** that do not talk to each other:

| YAML rule (parsed by bridge into `*SolverConfig` field) | Decoder/colorer path (uses it) | Encoder path (`schedule_solver.py`) | Verdict |
|---|---|---|---|
| `night_blocked_services` → `NightSolverConfig.blocking_exact_services` / `blocking_substring_services` | `is_night_blocked()` reads it (raw names) | Encoder uses `NIGHT_BLOCKED_SHIFTS` / `NIGHT_BLOCKED_ALL_WEEK` constants; **never reads the config field** | **DEAD for the solve** |
| `weekend_blocked_services` → `WeekendSolverConfig.blocking_exact_services` / `blocking_substring_services` | `is_weekend_blocked()` reads it | Encoder uses `WEEKEND_BLOCKED_SHIFTS` constant; **never reads the config field** | **DEAD for the solve** |
| `night_sunday_following` → `NightSolverConfig.sunday_preferred_services` | `is_preferred_sunday_following_service()` reads it | Encoder uses `NON_PREFERRED_SUNDAY_FOLLOWING` constant; **never reads the config field** | **DEAD for the solve** |
| `night_holiday_eligibility` → `NightSolverConfig.holiday_allowed_services` | `is_night_holiday_eligible()` reads it | Encoder uses `HOLIDAY_ELIGIBLE_SHIFTS` constant; **never reads the config field** | **DEAD for the solve** |
| `night_penalties.weights` → `NightSolverConfig.penalty_weights` | `criteria_counts_for_solution()` can take a `NightPolicyWeights` | Encoder reads `config.night_weights` (a separate `NightPolicyWeights`), but **the bridge never sets `night_weights`**, so it always defaults to 1/1/5/1/40 | **DEAD for the solve** |
| `weekend_penalties.weights` → `WeekendSolverConfig.penalty_weights` | decoder summaries | Encoder uses `weekend_mismatch_weight` / `weekend_night_*_weight` dataclass defaults; **never reads `WeekendSolverConfig.penalty_weights`** | **DEAD for the solve** |

**Confirmed by:** `grep` for `night_config.` / `wk_config.` usages in `schedule_solver.py` shows the
encoder reads only `ccm_fellows`, `total_nights`, `friday_nights`, multisets, `spacing_max_nights`,
`spacing_window_days`, `horizon_start_date` from `night_config`, and `ncc_totals`, `stroke_totals`,
`stroke_cohort*`, `*_eligible` from `weekend_config`. None of the blocking/holiday/penalty/sunday
fields are referenced. `solver_bridge.build_solver_config_from_request` (line 113) constructs
`ScheduleSolverConfig(...)` without `night_weights` or `night_hard_criteria` — only `cli.py` (the
night-only standalone CLI) passes them.

**Additional dead-config / format bug found:** the *standing* YAML writes blocking rules at top level
in **snake_case** (`exact_services:`, `substring_services:` — which the bridge `_apply_night_rules`
reads via `rule.get("exact_services")`), but the *annual* YAML (UI-serialized) nests them under
`params:` in **camelCase** (`params: {exactServices: [...], substringServices: [...]}`). The bridge
does **not** read `params.exactServices`, so the annual night/weekend rule overrides parse to empty
and silently fall back to defaults. Since the bridge merges annual-over-standing
(`merged_night_rules = annual_night_rules if annual_night_rules else standing_night_rules`), an annual
override actually **replaces** the working standing rule with an empty one. This is moot for the
encoder (which ignores all of it) but will become a real bug the moment we wire the field through —
**fix the schema/parse mismatch as part of Phase 0.**

**`STROKE_SHIFTS` is also effectively dead** even within the encoder: the night/clinic/stroke
criterion code uses `shift_idx.get("Stroke")` directly (line 2035), not the `STROKE_SHIFTS` constant.

---

## 5. Proposed YAML schema additions

Guiding principle: **canonical names only**, validated at load time, encoder-facing. Keep the existing
raw-named `*_blocked_services` rules for the decoder/colorer, OR (preferred) unify on canonical and add
`shift_aliases` for import normalization (decided in Phase 0).

### 5a. Night-blocking with the Sun–Thu vs all-7 distinction (High)

The single most subtle item. Today one constant (`NIGHT_BLOCKED_ALL_WEEK`) is a subset of another
(`NIGHT_BLOCKED_SHIFTS`). Expose both, explicitly, as two scopes:

```yaml
night_rules:
  - name: Night blocked services (canonical)
    type: night_blocked_services_canonical   # NEW encoder-facing type
    groups: [STROKE, NCC_JR, NCC_SR, NH, CCM]
    # Block only the nights before a workday (Sun-prev .. Thu):
    weekday_blocked: [SICU, MICU, Vac, NS, "SCVMC Rehab", NHS, AAN, RWC, "NCS 2026"]
    # Block ALL 7 nights of the week (fellow is fully off-service):
    all_week_blocked: [SICU, MICU, Vac, NS]
    strength: hard
    active: true
```

Loader contract: every name in `weekday_blocked`/`all_week_blocked` MUST appear in the canonical
`shifts:` list (hard error otherwise). `all_week_blocked` SHOULD be a subset of `weekday_blocked`
(warn if not). Encoder reads these instead of the two constants.

### 5b. Sunday-following (High) — store the preferred set, derive the complement

The encoder penalizes the **complement** (non-preferred). To avoid the author having to enumerate the
complement, keep the YAML in the existing *preferred* form and compute the non-preferred set in the
encoder as `all_shifts - preferred`:

```yaml
  - name: Sunday following preference (canonical)
    type: night_sunday_following_canonical
    groups: [STROKE, NCC_JR, NCC_SR, NH]
    preferred_services: [Elec, "Telestroke/Clinic", "Clinic/Elective", "SCVMC Rehab", NIR, ISC, Vac]
    strength: soft
    penalty: 1
    active: true
```

NOTE a discrepancy to resolve: the current `NON_PREFERRED_SUNDAY_FOLLOWING` constant *includes*
`Telestroke/Clinic` and `Clinic/Elective`, yet the standing YAML's `preferred_services` *also* lists
them as preferred. The two systems disagree today (harmless because the encoder uses the constant). The
migration must pick the intended semantics (the constant is the live behaviour) and write the
complement accordingly — **flag for the scheduler to confirm**.

### 5c. Consecutive-weekend buffer + hard/soft flag (High)

```yaml
weekend_rules:
  - name: Consecutive weekend buffer
    type: weekend_consecutive_buffer        # NEW
    groups: [STROKE, NCC_JR, NCC_SR, NH]
    buffer_services: [Vac, ISC, APBN, NHS, AAN, "NCS 2026"]
    mode: soft            # soft (penalize un-buffered pair) | hard (forbid it)
    penalty: 20           # weekend_mismatch_weight today
    active: true
```

### 5d. Weekend-blocking, holiday eligibility, criterion-trigger sets (Medium)

```yaml
  - name: Weekend blocked services (canonical)
    type: weekend_blocked_services_canonical
    groups: [STROKE, NCC_JR, NCC_SR, NH, CCM]
    blocked_services: [SICU, MICU, NS, Anaesthesia, Vac]
    strength: hard

night_rules:
  - name: Holiday eligibility (canonical)
    type: night_holiday_eligibility_canonical
    allowed_services: [NCC1, NCC2, Stroke]
    strength: hard
  - name: Night criterion triggers
    type: night_criterion_triggers          # anaesthesia / clinic / stroke trigger sets
    anaesthesia_services: [Anaesthesia]
    clinic_services: ["Clinic/Elective"]    # deliberately NOT Telestroke/Clinic (see §6)
    stroke_services: [Stroke]
```

### 5e. Weights (Medium) — wire `night_weights` through the bridge

No new schema needed; the `night_penalties.weights` rule already exists. The fix is **wiring**: have
`solver_bridge` build `NightPolicyWeights.from_config(night_config.penalty_weights)` and pass it as
`ScheduleSolverConfig(night_weights=...)`. Likewise surface `weekend_total_tolerance`,
`weekend_consecutive_hard`, `weekend_night_saturday_hard`, `weekend_night_sunday_hard`,
`weekly_soft_weight`, and the `weekend_night_*_weight` values as optional top-level config keys with
the current defaults.

---

## 6. Risks & open questions

### Load-bearing correctness (do NOT casually loosen)

- **Sun–Thu vs all-7 night blocking.** Verified at `schedule_solver.py:1865–1890`: services in
  `NIGHT_BLOCKED_ALL_WEEK` (SICU/MICU/Vac/NS) get `at_most_k([xs..., xn], 1)` for **every** day `d`
  of the week (1878–1880), while services only in `NIGHT_BLOCKED_SHIFTS` are constrained **only when
  `dow not in (4,5)`** using `next_day_week` (1882–1886) — i.e. they block Sun/Mon/Tue/Wed/Thu nights
  (the night *before* a workday), NOT Fri/Sat nights. `ISC` is special-cased identically (1888–1890).
  Any YAML schema MUST preserve both scopes; collapsing them to one list would either let ICU fellows
  take Fri/Sat call (under-block) or forbid call before weekends for light rotations (over-block).
- **Clinic criterion excludes Telestroke/Clinic.** `CLINIC_SHIFTS = {Clinic/Elective}` only
  (line 80, comment 78–79). Telestroke/Clinic fellows may take any weekday night. If a YAML author
  adds `Telestroke/Clinic` to `clinic_services`, they will wrongly penalize valid assignments.
  Document this; consider a load-time warning.
- **The constants silently drop unknown names** (`if s in shift_idx`). A typo today is a no-op, not an
  error. Once migrated, prefer a hard load-time error so a mistyped service can't silently disable a
  blocking rule.

### Where config flexibility can create infeasible or wrong schedules

- **Hardening the consecutive-weekend rule is known-UNSAT.** Comments at 207–215 and 1588–1595 record
  that both a blanket-hard and a buffered-hard consecutive rule are **proven UNSAT on workbook6** (Slurm
  SAT bisect). Exposing `mode: hard` lets a user produce UNSAT. Mitigation: default `soft`, document the
  risk, and (Phase 5) add a pre-solve feasibility note.
- **Hard Sunday-night-must-be-Weekend-Stroke is INFEASIBLE on WB5** (comment 198–204): forcing the
  single weekend-Stroke holder onto Sunday night every week collides with night spacing + NS-all-week
  blocking + week-0 Elec pins. Keep `weekend_night_sunday_hard: false` default; warn if flipped.
- **Hard `sunday_following`** conflicts with the Stroke weekend-Sunday preference (comment 180–185):
  its non-preferred set includes Telestroke/Clinic + Clinic/Elective, the rotations Stroke fellows
  spend ~23/53 weeks on. Keep soft.
- **Weekend total band tolerance.** Lowering `weekend_total_tolerance` to 0 turns the soft nudge into a
  rigid per-fellow exact count that may over-constrain coverage. Targets are tuned to sum slightly above
  demand (comment 1502–1510); a user shrinking the band can make it UNSAT.

### Open questions (state, don't guess)

1. **Sunday-following semantics conflict (§5b):** the live constant and the standing YAML disagree on
   whether Telestroke/Clinic and Clinic/Elective are preferred. Which is correct? (The constant is what
   actually runs.) Needs scheduler confirmation before writing the canonical rule.
2. **Decoder vs encoder vocabulary:** do we keep the raw-named `*_blocked_services` rules for CSV
   coloring AND add canonical encoder rules (two rules, two vocabularies), or unify on canonical +
   `shift_aliases` and refactor the decoder helpers to map raw→canonical first? Unifying is cleaner but
   touches the colorer and its tests.
3. **Annual `params:` camelCase parse bug (§4):** fix the bridge to read both shapes, or normalize the
   UI serializer to emit top-level snake_case? Either is fine; pick one in Phase 0.
4. **`STROKE_SHIFTS` dead constant:** delete it, or wire it into the stroke criterion (currently uses
   `shift_idx.get("Stroke")`)? Low stakes; recommend delete during Phase 2.

---

## 7. Migration phases

Constraints on every phase:
- **TDD:** add/extend tests under `tests/` and `new_approach/tests/`; run
  `PYTHONPATH=src:new_approach/src python -m pytest`.
- **SAT-check on Slurm** for any change that can alter the feasible region. RoundingSat is
  single-threaded; submit to `defq -A prescient1`. A phase is "done" only when the production workbook
  (WB6) still solves SAT and the optimized soft-penalty total has not regressed.
- **Each phase ships independently** and is a no-op behaviour change unless explicitly noted.

### Phase 0 — Stop the bleeding: fix the parse mismatch + add a regression guard (no behaviour change)
- Fix the annual `params:`/camelCase vs top-level/snake_case parse mismatch in `solver_bridge`
  (`_apply_night_rules` / `_apply_weekend_rules`) so the existing YAML at least parses consistently.
- Add a **test that asserts the encoder's effective blocking sets equal what the YAML declares** — this
  test will FAIL today (proving the dead-config gap) and becomes the green target for Phase 1. Mark it
  `xfail` with a reference to this plan until Phase 1.
- No SAT run needed (no feasible-region change); unit tests only.

### Phase 1 — Wire weights through (lowest-risk live change)
- Build `NightPolicyWeights.from_config(night_config.penalty_weights)` in `solver_bridge` and pass
  `ScheduleSolverConfig(night_weights=...)`. Surface `weekly_soft_weight`, `weekend_mismatch_weight`,
  `weekend_night_*_weight`, `weekend_total_tolerance`, and the three hard/soft flags as optional config
  keys defaulting to today's values.
- Because defaults match current constants, **SAT and optimum are unchanged** — verify on Slurm that
  WB6 soft total is identical. This is the safest first live wiring and proves the plumbing.

### Phase 2 — Migrate the High-priority shift-set policy onto canonical YAML
- Decide §6 OQ2 (unify vs dual vocabulary) and OQ3 (alias location). Add `shift_aliases` to annual
  config if unifying; move `SHIFT_MAP` out of run scripts.
- Implement `night_blocked_services_canonical` (with `weekday_blocked` + `all_week_blocked`),
  `weekend_blocked_services_canonical`, `night_sunday_following_canonical`, and
  `weekend_consecutive_buffer`. Add load-time validation against `shifts:`.
- Encoder reads the new config fields; **delete the four High constants** once parity is proven.
- Resolve the §5b Sunday-following semantics OQ before writing the complement.
- SAT-verify WB6 after each rule type is switched (one at a time) to localize any regression.

### Phase 3 — Medium-priority sets and flags
- Migrate `HOLIDAY_ELIGIBLE_SHIFTS`, `ANAESTHESIA_SHIFTS`, `CLINIC_SHIFTS`
  (`night_criterion_triggers`), the weekend "≤2 in 4 weeks" window params, and the Sat/Sun
  hard-flag / tolerance values into YAML. Delete the now-dead `STROKE_SHIFTS` constant.
- SAT-verify; defaults preserve behaviour.

### Phase 4 — Decoder/encoder vocabulary unification (if OQ2 = unify)
- Refactor `is_night_blocked` / `is_weekend_blocked` / `is_preferred_sunday_following_service` and the
  workbook colorer to map raw→canonical via `shift_aliases` first, so decoder and encoder share one
  canonical vocabulary and one set of YAML rules. Retire the raw-named duplicate rules.
- Heaviest test surface (colorer + summaries); SAT-verify and visually diff a colored workbook.

### Phase 5 — Guardrails for user-authored infeasibility
- Add load-time warnings for the known UNSAT/dangerous combos (§6): `consecutive mode: hard`,
  `weekend_night_sunday_hard: true`, hard `sunday_following`, `weekend_total_tolerance: 0`,
  `all_week_blocked` not a subset of `weekday_blocked`, `Telestroke/Clinic` in `clinic_services`.
- Optionally a fast pre-solve "smoke" feasibility probe before launching a full Slurm optimize.

---

## 8. Quick reference — verified line numbers (schedule_solver.py, branch partial-import)

- Constants: 66–112. Eligibility tiers: 1731–1768. Weekend blocking: 1689–1729 (uses 1700).
- Consecutive buffer: 1386–1455; driver 1588–1618 (buffer set 1605).
- Night blocking (all-7 vs Sun–Thu): 1860–1902 (sets 1865–1866; holiday 1892–1902).
- Night spacing (config-driven params): 1967–1976.
- Night policy criteria: 2015–2187 (anaesthesia 2062–2077, clinic 2079–2094, stroke 2096–2141,
  friday_weekend_ncc1 2143–2157, sunday_following 2159–2187).
- Weekend-night linking (Sat/Sun flags): 2190–2300.
- `ScheduleSolverConfig` flags/fields: 186 (`night_hard_criteria`), 205–206 (Sat/Sun hard),
  215 (`weekend_consecutive_hard`), 218 (`weekend_total_tolerance`).
- Bridge construction omitting `night_weights`: `solver_bridge.py:113`. Night/weekend rule parsing:
  `solver_bridge.py:419–479`.
