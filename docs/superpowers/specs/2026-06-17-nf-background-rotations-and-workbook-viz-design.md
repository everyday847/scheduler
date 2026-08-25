# Design — NF Model: Background Rotations + Workbook Visualization

**Date:** 2026-06-17
**Status:** Design approved; ready for implementation planning.
**Builds on:** the NF model Phases 1–2 (day-granular call tier NCC1/NCC2/NF, generic
weekly `NCC` label, hard 4–6 day NF runs + symmetric rest). Configs
`config/{annual,standing}/ncc-nf-model.yaml`.

## Context

The NF model currently produces a call schedule (NCC1/NCC2/NF coverage with 4–6 day
runs) but the NCC fellows' **weekly background service is all blank** — no MICU, Elec,
Vac, Anaesthesia, SICU, NS, Stroke, or Telestroke rotations. That is not a usable
schedule: an NCC_JR fellow still owes ~16 weeks of MICU (including orientation),
electives, vacation, etc. Separately, the only way to view the schedule today is an
ad-hoc CSV/ASCII renderer (`experiments/render_nf_schedule.py`) with no relationship to
the familiar weekly workbooks — making it hard to see, e.g., whether MICU obligations
are met per fellow.

This design adds, in two independent parts:
- **Part A — Background rotations:** per-group rotation quotas for NCC_JR/NCC_SR, and a
  block-structured generic-CCM coverage pool, so every fellow gets a normal weekly
  service schedule.
- **Part B — Workbook visualization:** render the NF solution through the existing
  multi-sheet `write_schedule_workbook` — Tab 1 the familiar weekly per-fellow grid,
  plus day-granular call-detail and NF tabs — retiring the ad-hoc renderer.

Build order: **A then B** (you need a schedule worth viewing before the viewer).

## Capacity (the load-bearing constraint)

Background rotation weeks consume fellow-weeks that can't also be call weeks (the
weekly↔day link + at-most-one-shift-per-week mean a MICU week is not an NCC call week).
Arithmetic (W=53; call demand ≈ (3 weekday + 2 weekend call slots) × 7-day-equiv ≈ 144
call fellow-weeks/yr):

| Config | Call-available fellow-weeks | vs ~144 need |
|--------|-----------------------------|--------------|
| 1 CCM, full quotas | 141 | **−3 (INFEASIBLE)** |
| 2 CCM, full quotas | 194 | +50 (+35%) |

**Decision: two generic CCM fellows** (`CCM Generic 1`, `CCM Generic 2`). They stand in
as "some other source of NCC coverage" (later: real Stroke fellows doing NCC service).
CCM need not be active all year.

## Part A — Background rotations

### Weekly palette
Expand the NF model's weekly `shifts` from `[NCC, MICU, NS, SICU, Elec, Vac]` to add
`Anaesthesia, Stroke, Telestroke/Clinic`. All are plain weekly shifts routing through
the existing `xs` layer (not call-tier). `NCC` remains the generic call-week label.

### The packing problem this design solves (why not weekly Elec pins)

A first attempt pinned Elec as a weekly `shift_total` (hard floor + soft target). It
exposed a representation bug + a tractability wall, both confirmed empirically:
- **Tractability:** any HARD constraint on the JR weekly NCC/Elec split (Elec `at_least`
  ≥6, Elec `exactly` ≥12, or an NCC-week `at_most` cap — even a loose one) makes the JR
  group's solve time out (>min, was <1s). The 3 identical JR fellows + heavy block-aligned
  fixed load (16 MICU + 4-wk Anaesthesia block + 4 SICU + 3 Vac + the wk0-3 orientation)
  make JR week-placement an intractable aligned-packing. SR (looser load) takes hard
  pins fine. A bare soft Elec target is feasible but hollow — the optimizer parked one JR
  on 24 NCC weeks (only ~4.3 call-days/week — wasteful) instead of electives.
- **Representation bug:** the weekly↔day link labels a week `NCC` if it has ANY call day.
  So a single weekend call day during an otherwise-elective week relabels the whole week
  NCC, forbidding the elective-weekend packing the prior system relied on (~2–3 of every
  NCC week's days are idle). This over-counts NCC weeks and is what the cap fights.

The fix: a targeted representation exemption + day-granular quotas (below), instead of
brittle weekly Elec pins.

### Weekend-NCC1-on-Elective exemption (the one representation change)

A **weekend NCC1** day during an **Elective** week does NOT relabel that week as NCC —
it stays Elective. This is the ONLY decoupling of the weekly↔day link:
- Scope: **weekend NCC1 only.** Weekend NCC2 does not exist (Phase-1 decision). Weekend
  **NF** during Elec still relabels to NCC (NF is a real service, not a stackable add-on).
  Weekday call always relabels to NCC.
- Weekend call during MICU/Anaesthesia/SICU/NS remains **forbidden** (those weeks block
  weekend roles). SR-only reluctant fallback (avoid initially): weekend during
  Telestroke/Clinic.
- Encoding: the weekly↔day link special-cases "if fellow f's only call day(s) in week w
  are weekend NCC1 and f's week-w background is Elec, do not force the NCC weekly label."

### NCC_JR background quotas (per fellow unless noted)
| Rotation | Rule | Strength |
|----------|------|----------|
| MICU total | `shift_total exactly 16` | hard (relax to 14 only if UNSAT) |
| MICU orientation | `shift_total exactly 4, window:[0,4]` (each JR on MICU weeks 0–3) | hard |
| Anaesthesia | `block_rotation block_size 4` + `shift_total exactly 4` | hard |
| SICU | `shift_total exactly 4` | hard |
| Vacation | 3 weeks via distinct **pinned** `specific_assignment` (see Symmetry below) | hard |

### NCC_SR background quotas (per fellow)
| Rotation | Rule | Strength |
|----------|------|----------|
| MICU | `shift_total exactly 8` | hard |
| NS | `block_rotation block_size 2` + `shift_total exactly 6` | hard |
| Stroke | `shift_total exactly 2` | hard |
| Telestroke/Clinic | `shift_total exactly 2` | hard |
| Vacation | 3 weeks via distinct **pinned** `specific_assignment` (see Symmetry below) | hard |

Consequence (intended): all 3 JRs on MICU in weeks 0–3 ⇒ **no JR weekday call weeks 0–3**;
early-year call is covered by the 2 SRs + CCM.

### Fellow symmetry — the real tractability fix (pinned vacation weeks)

The intractability above is **fellow symmetry**: the 3 identical NCC_JR fellows are
interchangeable, so the solver thrashes through equivalent permutations. Verified: the
full day-band model TIMES OUT with symmetric JRs but solves in **1.0s** once the 3 JRs
are distinguished. The chosen distinguisher (simulating the real per-fellow vacation
requests these fellows will have): **pin each fellow's 3 Vacation weeks to distinct
specific weeks** via `specific_assignment` (this also satisfies the Vac=3 quota, so the
`shift_total Vac exactly 3` rule becomes redundant and is dropped):
- JR1: weeks 8, 28, 32 · JR2: weeks 11, 25, 41 · JR3: weeks 14, 20, 27
- SR fellows get distinct Vac pins too (robustness/realism), e.g. SR1: 10, 30, 44 ·
  SR2: 17, 35, 48 (any distinct in-horizon weeks; tune later to real requests).

These pins replace the per-group `shift_total Vac exactly 3` rule. Without them the model
is intractable; with them, full quotas (no sacrifice) solve in ~1s.

### NCC service quotas — two coordinated bounds (replaces weekly Elec pins)

Instead of pinning Elec weeks, bound NCC service directly:
- **Hard upper bound on NCC-LABELED weeks per group** (`shift_total at_most` on `NCC`),
  with the weekend-elective caveat above (an Elec week carrying only weekend NCC1 is NOT
  an NCC week, so does not count against the cap). This keeps the weekly calendar clean
  and forces electives to materialize as their own weeks.
- **Hard ±5-day band on NCC SERVICE-DAYS per fellow** (day-granular): a service-day = any
  day holding NCC1/NCC2/NF (weekend-elective NCC1 days DO count — they are work). Center
  **~80 days (JR) / ~130 days (SR)**. These are lower than the prior system's 104/162
  because NF-as-a-real-service + no weekday-stacking legitimately reduce achievable
  service days. The day-band is the primary workload control; the week-cap is the
  calendar-cleanliness control.

Day-band centers (80/130) and the week-cap values are **starting points** — tune
empirically once the model solves fast (see Testing). Elec is the residual (≈53 − fixed
rotations − NCC weeks), so capping NCC weeks raises electives without a brittle Elec pin.
This may require slightly more CCM Generic 2 call support to close the day math.

### Hard/soft summary
Hard: MICU totals + orientation window, Anaesthesia block, SICU, NS block, Stroke,
Telestroke, Vacation, the NCC-week cap, the per-fellow NCC service-day band. Soft: CCM
per-block NF-day count. Probe full-year SAT after each addition; relax a pre-authorized
lever (JR MICU 16→14; widen the day-band; loosen the NCC-week cap; add CCM support) only
with sign-off, never silently. The day-band/week-cap is what makes the JR packing
tractable AND enforceable (unlike the abandoned weekly Elec pin).

### CCM coverage model
Each of the two CCM representations is a *concatenation of real fellows*, each doing
**one 4-week NCC block** — the exact analogue of prior schedules' 4-week
NCC1/NCC2/Swing blocks (wb7's `CCM NCC Block` = `block_rotation block_size 4` over the
NCC roles). For the NF model this is `block_rotation block_size 4` over the generic
`NCC` weekly label for the CCM group. The solver chooses how many blocks each CCM
representation uses (1–13); non-block CCM weeks are blank. Within a selected block the
weekly↔day link + NF-run/rest rules produce the day-level call assignment.

**Block grid — first block long (needs a small encoder addition).** The existing
`all_or_none_block`/`block_rotation` tiles fixed 4-week blocks from week 0, leaving a
ragged block at the END. The user wants the partial week absorbed at the START: block 0
= weeks [0–4] (5 weeks), then [5–8],[9–12],…,[49–52] — 13 aligned blocks. Add a general
`block_offset` (or variable-first-block) parameter to the block rule so the grid start
shifts; the NF CCM rule sets it to make the first block long. Keep the change generic
(a block-rule parameter), not NF-specific.

**Soft per-block NF-day count.** During each selected CCM 4-week (or 5-week first)
block, the CCM should do **6–8 NF days total** — a SOFT target (a violation is
"worrying" but not infeasible). The hard 4–6-day-run rule still shapes runs, so 6–8 NF
days = one 6-day run or two 4-day runs; no conflict. Rest of the block = NCC1/NCC2.

### Hard/soft summary
Mostly hard (enforce the schedule): MICU totals + orientation window, Anaesthesia
block, SICU, NS block, Stroke, Telestroke, Vacation. Soft: Elec floors, CCM per-block
NF-day count. Probe full-year SAT after wiring; relax the pre-authorized levers (JR
MICU 16→14) only if UNSAT.

### Backup
Backup-eligible weekly states: Elec or Telestroke/Clinic (the existing backup-eligible
set). With these rotations now present, backup coverage (soft, from Phase 1) will
actually populate.

## Part B — Workbook visualization

Reuse the existing multi-sheet `write_schedule_workbook` (`src/parafrost_scheduler/
workbook.py`); no solver change. Add an NF-aware entry point (extend `_write_outputs`
or a small `write_nf_workbook` wrapper) and retire `experiments/render_nf_schedule.py`.

- **Tab 1 — "Fellow Schedule" (weekly, familiar):** rows = weeks, columns = fellows;
  each cell = the fellow's weekly assignment from `weekly_assignments` (MICU/Elec/NCC/
  Vac/Anaesthesia/…), colored by the existing `_service_color` palette (already has
  MICU/SICU/Stroke/Telestroke/Anaesthesia/Vacation colors). For the NF model the legacy
  weekend/night sub-columns are empty, so **simplify to one weekday cell per fellow per
  week** (drop the per-fellow weekend + 7 night sub-columns the wb7 layout uses).
- **Tab 2 — "Call Detail" (day-granular, NEW):** rows = days (date, day-of-week),
  columns = NCC1 / NCC2 / NF holder that day, from `sol.call_assignments_by_day`.
- **Tab 3 — "NF Runs":** the NF-by-day view (NF is the night coverage here) showing
  runs legibly; may fold into Tab 2 if redundant.
- **Backup tab:** keep the existing backup sheet.

`solution_to_parsed` already feeds Tab 1 from `weekly_assignments`. Tab 2/3 read
`sol.call_assignments_by_day` (a new sheet builder).

## What is reused vs new

**Reused:** `shift_total` (incl. `window:`), `block_rotation`/`all_or_none_block`,
`vacation`/exact totals, the whole weekly `xs` layer, `write_schedule_workbook` +
`_service_color` + `solution_to_parsed`, the Phase-1/2 call tier + runs + rest.

**New:** a `block_offset` (variable-first-block) parameter on the block rule (first
block long); the soft CCM per-block 6–8-NF-day target; the new standing/annual rotation
rules + expanded palette + 2nd CCM in `ncc-nf-model.yaml`; a "Call Detail" workbook
sheet builder + NF-aware output entry point; retiring `render_nf_schedule.py`.

## Testing

- **Rotation quotas:** per-group unit tests (build minimal NF config with a quota,
  solve, assert the fellow hits the count / window / block) in the dispatch-style
  already used. MICU-orientation: assert each JR has MICU in weeks 0–3.
- **CCM blocks:** assert CCM `NCC` weeks come in 4-week blocks on the offset grid (first
  block weeks 0–4); soft NF-day-per-block target registers a penalty when violated.
- **Block offset:** unit test the new `block_offset` parameter tiles weeks as
  [0–4],[5–8],… and is byte-identical to the old behavior when offset is absent/0
  (wb7 untouched).
- **Full-year feasibility:** probe SAT with 2 CCM + all rotations; relax JR MICU to 14
  only if UNSAT.
- **Workbook:** golden-ish test that `write_nf_workbook` produces a file with the
  expected sheets and that Tab 1 shows a known fellow's MICU weeks; Tab 2 shows the
  day-granular holders. (Assert structure/cell values, not pixel layout.)
- **wb7 regression:** full suite green; the block_offset change must be byte-equivalent
  for wb7 (offset defaults to 0/absent).

## Open implementation choices (non-blocking, for the plan)
- Exact representation of `block_offset` (a param on the existing rule vs a variable
  first-block length) — encoder-efficiency detail.
- Whether Tab 3 folds into Tab 2.
- CCM per-block NF-day soft weight (tune after first solve).
