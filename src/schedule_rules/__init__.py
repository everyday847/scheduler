"""schedule_rules — the dependency-free home of the Rule Shape vocabulary.

A Rule's encode manifestation writes to a `ConstraintSink` (see `sink`), never
to a concrete pseudo-Boolean builder, so this package carries no dependency on
the solver package. The solver injects an adapter satisfying the Protocol.
"""
