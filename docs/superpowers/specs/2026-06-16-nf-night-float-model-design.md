# Design — No-Workbook NCC Schedule with NF (Night Float)

**Date:** 2026-06-16
**Status:** Design approved; ready for implementation planning.

## Context

This is a **brand-new scheduling model**, independent of the wb7 (workbook-imported)
line. The motivation is to explore an NCC service structure that replaces the **Swing**
shift with **NF (night float)** and is built **without a conditioning workbook** (no
imported/locked fellows). It is a clean-slate "what if we ran the year this way"
experiment, not a modification of the production wb7 schedule.

The core domain shift: **NF *is* the night coverage** (there is no separate per-night
call layer), and NF is worked in **multi-day runs** rather than single nights. That
forces a day-level treatment of call that the existing week-granular roster cannot
express — but the week remains the right unit for the *background* rotations (MICU, NS,
Elective, Vac), both because those services genuinely allocate in weeks and because we
want these calendars to stay apples-to-apples comparable with the Swing-style ones.

The intended outcome: a full-year (53-week / 365-day) schedule that (a) satisfies the
hard coverage + NF-run + rest rules, (b) hits rough per-fellow **service-day quotas**,
and (c) is **continuity-dominated** — service comes in long, clean blocks, not scattered
days.

## Fellows

No workbook import; fellows are listed in a new annual config and assembled via
`assemble_config(None, ...)` (the `workbook=None` path is already supported; it sets
`locked_assignments = {}`).

- **3 NCC_JR**, **2 NCC_SR**, **1 generic CCM** (baseline).
- **CCM Generic 2 is an optional lever** — add it if it helps feasibility/continuity.
  Not required: with the final coverage (no weekend NCC2) one CCM pencils out.
- No Stroke fellows are modeled.

## Two-tier architecture

The model has two coupled layers; the **background tier gates the call tier**.

### Background tier — WEEKLY (`xs[f][w][shift]`, reuses existing machinery)

The genuine multi-week rotations — **MICU, NS, Elective, Vac, and the rest of the
current NCC_JR/NCC_SR repertoire** — assigned per week, exactly as today. **Swing is
removed** from the palette. This preserves the week as the unit of allocation where it
matters and keeps the calendar comparable to Swing-style schedules.

A fellow's weekly background value **projects onto its days** to gate call availability:
a MICU/NS/Vac week makes that fellow **unavailable for call** (NCC1/NCC2/NF) on those
days — including the weekend. This is exactly how "MICU blocks the weekends" is
expressed: explicit weekly service ⇒ those days closed to the call tier.

### Call tier — DAY-granular (`call[d][f]`, NEW layer)

**NCC1, NCC2, NF are assigned per day** (not per week). This is the key revision from
the first-draft weekly model: a 4-6 day NF run that clips a week would otherwise
"poison" that whole week for NCC1/NCC2, bleeding capacity the tight supply can't spare.

Each call-eligible fellow/day gets a variable for each call role they could hold that
day (gated by the background tier). NF replaces the old `xn[day][f]` night layer
entirely — there is no separate night-call structure.

### The seam

`call[d][f][role]` may be 1 only if fellow f's background week permits call on day d.
A day a fellow spends on a call role is a "service day" for quota purposes. The weekly
calendar still *displays* a fellow's predominant call involvement per week (for
apples-to-apples reading), derived from the day layer.

## Coverage floor (HARD)

- **Weekdays:** NCC1 = exactly 1 fellow, NCC2 = exactly 1, NF = exactly 1 (3 call
  fellows on duty).
- **Weekends:** NCC1 = exactly 1, NF = exactly 1 (2 call fellows). **No weekend NCC2.**

## NF run + rest mechanics (HARD)

All on the day-granular NF assignment.

- **Exactly one fellow on NF each of the 365 days.**
- **Run length 4-6 consecutive days.** Encoded per fellow over the NF day-sequence:
  - *Min 4:* a run start (`nf[d] ∧ ¬nf[d-1]`) forces `nf[d..d+3]` true.
  - *Max 6:* no 7-day NF window all true (`at_most_k(nf[d..d+6], 6)`).
  - Consequence (no separate constraint): exactly-one-per-day + 4-6 runs ⇒ NF days
    **tile** the year into consecutive 4-6 day segments handed to distinct fellows;
    no fellow takes two adjacent segments (that would be a >6 run).
- **Rest — ≥2 fully-off days, symmetric (both directions).** "Fully off" = no call role
  that day **and** the background week is **off/unassigned** (genuinely no service).
  Neither Elective nor Vac counts as fully-off: Elective is **not** special (an
  Elective week does not satisfy the rest buffer), and Vac is **not** exempt — you
  cannot roll from an NF run straight into vacation. Only true off/unassigned days
  serve as the 2-day buffer.
  - *NF→other:* a run ending at d (`nf[d] ∧ ¬nf[d+1]`) ⇒ days d+1, d+2 fully off.
  - *other→NF:* a run starting at d (`nf[d] ∧ ¬nf[d-1]`) ⇒ days d-1, d-2 fully off.
  - Same-type NF days within a run are not "switches." Non-NF↔non-NF transitions are
    not gated (NCC1 → NCC1-weekend → NCC1 is fine).

## Continuity — the "call block" (SOFT, dominates the objective)

Continuity is measured over the **union NCC1 ∪ NCC2 ∪ NF as ONE continuous "call
service."** The ideal: a fellow goes on call service for a **multi-week block** that is
any mix of NCC1/NCC2/NF — including one NF run with its bracketing 2-day breaks counted
as **inside** the block — then comes off. So `NCC2-week → NF-run → 2 off → NCC1-week`
reads as a single tour, not three fragments.

Soft penalties (hard floor above is untouched):

- **Block-fragmentation penalty** over the call union — favor few, long blocks.
- **Isolated-NCC-fragment penalty.** An isolated NCC1/NCC2 stretch is **un-penalized
  only** when it is a **full week (Mon-Sun)** or an **isolated weekend (Sat-Sun)**.
  Every other isolated NCC fragment (e.g. 2-3 stray weekdays) is penalized → "very
  rare."
- **Isolated-NF penalty.** The only permitted isolated-NF shape is a **4-day run +
  2-day break** standing apart from a call block; it is allowed but penalized ("not
  ideal"). Shorter/other shapes are already forbidden by the hard run-length + rest
  rules.

## Quotas (SOFT bands) & objective

Quotas are **total service-days per fellow**, as soft bands (exact totals shouldn't be
brittle-hard):

| Group | Per-fellow target | Derivation |
|-------|-------------------|------------|
| NCC_JR (×3) | ≈ **104** days | 12×7 weeks + 20 nights |
| NCC_SR (×2) | ≈ **170** days | 20×7 weeks + 30 nights |
| CCM (×1, +optional 2nd) | ≈ **365** days | full-year coverage support |

**Supply vs demand (final, no weekend NCC2):**
- Demand ≈ weekdays 3×261 (783) + weekends 2×104 (208) = **~991 service-days**.
- Supply, 1 CCM = 312 + 340 + 365 = **1017** → ~26 slack (~2.6%, tight but feasible
  on paper). Optional CCM Generic 2 → supply ~1382, ~28% slack (roomy).

**Objective** = weighted sum of soft penalties (continuity fragmentation + isolated-
fragment + quota-band violations), minimized via the existing `optimize_stream`.
Feasibility (hard rules) first; the soft terms shape toward clean, well-distributed
blocks.

## What is reused vs new

**Reused:** `assemble_config(None, ...)` no-workbook path; the weekly `xs[f][w][shift]`
background roster and its coverage/quota encoders; `optimize_stream` and the
RoundingSat runner; the soft-penalty/band patterns; day/week calendar helpers
(`day_of_week`, `day_to_week`).

**New:**
- A day-granular **call tier** (`call[d][f][role]` for NCC1/NCC2/NF) replacing the old
  `xn[day][f]` night layer.
- The **seam** linking background-week availability to call-day eligibility.
- **NF run-length (4-6)** and **symmetric 2-day fully-off rest** encoders (day-level).
- **Call-block continuity** soft penalties over the NCC1∪NCC2∪NF union, with the
  isolated-fragment exemptions (full-week / isolated-weekend for NCC; 4+2 for NF).
- A new **annual config** (e.g. `config/annual/ncc-nf-model.yaml`) listing the fellows
  (parameterized CCM pool) and the Swing→NF palette swap.
- Removal/replacement of **Swing**-specific encoder logic for this model
  (`_RELAX_NCC_TRIO`, `jr_ncc_before_swing`, NCC+Swing cap, etc.).

## Testing

- **Unit (encoder) tests** for each new primitive, in the dispatch-level style already
  established (`tests/_dispatch_helpers.py`): build a minimal config, pin a violation,
  assert hard ⇒ UNSAT / soft ⇒ SAT + penalty.
  - NF run length: a 3-day NF run ⇒ UNSAT; a 7-day run ⇒ UNSAT; 4-6 ⇒ SAT.
  - Rest: NF run immediately followed by NCC/Vac with <2 off days ⇒ UNSAT (both
    directions); ≥2 fully-off ⇒ SAT.
  - Coverage: each day has exactly one NCC1/NCC2/NF (NCC2 weekdays only).
  - Seam: a MICU background week ⇒ that fellow's weekend call vars absent/forbidden.
  - Continuity penalties: an isolated mid-week NCC fragment registers a penalty; a
    full-week / isolated-weekend NCC stretch does not; an isolated 4+2 NF run registers
    the (allowed) penalty.
- **Feasibility probe** (objective=False + `solve()`, per the established probe harness)
  on the full year: 1-CCM first; if UNSAT or too tight, add CCM Generic 2.
- **Heavy optimize** on Slurm (never the login node) once feasible.

## Open implementation choices (for the plan, not blocking design)

- Exact variable shape of the call tier (one var per (day, fellow, role) vs a single
  categorical) — a planning/encoding-efficiency decision.
- Whether the weekly *display* value for a fellow's call week is derived or stored.
- Precise penalty weights for the continuity hierarchy (tuned empirically after the
  first feasible solve).
