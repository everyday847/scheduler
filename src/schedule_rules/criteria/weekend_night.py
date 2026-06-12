"""WeekendNightCriterion — the config-driven archetype for the relationship
between a fellow's WEEKEND ROLE (weekend service) and their WEEKEND-NIGHT call.

ONE Rule Shape (CONTEXT.md) with one instance per weekend night:
  - Friday  (dow 4): the night-holder should hold NO weekend role  -> FORBID.
  - Saturday(dow 5): the night-holder SHOULD hold an NCC weekend role
                     (Weekend NCC1 or NCC2)                         -> REQUIRE.
  - Sunday  (dow 6): the night-holder SHOULD hold Weekend Stroke    -> REQUIRE,
                     plus an eligibility-forbid: a fellow who cannot hold Weekend
                     Stroke that week is barred from Sunday night entirely.

Polarity is per-criterion; STRENGTH is per-role (hard, or soft with a weight) so
the Friday rule can keep NCC1 hard while NCC2/Stroke stay soft. The role set is
weekend-role NAMES (intrinsic to the rule — no palette/shift resolution needed),
so this leaf is dependency-free like the other criteria archetypes. Encode
consumes already-resolved solver vars (a role->var map for the night-holder);
evaluate reads a ScheduleView. The two share the held-roles geometry so they
cannot drift (ADR-0005).
"""

from __future__ import annotations

from dataclasses import dataclass

from schedule_rules.criteria._emit import emit_or, emit_pair
from schedule_rules.sink import ConstraintSink
from schedule_rules.strength import HARD, SOFT
from schedule_rules.view import ScheduleView


REQUIRE = "require"   # violation = night AND NOT (holds any required role)
FORBID = "forbid"     # violation = night AND (holds a forbidden role), per role


@dataclass(frozen=True)
class RoleStrength:
    """Per-role strength for a WeekendNightCriterion. `weight` applies only when
    not `hard`."""
    role: str
    hard: bool
    weight: int = 0


@dataclass(frozen=True)
class EligibilityForbid:
    """REQUIRE-only: a fellow who cannot hold `role` in a week (no weekend-role
    var for it) is forbidden the night (hard) or penalized (soft). Models the
    Sunday non-stroke-eligible case."""
    role: str
    hard: bool
    weight: int = 0


class WeekendNightCriterion:
    def __init__(
        self,
        name: str,
        *,
        dow: int,
        polarity: str,
        role_strengths: tuple[RoleStrength, ...],
        eligibility: EligibilityForbid | None = None,
    ) -> None:
        if polarity not in (REQUIRE, FORBID):
            raise ValueError(f"polarity must be {REQUIRE!r} or {FORBID!r}")
        self.name = name
        self.dow = dow
        self.polarity = polarity
        self.role_strengths = role_strengths
        self.eligibility = eligibility

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(rs.role for rs in self.role_strengths)

    # --- evaluate manifestation ----------------------------------------------
    def evaluate(self, view: ScheduleView, week: int, dow: int, fellow: str) -> bool:
        """Whether THIS (week, dow, fellow-who-holds-that-night) is a violation.

        Driven like NightGatingCriterion.evaluate — the caller already knows
        `fellow` holds the night at (week, dow). Needs no palette/eligibility
        data: a REQUIRE violation already fires when the fellow holds none of the
        required roles, which covers the not-eligible Sunday case for coloring."""
        if dow != self.dow:
            return False
        holds_any = any(
            view.weekend_role_holder(week, rs.role) == fellow
            for rs in self.role_strengths
        )
        return holds_any if self.polarity == FORBID else not holds_any

    # --- encode manifestation ------------------------------------------------
    def encode(
        self,
        sink: ConstraintSink,
        *,
        night_var: int,
        role_vars: dict[str, int],
        eligible: bool = True,
    ) -> None:
        """Emit the criterion for one (week, dow, fellow) night.

        role_vars maps a weekend-role name to the fellow's solver var for holding
        that role this week — present ONLY for roles the fellow CAN hold (others
        absent). `eligible` (REQUIRE only) is False when the fellow can hold none
        of the required roles this week -> the eligibility-forbid fires instead.
        """
        if self.polarity == FORBID:
            # One independent constraint per forbidden role the fellow can hold.
            for rs in self.role_strengths:
                rv = role_vars.get(rs.role)
                if rv is None:
                    continue
                emit_pair(sink, condition_var=rv, night_var=night_var,
                          strength=HARD if rs.hard else SOFT, weight=rs.weight)
            return

        # REQUIRE: the night should be held by someone holding one of the roles.
        present = [role_vars[rs.role] for rs in self.role_strengths
                   if rs.role in role_vars]
        if not present:
            # Fellow can hold none of the required roles this week.
            if self.eligibility is not None:
                if self.eligibility.hard:
                    sink.add_unit(-night_var)
                else:
                    sink.soft(night_var, self.eligibility.weight)
            return
        # violation = night AND NOT OR(present roles). Strength is set-level for a
        # REQUIRE set (holding ANY satisfies); take the strongest role strength.
        hard = any(rs.hard for rs in self.role_strengths if rs.role in role_vars)
        weight = max((rs.weight for rs in self.role_strengths
                      if rs.role in role_vars and not rs.hard), default=0)
        or_held = emit_or(sink, present)
        not_held = sink.new_var()
        sink.weighted_sum_at_least([(or_held, 1), (not_held, 1)], 1)
        sink.at_most_k([or_held, not_held], 1)
        emit_pair(sink, condition_var=not_held, night_var=night_var,
                  strength=HARD if hard else SOFT, weight=weight)


@dataclass(frozen=True)
class ConfiguredWeekendNight:
    """A WeekendNightCriterion carrier — symmetric to ConfiguredNightGating but
    needs no resolved targets (the role set is intrinsic)."""
    criterion: WeekendNightCriterion


def weekend_night_from_params(params: dict) -> WeekendNightCriterion:
    """Build a WeekendNightCriterion from a weekend_night constraint's params —
    the ONE factory the encoder and the production evaluator both call.

    params shape (produced by the config converter):
      {criterion, dow, polarity, role_strengths: [{role, hard, weight}],
       eligibility: {role, hard, weight} | None}
    """
    role_strengths = tuple(
        RoleStrength(rs["role"], bool(rs.get("hard", False)), int(rs.get("weight", 0)))
        for rs in params["role_strengths"]
    )
    el = params.get("eligibility")
    eligibility = (
        EligibilityForbid(el["role"], bool(el.get("hard", False)), int(el.get("weight", 0)))
        if el else None
    )
    return WeekendNightCriterion(
        params["criterion"],
        dow=int(params["dow"]),
        polarity=params["polarity"],
        role_strengths=role_strengths,
        eligibility=eligibility,
    )


def configured_weekend_night(constraints) -> list[ConfiguredWeekendNight]:
    """Filter a constraint list to its weekend_night entries. Duck-types on
    `.kind`/`.params` so this leaf needs no dependency on SemanticConstraint."""
    return [
        ConfiguredWeekendNight(weekend_night_from_params(c.params))
        for c in constraints
        if getattr(c, "kind", None) == "weekend_night"
    ]
