import pytest

from scheduler.nf_param_resolver import resolve_nf_parameters

FG = {"NCC_JR": ["JR1", "JR2"], "CCM": ["C1"]}

def test_derives_service_and_nf_band_from_explicit_inputs():
    # weeks 12-14, density 5.0-5.5, fraction 1/3, tol 1
    #   service = [ceil(12*5.0), floor(14*5.5)] = [60, 77]
    #   nf      = [round(60/3)-1, round(77/3)+1] = [19, 27]
    nf_params = {"NCC_JR": {"ncc_weeks": [12, 14], "density": [5.0, 5.5],
                            "nf_fraction": 1/3, "nf_tolerance": 1}}
    out = resolve_nf_parameters(nf_params, {}, FG, [])
    assert out["nf_service_day_band"]["NCC_JR"] == [60, 77]
    assert out["nf_nf_day_band"]["NCC_JR"] == [19, 27]

def test_no_params_is_noop_copy():
    opts = {"nf_week_off_cap": 3}
    out = resolve_nf_parameters(None, opts, FG, [])
    assert out == opts and out is not opts

def test_explicit_band_overrides_derived():
    nf_params = {"NCC_JR": {"ncc_weeks": [12, 14], "density": [5.0, 5.5]}}
    opts = {"nf_nf_day_band": {"NCC_JR": [22, 27]}}
    out = resolve_nf_parameters(nf_params, opts, FG, [])
    assert out["nf_nf_day_band"]["NCC_JR"] == [22, 27]  # unchanged

def test_ncc_weeks_sourced_from_rules_when_omitted():
    rules = [
        {"type": "shift_total", "name": "JR NCC week floor", "groups": ["NCC_JR"],
         "shifts": ["NCC"], "relation": "at_least", "count": 12},
        {"type": "shift_total", "name": "JR NCC week ceil", "groups": ["NCC_JR"],
         "shifts": ["NCC"], "relation": "at_most", "count": 14},
    ]
    nf_params = {"NCC_JR": {"density": [5.0, 5.5]}}  # no ncc_weeks
    out = resolve_nf_parameters(nf_params, {}, FG, rules)
    assert out["nf_service_day_band"]["NCC_JR"] == [60, 77]

def test_unknown_group_raises():
    with pytest.raises(ValueError, match="unknown group"):
        resolve_nf_parameters({"NOPE": {"density": [5, 5]}}, {}, FG, [])

def test_missing_weeks_raises():
    with pytest.raises(ValueError, match="ncc_weeks"):
        resolve_nf_parameters({"NCC_JR": {"density": [5, 5]}}, {}, FG, [])  # no rules, no explicit

def test_bad_fraction_raises():
    with pytest.raises(ValueError, match="nf_fraction"):
        resolve_nf_parameters(
            {"NCC_JR": {"ncc_weeks": [12, 14], "density": [5, 5], "nf_fraction": 1.5}},
            {}, FG, [])

def test_lo_gt_hi_raises():
    with pytest.raises(ValueError, match="lo > hi|empty band"):
        resolve_nf_parameters(
            {"NCC_JR": {"ncc_weeks": [14, 12], "density": [5, 5]}}, {}, FG, [])
