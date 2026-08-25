# Shift-Attribute Policy with Orthogonal Strength

Scheduling policy that is shaped by what a shift *means* — which shifts block night call, block weekend roles, block specific weekday nights, are holiday-eligible, make the following week non-preferred for a Sunday-night holder, or count as a "light" consecutive-weekend buffer — is modeled as **Shift Attributes** carried on the **Shift**, not as hardcoded name-sets in code. The encoder implements a fixed, code-defined vocabulary of attribute *behaviors* (a declarative archetype: YAML can only select and parameterize behaviors that code already knows how to encode, never invent new ones); configuration chooses which shifts carry which attributes. This lets a different institution re-describe its shifts (its own MICU-equivalent, its own clinic rules) without code changes, and lets the web app add/edit shifts.

**Tier and Strength are orthogonal.** An attribute's *presence* on a shift is a static fact (a **Standing Rule**); its enforcement **Strength** (hard, or soft with a weight) is a separate dial with a Standing default that any run may override. This is deliberate: some years a hard policy (e.g. hard-blocked weekends) is infeasible, and finding the best schedule routinely means re-solving with different strengths. Baking strength into the attribute *type* would turn every such experiment into a code change — exactly the friction the run-level variants (`aan_hard`, `hard_consec`, `no_stroke_align`, etc.) exist to avoid.

## Considered Options

- **Keep policy as Python frozensets** (`NIGHT_BLOCKED_SHIFTS`, `WEEKEND_BLOCKED_SHIFTS`, `NON_PREFERRED_SUNDAY_FOLLOWING`, …) — rejected: this is the status quo that drifted. It produced a shadow call-policy YAML that the encoder largely ignored (dead config), let the `APBN→ABPN` typo hide in a constant, and let `NON_PREFERRED_SUNDAY_FOLLOWING` diverge from the YAML's `sunday_preferred_services`. Single source of truth was impossible.
- **Fully open declarative config** (arbitrary user-defined behaviors) — rejected: the solver can only encode behaviors that exist in code, so an open schema would promise behaviors that cannot be honored.

## Consequences

- The existing frozensets become *projections* over shift attributes; substring hacks (`"Stroke" in svc and "Telestroke" not in svc`) are replaced by explicit attribute flags.
- The genuine **Solver Invariants** (one shift per week, no double-booking — what makes the SAT representation *be* scheduling at all) remain behind the solver seam and are NOT shift attributes.
- Per [ADR-0001](./0001-separate-constraint-lifecycles.md), Standing attribute data lives in deliberate YAML; the orthogonal Strength dial is what user-facing surfaces and run variants expose.
