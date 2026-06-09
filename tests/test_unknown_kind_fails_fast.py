"""Slice 5 — an unknown constraint kind must FAIL FAST, not silently vanish.

Previously `_encode_weekly_rules` printed a stderr warning and `continue`d on an
unknown kind, so a YAML typo (e.g. "ncc_coverag") silently dropped that rule from
the Schedule — the solver produced a schedule missing a rule nobody asked to
drop. The encoder now raises, surfacing the typo at build time.

Every kind reaching the weekly dispatch is expected to be handled: palette input
types are validated upstream (PALETTE_TYPES) and converted to handled kinds, and
night/weekend/call rules travel in separate config (night_config / weekend_config
/ call_rules), never in config.constraints.
"""

from __future__ import annotations

from datetime import date

import pytest

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


def _config(constraints) -> ScheduleSolverConfig:
    return ScheduleSolverConfig(
        fellow_groups={"NCC_SR": ["Bob"]},
        shifts=["NCC1", "NCC2", "Stroke", "Elec"],
        constraints=constraints,
        night_config=NightSolverConfig(
            total_nights={}, friday_nights={}, total_night_multisets=(),
            friday_night_multisets=(), ccm_fellows=frozenset(), holiday_dates=(),
            horizon_start_date=date(2026, 7, 6)),
        weekend_config=WeekendSolverConfig(
            ncc_totals={}, stroke_totals={}, stroke_cohort=(), stroke_cohort_total=None,
            ccm_fellows=frozenset(), always_stroke_eligible=frozenset(),
            telestroke_stroke_eligible=frozenset(), stroke_only_eligible=frozenset()),
        night_hard_criteria=frozenset(),
        start_dow=0, num_days=70)


def _rule(kind: str) -> SemanticConstraint:
    return SemanticConstraint(
        kind=kind,
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=ConstraintStrength.HARD,
        fellows=FellowSelector.by_groups("NCC_SR"),
        params={"name": "typo'd rule"},
    )


def test_unknown_kind_raises_with_helpful_message():
    config = _config([_rule("ncc_coverag")])  # typo of ncc_coverage
    with pytest.raises(ValueError) as exc:
        build_full_schedule_opb(config, objective=True)
    msg = str(exc.value)
    assert "ncc_coverag" in msg  # names the offending kind


def test_known_kind_still_builds():
    config = _config([_rule("ncc_coverage")])
    opb, _ = build_full_schedule_opb(config, objective=True)
    assert opb.num_constraints > 0
