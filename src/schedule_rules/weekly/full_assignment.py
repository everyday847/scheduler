"""The full_assignment archetype — each fellow in scope holds exactly one shift
per week (the Solver Invariant for the assignment grid).

encode (per fellow, per week): HARD ⇒ at least one of the fellow's active shift
vars is true. SOFT ⇒ a fresh indicator v fires iff the fellow holds no shift that
week (sum(active) + v >= 1 AND sum(active) + v <= 1; the fundamental exactly-one
constraint elsewhere already caps sum(active) <= 1, so v == 1 iff the fellow is
unassigned). One soft indicator per (fellow, week).

evaluate flags BOTH unassigned (0 shifts) and double-booked (>1) — the latter the
solver structurally prevents but a hand-edit/import can introduce, so the
evaluator still reports it (mirrors group_count_balance reporting structurally
impossible states).
"""

from __future__ import annotations

from schedule_rules.sink import ConstraintSink
from schedule_rules.strength import HARD, Strength
from schedule_rules.view import ScheduleView


class FullAssignmentCriterion:
    name = "full_assignment"

    def evaluate(
        self,
        view: ScheduleView,
        *,
        fellows: list[str],
        window: range,
    ) -> list[tuple[str, int, int]]:
        """Return (fellow, week, n_shifts) for each (fellow, week) not holding
        exactly one shift (empty ⇒ everyone fully assigned)."""
        out: list[tuple[str, int, int]] = []
        for w in window:
            for f in fellows:
                n = len(view.all_services(w, f))
                if n != 1:
                    out.append((f, w, n))
        return out

    def encode(
        self,
        sink: ConstraintSink,
        *,
        week_var_lists: list[list[int]],
        strength: Strength,
        weight: int,
    ) -> None:
        """week_var_lists[i] = the active shift vars for one (fellow, week) cell.
        Emits, per cell, at-least-one (HARD) or an unassigned soft indicator."""
        for active in week_var_lists:
            if not active:
                continue
            if strength is HARD:
                sink.at_least_k(active, 1)
            else:
                v = sink.new_var()
                # v == 1 iff no shift assigned (the fellow's exactly-one grid
                # constraint caps sum(active) <= 1 elsewhere).
                sink.at_least_k(active + [v], 1)
                sink.at_most_k(active + [v], 1)
                sink.soft(v, weight)
