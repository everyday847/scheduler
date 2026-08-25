from pathlib import Path
from scheduler.solver_bridge import build_solver_config_from_request

_STANDING = Path("config/standing/ncc-nf-model.yaml")

def _min_request(**extra):
    req = {
        "fellow_groups": {"NCC_JR": ["JR1", "JR2", "JR3"]},
        "shifts": ["NCC", "MICU", "Elec", "Vac"],
        "num_weeks": 8,
        "solver_options": {"call_tier_day_granular": True},
        "rules": [
            {"type": "shift_total", "groups": ["NCC_JR"], "shifts": ["NCC"],
             "relation": "at_least", "count": 12, "name": "f", "strength": "hard"},
            {"type": "shift_total", "groups": ["NCC_JR"], "shifts": ["NCC"],
             "relation": "at_most", "count": 14, "name": "c", "strength": "hard"},
        ],
    }
    req.update(extra)
    return req

def test_nf_parameters_resolved_into_config_band():
    req = _min_request(nf_parameters={"NCC_JR": {"density": [5.0, 5.5]}})
    cfg = build_solver_config_from_request(req, standing_path=_STANDING)
    assert cfg.nf_nf_day_band["NCC_JR"] == [20, 26]  # svc [60,77] * 1/3, tol 0

def test_no_nf_parameters_leaves_bands_untouched():
    req = _min_request(solver_options={"call_tier_day_granular": True,
                                       "nf_nf_day_band": {"NCC_JR": [22, 27]}})
    cfg = build_solver_config_from_request(req, standing_path=_STANDING)
    assert cfg.nf_nf_day_band["NCC_JR"] == [22, 27]
