"""Post-cutover guards for the S3 weekend-pin migration.

The annual weekend-pin types (specific_weekend_assignment PIN,
blocked_weekend FORBID) were dissolved out of the raw `call_rules` channel into
the typed `config.constraints` pipeline (encoded by the weekend-layer registry
walk → WeekendRolePin adapter onto the wr layer). The old `call_rules` side has
been removed (`_encode_call_rules` now raises for any migrated type), so the
original OLD-vs-NEW byte-for-byte equivalence tests — which imported and called
`_encode_call_rules` directly — no longer apply. Their equivalence was proven
during the migration and is now locked in by the live OPB-triple regression gate
plus the archetype's own contract test (tests/test_weekend_role_pin_contract.py
covers encode/evaluate correctness).

What remains here are lighter new-path-only guards: route each pin through the
typed pipeline and assert the build with the rule emits MORE constraints than an
otherwise-identical build without it.

CRITICAL (per the migration brief): weekend role vars only exist for eligible
fellows, so the pinned fellow MUST be weekend-eligible for the role or the pin
resolves to no vars and the guard is trivial. `_make_config` makes all three
fellows always-Stroke-eligible (so wr[w][role] vars exist for every role), which
keeps the pinned-var-exists precondition true and the guard meaningful.
"""

from __future__ import annotations

from datetime import date

from parafrost_scheduler.schedule_types import ScheduleSolverConfig
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig
from scheduler.semantic_constraints import (
    SemanticConstraint,
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
)


_FELLOW_GROUPS = {"NCC_SR": ["Alice", "Bob", "Carol"]}
_NUM_DAYS = 21
_START_DOW = 0


def _make_config(constraints=None) -> ScheduleSolverConfig:
    """Minimal weekend-eligible ScheduleSolverConfig.

    always_stroke_eligible covers all three fellows so every wr[w][role] var
    exists (NCC1/NCC2/Stroke) — the pinned role var is therefore present and the
    emit-something guard is meaningful, not trivially empty."""
    night_config = NightSolverConfig(
        total_nights={}, friday_nights={}, total_night_multisets=(),
        friday_night_multisets=(), ccm_fellows=frozenset(), holiday_dates=(),
        horizon_start_date=date(2026, 7, 6))
    weekend_config = WeekendSolverConfig(
        ncc_totals={}, stroke_totals={}, stroke_cohort=(), stroke_cohort_total=None,
        ccm_fellows=frozenset(),
        always_stroke_eligible=frozenset({"Alice", "Bob", "Carol"}),
        telestroke_stroke_eligible=frozenset(), stroke_only_eligible=frozenset())
    return ScheduleSolverConfig(
        fellow_groups=_FELLOW_GROUPS,
        shifts=["NCC1", "NCC2", "Stroke"],
        constraints=constraints or [],
        night_config=night_config,
        weekend_config=weekend_config,
        night_hard_criteria=frozenset(),
        start_dow=_START_DOW,
        num_days=_NUM_DAYS,
        call_rules=[])


def _annual(kind, **kw) -> SemanticConstraint:
    return SemanticConstraint(
        kind=kind,
        lifecycle=ConstraintLifecycle.ANNUAL_RULE,
        strength=ConstraintStrength.HARD,
        **kw,
    )


def _num_constraints(constraints):
    opb, _ = build_full_schedule_opb(_make_config(constraints), objective=True)
    return opb.num_constraints


# ---------------------------------------------------------------------------
# specific_weekend_assignment  (PIN)
# ---------------------------------------------------------------------------
class TestSpecificWeekendAssignmentNewPathFires:
    def test_ncc1_pin_emits_constraint(self):
        """PIN Alice into the NCC1 weekend role in week 1 forces that role var
        true → strictly more constraints than the no-rule baseline."""
        constraint = _annual(
            "specific_weekend_assignment",
            fellows=FellowSelector.by_names("Alice"),
            params={"role": "NCC1", "weeks": [1], "action": "pin"})
        assert _num_constraints([constraint]) > _num_constraints([])

    def test_stroke_role_pin_emits_constraints(self):
        """PIN Carol into the Stroke weekend role across weeks 0 and 2 → two
        added pin lines (the Stroke role var exists because Carol is
        always-Stroke-eligible)."""
        constraint = _annual(
            "specific_weekend_assignment",
            fellows=FellowSelector.by_names("Carol"),
            params={"role": "Stroke", "weeks": [0, 2], "action": "pin"})
        assert _num_constraints([constraint]) > _num_constraints([])


# ---------------------------------------------------------------------------
# blocked_weekend  (FORBID all three roles)
# ---------------------------------------------------------------------------
class TestBlockedWeekendNewPathFires:
    def test_blocked_weekend_emits_constraints(self):
        """FORBID Bob from every weekend role in week 2 → one forbid line per
        role (NCC1/NCC2/Stroke), all strictly above the no-rule baseline."""
        constraint = _annual(
            "blocked_weekend",
            fellows=FellowSelector.by_names("Bob"),
            params={"weeks": [2], "action": "forbid"})
        assert _num_constraints([constraint]) > _num_constraints([])
