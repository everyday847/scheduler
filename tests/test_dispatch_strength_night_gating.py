"""Dispatch-level strength test — NIGHT_GATING mechanism (Family C).

Mechanism under test: ``_encode_configured_night_gating`` (schedule_encoder.py
~2607). Unlike the weekly/weekend_gating mechanisms, a night_gating criterion's
hardness does NOT come from the constraint's ``.strength`` field. It comes from a
NAME LOOKUP: ``_strength_for(crit.name, config.night_hard_criteria)``
(schedule_encoder.py:86) returns HARD iff ``crit.name`` is in the
``config.night_hard_criteria`` frozenset, else SOFT. The soft penalty weight is
``config.night_weights.for_criterion(crit.name)``.

Existing ``test_*contract*`` files call ``crit.encode(..., strength=HARD_or_SOFT)``
with strength as a LITERAL — they prove the criterion honors a strength it is
HANDED, never that the DISPATCH routes the CONFIGURED strength through correctly.
A real bug shipped green that way for weekend_gating (the dispatch hardcoded SOFT;
fixed d2f2a50), which also silently invalidated the SAT decision built on it. This
file closes the same gap for night_gating: drive the strength through the REAL
``build_full_schedule_opb`` via ``night_hard_criteria`` and assert hard -> UNSAT /
soft -> SAT *and the penalty weight was actually registered*.

THE CRITERION-NAME GOTCHA (load-bearing)
----------------------------------------
``NightPolicyWeights.for_criterion(name)`` is ``getattr(self, name)`` with NO
default (night_policy_types.py:119). So in the SOFT path the criterion name MUST be
one of NightPolicyWeights' real attributes (anaesthesia, clinic, stroke,
friday_weekend_ncc1, sunday_following) — an invented name like "test_night_gate"
would raise AttributeError. We therefore use ``criterion="clinic"`` and give it a
sentinel soft weight by passing ``night_weights=NightPolicyWeights(clinic=777)``.

THE VIOLATION GEOMETRY (a pure weekday-shift term, no exemption, no weekend role)
---------------------------------------------------------------------------------
Our constraint has one term: ``{dows:[2], week_offset:0, target_key:"gated",
is_weekend_role:False}`` with ``resolved_targets={"gated":["MICU"]}``. Read against
``NightGatingCriterion.gating_terms`` + ``encode`` (night_gating.py:80, 134) and the
dispatch loop (schedule_encoder.py:2635-2661):

  - ``gating_terms(week, dow)`` emits a GatingTerm only when ``dow in {2}``
    (Wednesday). week_offset 0 => the term lives in the night's OWN week.
  - For that term the dispatch ORs the fellow's weekday-shift vars whose shift is in
    the resolved set ({MICU}) for the term week, then ``encode`` emits via
    ``emit_pair``: (night fires) AND (fellow holds MICU that week) is a violation.
    HARD => the pair is forbidden; SOFT => the pair costs ``weight``.

So a violation = a fellow holds MICU in week 0 AND works the Wednesday night of
week 0. With start_dow=0, week 0's Wednesday (dow 2) is absolute day 2 (verified:
``day_of_week(2, 0) == 2``). We pin ``pin_shift(F0, week 0, "MICU")`` +
``pin_night(F0, day 2)``.

ISOLATION (verified empirically before writing assertions)
----------------------------------------------------------
The night-policy criteria are ENTIRELY config-driven: ``_encode_night_policy_criteria``
delegates to ``_encode_configured_night_gating``, which returns early when
``configured_night_gating(config.constraints)`` is empty — there is no built-in
inline "clinic" night block anymore (it was deleted when the criterion migrated to
config; see the comment at schedule_encoder.py:2710-2711). So a baseline build with
``constraints=[]`` emits NO clinic night_gating constraint and NO weight-777 penalty
(confirmed: ``777 not in soft_weights`` for the empty-constraints baseline). Our rule
is the only thing that can produce the violation, and ``assert_baseline_sat`` proves
the pins alone (rule removed, same shifts/config) stay SAT — so a hard-variant UNSAT
is unambiguously attributable to OUR rule, not the baseline floor.

WHY THE SOFT-WEIGHT ASSERTION IS LOAD-BEARING
---------------------------------------------
A SAT result alone is worthless: a dropped/no-op rule is also SAT. Only
``has_soft_weight(vm, 777)`` proves the dispatch actually emitted OUR penalty (the
right criterion, the right configured weight) on the SOFT path.
"""

from __future__ import annotations

from _dispatch_helpers import (
    make_dispatch_config, build, pin_shift, pin_night, solve_sat,
    has_soft_weight, assert_baseline_sat)
from parafrost_scheduler.schedule_types import day_of_week
from scheduler.semantic_constraints import (
    SemanticConstraint, ConstraintLifecycle, ConstraintStrength)
from scheduler.night_policy_types import NightPolicyWeights


# "MICU" must be a real shift so the weekday-shift target var exists; add it to the
# baseline palette. Both the rule build AND every assert_baseline_sat call must use
# this same shift set so the delta (baseline vs rule) is apples-to-apples.
_SHIFTS = ("Stroke", "NCC1", "NCC2", "Vac", "Elec", "MICU")

# A sentinel soft weight the baseline floor never emits, so a soft penalty of this
# weight is unambiguously OUR rule. Threaded via night_weights=NightPolicyWeights(
# clinic=777) — clinic is the criterion name, which MUST be a NightPolicyWeights
# attribute (see the module docstring's name gotcha).
_SENTINEL_WEIGHT = 777

# Wednesday (dow 2) of week 0 with start_dow=0 is absolute day 2 — the night day we
# pin. Asserted at import time so a geometry change here fails loudly rather than
# silently making the test vacuous.
_WED_DAY = 2
assert day_of_week(_WED_DAY, 0) == 2, "week-0 Wednesday must be absolute day 2"


def _clinic_micu_wednesday() -> SemanticConstraint:
    """A night_gating rule, criterion "clinic", one pure weekday-shift term:
    a Wednesday (dow 2) night in the night's own week (week_offset 0) is a violation
    when the fellow holds MICU that week. No exemption, no weekend-role term.

    ``strength`` on the constraint is IRRELEVANT for night_gating (hardness comes
    from night_hard_criteria); we set SOFT to make that explicit.
    """
    return SemanticConstraint(
        kind="night_gating",
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=ConstraintStrength.SOFT,
        params={
            "criterion": "clinic",
            "terms": [{
                "dows": [2],            # Wednesday
                "week_offset": 0,       # the night's own week
                "target_key": "gated",
                "is_weekend_role": False,
            }],
            "resolved_targets": {"gated": ["MICU"]},
            "exemption": None,
        },
    )


def _pin_violation(opb, vm) -> None:
    """Pin the violating assignment: F0 holds MICU in week 0 AND works the week-0
    Wednesday night. The pin helpers assert each var is real (!= 0), so a missing
    var fails loudly instead of making the test vacuous."""
    pin_shift(opb, vm, "F0", 0, "MICU")
    pin_night(opb, vm, "F0", _WED_DAY)


def test_baseline_sat():
    """The pins alone (MICU wk0 + week-0 Wednesday night for F0), with the rule
    REMOVED, are SAT — so any hard-variant UNSAT below is the rule, not the floor.
    Same shifts as the rule build so the delta is apples-to-apples."""
    assert_baseline_sat(_pin_violation, shifts=_SHIFTS)


def test_hard_via_night_hard_criteria_is_unsat():
    """Configured HARD (criterion name "clinic" IN night_hard_criteria): the
    dispatch's ``_strength_for`` returns HARD, so ``encode`` forbids the
    (night AND MICU) pair. The pinned violation makes the model UNSAT.

    Delta: ``test_baseline_sat`` already proved the same pins are SAT with the rule
    removed, so this UNSAT is attributable to the hard rule alone."""
    # Guard the delta with the SAME config the rule build uses (hard criteria set,
    # MICU shifts) so the baseline floor matches exactly.
    assert_baseline_sat(
        _pin_violation, shifts=_SHIFTS,
        night_hard_criteria=frozenset({"clinic"}))

    cfg = make_dispatch_config(
        constraints=[_clinic_micu_wednesday()], shifts=_SHIFTS,
        night_hard_criteria=frozenset({"clinic"}))
    opb, vm = build(cfg)
    _pin_violation(opb, vm)
    assert not solve_sat(opb), (
        "configured-hard night_gating did NOT make the pinned violation UNSAT — "
        "the dispatch handed the criterion SOFT despite 'clinic' in "
        "night_hard_criteria (the _strength_for routing bug this test guards).")


def test_soft_when_not_in_hard_criteria_is_sat_and_penalizes():
    """Configured SOFT (criterion name "clinic" NOT in night_hard_criteria): the
    dispatch's ``_strength_for`` returns SOFT, so the violation is allowed but
    PENALIZED at the criterion's configured weight. We set that weight to a sentinel
    777 via ``night_weights=NightPolicyWeights(clinic=777)``.

    SAT alone is worthless (a dropped rule is also SAT); the load-bearing assertion
    is ``has_soft_weight(vm, 777)`` — it proves the dispatch actually emitted OUR
    penalty (right criterion, right configured weight) on the soft path."""
    # Delta guard with the same shifts/weights so the baseline floor matches.
    assert_baseline_sat(
        _pin_violation, shifts=_SHIFTS,
        night_weights=NightPolicyWeights(clinic=_SENTINEL_WEIGHT))

    cfg = make_dispatch_config(
        constraints=[_clinic_micu_wednesday()], shifts=_SHIFTS,
        night_hard_criteria=frozenset(),
        night_weights=NightPolicyWeights(clinic=_SENTINEL_WEIGHT))
    opb, vm = build(cfg)
    _pin_violation(opb, vm)
    assert solve_sat(opb), "configured-soft night_gating should leave the model SAT"
    assert has_soft_weight(vm, _SENTINEL_WEIGHT), (
        "configured-soft night_gating did NOT register its penalty — the dispatch "
        "dropped the soft constraint (SAT alone would mask this no-op).")
