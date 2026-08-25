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


def test_nf_run_length_and_rest_days_parse():
    from scheduler.solver_bridge import _solver_options_kwargs
    import pytest
    kw = _solver_options_kwargs({"nf_run_length": [4, 7], "nf_rest_days": [1, 2]})
    assert kw["nf_run_length"] == (4, 7)
    assert kw["nf_rest_days"] == (1, 2)
    with pytest.raises(ValueError):
        _solver_options_kwargs({"nf_run_length": [4]})       # not a pair


def test_nf_run_rest_defaults():
    from parafrost_scheduler.schedule_types import ScheduleSolverConfig
    from scheduler.night_call_types import NightSolverConfig
    from scheduler.weekend_call_types import WeekendSolverConfig
    c = ScheduleSolverConfig(fellow_groups={}, shifts=[], constraints=[],
                             night_config=NightSolverConfig(),
                             weekend_config=WeekendSolverConfig())
    assert c.nf_run_length == (4, 6)
    assert c.nf_rest_days == (1, 2)


def test_nf_stroke_and_run_min_options_parse():
    """The five new Stroke/run/no-triple-ccm flags ride through _solver_options_kwargs."""
    opts = {
        "nf_min_ncc_run_days": 4,
        "nf_weekend_ncc1_paired": True,
        "nf_no_triple_ccm": True,
        "nf_stroke_lex_order": True,
        "nf_stroke_nf_cap": 12,
    }
    kw = _solver_options_kwargs(opts)
    assert kw["nf_min_ncc_run_days"] == 4
    assert kw["nf_weekend_ncc1_paired"] is True
    assert kw["nf_no_triple_ccm"] is True
    assert kw["nf_stroke_lex_order"] is True
    assert kw["nf_stroke_nf_cap"] == 12


def test_nf_stroke_and_run_min_defaults_off():
    c = ScheduleSolverConfig(fellow_groups={}, shifts=[], constraints=[],
                             night_config=NightSolverConfig(),
                             weekend_config=WeekendSolverConfig())
    assert c.nf_min_ncc_run_days == 0
    assert c.nf_weekend_ncc1_paired is False
    assert c.nf_no_triple_ccm is False
    assert c.nf_stroke_lex_order is False
    assert c.nf_stroke_nf_cap == 0


def test_nf_weekend_ncc2_and_ncc2_run_parse():
    """The weekend-NCC2 + NCC2-min-run flags ride through _solver_options_kwargs."""
    kw = _solver_options_kwargs({"nf_weekend_ncc2": True, "nf_min_ncc2_run_days": 2})
    assert kw["nf_weekend_ncc2"] is True
    assert kw["nf_min_ncc2_run_days"] == 2


def test_nf_weekend_ncc2_and_ncc2_run_defaults_off():
    c = ScheduleSolverConfig(fellow_groups={}, shifts=[], constraints=[],
                             night_config=NightSolverConfig(),
                             weekend_config=WeekendSolverConfig())
    assert c.nf_weekend_ncc2 is False
    assert c.nf_min_ncc2_run_days == 0


def test_nf_day_band_parse_and_defaults():
    kw = _solver_options_kwargs({
        "nf_nf_day_band": {"NCC_JR": [20, 28], "Stroke": [9, 12]},
        "nf_ccm_block_nf_cap": 12,
    })
    assert kw["nf_nf_day_band"] == {"NCC_JR": [20, 28], "Stroke": [9, 12]}
    assert kw["nf_ccm_block_nf_cap"] == 12
    kw2 = _solver_options_kwargs({"nf_block_nf_band": {"NCC_JR": [3, 11], "CCM": [4, 11]}})
    assert kw2["nf_block_nf_band"] == {"NCC_JR": [3, 11], "CCM": [4, 11]}
    c = ScheduleSolverConfig(fellow_groups={}, shifts=[], constraints=[],
                             night_config=NightSolverConfig(),
                             weekend_config=WeekendSolverConfig())
    assert c.nf_nf_day_band is None
    assert c.nf_ccm_block_nf_cap == 0


def test_nf_one_third_parse_and_defaults():
    kw = _solver_options_kwargs({"nf_one_third_nf_band": True, "nf_one_third_nf_weight": 25})
    assert kw["nf_one_third_nf_band"] is True
    assert kw["nf_one_third_nf_weight"] == 25
    c = ScheduleSolverConfig(fellow_groups={}, shifts=[], constraints=[],
                             night_config=NightSolverConfig(),
                             weekend_config=WeekendSolverConfig())
    assert c.nf_one_third_nf_band is False
    assert c.nf_one_third_nf_weight == 0
