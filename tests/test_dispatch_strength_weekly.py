"""Dispatch-level strength test — WEEKLY handler mechanism (Family D).

Mechanism under test: the ``_encode_weekly_rules`` dispatch loop
(schedule_encoder.py ~462) threads each ``SemanticConstraint.strength`` to its
handler. The handlers are exercised by the harnesses in test_constraint_semantics.py
via direct ``_encode_*`` calls — but the DISPATCH LOOP's strength-threading (config
strength -> the strength the handler actually emits) is not tested through
``build_full_schedule_opb``. This file closes that gap for ``shift_total``, whose
handler ``_encode_shift_total`` has a genuine config-strength-driven soft/hard branch
(``is_soft = constraint.strength == ConstraintStrength.SOFT``, schedule_encoder.py:3303).

We deliberately do NOT use ``zero_shifts`` — it is ALWAYS hard regardless of the
configured strength (schedule_encoder.py:1205), so it cannot exercise the loop's
strength threading.

TDD bug-catching proof (must hold before this file is accepted): if the dispatch
loop hands SOFT where config says HARD, ``test_hard_shift_total_is_unsat`` flips to
SAT. Verified by temporarily forcing ``fellow_is_soft = True`` in
``_encode_shift_total`` -> the hard test goes RED; reverted.
"""

from __future__ import annotations

from _dispatch_helpers import (
    make_dispatch_config, build, pin_shift, solve_sat, has_soft_weight,
    assert_baseline_sat)
from scheduler.semantic_constraints import (
    SemanticConstraint, ConstraintLifecycle, ConstraintStrength, FellowSelector,
    ShiftSet)


# A sentinel soft weight that the baseline floor never emits, so a soft penalty of
# this weight is unambiguously OUR rule's. Threaded via config.weekly_soft_weight,
# which is the weight _encode_shift_total registers (schedule_encoder.py:3304).
_SENTINEL_WEIGHT = 777


def _no_ncc1_for_f0(strength: ConstraintStrength) -> SemanticConstraint:
    """``shift_total`` forbidding F0 from NCC1 in all weeks (at_most 0). A violation
    is a single pinned NCC1 assignment for F0."""
    return SemanticConstraint(
        kind="shift_total",
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=strength,
        fellows=FellowSelector.by_names("F0"),
        shifts=ShiftSet.single("NCC1"),
        params={"name": "F0 no NCC1", "relation": "at_most", "count": 0},
    )


def _pin_violation(opb, vm) -> None:
    """Pin the at_most-0 violation: F0 holds NCC1 in week 0."""
    pin_shift(opb, vm, "F0", 0, "NCC1", value=True)


def test_baseline_sat_without_rule():
    """Pins alone (rule removed) are SAT — so a hard-variant UNSAT is attributable
    to the shift_total rule, not to the baseline floor."""
    assert_baseline_sat(_pin_violation, weekly_soft_weight=_SENTINEL_WEIGHT)


def test_hard_shift_total_is_unsat():
    """A HARD shift_total, violated by the pin, makes the full build UNSAT."""
    config = make_dispatch_config(
        constraints=[_no_ncc1_for_f0(ConstraintStrength.HARD)],
        weekly_soft_weight=_SENTINEL_WEIGHT)
    opb, vm = build(config)
    _pin_violation(opb, vm)
    assert not solve_sat(opb)


def test_soft_shift_total_is_sat_and_registers_penalty():
    """A SOFT shift_total, same violation, stays SAT AND registers the penalty.

    SAT alone is insufficient (a dropped rule is also SAT); the sentinel-weight
    penalty proves the dispatch actually emitted the soft rule."""
    config = make_dispatch_config(
        constraints=[_no_ncc1_for_f0(ConstraintStrength.SOFT)],
        weekly_soft_weight=_SENTINEL_WEIGHT)
    opb, vm = build(config)
    _pin_violation(opb, vm)
    assert solve_sat(opb)
    assert has_soft_weight(vm, _SENTINEL_WEIGHT)
