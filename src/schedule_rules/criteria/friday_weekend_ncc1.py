"""The Friday/Weekend-NCC1 night-policy Criterion — one Rule, co-located.

The Friday-night fellow who also holds the Weekend NCC1 role that week is a
violation (a Friday night rolling into the NCC1 weekend). Fires only on Friday
nights (dow 4). Hard by default in the shipped config.
"""

from __future__ import annotations

from dataclasses import dataclass

from schedule_rules.criteria._emit import emit_pair
from schedule_rules.sink import ConstraintSink
from schedule_rules.strength import Strength
from schedule_rules.view import ScheduleView


_WEEKEND_NCC1_ROLE = "Weekend NCC1"


@dataclass(frozen=True)
class GatingTerm:
    week: int
    target: str
    is_weekend_role: bool = True


class FridayWeekendNcc1Criterion:
    name = "friday_weekend_ncc1"

    def gating_terms(self, week: int, dow: int) -> list[GatingTerm]:
        if dow == 4:  # Friday night
            return [GatingTerm(week, _WEEKEND_NCC1_ROLE)]
        return []

    def evaluate(self, view: ScheduleView, week: int, dow: int, fellow: str) -> bool:
        for term in self.gating_terms(week, dow):
            if view.weekend_role_holder(term.week, term.target) == fellow:
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
