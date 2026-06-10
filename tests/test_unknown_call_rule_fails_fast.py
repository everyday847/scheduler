"""Fail-fast contract for the residual `call_rules` channel.

`_encode_call_rules` once dispatched raw `call_rules` dicts by `rule["type"]`
through an if/elif chain; an unrecognized type fell through silently, so a typo
dropped a night/weekend pin or block with no signal. The pin/block/prerequisite/
per-fellow call-rule types have since been dissolved into the typed `rules:`
pipeline, leaving exactly ONE residual type that still rides this legacy
channel: `dual_stroke_window` (a Supervision-shaped rule encoded separately by
`_encode_dual_stroke_window`; here it is parsed-and-skipped).

The channel now fails fast for anything that is not the residual type:
  * a genuinely-unknown type (typo) raises, naming the offending type;
  * a MIGRATED type (e.g. blocked_night) raises with the "migrated to the
    typed `rules:` pipeline" message, pinning the post-cutover contract;
  * the residual `dual_stroke_window` still builds;
  * inactive rules (active:False) are still skipped before type dispatch.
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


def test_migrated_call_rule_type_raises():
    """A type that was migrated to the typed `rules:` pipeline (here blocked_night)
    is no longer encoded via call_rules — leaving it in the legacy channel must
    fail fast with the migration-pointer message rather than silently no-op."""
    config = _config([{"type": "blocked_night", "fellow": "Bob",
                       "dates": ["2026-07-10"], "active": True}])
    with pytest.raises(ValueError) as exc:
        build_full_schedule_opb(config, objective=True)
    msg = str(exc.value)
    assert "blocked_night" in msg
    assert "migrated to the typed" in msg


def test_residual_dual_stroke_window_still_builds():
    """The one residual call_rule type still rides this channel (parsed-and-skipped
    here, encoded by _encode_dual_stroke_window) and must build without raising."""
    config = _config([{"type": "dual_stroke_window", "name": "dsw",
                       "window": [0, 2], "supervisors": ["Bob"], "active": True}])
    opb, _ = build_full_schedule_opb(config, objective=True)
    assert opb.num_constraints > 0
