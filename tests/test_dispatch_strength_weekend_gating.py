"""Dispatch-level strength-flow tests for the ``weekend_gating`` mechanism.

Why this file exists
--------------------
The ~13 ``test_*contract*.py`` files call ``crit.encode(..., strength=HARD_or_SOFT)``
with strength as a LITERAL argument. They prove a criterion honors a strength it is
*handed*; they prove NOTHING about whether the encoder DISPATCH
(``build_full_schedule_opb``) hands the criterion the strength the CONFIG declares.
A real bug shipped green that way: a configured-hard ``weekend_gating`` rule emitted
SOFT because ``_encode_configured_weekend_gating`` hardcoded the strength (fixed
d2f2a50), which silently invalidated the SAT decision built on it.

This file closes that gap for ``weekend_gating`` by building a minimal config that
carries ONE such rule, running the REAL dispatch, pinning a VIOLATING assignment,
solving, and asserting hard -> UNSAT / soft -> SAT *and the penalty (var, weight)
was actually registered*. The strength is threaded through the dispatch only via
``ConfiguredWeekendGating.strength`` (mapped from ``SemanticConstraint.strength`` in
the ``configured_weekend_gating`` factory) — exactly the seam that shipped broken.

The criterion has TWO modes (see ``src/schedule_rules/criteria/weekend_gating.py``):

  GATE: holding a named weekend role in week w while ON the gating service ("Vac")
    in week w+1 is the violation. The GATE penalty weight is a literal 1
    (schedule_encoder.py:2164).

  ALIGN: holding a weekend role in week w while NOT on the matching weekday shift
    that week is the violation. The per-role weight defaults to
    ``config.weekend_mismatch_weight``.

DELTA DISCIPLINE
----------------
The dispatch always emits a floor of baseline hard constraints, so every HARD test
FIRST proves (via ``assert_baseline_sat`` with the SAME pins, rule removed) that the
pins alone are SAT. Only then does the hard rule's UNSAT attribute unambiguously to
the rule, not to the baseline floor or to the pins colliding with each other.

The LOAD-BEARING half of each soft test is the weight assertion, not the SAT result:
a SAT result alone is worthless because a DROPPED rule is also SAT (the lost-strength
bug was SAT too). ``has_soft_weight`` proves the rule actually registered a penalty.
The empty baseline emits only weights {10, 20} (verified empirically), so:
  - GATE uses weight 1 (clean, no baseline collision).
  - ALIGN's default weight 20 WOULD collide with the baseline, so these tests set a
    sentinel ``weekend_mismatch_weight=777`` and assert weight 777 — disambiguating
    our rule's penalty from the baseline's weight-20 penalties.
"""

from __future__ import annotations

from _dispatch_helpers import (
    assert_baseline_sat,
    build,
    has_soft_weight,
    make_dispatch_config,
    pin_role,
    pin_shift,
    solve_sat,
)
from parafrost_scheduler.schedule_types import _ROLE_NCC1, _ROLE_STROKE
from scheduler.semantic_constraints import (
    ConstraintLifecycle,
    ConstraintStrength,
    SemanticConstraint,
)


# ---------------------------------------------------------------------------
# GATE mode (the prevacation shape)
# ---------------------------------------------------------------------------

def _gate_constraint(strength: ConstraintStrength) -> SemanticConstraint:
    """A GATE-mode ``weekend_gating`` rule (the prevacation shape).

    Violation geometry: holding ANY named weekend role in week w while ON "Vac"
    in week w+1 (gate_week_offset=1). Param shape matches
    ``tests/test_prevacation_penalty.py::_prevac_constraint``. The ONLY thing that
    varies between the hard and soft tests is ``strength`` — the field the dispatch
    must thread through ``ConfiguredWeekendGating.strength``.
    """
    return SemanticConstraint(
        kind="weekend_gating",
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=strength,
        fellows=None,
        params={
            "name": "prevacation_weekend", "criterion": "prevacation_weekend",
            "mode": "gate", "roles": ["Weekend NCC1", "Weekend NCC2", "Weekend Stroke"],
            "gate_target_key": "vac", "gate_week_offset": 1,
            "resolved_targets": {"vac": ["Vac"]},
        },
    )


def _gate_pins(opb, vm) -> None:
    """Pin the GATE violation: F0 holds Weekend NCC1 in week 0 AND is on Vac in
    week 1. With ``gate_week_offset=1`` this is exactly role(w) AND gate(w+1)."""
    pin_role(opb, vm, "F0", 0, _ROLE_NCC1, value=True)
    pin_shift(opb, vm, "F0", 1, "Vac", value=True)


def test_gate_baseline_sat():
    """The GATE pins alone (rule REMOVED) must leave the model SAT, so the
    hard-variant UNSAT below is attributable to the rule, not the baseline floor."""
    assert_baseline_sat(_gate_pins)


def test_gate_hard_is_unsat():
    """Config says strength=HARD: the dispatch must emit a HARD ``role + gate <= 1``,
    so pinning role(w=0) AND gate(w=1) is UNSAT. (Baseline-SAT proven above, so the
    UNSAT is the rule.) If the dispatch hardcoded SOFT, this would flip to SAT."""
    config = make_dispatch_config(constraints=[_gate_constraint(ConstraintStrength.HARD)])
    opb, vm = build(config)
    _gate_pins(opb, vm)
    assert not solve_sat(opb), "configured-HARD GATE rule must make the pinned violation UNSAT"


def test_gate_soft_is_sat_and_penalizes():
    """Config says strength=SOFT: the same pinned violation must be SAT, AND the
    dispatch must register a soft penalty of weight 1 (the GATE literal weight).

    The weight assertion is the load-bearing half: SAT alone is worthless because a
    dropped rule is ALSO SAT. The empty baseline emits only weights {10, 20}, so a
    registered weight 1 can only have come from THIS rule."""
    config = make_dispatch_config(constraints=[_gate_constraint(ConstraintStrength.SOFT)])
    opb, vm = build(config)
    _gate_pins(opb, vm)
    assert solve_sat(opb), "configured-SOFT GATE rule must leave the pinned violation SAT"
    assert has_soft_weight(vm, 1), (
        "configured-SOFT GATE rule must register a soft penalty of weight 1 "
        "(GATE literal weight); its absence would mean the rule was silently dropped")


# ---------------------------------------------------------------------------
# ALIGN mode (the weekend_role_mismatch shape)
#
# We deliberately violate the Weekend STROKE role, not an NCC role: the baseline
# build ALSO emits a built-in "NCC1/NCC2 weekday-weekend alignment (soft)" nudge
# (schedule_encoder.py:1855) that penalizes the NCC roles. Stroke is untouched by
# that built-in, so ONLY our rule acts on a Weekend-Stroke mismatch. We further set
# a sentinel ``weekend_mismatch_weight=777`` so the soft penalty's weight is
# unmistakably ours (the default 20 collides with baseline weight-20 penalties).
# ---------------------------------------------------------------------------

# Sentinel weight: distinct from the baseline's {10, 20} so a weight-777 soft
# penalty can only have come from our ALIGN rule.
_ALIGN_WEIGHT = 777


def _align_constraint(strength: ConstraintStrength) -> SemanticConstraint:
    """An ALIGN-mode ``weekend_gating`` rule (the weekend_role_mismatch shape).

    Violation geometry: holding a weekend role in week w while NOT on the matching
    weekday shift (role_to_shift) that week. Unconditional (conditional=False), so a
    holder who cannot be on the matching shift is also a violation. Strength is the
    only thing that varies between the hard/soft tests."""
    return SemanticConstraint(
        kind="weekend_gating",
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=strength,
        fellows=None,
        params={
            "name": "weekend_role_mismatch", "criterion": "weekend_role_mismatch",
            "mode": "align",
            "role_to_shift": {"Weekend NCC1": "NCC1", "Weekend NCC2": "NCC2",
                              "Weekend Stroke": "Stroke"},
            "conditional": False,
        },
    )


def _align_pins(opb, vm) -> None:
    """Pin the ALIGN violation: F0 holds Weekend Stroke in week 0 but is NOT on
    weekday Stroke that week. We pin a DIFFERENT weekday shift (Elec) on; the
    baseline's at-most-one-weekday-shift-per-week then forces weekday Stroke false,
    so role(w) AND NOT match(w) holds — the mismatch."""
    pin_role(opb, vm, "F0", 0, _ROLE_STROKE, value=True)
    pin_shift(opb, vm, "F0", 0, "Elec", value=True)


def test_align_baseline_sat():
    """The ALIGN pins alone (rule REMOVED) must leave the model SAT, so the
    hard-variant UNSAT below is attributable to the rule, not the baseline floor.
    Built with the same sentinel weight so the configs are identical sans rule."""
    assert_baseline_sat(_align_pins, weekend_mismatch_weight=_ALIGN_WEIGHT)


def test_align_hard_is_unsat():
    """Config says strength=HARD: the dispatch must emit a HARD ``role + ~match <= 1``,
    so pinning Weekend Stroke(w=0) with weekday Stroke forced false is UNSAT.
    (Baseline-SAT proven above.) If the dispatch hardcoded SOFT, this would flip to
    SAT — the exact lost-strength regression."""
    config = make_dispatch_config(
        constraints=[_align_constraint(ConstraintStrength.HARD)],
        weekend_mismatch_weight=_ALIGN_WEIGHT)
    opb, vm = build(config)
    _align_pins(opb, vm)
    assert not solve_sat(opb), "configured-HARD ALIGN rule must make the pinned mismatch UNSAT"


def test_align_soft_is_sat_and_penalizes():
    """Config says strength=SOFT: the same pinned mismatch must be SAT, AND the
    dispatch must register a soft penalty of the sentinel weight 777.

    The weight assertion is the load-bearing half: SAT alone is worthless (a dropped
    rule is also SAT). The sentinel weight disambiguates our penalty from the
    baseline's {10, 20} penalties — a registered weight 777 can only be ours."""
    config = make_dispatch_config(
        constraints=[_align_constraint(ConstraintStrength.SOFT)],
        weekend_mismatch_weight=_ALIGN_WEIGHT)
    opb, vm = build(config)
    _align_pins(opb, vm)
    assert solve_sat(opb), "configured-SOFT ALIGN rule must leave the pinned mismatch SAT"
    assert has_soft_weight(vm, _ALIGN_WEIGHT), (
        "configured-SOFT ALIGN rule must register a soft penalty of the sentinel "
        "weight 777; its absence would mean the rule was silently dropped")
