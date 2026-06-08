# Supervision as a Single Parameterized Constraint

**Supervision** — when a *supervisee* fellow is on a supervised shift-set in a week, at least one qualifying *supervisor* fellow must be on that shift-set the same week — is one parameterized constraint type, not several bespoke rules. The Helena early-Stroke window and the "≥1 of {STROKE, NCC_JR, NCC_SR} on {NCC1, NCC2, Swing} whenever a CCM/NH fellow is" requirement are the *same* relationship with different parameters (supervisee selector, supervisor selector, shift-set, optional window, **Strength**). The apparently-unconditional NCC case is just the conditional one with an always-true antecedent (NCC service is always staffed). The encoder provides the generic predicate; config supplies who/which-shifts/when/how-hard. This replaces the Helena-hardcoded `dual_stroke_window` branch (named supervisors list + Helena-specific pins).

Supervisor/supervisee are **per-rule roles, not global fellow properties**: Helena is in the STROKE group yet is a supervisee on early Stroke weeks, and NCC supervisors include NCC_JR — so each rule names its own selectors.

## Consequences

- **Tier follows parameters, not the relationship.** Structural, all-year supervision (NCC) is a **Standing Rule**; windowed, roster-specific supervision (Helena this year) is an **Annual Rule**. The same code path serves both.
- **Strength follows coverage demand vs. fellow supply** (the mechanics behind the choice, not a modeled concept): Stroke needs one fellow per week and the roster has slack (NH/NCC_SR can cover Stroke), so supervising Helena is nearly free and is kept **soft** — "arrange the extras we'll have anyway to give her supervision." NCC needs three per week and the four NCC fellows yield only ~64 of the ~90 required fellow-weeks, with CCM/NH supplying the rest; supervising those supervisees genuinely constrains the whole schedule, so it is **hard**. This supply/demand reasoning is intentionally not a glossary term — it explains strength/tier choices but is not itself something the scheduler models.

## Considered Options

- **Two separate behaviors** (`qualified_coverage` unconditional + `supervision` conditional) — rejected: they are the same domain statement under rephrasing; the only real differences are window, strength, and tier, all of which are already parameters/axes.
