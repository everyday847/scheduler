from scheduler.solver_bridge import _solver_options_kwargs
from parafrost_scheduler.schedule_types import ScheduleSolverConfig
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig
import pytest


def test_nf_service_options_parse():
    opts = {
        "call_tier_day_granular": True,
        "nf_max_consecutive_off": 2,
        "nf_ncc1_continuity": "weekday",
        "nf_ncc_week_penalty": 10,
        "nf_ccm_block_penalty": 200,
    }
    kw = _solver_options_kwargs(opts)
    assert kw["nf_max_consecutive_off"] == 2
    assert kw["nf_ncc1_continuity"] == "weekday"
    assert kw["nf_ncc_week_penalty"] == 10
    assert kw["nf_ccm_block_penalty"] == 200


def test_nf_ncc1_continuity_bad_value_raises():
    with pytest.raises(ValueError):
        _solver_options_kwargs({"nf_ncc1_continuity": "sometimes"})


def test_nf_service_defaults_off():
    """Verify NF service fields exist on ScheduleSolverConfig with correct defaults.

    Constructs a minimal config directly — no config files, no skip-guard —
    so this test is never vacuous.
    """
    c = ScheduleSolverConfig(
        fellow_groups={},
        shifts=[],
        constraints=[],
        night_config=NightSolverConfig(),
        weekend_config=WeekendSolverConfig(),
    )

    assert c.nf_max_consecutive_off == 0
    assert c.nf_ncc1_continuity == "off"
    assert c.nf_ncc_week_penalty == 0
    assert c.nf_ccm_block_penalty == 0
    assert c.nf_service_day_band is None
