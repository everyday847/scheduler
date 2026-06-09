"""The Sunday-following night-policy Criterion — one Rule, co-located.

A Sunday-night fellow whose NEXT week's weekday service is "non-preferred" is a
violation (a Sunday night rolling into a service where the following Monday is
undesirable to be post-call). Fires only on Sunday nights (dow 6); the last week
has no next week and never fires.

Single source of truth for "non-preferred": NON_PREFERRED_SUNDAY_FOLLOWING. This
is the ENCODER's historical set, chosen as canonical when co-locating (the prior
evaluator's config=None predicate under-reported NS / SCVMC Rehab / Vac, a
false-negative the workbook displayed). See ADR-0005 and the slice plan.

The encode side ORs the non-preferred shift vars for the next week into one
indicator, then pairs it with the night — so the caller passes the resolved
term vars (one per non-preferred shift present that week).
"""

from __future__ import annotations

from dataclasses import dataclass

from schedule_rules.criteria._emit import emit_or, emit_pair
from schedule_rules.sink import ConstraintSink
from schedule_rules.strength import Strength
from schedule_rules.view import ScheduleView


NON_PREFERRED_SUNDAY_FOLLOWING = frozenset(
    {"Anaesthesia", "Clinic/Elective", "Telestroke/Clinic", "Vac", "NS", "NIR", "SICU", "SCVMC Rehab"}
)


@dataclass(frozen=True)
class GatingTerm:
    week: int
    target: str  # a non-preferred shift name
    is_weekend_role: bool = False


class SundayFollowingCriterion:
    name = "sunday_following"

    def gating_terms(self, week: int, dow: int) -> list[GatingTerm]:
        if dow != 6:
            return []
        return [GatingTerm(week + 1, s) for s in sorted(NON_PREFERRED_SUNDAY_FOLLOWING)]

    def evaluate(self, view: ScheduleView, week: int, dow: int, fellow: str) -> bool:
        if dow != 6:
            return False
        nxt = week + 1
        if not (0 <= nxt < view.num_weeks):
            return False
        return view.weekday_service(nxt, fellow) in NON_PREFERRED_SUNDAY_FOLLOWING

    def encode(
        self,
        sink: ConstraintSink,
        *,
        night_var: int,
        term_vars: list[int],
        strength: Strength,
        weight: int,
    ) -> None:
        """Emit: (night AND any-non-preferred-next-week) is a violation.

        *term_vars* are the next-week shift vars for the non-preferred shifts
        present that week (empty ⇒ nothing to encode)."""
        if not term_vars:
            return
        or_var = emit_or(sink, term_vars)
        emit_pair(sink, condition_var=or_var, night_var=night_var,
                  strength=strength, weight=weight)
