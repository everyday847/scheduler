# tests/test_call_rules_routing.py
from pathlib import Path
import pytest
from scheduler.solver_bridge import build_solver_config_from_request

_STANDING = Path("config/standing/ncc-nf-model.yaml")

def _req(**extra):
    r = {"fellow_groups": {"NCC_JR": ["JR1", "JR2", "JR3"], "CCM": ["C1"]},
         "shifts": ["NCC", "MICU", "Elec", "Vac"], "num_weeks": 8,
         "solver_options": {"call_tier_day_granular": True}, "rules": []}
    r.update(extra); return r

def test_blocked_weekend_in_rules_list_is_accepted():
    rule = {"type": "blocked_weekend", "name": "blk", "fellow": "JR1",
            "weeks": [3, 4], "active": True}
    cfg = build_solver_config_from_request(_req(rules=[rule]), standing_path=_STANDING)
    assert cfg is not None  # routed via _migrated_call_rule_to_constraint, no raise

def test_active_call_rules_channel_still_rejected():
    rule = {"type": "blocked_weekend", "name": "blk", "fellow": "JR1",
            "weeks": [3, 4], "active": True}
    with pytest.raises(ValueError, match="call_rules"):
        build_solver_config_from_request(_req(call_rules=[rule]), standing_path=_STANDING)
