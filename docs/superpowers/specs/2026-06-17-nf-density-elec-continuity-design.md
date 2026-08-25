# NF model: Elec recovery, NCC density, service continuity, and viz polish

**Date:** 2026-06-17
**Branch:** `partial-import`
**Status:** design — pending user review
**Supersedes the "ELEC-MISSING INVESTIGATION" framing** in memory
`project_nf_background_rotations.md` (that analysis mis-attributed the cause).

## Problem

The shipped NF schedule (HEAD `96c8b6e`) had three linked defects, all surfaced by
the user:

1. **No elective time.** JR/SR fellows showed ZERO `Elec` weeks; non-call weeks came
   out BLANK. The user requires *substantial* elective time (weeks cannot be unlabeled).
2. **Suspiciously low NCC-week density** (~3.7–4.6 call-days per NCC week; the user
   expects >5 during real NCC service). Dense 7-day NCC1 weeks essentially never occurred.
3. **Scattered service.** Fellows bounce between NCC1/NCC2/NF/off day-to-day rather than
   holding a role contiguously; e.g. one CCM was NCC1 Mon/Tue/Fri/Sun and NCC2 Wed/Thu.

These were investigated to root cause (not guessed). The findings drive the design.

## Root-cause findings (measured this session)

All measured with `experiments/nf_density_diag.py`, `nf_optimize.py`, and targeted probes.

- **Elec was FORBIDDEN by construction.** `scheduler/palette_derivations.py:
  derive_forbidden_shifts` hard-zeroes every shift not named in a group's rules (except
  the explicitly-exempt `"NCC"`). `Elec` appeared in NO rule, so it was pinned to 0 for
  every JR/SR. This — not packing or density — is why "hard Elec ≥ 2 → instant proven
  UNSAT" (unit propagation on a pinned-zero variable). **Fix: give Elec a budget rule.**
- **The schedule was solved `objective=False` (first feasible).** Nothing rewarded Elec,
  density, or concentration, so the solver returned an arbitrary scattered point with
  empty-but-legal days. The thin weeks were neither structurally forced nor forbidden —
  just **unrewarded**. Verified: forcing fills / requiring density only works under a real
  optimization objective.
- **Cardinality-sum levers blow up RoundingSat.** A hard per-NCC-week *density floor* is
  instant-UNSAT (cross-fellow supply contradiction); a hard *NCC-week cap* times out
  (>120s). Both are cardinality sums — the construct this solver handles worst here.
- **Local-window clauses are tractable.** The user's own proposal — "you get 2 days off
  after NF, then must be back on service; no MORE than 2 consecutive off" — is a per-3-day-
  window clause (same shape as the NF run/rest rules) and solves fast. Combined with a
  SOFT per-NCC-week penalty under `optimize()`, it produces the regime the user wanted:
  JR Elec 6–11, density ≈5, zero blanks, **provably reachable** (one 5-min run hit it).
- **"off" already treats Elec/Vac/MICU as WORKING** (`_build_off_indicator`), so a
  max-consecutive-off rule does NOT punish a 7-day elective week — it only forbids dead
  stretches *inside* call-land, forcing dense NCC blocks.
- **NCC1 can be locked to full 7-day weeks** (one fellow holds NCC1 all week) — SAT in
  4.7s; NCC2 then absorbs all the NF-boundary fragmentation. This is the clean fix for
  the scatter complaint.

## Goals

- Real, substantial elective time for JR/SR (target ≈ 6–9 weeks each; hard floor ≥ 6).
- NCC service comes in dense, contiguous blocks; no long dead stretches inside call-land.
- NCC1 reads as a clean weekly rotation; NCC2 carries the unavoidable NF-boundary flex.
- MICU runs in 4-week blocks (matching real rotation structure).
- A legible call-detail workbook: week-grid layout, fellow coloring, bordered blocks.
- Stay tractable; accept multi-minute (even multi-hour) optimization, with budget control.

## Non-goals

- Re-deriving the day-vs-week model, NF run mechanics, or background quotas — those are
  settled (see `nf-night-float-model-design.md`, `nf-background-rotations-...-design.md`).
  (NF *rest* IS revised here — see §4.5.)
- wb7 production path changes — every change here is config-only or gated behind
  `config.call_tier_day_granular`; wb7 stays byte-equivalent.

## Design

### 1. Un-forbid Elec (precondition)

Add Elec budget rules so `derive_forbidden_shifts` stops zeroing it:

```yaml
- {type: shift_total, name: "JR Elec floor", groups: [NCC_JR], shifts: [Elec], relation: at_least, count: 6, strength: hard}
- {type: shift_total, name: "SR Elec floor", groups: [NCC_SR], shifts: [Elec], relation: at_least, count: 6, strength: hard}
```

**Tractability caveat (measured 2026-06-17):** a hard Elec floor is a tight cardinality
bound and stacks badly with the MICU 4-week block (§2). Locally: MICU-block + hard Elec≥2
SAT 1s, ≥3 SAT 21s, ≥4 timeout >120s. The earlier "Elec≥6 reached organically in 5 min"
run was BEFORE MICU blocks were added. So the hard floor level is a tractability decision,
resolved empirically on Slurm (§Tractability). Plan: keep the hard floor LOW (≥2–3, enough
to un-forbid + guarantee a minimum) and drive the real elective volume with the SOFT target
(below) under a long optimize. The Slurm sweep tests floors {2,4,6} to find the ceiling.

### 2. MICU 4-week blocks (hard)

```yaml
- {type: block_rotation, name: "JR MICU 4wk blocks", groups: [NCC_JR], shifts: [MICU], block_size: 4, strength: hard}
- {type: block_rotation, name: "SR MICU 4wk blocks", groups: [NCC_SR], shifts: [MICU], block_size: 4, strength: hard}
```

JR has 16 MICU weeks (4 blocks incl. the weeks-0–3 orientation block), SR has 8 (2 blocks).
Validated SAT in 2s. Mirrors the existing JR Anaesthesia / SR NS block rules.

### 3. Density via max-consecutive-off (hard) — the user's rule

For each JR/SR fellow, in every sliding window of `max_off+1 = 3` days, at least one day
is NOT "off" (off per `_build_off_indicator`: no call role that day AND not on a working
background rotation that week). Clause: `Σ (¬off) ≥ 1` over each 3-day window.

This forces dense NCC blocks (no >2-day dead stretch inside call-land) while leaving
genuine elective/MICU/Vac weeks untouched (they count as working, not off). New encoder
function `_encode_nf_max_consecutive_off`, gated under `call_tier_day_granular`, applied to
NCC_JR/NCC_SR fellows. **Hard.**

### 4. NCC1 contiguous blocks (hard) — the continuity fix; TWO candidate modes

NCC1 is forced constant across consecutive days within a week per fellow:
`NCC1[d][f] == NCC1[d+1][f]`. With hard coverage (exactly 1 NCC1 holder/day), NCC1 becomes a
clean block and NCC2 (day-granular) absorbs the NF-boundary fragmentation. Two modes to
evaluate (Slurm sweep compares them):

- **Full-week:** constant across all 7 days. NCC1 = one fellow for the whole week. Validated
  SAT in 4.7s standalone.
- **Weekday-only (`--ncc1-weekday`):** constant across the 5 weekdays (Mon–Fri); weekend NCC1
  is free. This lets a fellow finish a weekday NCC1 block, take the weekend off, then start an
  NF run Monday — the NCC1→weekend-off→NF transition the user wants. Preferred candidate.

New encoder function `_encode_nf_ncc1_continuity(mode)`, gated under `call_tier_day_granular`,
applied to all call-eligible fellows incl. CCM. **Hard.** This delivers the user's "NCC1
straight through, then SR2 NCC2 straight through" continuity. A soft NCC2 same-role nudge is a
possible later refinement, not in this spec.

### 4.5. Asymmetric NF rest — 1 day before, 2 days after (hard)

Revised from the prior symmetric ≥2-off rule (user, 2026-06-17): a full day off is needed
*before* starting nights, but only one; the post-NF recovery still needs two. `_encode_nf_rest`
before-side changed from `k in (1,2)` to `k in (1,)`; after-side unchanged at `k in (1,2)`.
This loosens the model (helps the packing / tractability) and enables NCC1→weekend-off→NF
transitions. Tests updated: stale "symmetric" wording fixed, new `test_before_rest_is_only_
one_day` mutation guard (a d-2 call before a run start must stay SAT). Done this session.

### 5. Concentration objective (soft) under optimize()

The model is solved with `RoundingSatRunner.optimize()` and a `min:` objective, NOT
`objective=False`. Objective terms (added to the framework's existing `soft_violations`):

- **SOFT penalty per NCC-labeled week, JR/SR** (weight ~10): rewards concentrating call
  into fewer, denser weeks → frees weeks to label Elec.
- **SOFT block-level penalty per ACTIVE CCM 4-week NCC block** (weight an empirical tuning
  parameter; weight 200 was insufficient — see §6): rewards using as few CCM blocks as
  coverage allows, so unneeded blocks idle. `blk_active = OR(block's NCC week vars)`,
  penalize `blk_active`. Effectiveness is bounded by the §6 coverage tradeoff, not weight alone.

The renderer and any production solve use `optimize()` with a wall-clock time limit
(default 300s; raise for higher quality — hours acceptable per user, with budget control).
Results are best-incumbent (often not proven optimal at 5 min; that's fine).

### 6. CCM concentration — OPEN, deferred to inspection

Measured tradeoff: with full JR/SR quotas + Elec ≥ 6, only ~1 week structurally needs both
CCMs; ~27 weeks the JR/SR cohort can nearly self-cover; ~25 need one CCM. Yet both CCMs
came out 53/53 even at penalty weight 200 — coverage demand dominates the soft penalty.
**Concentration is achieved by deciding how much coverage to drop, not by a bigger penalty.**
The user will inspect a freshly-optimized workbook (NCC1-full-week + all above) before
choosing the relaxation lever (soften a weekday slot vs. trim JR/SR background quotas vs.
accept CCM-full). This section is INTENTIONALLY unresolved; it does not block items 1–5.

### 7. Workbook visualization

- **Call Detail tab = week grid.** Per week, a 3-row block (NCC1 / NCC2 / NF) × 7 day
  columns (Mon–Sun); each cell holds the fellow on that role that day. Col 1 = week label +
  start date (merged over the 3 rows), col 2 = role label.
- **Fellow coloring.** Three greens for NCC fellows (NCC_JR ∪ NCC_SR, cycled by index),
  three blues for CCM (cycled by index). Role-label cells are NOT colored. Matches the
  old-schedule blue=CCM / green=NCC convention.
- **Thick block borders.** Each week's 3-row block is wrapped in a medium border.
- **Weekly grid (Fellow Schedule tab).** `"NCC"` and `"Elec"` added to `SERVICE_COLORS`
  (NCC = green matching NCC1; Elec = gray) so those cells are colored.

All in `src/parafrost_scheduler/workbook.py` (`_build_call_detail_sheet`,
`_call_fellow_fill_map`, `write_nf_workbook`). Implemented + tested this session
(`tests/test_nf_workbook.py`, 5 pass).

## Tractability & testing

- Feasibility probes: `objective=False` + `runner.solve()`. Quality runs: `optimize()`
  with `--time-limit`.
- **Run on Slurm, many jobs in parallel.** This node is compute-limited; RoundingSat is
  single-threaded. Submit with `sbatch -A prescient1 -p defq -n 1`. `experiments/
  nf_sweep_slurm.sh` fans the (Elec floor × NCC1 mode × CCM weight) matrix across `defq`
  at once (18 jobs, 1h each); `experiments/nf_optimize.slurm` wraps a single run. The
  hard-Elec ceiling and best NCC1 mode are resolved empirically from the sweep, not pinned
  in advance — outputs in `results_slurm/sweep_<tag>_<jobid>.out` + `out_<tag>.xlsx`.
- Every new hard constraint gets a red→green guard test (neuter it → its test fails),
  per the project's VACUOUS-test history (`feedback_test_strength_dispatch_gap`).
- wb7 byte-equivalence preserved (all changes gated under `call_tier_day_granular` or
  config-only); verify via OPB multiset diff as in prior NF phases.

## Encoder config-driving (cleanup)

The day-band lo/hi (currently hardcoded 75/85 JR, 125/135 SR in
`_encode_nf_service_day_band`) and the new max-off window / NCC1-continuity-mode toggles
should be config-driven (a `nf_service` block in the annual config), not hardcoded — folding
in the long-standing TODO at `schedule_encoder.py:_encode_nf_service_day_band`.

## Validated numbers (5-minute optimize, all levers except NCC1-full-week)

JR: NCC 17–19 wk, **Elec 7–9**, density 4.5–4.5, svc 78–85. SR: NCC 23–26, **Elec 6–9**,
density 5.0–5.4, svc 125–130. CCM: 53/53 (the open item). Zero blank weeks. Hard Elec ≥ 6,
MICU 4-wk blocks, max-2-off all active. Not proven optimal at 5 min (acceptable).
