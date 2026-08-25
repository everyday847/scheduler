"""Co-located night-layer Rule Shapes — each exposes encode() and evaluate()
side by side over a single shared geometry, pinned by a contract test (ADR-0005).

The NIGHT layer pins or forbids literals on the per-day night variables (xn).
Like weekly/ and criteria/, the encode side writes to a ConstraintSink ONLY (no
solver-package dependency); the evaluate side reads a ScheduleView's
night_holder(day).
"""
