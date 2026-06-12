"""WeekendGatingCriterion — the config-driven archetype for weekend-role criteria.

The two historical weekend-cell criteria are instances of one shape: a fellow
holding a Weekend Role in a week is a violation when gated by that fellow's
weekday service in a related week. They differ only in polarity and which
service gates:

  ALIGN (weekend_role_mismatch): the role REQUIRES a matching same-week weekday
    shift (Weekend NCC1↔NCC1, Weekend NCC2↔NCC2, Weekend Stroke↔Stroke); holding
    the role while NOT on the matching shift is the violation (absent polarity).
    When the fellow *cannot* be on the matching shift that week (no such var),
    holding the role is unconditionally a mismatch.

  GATE (prevacation_weekend): holding ANY of the named roles while ON a gating
    service in a related week (offset, e.g. Vac next week) is the violation
    (present polarity).

Geometry/targets are config data, not hardcoded shift names. The caller resolves
the role var and the gating weekday var; this leaf only signs them. Symmetric to
NightGatingCriterion. evaluate reads a ScheduleView; the resolved gating shift
set comes in as `resolved_targets` (GATE) or the role→shift map (ALIGN), so this
leaf stays palette-free and encode + evaluate consume one source.
"""

from __future__ import annotations

from dataclasses import dataclass

from schedule_rules.sink import ConstraintSink
from schedule_rules.strength import HARD, Strength
from schedule_rules.view import ScheduleView


ALIGN = "align"
GATE = "gate"


class WeekendGatingCriterion:
    def __init__(
        self,
        name: str,
        *,
        mode: str,
        role_to_shift: dict[str, str] | None = None,
        role_weights: dict[str, int] | None = None,
        roles: tuple[str, ...] | None = None,
        gate_target_key: str | None = None,
        gate_week_offset: int = 0,
    ) -> None:
        if mode not in (ALIGN, GATE):
            raise ValueError(f"WeekendGatingCriterion mode must be {ALIGN!r} or {GATE!r}")
        self.name = name
        self.mode = mode
        self.role_to_shift = role_to_shift or {}
        # ALIGN: per-role mismatch weight (role name -> weight). A role absent
        # here falls back to the caller's default (weekend_mismatch_weight).
        self.role_weights = role_weights or {}
        self.roles = roles or ()
        self.gate_target_key = gate_target_key
        self.gate_week_offset = gate_week_offset

    # --- evaluate manifestation ----------------------------------------------
    def evaluate(
        self,
        view: ScheduleView,
        week: int,
        role: str,
        fellow: str,
        *,
        resolved_targets: dict[str, frozenset[str]] | None = None,
    ) -> bool:
        if self.mode == ALIGN:
            shift = self.role_to_shift.get(role)
            if shift is None:
                return False
            if view.weekend_role_holder(week, role) != fellow:
                return False
            return view.weekday_service(week, fellow) != shift
        # GATE
        if role not in self.roles:
            return False
        if view.weekend_role_holder(week, role) != fellow:
            return False
        gw = week + self.gate_week_offset
        if not (0 <= gw < view.num_weeks):
            return False
        targets = (resolved_targets or {}).get(self.gate_target_key, frozenset())
        return view.weekday_service(gw, fellow) in targets

    # --- encode manifestation ------------------------------------------------
    def encode(
        self,
        sink: ConstraintSink,
        *,
        role_var: int,
        gate_var: int | None,
        strength: Strength,
        weight: int,
    ) -> None:
        """Emit the violation for one (week, role, fellow).

        ALIGN: gate_var is the matching same-week weekday-shift var (None ⇒ the
          fellow cannot be on the matching shift ⇒ holding the role is an
          unconditional violation). Violation = role AND NOT gate.
        GATE: gate_var is the gating-service var in the related week (never None;
          the caller skips when absent). Violation = role AND gate.
        """
        if self.mode == ALIGN:
            if gate_var is None:
                if strength is HARD:
                    sink.add_unit(-role_var)
                else:
                    sink.soft(role_var, weight)
                return
            if strength is HARD:
                # forbid role AND NOT match: role + ~match <= 1
                sink.weighted_sum_at_most([(role_var, 1), (-gate_var, 1)], 1)
            else:
                mismatch = sink.new_var()
                sink.weighted_sum_at_most([(role_var, 1), (-gate_var, 1), (-mismatch, 1)], 2)
                sink.weighted_sum_at_least([(role_var, 1), (-mismatch, 1)], 1)
                sink.weighted_sum_at_least([(-gate_var, 1), (-mismatch, 1)], 1)
                sink.soft(mismatch, weight)
            return
        # GATE: role AND gate is the violation (gate-first term order matches the
        # prior prevacation helper so the relocation is byte-neutral).
        if strength is HARD:
            sink.at_most_k([gate_var, role_var], 1)
        else:
            ind = sink.new_var()
            sink.weighted_sum_at_most([(gate_var, 1), (role_var, 1), (-ind, 1)], 2)
            sink.weighted_sum_at_least([(gate_var, 1), (-ind, 1)], 1)
            sink.weighted_sum_at_least([(role_var, 1), (-ind, 1)], 1)
            sink.soft(ind, weight)


@dataclass(frozen=True)
class ConfiguredWeekendGating:
    criterion: WeekendGatingCriterion
    resolved_targets: dict[str, frozenset[str]]


def weekend_gating_from_params(params: dict) -> tuple[WeekendGatingCriterion, dict[str, frozenset[str]]]:
    """Build a (criterion, resolved_targets) pair from a weekend_gating
    constraint's params — the ONE factory the encoder and the production
    evaluator both call.

    params shape (produced by the config converter):
      ALIGN: {criterion, mode:"align", role_to_shift:{role:shift}}
      GATE:  {criterion, mode:"gate", roles:[role...], gate_target_key:str,
              gate_week_offset:int, resolved_targets:{key:[shifts]}}
    """
    mode = params["mode"]
    crit = WeekendGatingCriterion(
        params["criterion"],
        mode=mode,
        role_to_shift=params.get("role_to_shift"),
        role_weights=params.get("role_weights"),
        roles=tuple(params.get("roles", ())),
        gate_target_key=params.get("gate_target_key"),
        gate_week_offset=int(params.get("gate_week_offset", 0)),
    )
    resolved = {k: frozenset(v) for k, v in params.get("resolved_targets", {}).items()}
    return crit, resolved


def configured_weekend_gating(constraints) -> list[ConfiguredWeekendGating]:
    out: list[ConfiguredWeekendGating] = []
    for c in constraints:
        if getattr(c, "kind", None) == "weekend_gating":
            crit, resolved = weekend_gating_from_params(c.params)
            out.append(ConfiguredWeekendGating(crit, resolved))
    return out
