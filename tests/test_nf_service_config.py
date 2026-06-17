from scheduler.solver_bridge import _solver_options_kwargs
from parafrost_scheduler.experiment import assemble_config
from pathlib import Path
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
    """Verify NF service fields exist on ScheduleSolverConfig with correct defaults."""
    repo = Path(__file__).resolve().parent.parent
    annual = repo / "config/annual/ncc-nf-model.yaml"
    standing = repo / "config/standing/ncc-nf-model.yaml"

    if not annual.exists() or not standing.exists():
        pytest.skip("NF model config files not found")

    c = assemble_config(None, annual_path=annual, standing_path=standing, verbose=False)
    c = c[0] if isinstance(c, tuple) else c

    assert c.nf_max_consecutive_off == 0
    assert c.nf_ncc1_continuity == "off"
    assert c.nf_ncc_week_penalty == 0
    assert c.nf_ccm_block_penalty == 0
    assert c.nf_service_day_band is None
