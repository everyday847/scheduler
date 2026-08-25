# Supervision as a Single Parameterized Constraint

**Supervision** — when a *supervisee* fellow is on a supervised shift-set in a week, at least one qualifying *supervisor* fellow must be on that shift-set the same week — is one parameterized constraint type, not several bespoke rules. The Helena early-Stroke window and the "≥1 of {STROKE, NCC_JR, NCC_SR} on {NCC1, NCC2, Swing} whenever a CCM/NH fellow is" requirement are the *same* relationship with different parameters (supervisee selector, supervisor selector, shift-set, optional window, **Strength**). The apparently-unconditional NCC case is just the conditional one with an always-true antecedent (NCC service is always staffed). The encoder provides the generic predicate; config supplies who/which-shifts/when/how-hard. This replaces the Helena-hardcoded `dual_stroke_window` branch (named supervisors list + Helena-specific pins).

Supervisor/supervisee are **per-rule roles, not global fellow properties**: Helena is in the STROKE group yet is a supervisee on early Stroke weeks, and NCC supervisors include NCC_JR — so each rule names its own selectors.

## Consequences

- **Tier follows parameters, not the relationship.** Structural, all-year supervision (NCC) is a **Standing Rule**; windowed, roster-specific supervision (Helena this year) is an **Annual Rule**. The same code path serves both.
- **Strength follows coverage demand vs. fellow supply** (the mechanics behind the choice, not a modeled concept): Stroke needs one fellow per week and the roster has slack (NH/NCC_SR can cover Stroke), so supervising Helena is nearly free and is kept **soft** — "arrange the extras we'll have anyway to give her supervision." NCC needs three per week and the four NCC fellows yield only ~64 of the ~90 required fellow-weeks, with CCM/NH supplying the rest; supervising those supervisees genuinely constrains the whole schedule, so it is **hard**. This supply/demand reasoning is intentionally not a glossary term — it explains strength/tier choices but is not itself something the scheduler models.

## Considered Options

- **Two separate behaviors** (`qualified_coverage` unconditional + `supervision` conditional) — rejected: they are the same domain statement under rephrasing; the only real differences are window, strength, and tier, all of which are already parameters/axes.

## Status: superseded in part by implementation (2026-06-10)

The "one parameterized constraint" claim above proved **too strong** when the rules were actually built (the `call_rules` dissolution, S5). Supervision realizes as **two distinct Rule Shapes with different encodings**, split by the supervised service's supply/demand — not one shape with a window/strength parameter:

- **Mandatory qualified coverage ("must")** — `ncc_stroke_oversight`: a hard, unconditional "≥1 qualifying fellow on the shift-set each week" coverage *floor*. Viable only because NCC is a high-supply service always staffed above one, so a supervisor is always available to require.
- **Opportunistic windowed supervision ("may")** — `dual_stroke_window` → `WindowedSupervisionCriterion`: a windowed *cap* (in-window ≤1 supervisor + ≤1 supervisee, out-of-window ≤1 total) that *permits and shapes* a supervised pairing when one is feasible. Used precisely because Stroke is low-supply — a supervisor often *can't* be present, so presence cannot be required.

The encodings are not the same statement under rephrasing: one is `at_least_k(..., 1)` over a selector each week, the other is per-week `at_most_k` caps gated by a window. The supply/demand reasoning the original ADR called "intentionally not a glossary term" turned out to be the very thing that *determines which shape applies* — so it now lives in CONTEXT.md's **Supervision** entry as the load-bearing distinction. The Helena early-Stroke case is the "may" shape; the NCC case is the "must" shape. They share the supervisee/supervisor *selector* vocabulary but not an encoding.

Also corrected: the "Consequences" line above tiers the Helena/windowed case as an **Annual Rule**. As built it is **Standing** — the *structure* (early-window opportunistic Stroke supervision) is permanent program design; only the selector's named seniors change yearly, and selector churn does not set tier (per CONTEXT.md, tier follows the structure's permanence). The "must"/NCC case remains Standing as stated.
