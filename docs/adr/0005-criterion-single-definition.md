# One Definition per Criterion (Encode + Evaluate Co-located)

Each **Criterion** is defined in ONE place that exposes both of its manifestations: how it is *encoded* for the SAT/PB solver (compiled to pseudo-Boolean constraints) and how it is *evaluated* against a concrete **Schedule** (the value used for scoring and for human-facing presentation such as the workbook). The two manifestations are written separately but live together, are reviewed together, and are pinned by a contract test that runs both on sample schedules and asserts they agree. A criterion change touches one location, so the solver's penalty and the evaluator's judgment cannot drift out of sync.

## Why

The encoder (`schedule_encoder.py`, the `parafrost_scheduler` package) and the evaluator (`night_policy_types.criteria_for_assignment`, the `scheduler` package) historically computed the same criteria *independently, in different packages*. They drifted, producing repeated false-positive bugs where the workbook flagged an assignment the solver had not actually penalized — e.g. the Saturday-only stroke false-red, and the earlier clinic/stroke colorer divergences. "The evaluator MUST mirror the encoder" was an unwritten invariant enforced only by vigilance.

## Decision detail

- **Co-located dual manifestation (chosen).** One criterion object/module per criterion with an `encode(...)` and an `evaluate(...)` path side by side, plus a contract test asserting agreement on concrete schedules. Gets the property that actually matters — *cannot drift unnoticed* — without a large build.
- **Single compiled expression (rejected for now).** Express each criterion once as a pure function the evaluator runs directly and the encoder compiles to PB constraints — a true single source. Rejected as over-engineering for the current ~8 criteria; it requires an expression-compilation layer. Revisit if the criterion count grows substantially.

## Consequences

- Presentation (currently Excel-coupled, in `workbook.py`) is a *separate* concern from a criterion's value; a criterion may judge a relationship among several assignments and is not inherently "a red cell." The display layer consumes evaluated criterion values; it does not redefine them.
- The contract test is the regression guard the two prior divergence bugs would have failed.
