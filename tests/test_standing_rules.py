import pytest

from scheduler.semantic_constraints import ConstraintLifecycle, ConstraintStrength
from scheduler.standing_rules import constraints_from_config


def test_constraints_from_config_builds_block_rules():
    constraints = constraints_from_config({
        "block_rules": [
            {
                "name": "micu_blocks",
                "fellow_groups": ["NCC_JR", "NCC_SR"],
                "shifts": ["MICU"],
                "block_size": 4,
                "strength": "hard",
            }
        ]
    })

    assert len(constraints) == 1
    constraint = constraints[0]
    assert constraint.kind == "all_or_none_block"
    assert constraint.lifecycle is ConstraintLifecycle.STANDING_RULE
    assert constraint.strength is ConstraintStrength.HARD
    assert constraint.fellows.groups == ("NCC_JR", "NCC_SR")
    assert constraint.shifts.shifts == ("MICU",)
    assert constraint.params == {"block_size": 4, "name": "micu_blocks"}


def test_constraints_from_config_builds_generic_rule_entries():
    constraints = constraints_from_config({
        "rules": [
            {
                "name": "ncc_coverage",
                "kind": "ncc_coverage",
                "active": True,
                "strength": "hard",
                "fellow_groups": ["NCC_JR", "NCC_SR", "STROKE"],
                "shifts": ["NCC1", "NCC2", "Swing"],
                "swing_deficit": 8,
                "max_ncc_fellows": 3,
                "max_ncc_plus_swing_fellows": 4,
            }
        ]
    })

    assert len(constraints) == 1
    constraint = constraints[0]
    assert constraint.kind == "ncc_coverage"
    assert constraint.lifecycle is ConstraintLifecycle.STANDING_RULE
    assert constraint.strength is ConstraintStrength.HARD
    assert constraint.fellows.groups == ("NCC_JR", "NCC_SR", "STROKE")
    assert constraint.shifts.shifts == ("NCC1", "NCC2", "Swing")
    assert constraint.params == {
        "name": "ncc_coverage",
        "swing_deficit": 8,
        "max_ncc_fellows": 3,
        "max_ncc_plus_swing_fellows": 4,
    }


def test_constraints_from_config_skips_inactive_generic_rules():
    constraints = constraints_from_config({
        "rules": [
            {
                "name": "inactive",
                "kind": "max_consecutive",
                "active": False,
                "strength": "hard",
                "fellow_groups": ["NCC_JR"],
                "shifts": ["MICU"],
                "weeks": 8,
            }
        ]
    })

    assert constraints == []


def test_constraints_from_config_builds_max_consecutive_rules():
    constraints = constraints_from_config({
        "max_consecutive": [
            {
                "name": "core_icu",
                "fellow_groups": ["NCC_JR", "NCC_SR", "STROKE"],
                "shifts": ["NCC1", "NCC2", "Swing", "MICU"],
                "weeks": 8,
                "strength": "soft",
            }
        ]
    })

    assert len(constraints) == 1
    constraint = constraints[0]
    assert constraint.kind == "max_consecutive"
    assert constraint.lifecycle is ConstraintLifecycle.STANDING_RULE
    assert constraint.strength is ConstraintStrength.SOFT
    assert constraint.params == {"weeks": 8, "name": "core_icu"}


def test_constraints_from_config_builds_swing_deficit_minimization():
    constraints = constraints_from_config({
        "swing_deficit": {
            "fellow_groups": ["NCC_JR", "NCC_SR", "STROKE", "CCM", "NH", "LIA"],
            "shift": "Swing",
        }
    })

    assert len(constraints) == 1
    constraint = constraints[0]
    assert constraint.kind == "minimize_uncovered_shift_weeks"
    assert constraint.lifecycle is ConstraintLifecycle.STANDING_RULE
    assert constraint.strength is ConstraintStrength.MINIMIZE
    assert constraint.fellows.groups == ("NCC_JR", "NCC_SR", "STROKE", "CCM", "NH", "LIA")
    assert constraint.shifts.shifts == ("Swing",)


def test_constraints_from_config_rejects_unknown_strength():
    with pytest.raises(ValueError, match="Unknown constraint strength"):
        constraints_from_config({
            "block_rules": [
                {
                    "name": "bad",
                    "fellow_groups": ["NCC_JR"],
                    "shifts": ["MICU"],
                    "block_size": 4,
                    "strength": "required",
                }
            ]
        })
