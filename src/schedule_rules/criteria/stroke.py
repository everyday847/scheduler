"""The Stroke night-policy Criterion — one Rule, two co-located manifestations.

Historically the Stroke criterion was implemented twice (encoder in the solver
package, evaluator in the model package) and the two drifted — the Saturday
false-red bug. Here both manifestations consume ONE geometry method,
`gating_terms`, so the day -> gating-target mapping has a single definition.

Geometry (mirrors both prior implementations):
  Rule 1 — weekday Stroke: the night before a Stroke workday is a violation.
    Mon-Thu nights (dow 0-3) gate on the SAME week's Stroke service; the Sunday
    night (dow 6) gates on the NEXT week's. Fri/Sat (dow 4,5) do not gate.
  Rule 2 — weekend Stroke role: the Weekend Stroke holder is penalized on
    Saturday night (dow 5) only. (Sunday is intentionally steered TO the
    weekend-Stroke fellow elsewhere, so it is not penalized.)
  Exemption — a dual-stroke week (>=2 fellows on Stroke) exempts both rules
    that week.
"""

from __future__ import annotations

from dataclasses import dataclass

from schedule_rules.sink import ConstraintSink
from schedule_rules.strength import HARD, Strength
from schedule_rules.view import ScheduleView


_STROKE_SHIFT = "Stroke"
_WEEKEND_STROKE_ROLE = "Weekend Stroke"


@dataclass(frozen=True)
class GatingTerm:
    """One thing whose truth, ANDed with the night, triggers the criterion.

    week: the week the gating fact lives in.
    target: the Shift name (is_weekend_role=False) or Weekend Role name (True).
    is_weekend_role: whether `target` names a Weekend Role rather than a Shift.
    """
    week: int
    target: str
    is_weekend_role: bool


class StrokeCriterion:
    name = "stroke"

    # --- shared geometry: the single source both manifestations consume ------
    def gating_terms(self, week: int, dow: int) -> list[GatingTerm]:
        terms: list[GatingTerm] = []
        # Rule 1: night before a stroke workday.
        if dow in (0, 1, 2, 3):       # Mon-Thu night -> next morning, same week
            terms.append(GatingTerm(week, _STROKE_SHIFT, is_weekend_role=False))
        elif dow == 6:                 # Sun night -> Monday of next week
            terms.append(GatingTerm(week + 1, _STROKE_SHIFT, is_weekend_role=False))
        # Rule 2: weekend Stroke role holder, Saturday only.
        if dow == 5:
            terms.append(GatingTerm(week, _WEEKEND_STROKE_ROLE, is_weekend_role=True))
        return terms

    def _is_exempt(self, view: ScheduleView, week: int) -> bool:
        """Dual-stroke week: >=2 fellows on Stroke that week."""
        if 0 <= week < view.num_weeks:
            return len(view.fellows_on_shift(week, _STROKE_SHIFT)) >= 2
        return False

    # --- evaluate manifestation ----------------------------------------------
    def evaluate(
        self,
        view: ScheduleView,
        week: int,
        dow: int,
        fellow: str,
        *,
        exempt_weeks: frozenset[int] | None = None,
    ) -> bool:
        """Whether a stroke-night violation fires for this (week, dow, fellow).

        Dual-stroke exemption defaults to being computed from the view
        (>=2 fellows on Stroke that week — the single source of truth). A caller
        may inject *exempt_weeks* to override (e.g. a precomputed dual-stroke set
        threaded through a legacy code path)."""
        for term in self.gating_terms(week, dow):
            exempt = (
                term.week in exempt_weeks
                if exempt_weeks is not None
                else self._is_exempt(view, term.week)
            )
            if exempt:
                continue
            if term.is_weekend_role:
                if view.weekend_role_holder(term.week, term.target) == fellow:
                    return True
            else:
                if view.weekday_service(term.week, fellow) == term.target:
                    return True
        return False

    # --- encode manifestation ------------------------------------------------
    def encode(
        self,
        sink: ConstraintSink,
        *,
        night_var: int,
        term_var: int,
        exempt_var: int | None,
        strength: Strength,
        weight: int,
    ) -> None:
        """Emit: (night AND term [AND NOT exempt]) is a violation.

        The caller resolves a GatingTerm to its pinned/solver variable and the
        gating week's dual-stroke exemption var (or None when no exemption
        applies), then calls encode once per term.
        """
        # Term-first emission order (condition before night), matching the
        # solver's prior helpers so the relocation is byte-for-byte neutral.
        if exempt_var is None:
            if strength is HARD:
                sink.at_most_k([term_var, night_var], 1)
            else:
                ind = sink.new_var()
                sink.weighted_sum_at_most([(term_var, 1), (night_var, 1), (-ind, 1)], 2)
                sink.weighted_sum_at_least([(term_var, 1), (-ind, 1)], 1)
                sink.weighted_sum_at_least([(night_var, 1), (-ind, 1)], 1)
                sink.soft(ind, weight)
        else:
            if strength is HARD:
                # term + night + (1 - exempt) <= 2  ⟺  can't have term & night & not-exempt
                sink.weighted_sum_at_most([(term_var, 1), (night_var, 1), (-exempt_var, 1)], 2)
            else:
                ind = sink.new_var()
                sink.weighted_sum_at_most(
                    [(term_var, 1), (night_var, 1), (-exempt_var, 1), (-ind, 1)], 3
                )
                sink.weighted_sum_at_least([(term_var, 1), (-ind, 1)], 1)
                sink.weighted_sum_at_least([(night_var, 1), (-ind, 1)], 1)
                sink.weighted_sum_at_least([(-exempt_var, 1), (-ind, 1)], 1)
                sink.soft(ind, weight)
