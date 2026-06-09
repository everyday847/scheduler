"""The pre-vacation weekend Criterion — co-located Rule.

A fellow holding ANY Weekend role in the week immediately before a week they are
on Vac is a soft violation (weight 1) — a cell-localizable weekend fault whose
guilty cell is that week's Weekend-role cell. evaluate is net-new (no prior
weekend evaluator existed).

encode emits: ind = vac_var AND role_var (vac-first term order, matching the
prior encoder helper so the relocation is byte-neutral).
"""

from __future__ import annotations

from schedule_rules.sink import ConstraintSink
from schedule_rules.strength import HARD, Strength
from schedule_rules.view import ScheduleView


_VAC_SHIFT = "Vac"
_WEEKEND_ROLES = ("Weekend NCC1", "Weekend NCC2", "Weekend Stroke")


class PrevacationWeekendCriterion:
    name = "prevacation_weekend"

    def evaluate(self, view: ScheduleView, week: int, role: str, fellow: str) -> bool:
        if role not in _WEEKEND_ROLES:
            return False
        if view.weekend_role_holder(week, role) != fellow:
            return False
        nxt = week + 1
        if not (0 <= nxt < view.num_weeks):
            return False
        return view.weekday_service(nxt, fellow) == _VAC_SHIFT

    def encode(
        self,
        sink: ConstraintSink,
        *,
        role_var: int,
        vac_var: int,
        strength: Strength,
        weight: int,
    ) -> None:
        """role_var: holds the weekend role in week w. vac_var: on Vac in w+1."""
        if strength is HARD:
            sink.at_most_k([vac_var, role_var], 1)
        else:
            ind = sink.new_var()
            sink.weighted_sum_at_most([(vac_var, 1), (role_var, 1), (-ind, 1)], 2)
            sink.weighted_sum_at_least([(vac_var, 1), (-ind, 1)], 1)
            sink.weighted_sum_at_least([(role_var, 1), (-ind, 1)], 1)
            sink.soft(ind, weight)
