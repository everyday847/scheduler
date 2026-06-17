# tests/test_nf_max_call_days.py
from pathlib import Path
import pytest
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb, decode_solution, CALL_ROLES)

_REPO = Path(__file__).resolve().parent.parent
_RS = _REPO / "vendor/roundingsat/build/roundingsat"


def _runner_or_skip():
    from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
    if not _RS.exists():
        pytest.skip("RoundingSat not built")
    return RoundingSatRunner(_RS)


def _cfg(cap):
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-model.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    object.__setattr__(cfg, "nf_max_consecutive_call_days", cap)
    return cfg


def test_no_fellow_exceeds_14_consecutive_call_days():
    cfg = _cfg(14)
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = _runner_or_skip().solve(opb, timeout=1200)
    assert r.satisfiable
    sol = decode_solution(r.assignment, vm)
    for name in vm.fellow_names:
        on = [1 if any(sol.call_assignments_by_day[d][role] == name for role in CALL_ROLES)
              else 0 for d in range(vm.num_days)]
        run = 0
        for v in on:
            run = run + 1 if v else 0
            assert run <= 14, (name, "consecutive call days exceeded 14")
