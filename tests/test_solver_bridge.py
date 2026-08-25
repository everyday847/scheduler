from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from scheduler.night_call_types import NightSolverConfig
from parafrost_scheduler.orchestrator import (
    _apply_night_rules,
    _apply_weekend_rules,
    _validate_night_config,
    _validate_weekend_config,
)
from scheduler.solver_bridge import build_solver_config_from_request
from scheduler.weekend_call_types import WeekendSolverConfig


# ---------------------------------------------------------------------------
# _apply_night_rules unit tests
# ---------------------------------------------------------------------------

def test_apply_night_rules_empty():
    assert _apply_night_rules({}) == {}
    assert _apply_night_rules({"night_rules": []}) == {}


def test_apply_night_rules_spacing():
    result = _apply_night_rules({
        "night_rules": [
            {"name": "NS", "type": "night_spacing", "max_nights": 2, "window_days": 4, "active": True},
        ],
    })
    assert result["spacing_max_nights"] == 2
    assert result["spacing_window_days"] == 4


def test_apply_night_rules_blocked_services():
    result = _apply_night_rules({
        "night_rules": [
            {
                "name": "Blocked", "type": "night_blocked_services", "active": True,
                "exact_services": ["Vacation", "AAN"],
                "substring_services": ["ICU"],
            },
        ],
    })
    assert result["blocking_exact_services"] == ("Vacation", "AAN")
    assert result["blocking_substring_services"] == ("ICU",)


def test_apply_night_rules_holiday_eligibility():
    result = _apply_night_rules({
        "night_rules": [
            {"name": "Holiday", "type": "night_holiday_eligibility", "active": True, "allowed_services": ["NCC1", "Stroke"]},
        ],
    })
    assert result["holiday_allowed_services"] == ("NCC1", "Stroke")


def test_apply_night_rules_penalties():
    result = _apply_night_rules({
        "night_rules": [
            {"name": "Penalties", "type": "night_penalties", "active": True, "weights": {"anaesthesia": 2, "stroke": 10}},
        ],
    })
    assert result["penalty_weights"] == {"anaesthesia": 2, "stroke": 10}


def test_apply_night_rules_inactive_skipped():
    result = _apply_night_rules({
        "night_rules": [
            {"name": "Inactive", "type": "night_spacing", "max_nights": 5, "window_days": 10, "active": False},
        ],
    })
    assert result == {}


def test_apply_night_rules_all_types():
    result = _apply_night_rules({
        "night_rules": [
            {"name": "S", "type": "night_spacing", "max_nights": 1, "window_days": 3, "active": True},
            {"name": "B", "type": "night_blocked_services", "active": True, "exact_services": ["V"], "substring_services": ["S"]},
            {"name": "H", "type": "night_holiday_eligibility", "active": True, "allowed_services": ["N1"]},
            {"name": "P", "type": "night_penalties", "active": True, "weights": {"a": 1}},
        ],
    })
    assert result["spacing_max_nights"] == 1
    assert result["blocking_exact_services"] == ("V",)
    assert result["holiday_allowed_services"] == ("N1",)
    assert result["penalty_weights"] == {"a": 1}


# ---------------------------------------------------------------------------
# _apply_weekend_rules unit tests
# ---------------------------------------------------------------------------

def test_apply_weekend_rules_empty():
    assert _apply_weekend_rules({}) == {}
    assert _apply_weekend_rules({"weekend_rules": []}) == {}


def test_apply_weekend_rules_spacing():
    result = _apply_weekend_rules({
        "weekend_rules": [
            {"name": "WS", "type": "weekend_spacing", "max_weekends": 2, "window_weekends": 3, "active": True},
        ],
    })
    assert result["spacing_max_weekends"] == 2
    assert result["spacing_window_weekends"] == 3


def test_apply_weekend_rules_blocked_services():
    result = _apply_weekend_rules({
        "weekend_rules": [
            {
                "name": "WB", "type": "weekend_blocked_services", "active": True,
                "exact_services": ["Vacation"], "substring_services": ["MSICU"],
            },
        ],
    })
    assert result["blocking_exact_services"] == ("Vacation",)
    assert result["blocking_substring_services"] == ("MSICU",)


def test_apply_weekend_rules_stroke_eligibility():
    result = _apply_weekend_rules({
        "weekend_rules": [
            {"name": "SE", "type": "weekend_stroke_eligibility", "active": True, "eligible_services": ["Stroke", "Neuro"]},
        ],
    })
    assert result["stroke_eligible_services"] == ("Stroke", "Neuro")


def test_apply_weekend_rules_penalties():
    result = _apply_weekend_rules({
        "weekend_rules": [
            {"name": "WP", "type": "weekend_penalties", "active": True, "weights": {"anaesthesia": 2, "clinic": 3}},
        ],
    })
    assert result["penalty_weights"] == {"anaesthesia": 2, "clinic": 3}


def test_apply_weekend_rules_inactive_skipped():
    result = _apply_weekend_rules({
        "weekend_rules": [
            {"name": "I", "type": "weekend_spacing", "max_weekends": 5, "window_weekends": 10, "active": False},
        ],
    })
    assert result == {}


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

def test_validate_night_config_passes():
    _validate_night_config({"spacing_max_nights": 1, "spacing_window_days": 2})


def test_validate_night_config_bad_max_nights():
    with pytest.raises(ValueError, match="spacing_max_nights must be >= 1"):
        _validate_night_config({"spacing_max_nights": 0})


def test_validate_night_config_bad_window():
    with pytest.raises(ValueError, match="spacing_window_days must be >= 2"):
        _validate_night_config({"spacing_window_days": 1})


def test_validate_night_config_empty():
    _validate_night_config({})


def test_validate_weekend_config_passes():
    _validate_weekend_config({"spacing_max_weekends": 1, "spacing_window_weekends": 2})


def test_validate_weekend_config_bad_max():
    with pytest.raises(ValueError, match="spacing_max_weekends must be >= 1"):
        _validate_weekend_config({"spacing_max_weekends": 0})


def test_validate_weekend_config_bad_window():
    with pytest.raises(ValueError, match="spacing_window_weekends must be >= 2"):
        _validate_weekend_config({"spacing_window_weekends": 1})


def test_validate_weekend_config_empty():
    _validate_weekend_config({})


# ---------------------------------------------------------------------------
# Integration: _build_night_config populates new fields
# ---------------------------------------------------------------------------

def test_build_night_config_with_rules():
    """Night rules should be reflected in the returned NightSolverConfig."""
    request = {
        "fellow_groups": {"STROKE": ["Alice"], "NCC_JR": ["Bob"]},
        "shifts": ["NCC1"],
        "fellow_week_pairs": {},
        "night_call": [],
        "holiday_dates": [],
        "horizon_start": "2026-06-29",
        "night_rules": [
            {"name": "S", "type": "night_spacing", "max_nights": 2, "window_days": 5, "active": True},
            {"name": "P", "type": "night_penalties", "active": True, "weights": {"anaesthesia": 3}},
        ],
    }
    config = build_solver_config_from_request(request).night_config
    assert config.spacing_max_nights == 2
    assert config.spacing_window_days == 5
    assert config.penalty_weights == {"anaesthesia": 3}


def test_build_weekend_config_with_rules():
    """Weekend rules should be reflected in the returned WeekendSolverConfig."""
    request = {
        "fellow_groups": {"STROKE": ["Alice"], "NCC_JR": ["Bob"]},
        "shifts": ["NCC1"],
        "fellow_week_pairs": {},
        "weekend_call": [],
        "weekend_rules": [
            {"name": "WS", "type": "weekend_spacing", "max_weekends": 2, "window_weekends": 3, "active": True},
            {"name": "SE", "type": "weekend_stroke_eligibility", "active": True, "eligible_services": ["Stroke"]},
        ],
    }
    config = build_solver_config_from_request(request).weekend_config
    assert config.spacing_max_weekends == 2
    assert config.spacing_window_weekends == 3
    assert config.stroke_eligible_services == ("Stroke",)


def test_build_weekend_config_ncc_ranges():
    """A weekend_call entry with ncc_ranges should produce per-fellow ranges + a
    group-sum constraint, and EXCLUDE those fellows from the even-split totals."""
    request = {
        "fellow_groups": {
            "CCM": ["CCM A", "CCM B", "CCM C"],
            "NCC_JR": ["Bob", "Cara"],
        },
        "shifts": ["NCC1"],
        "fellow_week_pairs": {},
        "weekend_call": [
            {
                "group": "CCM",
                "ncc_total": 22,
                "stroke_total": 0,
                "ncc_ranges": {
                    "CCM A": [15, 22],
                    "CCM B": [4, 7],
                    "CCM C": [0, 4],
                },
            },
            {"group": "NCC_JR", "ncc_total": 10, "stroke_total": 0},
        ],
    }
    config = build_solver_config_from_request(request).weekend_config
    # Ranges populated for CCM fellows.
    assert config.ncc_ranges == {
        "CCM A": (15, 22), "CCM B": (4, 7), "CCM C": (0, 4),
    }
    # Group-sum recorded for the CCM trio.
    assert (("CCM A", "CCM B", "CCM C"), 22) in config.ncc_group_sums
    # CCM fellows are NOT in the even-split ncc_totals (range model owns them).
    for f in ("CCM A", "CCM B", "CCM C"):
        assert f not in config.ncc_totals
    # NCC_JR still even-split (10 / 2 = 5 each).
    assert config.ncc_totals["Bob"] == 5
    assert config.ncc_totals["Cara"] == 5


def test_build_weekend_config_every_other_weekend_flag():
    """A weekend_call entry with every_other_weekend: true marks its fellows for
    the hard alternating-weekend constraint."""
    request = {
        "fellow_groups": {"CCM": ["A", "B"], "NCC_JR": ["Bob"]},
        "shifts": ["NCC1"],
        "fellow_week_pairs": {},
        "weekend_call": [
            {"group": "CCM", "ncc_total": 4, "stroke_total": 0,
             "ncc_ranges": {"A": [0, 4], "B": [0, 4]},
             "every_other_weekend": True},
            {"group": "NCC_JR", "ncc_total": 4, "stroke_total": 0},
        ],
    }
    config = build_solver_config_from_request(request).weekend_config
    assert config.every_other_weekend_fellows == frozenset({"A", "B"})


def test_build_weekend_config_ncc_ranges_infeasible_sum_rejected():
    """If the group total can't fit inside the summed ranges, raise at load."""
    request = {
        "fellow_groups": {"CCM": ["A", "B"]},
        "shifts": ["NCC1"],
        "fellow_week_pairs": {},
        "weekend_call": [
            {"group": "CCM", "ncc_total": 50, "stroke_total": 0,
             "ncc_ranges": {"A": [0, 4], "B": [0, 4]}},  # max 8 < 50
        ],
    }
    with pytest.raises(ValueError):
        build_solver_config_from_request(request)


def test_build_night_config_no_rules_uses_defaults():
    """Without night_rules, default values should be used."""
    request = {
        "fellow_groups": {"STROKE": ["Alice"]},
        "shifts": ["NCC1"],
        "fellow_week_pairs": {},
        "night_call": [],
        "holiday_dates": [],
    }
    config = build_solver_config_from_request(request).night_config
    assert config.spacing_max_nights == 1
    assert config.spacing_window_days == 3
    assert config.penalty_weights == {"anaesthesia": 1, "clinic": 1, "stroke": 5, "friday_weekend_ncc1": 1, "sunday_following": 1}
    assert config.sunday_preferred_services == ("Elec", "Telestroke/Clinic", "Clinic/Elective", "SCVMC Rehab", "NIR", "ISC", "Vac")


def test_build_weekend_config_no_rules_uses_defaults():
    """Without weekend_rules, default values should be used."""
    request = {
        "fellow_groups": {"STROKE": ["Alice"]},
        "shifts": ["NCC1"],
        "fellow_week_pairs": {},
        "weekend_call": [],
    }
    config = build_solver_config_from_request(request).weekend_config
    assert config.spacing_max_weekends == 1
    assert config.spacing_window_weekends == 2
    assert config.penalty_weights == {"anaesthesia": 1, "clinic": 1, "stroke": 5, "friday_weekend_ncc1": 1}


# ---------------------------------------------------------------------------
# Palette v2 merging tests (using monkeypatched standing config)
# ---------------------------------------------------------------------------

def test_night_rules_annual_overrides_standing(monkeypatch, tmp_path):
    """Annual night_rules should override standing night_rules in palette v2."""
    standing = tmp_path / "standing.yaml"
    standing.write_text(yaml.dump({
        "rules": [{"type": "full_assignment", "groups": ["STROKE"], "name": "FA", "active": True}],
        "night_rules": [
            {"name": "S", "type": "night_spacing", "max_nights": 99, "window_days": 99, "active": True},
        ],
    }))
    request = {
        "fellow_groups": {"STROKE": ["Alice"]},
        "shifts": ["NCC1"],
        "fellow_week_pairs": {},
        "night_call": [],
        "holiday_dates": [],
        "night_rules": [
            {"name": "S", "type": "night_spacing", "max_nights": 2, "window_days": 4, "active": True},
        ],
    }
    config = build_solver_config_from_request(request, standing_path=standing).night_config
    assert config.spacing_max_nights == 2
    assert config.spacing_window_days == 4


def test_night_rules_standing_used_when_no_annual(monkeypatch, tmp_path):
    """Standing night_rules should be used when annual doesn't provide them."""
    standing = tmp_path / "standing.yaml"
    standing.write_text(yaml.dump({
        "rules": [{"type": "full_assignment", "groups": ["STROKE"], "name": "FA", "active": True}],
        "night_rules": [
            {"name": "S", "type": "night_spacing", "max_nights": 3, "window_days": 5, "active": True},
        ],
    }))
    request = {
        "fellow_groups": {"STROKE": ["Alice"]},
        "shifts": ["NCC1"],
        "fellow_week_pairs": {},
        "night_call": [],
        "holiday_dates": [],
    }
    config = build_solver_config_from_request(request, standing_path=standing).night_config
    assert config.spacing_max_nights == 3
    assert config.spacing_window_days == 5


def test_weekend_rules_annual_overrides_standing(monkeypatch, tmp_path):
    """Annual weekend_rules should override standing weekend_rules in palette v2."""
    standing = tmp_path / "standing.yaml"
    standing.write_text(yaml.dump({
        "rules": [{"type": "full_assignment", "groups": ["STROKE"], "name": "FA", "active": True}],
        "weekend_rules": [
            {"name": "WS", "type": "weekend_spacing", "max_weekends": 99, "window_weekends": 99, "active": True},
        ],
    }))
    request = {
        "fellow_groups": {"STROKE": ["Alice"]},
        "shifts": ["NCC1"],
        "fellow_week_pairs": {},
        "weekend_call": [],
        "weekend_rules": [
            {"name": "WS", "type": "weekend_spacing", "max_weekends": 2, "window_weekends": 3, "active": True},
        ],
    }
    config = build_solver_config_from_request(request, standing_path=standing).weekend_config
    assert config.spacing_max_weekends == 2
    assert config.spacing_window_weekends == 3


# ---------------------------------------------------------------------------
# Validation integration tests
# ---------------------------------------------------------------------------

def test_night_rules_bad_spacing_raises():
    request = {
        "fellow_groups": {"STROKE": ["Alice"]},
        "shifts": ["NCC1"],
        "fellow_week_pairs": {},
        "night_call": [],
        "holiday_dates": [],
        "night_rules": [
            {"name": "S", "type": "night_spacing", "max_nights": 0, "window_days": 3, "active": True},
        ],
    }
    with pytest.raises(ValueError, match="spacing_max_nights must be >= 1"):
        build_solver_config_from_request(request)


def test_weekend_rules_bad_spacing_raises():
    request = {
        "fellow_groups": {"STROKE": ["Alice"]},
        "shifts": ["NCC1"],
        "fellow_week_pairs": {},
        "weekend_call": [],
        "weekend_rules": [
            {"name": "WS", "type": "weekend_spacing", "max_weekends": 1, "window_weekends": 1, "active": True},
        ],
    }
    with pytest.raises(ValueError, match="spacing_window_weekends must be >= 2"):
        build_solver_config_from_request(request)
