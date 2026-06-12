"""Dispatch-level strength test — WEEKEND_NIGHT mechanism (Family B).

Mechanism under test: ``_encode_configured_weekend_night`` (schedule_encoder.py
~2186) routes each ``weekend_night`` SemanticConstraint to
``WeekendNightCriterion.encode``. Unlike the other mechanisms, this dispatch
deliberately does NOT pass ``strength`` to ``crit.encode``: strength is PER-ROLE,
carried in ``params.role_strengths[*].hard``/``weight`` and resolved inside the
criterion's FORBID emit (weekend_night.py ~115,
``strength=HARD if rs.hard else SOFT``). The existing contract tests call
``crit.encode(...)`` directly and never prove the DISPATCH preserves a configured
per-role strength end-to-end through ``build_full_schedule_opb``. This file closes
that gap. (Cf. the weekend_gating bug that shipped green when the dispatch hardcoded
SOFT — fixed d2f2a50.)

By-design subtlety locked in here: the config converter
(``weekend_night_rule_to_constraint``, palette_rules.py ~368) ALWAYS sets
``SemanticConstraint.strength = ConstraintStrength.SOFT``, and the encoder IGNORES
that field for this kind. Per-role ``role_strengths`` is the SOLE source of truth.
``test_constraint_level_strength_is_inert_for_weekend_night`` proves a constraint
flagged HARD does NOT get promoted — guarding against a future "fix" that wires the
inert field through and silently hardens every weekend_night rule.

Violation geometry
------------------
Target rule: a FORBID-polarity weekend_night on Friday (dow=4), single role
"Weekend NCC1". The FORBID emit forbids (role held AND night worked) for each
forbidden role the fellow can hold. So a single fellow who BOTH holds Weekend NCC1
in a week AND works that week's Friday night is the violation.

With ``start_dow=0`` and ``num_days=21``, week 0's Friday is absolute day
``0*7 + 4 = 4`` (verified via day_of_week). We pin F0 to hold Weekend NCC1 in week 0
(all fellows are stroke-eligible by default, so the wr NCC1 var exists) and to work
night day 4 (its xn var exists). F0 working Friday night satisfies the baseline
"exactly one fellow per night" floor for day 4; the other six fellows cover the
remaining nights, so the pins alone are SAT (see test_baseline_sat). The only thing
that can flip the model UNSAT is a HARD target rule — that is delta discipline.
"""

from __future__ import annotations

from _dispatch_helpers import (
    make_dispatch_config, build, pin_role, pin_night, solve_sat,
    has_soft_weight, assert_baseline_sat)
from parafrost_scheduler.schedule_types import _ROLE_NCC1
from scheduler.semantic_constraints import (
    SemanticConstraint, ConstraintLifecycle, ConstraintStrength)


# A sentinel soft weight the baseline floor never emits (verified: an empty build
# registers only weights {10, 20}), so a soft penalty of this weight is
# unambiguously OUR rule's.
_SENTINEL_WEIGHT = 777

# Week 0's Friday (dow=4) is absolute day 0*7 + 4 with start_dow=0.
_WEEK0_FRIDAY_DAY = 0 * 7 + 4


def _friday_forbid_ncc1(
    *, hard: bool, weight: int,
    constraint_strength: ConstraintStrength = ConstraintStrength.SOFT,
) -> SemanticConstraint:
    """A FORBID weekend_night on Friday for the single role "Weekend NCC1".

    ``hard``/``weight`` drive the PER-ROLE strength (the real source of truth).
    ``constraint_strength`` is the constraint-level ``.strength`` field — the
    converter always emits SOFT and the encoder ignores it; we parameterize it only
    so the inert-strength test can set it HARD and prove that is a no-op.
    """
    return SemanticConstraint(
        kind="weekend_night",
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=constraint_strength,
        fellows=None,
        params={
            "criterion": "test_friday",
            "dow": 4,
            "polarity": "forbid",
            "role_strengths": [
                {"role": "Weekend NCC1", "hard": hard, "weight": weight}
            ],
        },
    )


def _pin_violation(opb, vm) -> None:
    """Pin the FORBID violation: F0 holds Weekend NCC1 in week 0 AND works week 0's
    Friday night. The FORBID emit forbids exactly this (role held AND night)."""
    pin_role(opb, vm, "F0", 0, _ROLE_NCC1, value=True)
    pin_night(opb, vm, "F0", _WEEK0_FRIDAY_DAY, value=True)


def test_baseline_sat():
    """Pins alone (rule removed) are SAT — so a hard-variant UNSAT is attributable
    to the weekend_night rule, not to the baseline floor or the pins colliding."""
    assert_baseline_sat(_pin_violation)


def test_role_hard_forbids_night():
    """A role-HARD FORBID, violated by the pin, makes the full build UNSAT.

    Proves the dispatch routes ``role_strengths[*].hard=True`` through to the
    criterion's HARD emit (a flat ``at_most_k([role, night], 1)``)."""
    config = make_dispatch_config(
        constraints=[_friday_forbid_ncc1(hard=True, weight=0)])
    opb, vm = build(config)
    _pin_violation(opb, vm)
    assert not solve_sat(opb)


def test_role_soft_is_sat_and_penalizes():
    """A role-SOFT FORBID, same violation, stays SAT AND registers the penalty.

    SAT alone is worthless (a dropped rule is also SAT); the sentinel-weight penalty
    is the load-bearing half — it proves the dispatch actually emitted the soft rule
    with the configured per-role weight rather than silently dropping it."""
    config = make_dispatch_config(
        constraints=[_friday_forbid_ncc1(hard=False, weight=_SENTINEL_WEIGHT)])
    opb, vm = build(config)
    _pin_violation(opb, vm)
    assert solve_sat(opb)
    assert has_soft_weight(vm, _SENTINEL_WEIGHT)


def test_constraint_level_strength_is_inert_for_weekend_night():
    """Constraint-level ``.strength`` does NOT promote weekend_night to hard.

    Same role-SOFT config as above (hard=False, weight=777) but with the
    SemanticConstraint's ``.strength`` set HARD. If the encoder ever (wrongly) wired
    that field through, this violated build would go UNSAT. It must stay SAT — and
    still carry the soft 777 — because per-role ``role_strengths`` is the sole source
    of truth and the constraint-level field is inert by design (the converter always
    emits SOFT; see palette_rules.weekend_night_rule_to_constraint)."""
    config = make_dispatch_config(
        constraints=[_friday_forbid_ncc1(
            hard=False, weight=_SENTINEL_WEIGHT,
            constraint_strength=ConstraintStrength.HARD)])
    opb, vm = build(config)
    _pin_violation(opb, vm)
    assert solve_sat(opb)
    assert has_soft_weight(vm, _SENTINEL_WEIGHT)
