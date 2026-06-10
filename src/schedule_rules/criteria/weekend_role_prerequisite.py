"""The WeekendRolePrerequisite archetype — a weekend role gated on prior service.

A fellow may hold a weekend role in week w only if they have ALREADY served the
qualifying weekday shift in some week w' <= w (the same week counts: a Mon-Fri
service satisfies that weekend). Two Standing-tier kinds share this shape, each
selecting a role-set and a prerequisite weekday shift-set:

  * weekend_stroke_prerequisite : role {Weekend Stroke}, prereq {Stroke}.
  * weekend_ncc_prerequisite    : role {Weekend NCC1, Weekend NCC2},
                                   prereq {NCC1, NCC2}.

encode reproduces the encoder's prior `_encode_weekend_prerequisites` against the
sink, per (fellow, week, role):

  prior = the fellow's qualifying weekday vars in weeks w' <= w (inclusive).
  - prior non-empty ⇒ weighted_sum_at_least(prior + [(-role_var,1)], 0), i.e.
    role_var <= sum(prior).
  - prior empty (no qualifying service is even POSSIBLE before w):
      HARD ⇒ add_unit(-role_var)  (forbid holding the role).
      SOFT ⇒ slack = new_var(); at_most_k([role_var, slack], 1); soft(slack, weight).

evaluate flags a fellow holding the weekend role in week w who had NO qualifying
weekday service in any week w' <= w. Exempt fellows are skipped.
"""

from __future__ import annotations

from schedule_rules.sink import ConstraintSink
from schedule_rules.strength import HARD, Strength
from schedule_rules.view import ScheduleView


class WeekendRolePrerequisiteCriterion:
    name = "weekend_role_prerequisite"

    def __init__(self, *, role_names: tuple[str, ...], prereq_shifts: tuple[str, ...]):
        """role_names: the weekend-role strings this rule gates (as read by
        view.weekend_role_holder, e.g. "Weekend Stroke"). prereq_shifts: the
        qualifying weekday shift names (e.g. "Stroke" or "NCC1"/"NCC2")."""
        self.role_names = role_names
        self.prereq_shifts = prereq_shifts

    # -- evaluate -----------------------------------------------------------
    def _has_prior_service(self, view: ScheduleView, fellow: str, week: int) -> bool:
        prereq = set(self.prereq_shifts)
        for wp in range(week + 1):  # inclusive window w' <= w
            if view.weekday_service(wp, fellow) in prereq:
                return True
        return False

    def evaluate(
        self,
        view: ScheduleView,
        *,
        fellows: list[str],
        exempt: set[str] | None = None,
    ) -> list[tuple[str, int, str]]:
        """Return (fellow, week, role_name) for each weekend role held without
        any qualifying weekday service in weeks w' <= w. *fellows* limits the
        scan; *exempt* names are skipped."""
        exempt = exempt or set()
        out: list[tuple[str, int, str]] = []
        for w in range(view.num_weeks):
            for role in self.role_names:
                holder = view.weekend_role_holder(w, role)
                if holder is None or holder in exempt:
                    continue
                if fellows and holder not in fellows:
                    continue
                if not self._has_prior_service(view, holder, w):
                    out.append((holder, w, role))
        return out

    # -- encode -------------------------------------------------------------
    def encode(
        self,
        sink: ConstraintSink,
        *,
        role_var: int,
        prior_vars: list[int],
        strength: Strength,
        weight: int,
    ) -> None:
        """Gate one (fellow, week, role) cell. *role_var*: the weekend-role var.
        *prior_vars*: the fellow's qualifying weekday vars in weeks w' <= w
        (already filtered to nonzero). Reproduces _encode_weekend_prerequisites:
        non-empty prior ⇒ role_var <= sum(prior); empty prior ⇒ forbid (HARD) or
        a penalized slack (SOFT)."""
        if prior_vars:
            sink.weighted_sum_at_least(
                [(v, 1) for v in prior_vars] + [(-role_var, 1)], 0
            )
        elif strength is HARD:
            sink.add_unit(-role_var)
        else:
            slack = sink.new_var()
            sink.at_most_k([role_var, slack], 1)
            sink.soft(slack, weight)
