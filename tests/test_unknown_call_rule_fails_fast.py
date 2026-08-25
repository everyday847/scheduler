"""Fail-loud contract for the dissolved `call_rules` channel.

`call_rules` was once a raw escape-hatch channel: `_encode_call_rules` dispatched
its dicts by `rule["type"]` through an if/elif chain, and an unrecognized type
fell through silently — a typo dropped a pin/block with no signal. That whole
channel has since been dissolved into the typed `rules:` pipeline (S1-S5), and
`_encode_call_rules` was deleted.

The anti-silent-drop guarantee is preserved at the request boundary: a request
that still carries an ACTIVE `call_rules` entry is rejected by
`build_solver_config_from_request`, pointing the caller at `rules:`. Inactive
entries are ignored (you can leave `active:false` history in a config)."""

from __future__ import annotations

import pytest

from scheduler.solver_bridge import build_solver_config_from_request


def _request(call_rules):
    return {
        "fellow_groups": {"NCC_SR": ["Bob"]},
        "shifts": ["NCC1", "NCC2", "Stroke", "Elec"],
        "fellow_week_pairs": {},
        "horizon_start": "2026-07-01",
        "standing_rules": [],   # minimal palette-format standing (empty)
        "call_rules": call_rules,
    }


def test_active_call_rule_is_rejected():
    """Any active call_rules entry — migrated type or otherwise — is rejected,
    naming the offending type(s) and pointing at the typed `rules:` pipeline."""
    with pytest.raises(ValueError) as exc:
        build_solver_config_from_request(
            _request([{"type": "blocked_night", "fellow": "Bob",
                       "dates": ["2026-07-10"], "active": True}]))
    msg = str(exc.value)
    assert "call_rules" in msg
    assert "blocked_night" in msg
    assert "rules:" in msg


def test_dual_stroke_window_in_call_rules_is_rejected():
    """Even the formerly-residual dual_stroke_window must now live in `rules:`."""
    with pytest.raises(ValueError) as exc:
        build_solver_config_from_request(
            _request([{"type": "dual_stroke_window", "name": "dsw",
                       "window": [0, 2], "supervisors": ["Bob"], "active": True}]))
    assert "dual_stroke_window" in str(exc.value)


def test_inactive_call_rule_is_ignored():
    """active:false entries are tolerated (config history) — no raise."""
    config = build_solver_config_from_request(
        _request([{"type": "blocked_night", "fellow": "Bob", "active": False}]))
    assert config.call_rules == []
