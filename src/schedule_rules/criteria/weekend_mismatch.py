"""The Weekend-role/weekday-service mismatch Criterion — co-located Rule.

A fellow holding a Weekend role (NCC1 / NCC2 / Stroke) whose weekday service that
week is NOT the matching shift is a soft violation — a cell-localizable weekend
fault (the guilty cell is that week's Weekend-role cell). Matching pairs:
Weekend NCC1↔NCC1, Weekend NCC2↔NCC2, Weekend Stroke↔Stroke.

encode emits: mismatch = role_var AND NOT weekday_match_var. When the fellow
*cannot* be on the matching weekday shift that week (no such var), holding the
role is unconditionally a mismatch (a unit penalty on the role var) — mirroring
the encoder's prior `weekday_var == 0` branch, so the relocation is byte-neutral.
"""

from __future__ import annotations

from schedule_rules.sink import ConstraintSink
from schedule_rules.strength import HARD, Strength
from schedule_rules.view import ScheduleView


ROLE_TO_SHIFT = {
    "Weekend NCC1": "NCC1",
    "Weekend NCC2": "NCC2",
    "Weekend Stroke": "Stroke",
}


class WeekendRoleMismatchCriterion:
    name = "weekend_role_mismatch"

    def matching_shift(self, role: str) -> str | None:
        return ROLE_TO_SHIFT.get(role)

    def evaluate(self, view: ScheduleView, week: int, role: str, fellow: str) -> bool:
        shift = ROLE_TO_SHIFT.get(role)
        if shift is None:
            return False
        if view.weekend_role_holder(week, role) != fellow:
            return False
        return view.weekday_service(week, fellow) != shift

    def encode(
        self,
        sink: ConstraintSink,
        *,
        role_var: int,
        weekday_match_var: int | None,
        strength: Strength,
        weight: int,
    ) -> None:
        """role_var: holds the weekend role. weekday_match_var: on the matching
        weekday shift that week (None ⇒ impossible ⇒ always a mismatch)."""
        if weekday_match_var is None:
            if strength is HARD:
                sink.add_unit(-role_var)
            else:
                sink.soft(role_var, weight)
            return
        if strength is HARD:
            # forbid role AND NOT match: role + ~match <= 1
            sink.weighted_sum_at_most([(role_var, 1), (-weekday_match_var, 1)], 1)
        else:
            mismatch = sink.new_var()
            sink.weighted_sum_at_most([(role_var, 1), (-weekday_match_var, 1), (-mismatch, 1)], 2)
            sink.weighted_sum_at_least([(role_var, 1), (-mismatch, 1)], 1)
            sink.weighted_sum_at_least([(-weekday_match_var, 1), (-mismatch, 1)], 1)
            sink.soft(mismatch, weight)
