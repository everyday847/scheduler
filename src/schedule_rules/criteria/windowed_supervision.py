"""Windowed opportunistic supervision — the "may" supervision shape.

Distinct from mandatory qualified coverage (the "must" shape — e.g. "≥1 NCC
fellow on NCC1/NCC2 every week", a high-supply service that is always staffed,
modeled as the unconditional `ncc_stroke_oversight` weekly floor). This is the
LOW-supply shape: a service (Stroke) with few qualified supervisors and rarely
room for more than one fellow. We cannot guarantee a supervisee is supervised
every week; instead, during a window, we ALLOW dual-coverage but cap it so the
second slot is the only unsupervised one:

  in-window  (w_start <= w < w_end): at most 1 supervisor AND at most 1
             non-supervisor on the shift that week (so dual-coverage = exactly
             one supervisor + one supervisee — a supervised pairing).
  out-window: at most 1 fellow total on the shift (no dual-coverage).

A week where an Imported fellow already occupies the slot is skipped by the
encode adapter (the workbook fixed it; we cannot also cap the remaining pool).

Hard-only: the rule emits at-most-k caps, never a soft penalty (steering the
second slot toward a *specific* supervisee is a separate, experimental concern).
"""

from __future__ import annotations

from schedule_rules.sink import ConstraintSink
from schedule_rules.view import ScheduleView


class WindowedSupervisionCriterion:
    name = "windowed_supervision"

    # --- encode manifestation ------------------------------------------------
    def encode(
        self,
        sink: ConstraintSink,
        *,
        supervisor_vars: list[int],
        non_supervisor_vars: list[int],
        in_window: bool,
    ) -> None:
        """Emit the per-week cap. The adapter resolves the week's Stroke vars and
        the supervisor/non-supervisor split, and decides in_window, then calls
        once per week. In-window: two separate at-most-1 caps (supervisors,
        non-supervisors). Out-of-window: one at-most-1 over the union."""
        if in_window:
            if supervisor_vars:
                sink.at_most_k(supervisor_vars, 1)
            if non_supervisor_vars:
                sink.at_most_k(non_supervisor_vars, 1)
        else:
            both = supervisor_vars + non_supervisor_vars
            if both:
                sink.at_most_k(both, 1)

    # --- evaluate manifestation ----------------------------------------------
    def evaluate(
        self,
        view: ScheduleView,
        *,
        supervisors: set[str],
        shift: str,
        window: tuple[int, int],
        num_weeks: int,
    ) -> list[tuple[int, str]]:
        """Return (week, reason) for each week whose cap is exceeded in a concrete
        Schedule. in-window: >1 supervisor or >1 non-supervisor on the shift;
        out-of-window: >1 fellow total on the shift."""
        w_start, w_end = window
        out: list[tuple[int, str]] = []
        for w in range(num_weeks):
            on = view.fellows_on_shift(w, shift)
            if not on:
                continue
            sup = [f for f in on if f in supervisors]
            non = [f for f in on if f not in supervisors]
            if w_start <= w < w_end:
                if len(sup) > 1:
                    out.append((w, f"{len(sup)} supervisors on {shift}"))
                if len(non) > 1:
                    out.append((w, f"{len(non)} non-supervisors on {shift}"))
            else:
                if len(on) > 1:
                    out.append((w, f"{len(on)} on {shift} outside the supervision window"))
        return out
