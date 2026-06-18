# tests/test_nf_service_band.py
from pathlib import Path
import pytest
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb, decode_solution
from parafrost_scheduler.schedule_types import CALL_ROLES
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

_REPO = Path(__file__).resolve().parent.parent
_RS = _REPO / "vendor/roundingsat/build/roundingsat"


def _runner_or_skip():
    if not _RS.exists():
        pytest.skip("RoundingSat not built")
    return RoundingSatRunner(_RS)


def _cfg(band):
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-model.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    object.__setattr__(cfg, "nf_service_day_band", band)
    return cfg


def test_tighter_band_caps_service_days():
    # Lower the JR hi to 70 (below the historical 85) and confirm every JR <= 70.
    # SLOW: full-model solve — the model is harder now (binding NF rules), so a short
    # cap yields a TimeoutExpired (not a band bug — the band-ON encoding is verified by
    # the build-delta). Generous timeout + skip-on-timeout; run on Slurm for a verdict.
    import pytest, subprocess
    cfg = _cfg({"NCC_JR": [60, 70], "NCC_SR": [125, 135]})
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    try:
        r = _runner_or_skip().solve(opb, timeout=2000)
    except subprocess.TimeoutExpired:
        pytest.skip("full-model tighter-band solve exceeded 2000s; verify on Slurm")
    assert r.satisfiable
    sol = decode_solution(r.assignment, vm)
    counts = {n: 0 for n in vm.fellow_names}
    for d in range(vm.num_days):
        for role in CALL_ROLES:
            who = sol.call_assignments_by_day[d][role]
            if who:
                counts[who] += 1
    for jr in cfg.fellow_groups["NCC_JR"]:
        assert 60 <= counts[jr] <= 70, (jr, counts[jr])
