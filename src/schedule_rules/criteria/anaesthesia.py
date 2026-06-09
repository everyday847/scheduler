"""The Anaesthesia night-policy Criterion — one Rule, co-located manifestations.

A weekday night (Mon-Fri, dow 0-4) held by a fellow on Anaesthesia THAT week is
a violation. Weekend nights (Sat/Sun) do not fire. The gating shift is matched
exactly against the canonical "Anaesthesia" name (SHIFT_MAP rewrites the
"Anesthesia" spelling at import, so exact-match is faithful).
"""

from __future__ import annotations

from dataclasses import dataclass

from schedule_rules.criteria._emit import emit_pair
from schedule_rules.sink import ConstraintSink
from schedule_rules.strength import Strength
from schedule_rules.view import ScheduleView


_ANAESTHESIA_SHIFT = "Anaesthesia"


@dataclass(frozen=True)
class GatingTerm:
    week: int
    target: str
    is_weekend_role: bool = False


class AnaesthesiaCriterion:
    name = "anaesthesia"

    def gating_terms(self, week: int, dow: int) -> list[GatingTerm]:
        if dow <= 4:  # weekday nights only
            return [GatingTerm(week, _ANAESTHESIA_SHIFT)]
        return []

    def evaluate(self, view: ScheduleView, week: int, dow: int, fellow: str) -> bool:
        for term in self.gating_terms(week, dow):
            if view.weekday_service(term.week, fellow) == term.target:
                return True
        return False

    def encode(
        self,
        sink: ConstraintSink,
        *,
        night_var: int,
        term_var: int,
        strength: Strength,
        weight: int,
    ) -> None:
        emit_pair(sink, condition_var=term_var, night_var=night_var,
                  strength=strength, weight=weight)
