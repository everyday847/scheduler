"""The Clinic night-policy Criterion — one Rule, co-located manifestations.

A Clinic/Elective fellow is softly discouraged from the night before a clinic
day. The nights that gate: Tue and Wed (dow 1,2) read the SAME week's service;
the Sunday night (dow 6) gates on the NEXT week (its Monday). Mon/Thu/Fri/Sat
nights do not gate. Gating shift matched exactly against "Clinic/Elective"
(Telestroke/Clinic fellows are exempt — exact-match excludes them).
"""

from __future__ import annotations

from dataclasses import dataclass

from schedule_rules.criteria._emit import emit_pair
from schedule_rules.sink import ConstraintSink
from schedule_rules.strength import Strength
from schedule_rules.view import ScheduleView


_CLINIC_SHIFT = "Clinic/Elective"


@dataclass(frozen=True)
class GatingTerm:
    week: int
    target: str
    is_weekend_role: bool = False


class ClinicCriterion:
    name = "clinic"

    def gating_terms(self, week: int, dow: int) -> list[GatingTerm]:
        if dow in (1, 2):       # Tue/Wed night -> clinic day same week
            return [GatingTerm(week, _CLINIC_SHIFT)]
        if dow == 6:            # Sun night -> Monday of next week
            return [GatingTerm(week + 1, _CLINIC_SHIFT)]
        return []

    def evaluate(self, view: ScheduleView, week: int, dow: int, fellow: str) -> bool:
        for term in self.gating_terms(week, dow):
            if 0 <= term.week < view.num_weeks:
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
