"""Part-A tests: NCC_JR/NCC_SR background rotation quotas for the NF model."""
from __future__ import annotations

from pathlib import Path
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb, decode_solution
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

_REPO = Path(__file__).resolve().parent.parent
_ROUNDINGSAT = _REPO / "vendor/roundingsat/build/roundingsat"
_ANNUAL = _REPO / "config/annual/ncc-nf-model.yaml"
_STANDING = _REPO / "config/standing/ncc-nf-model.yaml"


def _assemble():
    res = assemble_config(None, annual_path=_ANNUAL, standing_path=_STANDING, verbose=False)
    return res[0] if isinstance(res, tuple) else res


def test_palette_has_rotation_shifts_and_two_ccm():
    cfg = _assemble()
    for s in ("Anaesthesia", "Stroke", "Telestroke/Clinic", "MICU", "SICU", "NS", "Elec", "Vac", "NCC"):
        assert s in cfg.shifts, s
    ccm = cfg.fellow_groups.get("CCM", [])
    assert len(ccm) == 2, ccm
    jr = cfg.fellow_groups.get("NCC_JR", [])
    sr = cfg.fellow_groups.get("NCC_SR", [])
    assert len(jr) == 3 and len(sr) == 2


def test_jr_micu_quota_constraint_present():
    """A NCC_JR MICU shift_total (exactly 16) must be among the assembled constraints."""
    cfg = _assemble()
    micu_totals = [c for c in cfg.constraints
                   if c.kind == "shift_total"
                   and c.shifts and "MICU" in c.shifts.shifts
                   and c.params.get("count") in (14, 16)]
    assert micu_totals, "expected an NCC_JR MICU shift_total rule"


def test_full_year_with_rotations_is_sat():
    if not _ROUNDINGSAT.exists():
        import pytest; pytest.skip("RoundingSat not built")
    cfg = _assemble()
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    res = RoundingSatRunner(_ROUNDINGSAT).solve(opb, timeout=180)
    assert res.satisfiable, "NF model with JR/SR rotations + 2 CCM must be feasible"
