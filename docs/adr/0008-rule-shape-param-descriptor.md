# Rule Shape carries a Param Descriptor — a fourth co-located manifestation

Each **Rule Shape** gains a declared **param descriptor** (the names, types, options, layer, and hard/soft-ability of its config parameters) co-located in its `schedule_rules` leaf alongside the encode + evaluate manifestations of [ADR-0005](0005-criterion-single-definition.md). A single **Rule Shape Catalog** registers every shape's `{descriptor, converter}` and is the one dispatch point that turns a config `dict` into a `SemanticConstraint`; it is also served to the web app (`/api/rule-catalog`) so the rule-editor UI renders from the descriptor instead of from hand-maintained TypeScript switches.

## Why

ADR-0005 deliberately rejected the deeper "single compiled expression" version as over-engineering "for the current ~8 criteria." The count has since roughly doubled and the translation `dict → SemanticConstraint` fragmented into **three** independent paths (`palette_rules._CONVERTERS`, `solver_bridge._migrated_call_rule_to_constraint`, and the gating converters), each reading `rule["count"]`/`rule["window"]`/… ad hoc. The web app then re-encoded that same param vocabulary a fourth, fifth, sixth, and seventh time (a TS union plus four `switch` statements), and it drifted — e.g. `group_count_balance` exists in Python and config but had no editor, so configs using it could not round-trip through the UI. The param vocabulary was implicit in converter bodies and copied by hand across the Python↔JS seam, with no compiler or test catching divergence.

## Decision detail

- **Param descriptor co-located in the leaf (chosen).** Every Rule Shape is a true `schedule_rules` leaf with four manifestations: encode, evaluate, contract test, and param descriptor. This requires migrating the ~8 still-inline `_encode_*` functions out of `schedule_encoder.py` into leaves — done as part of this work, not deferred, because a half-migration (descriptors in leaves for some kinds, inline for others) is worse than either endpoint.
- **One converter registry (chosen).** The three converter paths fold into the catalog: every kind (palette, the surviving migrated-call types, and the gating trio) registers in one place. `solver_bridge` stops carrying its own per-type `if`-chain.
- **Served catalog drives a generic editor (chosen).** The frontend renders any kind's fields from the descriptor; the per-type `RuleEditor`/`RuleCard`/`RulePalette` switches and the bespoke `CallRuleEditor` are deleted.

## Consequences

- Extends, does not supersede, ADR-0005: the encode/evaluate co-location and contract test remain; the descriptor is an additional co-located manifestation.
- "Call rules" is **not** a separate channel or Rule Shape distinction — it is a presentation category. All rules are `rules:` entries; the web app's "call" view is a filter (night + weekend kinds) over the one rule list. The uncommitted `call_rules:`-channel work (`CallRuleEditor.tsx`, the `callRules` state) is dissolved, not landed — the backend already rejects that channel (`solver_bridge.py`).
- Adding a Rule Shape becomes one leaf (four manifestations) plus a catalog registration; the UI follows with no edit. The "4 edits on the JS side" tax and the Python↔JS drift class of bug are gone.
