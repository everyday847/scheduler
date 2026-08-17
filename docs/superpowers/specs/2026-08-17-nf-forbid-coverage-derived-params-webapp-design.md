# NF forbid-coverage rule, derived-parameter model, and webapp modernization

Date: 2026-08-17
Branch: `partial-import`
Status: approved design (pre-plan)

## Goal

Two user-facing capabilities on the NF (`call_tier_day_granular`) scheduling model, plus
the webapp changes to drive them:

1. **Impose "forbidding" specific assignments** — e.g. "in weeks A–B, CCM (or Stroke)
   fellows can hold no call coverage" — the same class of rule earlier (non-NF) schedules
   used for holiday weeks.
2. **A config-level derived-parameter model** so the NF bands (which are really a central
   fair value ± tolerance, derived from weeks × density and the ⅓-of-service target) are
   computed from their semantic inputs — changing an input (e.g. NCC_JR weeks) moves every
   dependent band automatically, instead of being hand-edited (a footgun we already hit
   when Stroke went 6→8 weeks and its NF band/cap had to be recomputed by hand).
3. **Webapp modernization** so the 4-group NF config loads/edits/solves in-app, all rule
   kinds (including the new forbid rule and the solver_options/derived params) are editable
   through one consistent card-based rules-editing paradigm, and the in-progress rules-editing
   work stops silently breaking solves.

Scope decisions (from brainstorming):
- Forbid granularity = **whole-week** (a forbidden group holds *no* call role that week).
  This needs **no new solver code** — it reuses the existing windowed-forbid pattern.
- Webapp scope = **broad NF modernization** (not just the forbid rule).
- `solver_options` editing = **full**, but modeled as **rules** in the same editor UX
  ("at the end of the day these do reflect rules"), and driven by the derived-parameter model.
- Derivation home = **config-level** (single source of truth; resolver at load).
- Derivation representation = **(a) fixed named schema + resolver formulas in Python**
  (not a general expression system).
- Delivered as **one combined spec** (this document).

Non-goals (explicitly deferred):
- Per-role / per-day forbids on the NF call layer (whole-week is enough now). The extension
  point is noted below.
- A general formula/expression language for derivations.
- Day-layer reinforcement of the forbid (only if the feasibility probe shows a leak).

## Background: how the pieces work today

- Rules are parsed in `src/scheduler/solver_bridge.py` (`_build_palette_constraints`,
  `~:333`), converted to typed `SemanticConstraint`s, and encoded in
  `src/parafrost_scheduler/schedule_encoder.py`. Palette rule types live in
  `src/scheduler/palette_rules.py` (`PALETTE_TYPES ~:15`, `_CONVERTERS ~:440`).
- **Windowed forbid already exists and ships in production** (`config/standing/
  stanford-fellowship-v3.yaml`): `staffing_per_week` with `relation: at_most, count: 0,
  window: [lo,hi]` = "these groups get zero of this shift in these weeks" (group-scoped);
  `shift_total` `exactly 0` + `window` is the per-fellow variant.
- In the NF model the call roles NCC1/NCC2/NF are on the **day-granular** `call[d][f][role]`
  layer; the weekly layer carries only the generic `NCC` label. `_encode_call_weekly_link`
  (`schedule_encoder.py ~:169`) ties `xs[f][w]['NCC']==1` iff the fellow has any call day that
  week — so a windowed forbid on the `NCC` label forbids *all* call for a group that week.
  Caveat: the weekend-Elec exemption (`~:177-211`) means a weekend NCC1 day inside an
  Elec-labelled week does not force the `NCC` label — a theoretical whole-week-forbid leak.
- The NF fairness bands are consumed by encoders as concrete dicts
  (`nf_nf_day_band`, `nf_service_day_band`, `nf_block_nf_band`, etc.), set via
  `solver_options` and plumbed in `solver_bridge._solver_options_kwargs`.
- Webapp: React 19 + TS (CRA) frontend under `src/web/`, Flask backend
  `src/scheduler/web_app.py` (`/api/*`, CORS to `localhost:3000`, solve via SSE). Groups
  and shifts are already fully dynamic (`fellow_groups` is a generic map), so the 4-group
  NF config loads. The committed `RuleEditor`/`RulePalette` pipeline routes rules correctly
  through `rules:`. The **in-progress, uncommitted** `CallRuleEditor.tsx` + `App.tsx` changes
  emit a separate `call_rules` array that the backend now **hard-rejects**
  (`solver_bridge.py ~:251`) — a latent solve-breaker.

## Section 1 — Architecture (spine)

```
YAML config
  nf_parameters:   (semantic inputs, NEW)
  rules:           (incl. forbid-coverage rule)
  solver_options:  (scalar dials; derived bands may be omitted when nf_parameters present)
        │
        ▼
  resolver (NEW, runs in assemble_config/solver_bridge at load)
        │  expands nf_parameters -> concrete nf_*_band / cap dicts
        │  explicit raw bands OVERRIDE derived ones (back-compat)
        ▼
  encoders (unchanged)  ->  solve  ->  evaluate_nf
```

The webapp reads/writes the **semantic** `nf_parameters`, the `rules` list, and scalar
`solver_options` — never the raw derived bands. The resolver is the single source of truth.

## Section 2 — Derived-parameter model

New optional config block `nf_parameters: {group: {…inputs…}}`. Semantic inputs per group:

- `ncc_weeks: [lo, hi]` — the NCC-week band. Source of truth is the existing NCC-week
  floor/ceil rules (e.g. `JR NCC week floor at_least 12` / `ceil at_most 14`); the resolver
  reads them so weeks are not duplicated. If a group has no such rules, `ncc_weeks` may be
  given explicitly.
- `density: [lo, hi]` — expected call-days per NCC week (e.g. `[5.0, 5.8]`).
- `nf_fraction` — target NF share of service (default `0.3333…` = 1/3).
- `nf_tolerance` — ± slack (in days) applied to the derived NF band.
- `budget` inputs for equal-division caps (CCM-style), where applicable.

Resolver formulas (fixed, in a small Python module, approach (a)):

- `service_band = [ceil(ncc_weeks.lo * density.lo), floor(ncc_weeks.hi * density.hi)]`
- `nf_day_band  = [round(service_band.lo * nf_fraction) - nf_tolerance,
                   round(service_band.hi * nf_fraction) + nf_tolerance]`
- Equal-division caps: `cap = ceil(budget / N)` (+ per-block adjustment where the encoder
  already does so).
- `nf_block_nf_band` is a **tractability scaffold**, not a fairness target. It is derived
  loosely (e.g. `[1, ceil(nf_day_band.hi / expected_active_blocks) + slack]`) and clearly
  labeled as a scaffold, OR left explicit. It is never presented as a fairness band.

Rules for resolution:
- The resolver only fills a band that is **not** explicitly present in `solver_options`.
  An explicit raw band always overrides (back-compat; direct control still possible).
- With **no** `nf_parameters` block, the resolver is a no-op → behavior byte-identical to
  today.
- Validation: a group named in `nf_parameters` must exist; density/fraction/tolerance must
  be sane (fraction in (0,1), tolerance ≥ 0); resolved `lo ≤ hi`. Fail loudly on bad input.

Extension point (non-goal now): additional derivation kinds are new named keys + a formula
in the resolver module — a small, localized code change, not an open expression system.

## Section 3 — Forbid-coverage rule

No new solver code. The rule is the existing:

```yaml
- name: "No CCM coverage (holiday)"
  type: staffing_per_week
  groups: [CCM]            # any group(s)
  shifts: [NCC]            # NF model: the generic call label
  relation: at_most
  count: 0
  window: [A, B]           # 0-indexed inclusive-exclusive per existing WeekSpan semantics
  strength: hard           # default hard; soft allowed
  active: true
```

General over any group(s) and window; not hardcoded to CCM/Stroke.

Editor: a friendly palette entry **"No coverage (group × weeks)"** that emits this shape
(group selector, shift defaults to `NCC` for NF configs, week range, strength) rather than
requiring the user to hand-craft `at_most 0`. Required fixes so windowed rules are usable:
- `RulePalette.createDefaultRule` seeds a `window` for windowed rule kinds.
- `RuleEditor` renders window inputs for `shift_total`/`staffing_per_week` unconditionally
  (today they render only if a `window` already exists).

Caveat handling: the weekly-`NCC` forbid has the weekend-Elec exemption hole. For whole-week
CCM/Stroke forbids this is low risk. Section 6 adds a Slurm feasibility probe to confirm the
forbid actually holds (no leaked weekend call day) on the production NF roster before we trust
it. Day-layer reinforcement is deferred unless the probe shows a leak.

## Section 4 — Rules-editing modernization (webapp)

- **Reconcile the retired channel.** Reroute the `CallRuleEditor` rule types into the
  `rules:` list (the backend's `_migrated_call_rule_to_constraint` already handles them there)
  and remove the separate `call_rules` request field, so adding a call-tier rule no longer
  triggers the backend `ValueError`. Solve/feasibility/draft/publish bodies carry these rules
  inside `rules:` only.
- **Dynamic groups.** Replace the hardcoded `SidebarSection` group union with a
  group-derived set so CCM/Stroke (and any future group) render cleanly with correct casing.
- **Full palette round-trip.** Add the missing TS palette types and fields so NF configs
  load/save without loss: `no_isolated_week`, `group_count_balance`, and
  `block_rotation` `block_offset` + `nf_days_per_block`.
- **End-to-end verify.** The 4-group NF config loads → edit/add a rule → solve stream runs
  (Flask boots, no CORS crash — per the "always verify Flask starts" project rule).

## Section 5 — solver_options as rules (editor)

Surface NF dials and `nf_parameters` inputs in the **same** card-based add/edit/enable/disable
UI as other rules ("these do reflect rules"):
- Scalar dials (toggles/ints/strings/pairs, e.g. `nf_week_off_cap`, `nf_ncc1_continuity`,
  `nf_one_third_nf_weight`, `nf_run_length`) as simple typed cards.
- Derived-parameter inputs as cards that show the semantic inputs **and** the live-computed
  `[lo,hi]` band (recomputed client-side from the same formulas, with the resolver as the
  authoritative backend copy).
- Backend: extend `build_solver_config_from_request` to accept `nf_parameters` and run the
  resolver (it already accepts `solver_options`). `types.ts` `AnnualConfig` gains
  `solver_options` and `nf_parameters`.

Client-side derivation mirrors the Python resolver formulas for live preview only; the
backend resolver remains authoritative at solve time.

## Section 6 — Testing & verification

Python (fast, fixture-based where possible):
- Resolver unit tests: inputs → expected bands; changing `ncc_weeks` moves `nf_day_band`;
  explicit raw band overrides the derived one; **no `nf_parameters` ⇒ byte-identical** to
  current config assembly (regression guard).
- Forbid-rule fixture test: the `staffing_per_week at_most 0 + window` builds and binds
  (pinned red→green: a forbidden group with a coverage day in-window ⇒ UNSAT; out-of-window
  ⇒ SAT).
- Validation tests: bad `nf_parameters` (unknown group, fraction out of range, lo>hi) fail
  loudly.

Feasibility (Slurm, full roster):
- `--mode sat` probe of the production NF config **with** a sample forbid (e.g. CCM off
  weeks 25–26) → confirm SAT and `evaluate_nf` clean, and inspect the workbook to confirm
  the forbidden group truly holds no call in-window (checks the weekend-Elec hole). Only
  after this do we trust the pattern.

Webapp (smoke):
- Flask boots; `/api/configs` + config load; add/edit the forbid rule and a solver_options
  card; draft save; solve stream returns a schedule. Confirm no `call_rules` rejection.

Byte-equivalence / safety:
- Resolver no-op without `nf_parameters`; existing production `ncc-nf-model.yaml` continues
  to solve unchanged. Foundation tests remain pinned to `ncc-nf-foundation-fixture.yaml`.

## Rollout / sequencing note

Implementation order will be finalized in the plan, but the natural spine is: resolver +
config schema (with regression guard) → forbid-rule backend/editor path → webapp channel
reconciliation + dynamic groups + palette round-trip → solver_options-as-rules editor →
feasibility probe + smoke verification.

## Open risks

- Weekend-Elec exemption leak on the whole-week forbid (mitigated by the feasibility probe;
  day-layer reinforcement deferred).
- Forbid windows must be consistent with the `block_rotation` blocks for CCM/Stroke or the
  model goes UNSAT — the editor should surface a clear error from the feasibility check
  rather than a silent UNSAT.
- Client/Python derivation drift — keep the formulas in one documented place and mirror
  intentionally; backend is authoritative.
