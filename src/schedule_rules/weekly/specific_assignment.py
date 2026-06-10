"""The specific_assignment archetype — a named shift must be filled in a named
week by SOMEONE from the selected fellows (a coverage pin).

encode operates on a single (shift, week): the disjunction "at least one selected
fellow holds the shift that week". HARD ⇒ at_least_k(vars, 1). SOFT ⇒ a fresh
indicator v fires iff none hold it (sum(vars) + v >= 1 AND, per fellow var vw,
v + vw <= 1 — so v can be 1 only when every vw is 0). One soft indicator total.

evaluate is per-fellow over the window (it flags each selected fellow not on the
target shift that week) — a stricter read than the encode's "someone covers it".
The validate.py wrapper preserves this; both are pinned by the contract test on
the geometry where they coincide (one fellow, one week).
"""

from __future__ import annotations

from schedule_rules.sink import ConstraintSink
from schedule_rules.strength import HARD, Strength
from schedule_rules.view import ScheduleView


class SpecificAssignmentCriterion:
    name = "specific_assignment"

    def evaluate(
        self,
        view: ScheduleView,
        *,
        fellows: list[str],
        target: str | None,
        window: range,
    ) -> list[tuple[str, int]]:
        """Return (fellow, week) pairs where the fellow is not on *target*
        (empty ⇒ all hold it across the window)."""
        out: list[tuple[str, int]] = []
        for w in window:
            for f in fellows:
                if target not in view.all_services(w, f):
                    out.append((f, w))
        return out

    def encode(
        self,
        sink: ConstraintSink,
        *,
        vars_for_week: list[int],
        strength: Strength,
        weight: int,
    ) -> None:
        """vars_for_week = the selected fellows' vars on the target shift in the
        target week. Emits the coverage disjunction (someone holds it)."""
        if not vars_for_week:
            return
        if strength is HARD:
            sink.at_least_k(vars_for_week, 1)
        else:
            v = sink.new_var()
            sink.at_least_k(vars_for_week + [v], 1)
            for vw in vars_for_week:
                sink.at_most_k([v, vw], 1)
            sink.soft(v, weight)
