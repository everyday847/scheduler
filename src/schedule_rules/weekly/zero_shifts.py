"""The zero_shifts archetype — selected fellows may never hold the named shifts.

encode forbids unconditionally: for every (forbidden shift, fellow, week) cell
that has a var, force it false (add_unit(-var)). The current handler emits this
regardless of Strength (a forbid is structurally hard), so encode here takes the
var list directly and forces each false — no soft channel.

evaluate flags any held forbidden shift, window-restricted, so a hand-edit/import
that drops a fellow onto a forbidden shift is reported.
"""

from __future__ import annotations

from schedule_rules.sink import ConstraintSink
from schedule_rules.view import ScheduleView


class ZeroShiftsCriterion:
    name = "zero_shifts"

    def evaluate(
        self,
        view: ScheduleView,
        *,
        fellows: list[str],
        forbidden: set[str],
        window: range,
    ) -> list[tuple[str, int, str]]:
        """Return (fellow, week, shift) for each forbidden shift held in the
        window (empty ⇒ clean)."""
        out: list[tuple[str, int, str]] = []
        for w in window:
            for f in fellows:
                for s in view.all_services(w, f):
                    if s in forbidden:
                        out.append((f, w, s))
        return out

    def encode(
        self,
        sink: ConstraintSink,
        *,
        forbidden_vars: list[int],
    ) -> None:
        """forbidden_vars = every (fellow, week, forbidden-shift) var in scope.
        Forces each false (the shift is never held)."""
        for var in forbidden_vars:
            sink.add_unit(-var)
