"""Phase-1 foundation tests for the NF model's day-granular call tier.

The whole tier activates iff config.call_tier_day_granular is True, set only from
a new config's solver_options. Default-off keeps the wb7 path byte-identical.
"""
from __future__ import annotations

from scheduler.solver_bridge import build_solver_config_from_request


def _minimal_request(**solver_options):
    """A tiny no-workbook request: 2 fellows, 3 shifts, no rules."""
    return {
        "fellow_groups": {"NCC_JR": ["A", "B"]},
        "shifts": ["NCC1", "NCC2", "Elec"],
        "standing_rules": [],  # bypass disk standing config
        "horizon_start": "2026-07-01",
        "num_weeks": 2,
        "solver_options": dict(solver_options),
    }


def test_flag_defaults_false_when_absent():
    cfg = build_solver_config_from_request(_minimal_request())
    assert cfg.call_tier_day_granular is False


def test_flag_threads_from_solver_options():
    cfg = build_solver_config_from_request(
        _minimal_request(call_tier_day_granular=True))
    assert cfg.call_tier_day_granular is True
