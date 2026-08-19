from pathlib import Path
from scheduler.solver_bridge import build_solver_config_from_request

_STANDING = Path("config/standing/ncc-nf-model.yaml")


def test_ui_nf_parameters_and_dials_roundtrip():
    req = {"fellow_groups": {"NCC_JR": ["JR1", "JR2", "JR3"]},
           "shifts": ["NCC", "MICU", "Elec", "Vac"], "num_weeks": 8,
           "solver_options": {"call_tier_day_granular": True, "nf_week_off_cap": 3},
           "nf_parameters": {"NCC_JR": {"ncc_weeks": [12, 14], "density": [5.0, 5.5]}},
           "rules": []}
    cfg = build_solver_config_from_request(req, standing_path=_STANDING)
    assert cfg.nf_week_off_cap == 3
    assert cfg.nf_nf_day_band["NCC_JR"] == [20, 26]
