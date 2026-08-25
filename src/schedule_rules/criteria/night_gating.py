"""NightGatingCriterion — the config-driven archetype for night-policy criteria.

ONE Rule Shape (CONTEXT.md), parameterized by a gating TABLE rather than a
hardcoded shift name. The five historical night criteria (anaesthesia, clinic,
stroke, friday_weekend_ncc1, sunday_following) are all instances of this shape:
a night assignment is a violation when, ANDed with some gating fact in a related
week, it fires. They differ only in their table (which nights fire, the week
offset, whether the target is a Shift or a Weekend Role) and whether a
dual-coverage exemption applies (stroke).

Geometry lives in ONE method, `gating_terms(week, dow)`, consumed by BOTH the
encode and evaluate manifestations (ADR-0005), so the day -> gating-target
mapping has a single definition and cannot drift.

Targets are NEVER shift-name literals in this module. A term names a *target
key*; the caller (encoder / evaluator) supplies the resolved concrete shift-name
set for that key (`resolved_targets`), so this leaf carries no instance data and
no dependency on the palette/model package.
"""

from __future__ import annotations

from dataclasses import dataclass

from schedule_rules.criteria._emit import emit_or, emit_pair
from schedule_rules.sink import ConstraintSink
from schedule_rules.strength import HARD, Strength
from schedule_rules.view import ScheduleView


@dataclass(frozen=True)
class NightTermSpec:
    """One gating term of a night criterion.

    dows: the night-of-week values (0=Mon .. 6=Sun) this term fires on.
    week_offset: 0 = the night's own week, +1 = next week (a Sunday night gating
        on the following Monday's service).
    target_key: names the resolved target set the caller supplies. For a
        weekday-shift target it keys into `resolved_targets`; for a weekend-role
        target it IS the role name.
    is_weekend_role: the target names a Weekend Role rather than a Shift set.
    """
    dows: tuple[int, ...]
    week_offset: int
    target_key: str
    is_weekend_role: bool = False


@dataclass(frozen=True)
class ExemptionSpec:
    """A dual-coverage exemption: a gating week is exempt when >= `threshold`
    fellows are on `target_shift` that week (the stroke dual-coverage rule)."""
    target_shift: str
    threshold: int


@dataclass(frozen=True)
class GatingTerm:
    """A resolved gating term: the week the gating fact lives in, plus its
    target. `target_key` keys into `resolved_targets` (shift) or names the
    Weekend Role directly (when `is_weekend_role`)."""
    week: int
    target_key: str
    is_weekend_role: bool


class NightGatingCriterion:
    def __init__(
        self,
        name: str,
        terms: tuple[NightTermSpec, ...],
        *,
        exemption: ExemptionSpec | None = None,
    ) -> None:
        self.name = name
        self.terms = terms
        self.exemption = exemption

    # --- shared geometry: the single source both manifestations consume ------
    def gating_terms(self, week: int, dow: int) -> list[GatingTerm]:
        out: list[GatingTerm] = []
        for spec in self.terms:
            if dow in spec.dows:
                out.append(GatingTerm(
                    week + spec.week_offset, spec.target_key, spec.is_weekend_role))
        return out

    def _is_exempt(self, view: ScheduleView, week: int) -> bool:
        if self.exemption is None:
            return False
        if 0 <= week < view.num_weeks:
            return (len(view.fellows_on_shift(week, self.exemption.target_shift))
                    >= self.exemption.threshold)
        return False

    # --- evaluate manifestation ----------------------------------------------
    def evaluate(
        self,
        view: ScheduleView,
        week: int,
        dow: int,
        fellow: str,
        *,
        resolved_targets: dict[str, frozenset[str]],
        exempt_weeks: frozenset[int] | None = None,
    ) -> bool:
        """Whether a violation fires for this (week, dow, fellow).

        resolved_targets maps a weekday-shift term's target_key to the concrete
        shift-name set; weekend-role terms read view.weekend_role_holder. The
        exemption defaults to being computed from the view; a caller may inject
        precomputed *exempt_weeks* (a legacy dual-stroke set)."""
        for term in self.gating_terms(week, dow):
            exempt = (
                term.week in exempt_weeks
                if exempt_weeks is not None
                else self._is_exempt(view, term.week)
            )
            if exempt:
                continue
            if term.is_weekend_role:
                if view.weekend_role_holder(term.week, term.target_key) == fellow:
                    return True
            else:
                if not (0 <= term.week < view.num_weeks):
                    continue
                if view.weekday_service(term.week, fellow) in resolved_targets.get(
                    term.target_key, frozenset()
                ):
                    return True
        return False

    # --- encode manifestation ------------------------------------------------
    def encode(
        self,
        sink: ConstraintSink,
        *,
        night_var: int,
        term_vars: list[int],
        exempt_var: int | None,
        strength: Strength,
        weight: int,
    ) -> None:
        """Emit: (night AND any-term [AND NOT exempt]) is a violation.

        term_vars are the already-resolved solver vars for ONE gating term — the
        weekday-shift vars (for shifts in the resolved set present that week) or
        the single weekend-role var. Multiple vars are ORed (a fellow on any of
        the target shifts). exempt_var is the gating week's dual-coverage
        indicator, or None when no exemption applies. Empty term_vars ⇒ nothing
        to encode. Term-first emission order matches the prior helpers."""
        if not term_vars:
            return
        term_var = emit_or(sink, term_vars)
        if exempt_var is None:
            emit_pair(sink, condition_var=term_var, night_var=night_var,
                      strength=strength, weight=weight)
            return
        # term + night + (1 - exempt) <= 2  ⟺  can't have term & night & not-exempt
        if strength is HARD:
            sink.weighted_sum_at_most(
                [(term_var, 1), (night_var, 1), (-exempt_var, 1)], 2)
        else:
            ind = sink.new_var()
            sink.weighted_sum_at_most(
                [(term_var, 1), (night_var, 1), (-exempt_var, 1), (-ind, 1)], 3)
            sink.weighted_sum_at_least([(term_var, 1), (-ind, 1)], 1)
            sink.weighted_sum_at_least([(night_var, 1), (-ind, 1)], 1)
            sink.weighted_sum_at_least([(-exempt_var, 1), (-ind, 1)], 1)
            sink.soft(ind, weight)


@dataclass(frozen=True)
class ConfiguredNightGating:
    """A NightGatingCriterion paired with its resolved concrete target sets.

    The single carrier threaded to BOTH the encoder and the production evaluator
    so they consume one resolved-target source (no drift). `resolved_targets`
    maps each weekday-shift term's target_key to the concrete shift-name set;
    weekend-role terms need no entry."""
    criterion: NightGatingCriterion
    resolved_targets: dict[str, frozenset[str]]


def night_gating_from_params(params: dict) -> tuple[NightGatingCriterion, dict[str, frozenset[str]]]:
    """Build a (criterion, resolved_targets) pair from a night_gating constraint's
    params — the ONE factory the encoder, the production evaluator, and validate()
    all call, so a single definition drives every manifestation (ADR-0005).

    params shape (produced by the config converter; concrete shift names already
    resolved, so this stays palette-free):
      {criterion: str, terms: [{dows, week_offset, target_key, is_weekend_role}],
       resolved_targets: {key: [shift names]}, exemption: {target_shift, threshold}|None}
    """
    terms = tuple(
        NightTermSpec(
            dows=tuple(t["dows"]),
            week_offset=int(t.get("week_offset", 0)),
            target_key=t["target_key"],
            is_weekend_role=bool(t.get("is_weekend_role", False)),
        )
        for t in params["terms"]
    )
    ex = params.get("exemption")
    exemption = ExemptionSpec(ex["target_shift"], int(ex["threshold"])) if ex else None
    crit = NightGatingCriterion(params["criterion"], terms, exemption=exemption)
    resolved = {k: frozenset(v) for k, v in params.get("resolved_targets", {}).items()}
    return crit, resolved


def configured_night_gating(constraints) -> list[ConfiguredNightGating]:
    """Filter a constraint list to its night_gating entries and build a
    ConfiguredNightGating for each. Duck-types on `.kind`/`.params` so this leaf
    needs no dependency on the model package's SemanticConstraint."""
    out: list[ConfiguredNightGating] = []
    for c in constraints:
        if getattr(c, "kind", None) == "night_gating":
            crit, resolved = night_gating_from_params(c.params)
            out.append(ConfiguredNightGating(crit, resolved))
    return out
