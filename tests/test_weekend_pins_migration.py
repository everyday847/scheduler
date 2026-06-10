"""Byte-equivalence test for the S3 weekend-pin migration.

Each annual weekend-pin type can be expressed two ways:

  Config A — the OLD channel: a raw `call_rules` dict, encoded by
    _encode_call_rules onto the wr (weekend) layer.
  Config B — the NEW channel: a typed SemanticConstraint(kind=<same string>)
    in config.constraints, encoded by the weekend-layer registry walk
    (_encode_weekend_layer_rules -> WeekendRolePin adapter).

The kind string equals the old type string, so the later YAML flip is a pure
rename. This test pins each type through BOTH channels onto an IDENTICALLY
var-numbered wr and asserts the resulting OPB is byte-for-byte identical.

CRITICAL (per the migration brief): weekend role vars only exist for eligible
fellows, so the test fellow MUST be weekend-eligible for the role or the
`fi in wr[w][role_idx]` guard skips everything and A == B trivially (both empty).
_make_wr here gives EVERY fellow a var for EVERY role, and each test asserts the
emitted constraint list is NON-EMPTY — so the equivalence is MEANINGFUL.
"""

from __future__ import annotations

from datetime import date

import pytest

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.schedule_types import ScheduleSolverConfig, _num_weeks_for
from parafrost_scheduler.schedule_encoder import (
    _encode_call_rules,
    _encode_weekend_layer_rules,
)
from scheduler.fellow_mapping import FellowMapping
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig
from scheduler.semantic_constraints import (
    SemanticConstraint,
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
)


_FELLOW_GROUPS = {"NCC_SR": ["Alice", "Bob", "Carol"]}
_FELLOW_NAMES = ["Alice", "Bob", "Carol"]
_NUM_DAYS = 21
_START_DOW = 0


def _make_config(call_rules=None, constraints=None) -> ScheduleSolverConfig:
    """Minimal ScheduleSolverConfig (mirrors tests/test_annual_call_rules.py).

    always_stroke_eligible covers all three fellows so Stroke pins are eligible
    too — though _make_wr below allocates vars unconditionally."""
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
        call_rules=call_rules or [])


def _make_xn(opb: OpbBuilder, num_days: int, num_fellows: int):
    return [[opb.new_var() for _ in range(num_fellows)] for _ in range(num_days)]


def _make_wr(opb: OpbBuilder, num_weeks: int, num_fellows: int):
    """wr[w][role_idx] = {fellow_idx: var} — a var for EVERY fellow/role so the
    eligibility guard passes for the test fellow (meaningful equivalence)."""
    wr = []
    for _w in range(num_weeks):
        week_roles = []
        for _role_idx in range(3):
            week_roles.append({f: opb.new_var() for f in range(num_fellows)})
        wr.append(week_roles)
    return wr


def _fellow_mapping() -> FellowMapping:
    fm = FellowMapping()
    for name in _FELLOW_NAMES:
        fm.add_fellow(name, "NCC_SR")
    return fm


def _opb_with_layers():
    """A fresh OpbBuilder with identically-numbered xn + wr (the var allocation
    order is the SAME for both configs, so any constraint-line difference is
    purely the channel)."""
    opb = OpbBuilder()
    num_weeks = _num_weeks_for(_START_DOW, _NUM_DAYS)
    xn = _make_xn(opb, _NUM_DAYS, len(_FELLOW_NAMES))
    wr = _make_wr(opb, num_weeks, len(_FELLOW_NAMES))
    return opb, xn, wr


def _encode_old(call_rule):
    opb, xn, wr = _opb_with_layers()
    config = _make_config(call_rules=[call_rule])
    _encode_call_rules(opb, xn, wr, config, _FELLOW_NAMES)
    return opb


def _encode_new(constraint):
    opb, xn, wr = _opb_with_layers()
    config = _make_config(constraints=[constraint])
    # Minimal kwargs the weekend-layer walk threads through to the adapter.
    _encode_weekend_layer_rules(
        opb, wr, xs=None, config=config, fellow_mapping=_fellow_mapping(),
        fellow_names=_FELLOW_NAMES, shift_idx={"NCC1": 0, "NCC2": 1, "Stroke": 2},
        soft_violations=[])
    return opb


def _assert_byte_equivalent(old: OpbBuilder, new: OpbBuilder) -> None:
    """The two channels emit byte-identical solver content: identical constraint
    lines, identical (num_vars, num_constraints, objective) triple. The ONLY
    difference is the weekend-layer walk's cosmetic `* Rule:` comment (the old
    call_rules path emits its own comment upstream in build_full_schedule_opb),
    so the comparison is on the constraint bytes + triple, not the comment line."""
    assert old._constraints == new._constraints
    assert old.num_vars == new.num_vars
    assert old.num_constraints == new.num_constraints
    assert old._objective == new._objective


# ---------------------------------------------------------------------------
# specific_weekend_assignment  (PIN)
# ---------------------------------------------------------------------------
class TestSpecificWeekendAssignmentEquivalence:
    def test_byte_equivalent(self):
        old = _encode_old({
            "type": "specific_weekend_assignment",
            "fellow": "Alice", "role": "NCC1", "weeks": [1], "active": True,
        })
        new = _encode_new(SemanticConstraint(
            kind="specific_weekend_assignment",
            lifecycle=ConstraintLifecycle.ANNUAL_RULE,
            strength=ConstraintStrength.HARD,
            fellows=FellowSelector.by_names("Alice"),
            params={"role": "NCC1", "weeks": [1], "action": "pin"}))
        assert old._constraints, "old channel emitted nothing — eligibility guard skipped all"
        _assert_byte_equivalent(old, new)

    def test_stroke_role_byte_equivalent(self):
        old = _encode_old({
            "type": "specific_weekend_assignment",
            "fellow": "Carol", "role": "Stroke", "weeks": [0, 2], "active": True,
        })
        new = _encode_new(SemanticConstraint(
            kind="specific_weekend_assignment",
            lifecycle=ConstraintLifecycle.ANNUAL_RULE,
            strength=ConstraintStrength.HARD,
            fellows=FellowSelector.by_names("Carol"),
            params={"role": "Stroke", "weeks": [0, 2], "action": "pin"}))
        assert old._constraints
        _assert_byte_equivalent(old, new)


# ---------------------------------------------------------------------------
# blocked_weekend  (FORBID all three roles)
# ---------------------------------------------------------------------------
class TestBlockedWeekendEquivalence:
    def test_byte_equivalent(self):
        old = _encode_old({
            "type": "blocked_weekend",
            "fellow": "Bob", "weeks": [2], "active": True,
        })
        new = _encode_new(SemanticConstraint(
            kind="blocked_weekend",
            lifecycle=ConstraintLifecycle.ANNUAL_RULE,
            strength=ConstraintStrength.HARD,
            fellows=FellowSelector.by_names("Bob"),
            params={"weeks": [2], "action": "forbid"}))
        assert old._constraints
        assert len(old._constraints) == 3  # all 3 weekend roles forbidden
        _assert_byte_equivalent(old, new)
