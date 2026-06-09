"""Slice 5 — an unknown call-rule TYPE must fail fast, like the weekly dispatch.

`_encode_call_rules` dispatches raw `call_rules` dicts by `rule["type"]` through
an if/elif chain. An unrecognized type previously fell through silently (the
rule simply did nothing), so a typo dropped a night/weekend pin or block with no
signal. Now it raises, naming the offending type. (Inactive rules — active:False
— are still skipped, by design.)
"""

from __future__ import annotations

from datetime import date

import pytest

from parafrost_scheduler.schedule_types import ScheduleSolverConfig
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig


def _config(call_rules) -> ScheduleSolverConfig:
    return ScheduleSolverConfig(
        fellow_groups={"NCC_SR": ["Bob"]},
        shifts=["NCC1", "NCC2", "Stroke", "Elec"],
        constraints=[],
        night_config=NightSolverConfig(
            total_nights={}, friday_nights={}, total_night_multisets=(),
            friday_night_multisets=(), ccm_fellows=frozenset(), holiday_dates=(),
            horizon_start_date=date(2026, 7, 6)),
        weekend_config=WeekendSolverConfig(
            ncc_totals={}, stroke_totals={}, stroke_cohort=(), stroke_cohort_total=None,
            ccm_fellows=frozenset(), always_stroke_eligible=frozenset(),
            telestroke_stroke_eligible=frozenset(), stroke_only_eligible=frozenset()),
        night_hard_criteria=frozenset(),
        start_dow=0, num_days=70, call_rules=call_rules)


def test_unknown_call_rule_type_raises():
    config = _config([{"type": "blocked_nite", "fellow": "Bob", "dates": []}])  # typo
    with pytest.raises(ValueError) as exc:
        build_full_schedule_opb(config, objective=True)
    assert "blocked_nite" in str(exc.value)


def test_inactive_unknown_rule_is_skipped():
    # active:False rules are intentionally ignored before type dispatch.
    config = _config([{"type": "blocked_nite", "fellow": "Bob", "active": False}])
    opb, _ = build_full_schedule_opb(config, objective=True)
    assert opb.num_constraints > 0


def test_known_call_rule_still_builds():
    config = _config([{"type": "blocked_night", "fellow": "Bob",
                       "dates": ["2026-07-10"], "active": True}])
    opb, _ = build_full_schedule_opb(config, objective=True)
    assert opb.num_constraints > 0
