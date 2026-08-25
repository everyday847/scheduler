"""Co-located weekly-archetype Rules — each exposes encode() and evaluate() side
by side over a single shared geometry, pinned by a contract test (ADR-0005).

These are the generic weekly shift archetypes (full_assignment,
specific_assignment, zero_shifts, shift_total, staffing_per_week). Distinct from
the night/weekend `criteria/` package only in domain; the co-location contract is
identical. The encode side writes to a ConstraintSink ONLY (no solver-package
dependency); the evaluate side reads a ScheduleView.
"""
