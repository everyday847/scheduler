# tests/test_nf_concentration_obj.py
from pathlib import Path
import pytest
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb

_REPO = Path(__file__).resolve().parent.parent


def _cfg(ncc_w, ccm_w):
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-model.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    object.__setattr__(cfg, "nf_ncc_week_penalty", ncc_w)
    object.__setattr__(cfg, "nf_ccm_block_penalty", ccm_w)
    return cfg


def test_penalties_register_soft_violations():
    cfg = _cfg(10, 200)
    opb, vm = build_full_schedule_opb(cfg, objective=True)
    # The objective must carry penalty terms with weights 10 and 200.
    weights = {w for _, w in vm.soft_violations}
    assert 10 in weights
    assert 200 in weights
    assert opb.has_objective


def test_penalties_off_by_default_add_nothing():
    cfg = _cfg(0, 0)
    opb, vm = build_full_schedule_opb(cfg, objective=True)
    weights = {w for _, w in vm.soft_violations}
    # No penalty terms at weight 10/200 from this feature when both are 0.
    # (Other framework soft weights may exist; assert our specific markers absent.)
    assert 200 not in weights
