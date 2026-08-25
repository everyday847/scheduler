# tests/test_nf_ccm_no_bridge.py
from pathlib import Path
import pytest
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb, decode_solution, _day_to_week, _block_starts_grid)
from _dispatch_helpers import solve_or_skip

_REPO = Path(__file__).resolve().parent.parent
_RS = _REPO / "vendor/roundingsat/build/roundingsat"


def _runner_or_skip():
    from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
    if not _RS.exists():
        pytest.skip("RoundingSat not built")
    return RoundingSatRunner(_RS)


def _cfg(flag):
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-foundation-fixture.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    object.__setattr__(cfg, "nf_ccm_no_bridge_blocks", flag)
    return cfg


def test_no_ccm_nf_run_bridges_a_block_boundary():
    cfg = _cfg(True)
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = solve_or_skip(_runner_or_skip(), opb, timeout=1200)
    assert r.satisfiable
    sol = decode_solution(r.assignment, vm)
    # boundary day = first day of each block start-week (except week 0)
    starts = [bs for bs, _ in _block_starts_grid(vm.num_weeks, 4, 1)][1:]
    bday = []
    for wk in starts:
        ds = [d for d in range(vm.num_days) if _day_to_week(d, vm.start_dow) == wk]
        if ds:
            bday.append(min(ds))
    for name in cfg.fellow_groups["CCM"]:
        for b in bday:
            if b - 1 >= 0:
                prev = sol.call_assignments_by_day[b - 1]["NF"] == name
                cur = sol.call_assignments_by_day[b]["NF"] == name
                assert not (prev and cur), (name, b, "NF run bridges block boundary")
