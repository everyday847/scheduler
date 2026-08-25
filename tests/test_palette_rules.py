from __future__ import annotations
import pytest
from scheduler.palette_rules import palette_rule_to_constraints
from scheduler.semantic_constraints import ConstraintStrength, ConstraintLifecycle


def test_shift_total_exactly():
    rule = {
        "name": "NCC Junior MICU Total",
        "type": "shift_total",
        "groups": ["NCC_JR"],
        "shifts": ["MICU"],
        "relation": "exactly",
        "count": 20,
        "strength": "hard",
        "active": True,
    }
    constraints = palette_rule_to_constraints(rule)
    assert len(constraints) == 1
    c = constraints[0]
    assert c.kind == "shift_total"
    assert c.strength == ConstraintStrength.HARD
    assert c.lifecycle == ConstraintLifecycle.ANNUAL_RULE
    assert c.fellows.groups == ("NCC_JR",)
    assert c.shifts.shifts == ("MICU",)
    assert c.params["relation"] == "exactly"
    assert c.params["count"] == 20
    assert c.params["name"] == "NCC Junior MICU Total"


def test_shift_total_with_window():
    rule = {
        "name": "NCC Junior Orientation MICU",
        "type": "shift_total",
        "groups": ["NCC_JR"],
        "shifts": ["MICU"],
        "relation": "exactly",
        "count": 4,
        "strength": "hard",
        "active": True,
        "window": [0, 4],
    }
    constraints = palette_rule_to_constraints(rule)
    assert len(constraints) == 1
    c = constraints[0]
    assert c.weeks is not None
    assert c.weeks.start == 0
    assert c.weeks.end == 4


def test_shift_total_at_least():
    rule = {
        "name": "Stroke Elective Min",
        "type": "shift_total",
        "groups": ["STROKE"],
        "shifts": ["Elec"],
        "relation": "at_least",
        "count": 2,
        "strength": "soft",
        "active": True,
    }
    constraints = palette_rule_to_constraints(rule)
    c = constraints[0]
    assert c.strength == ConstraintStrength.SOFT
    assert c.params["relation"] == "at_least"
    assert c.params["count"] == 2


def test_inactive_rule_returns_empty():
    rule = {
        "name": "Disabled Rule",
        "type": "shift_total",
        "groups": ["NCC_JR"],
        "shifts": ["MICU"],
        "relation": "exactly",
        "count": 20,
        "strength": "hard",
        "active": False,
    }
    constraints = palette_rule_to_constraints(rule)
    assert constraints == []


def test_unknown_type_raises():
    rule = {
        "name": "Bad Rule",
        "type": "nonexistent_type",
        "groups": ["NCC_JR"],
        "shifts": ["MICU"],
        "strength": "hard",
        "active": True,
    }
    with pytest.raises(ValueError, match="Unknown palette rule type"):
        palette_rule_to_constraints(rule)


def test_staffing_per_week():
    rule = {
        "name": "NCC1 Coverage",
        "type": "staffing_per_week",
        "groups": ["NCC_JR", "NCC_SR", "STROKE", "CCM", "NH"],
        "shifts": ["NCC1"],
        "relation": "at_least",
        "count": 1,
        "strength": "hard",
        "active": True,
    }
    constraints = palette_rule_to_constraints(rule)
    assert len(constraints) == 1
    c = constraints[0]
    assert c.kind == "staffing_per_week"
    assert c.params["relation"] == "at_least"
    assert c.params["count"] == 1


def test_staffing_per_week_with_window():
    rule = {
        "name": "Fourth Block MICU Staffing",
        "type": "staffing_per_week",
        "groups": ["NCC_JR", "NCC_SR"],
        "shifts": ["MICU"],
        "relation": "exactly",
        "count": 2,
        "strength": "hard",
        "active": True,
        "window": [12, 16],
    }
    constraints = palette_rule_to_constraints(rule)
    c = constraints[0]
    assert c.weeks.start == 12
    assert c.weeks.end == 16


def test_coverage_target():
    rule = {
        "name": "Swing Coverage Target",
        "type": "coverage_target",
        "groups": ["NCC_JR", "NCC_SR", "STROKE", "CCM", "NH"],
        "shifts": ["Swing"],
        "max_uncovered_weeks": 2,
        "strength": "soft",
        "active": True,
    }
    constraints = palette_rule_to_constraints(rule)
    assert len(constraints) == 1
    c = constraints[0]
    assert c.kind == "coverage_target"
    assert c.params["max_uncovered_weeks"] == 2


def test_max_consecutive():
    rule = {
        "name": "Core ICU Max Consecutive",
        "type": "max_consecutive",
        "groups": ["NCC_JR", "NCC_SR", "STROKE", "NH"],
        "shifts": ["NCC1", "NCC2", "Swing", "SICU", "MICU", "Stroke"],
        "max_weeks": 8,
        "strength": "hard",
        "active": True,
    }
    constraints = palette_rule_to_constraints(rule)
    assert len(constraints) == 1
    c = constraints[0]
    assert c.kind == "max_consecutive"
    assert c.params["weeks"] == 8


def test_block_rotation():
    rule = {
        "name": "MICU 4-Week Blocks",
        "type": "block_rotation",
        "groups": ["NCC_JR", "NCC_SR"],
        "shifts": ["MICU"],
        "block_size": 4,
        "strength": "hard",
        "active": True,
    }
    constraints = palette_rule_to_constraints(rule)
    assert len(constraints) == 1
    c = constraints[0]
    assert c.kind == "all_or_none_block"
    assert c.params["block_size"] == 4


def test_rotation_continuity():
    rule = {
        "name": "NCC 2-Week Team Continuity",
        "type": "rotation_continuity",
        "groups": ["NCC_JR", "NCC_SR", "CCM"],
        "block_size": 2,
        "choices": [["NCC1", "Swing"], ["NCC2", "Swing"]],
        "allow_none": True,
        "strength": "hard",
        "active": True,
    }
    constraints = palette_rule_to_constraints(rule)
    assert len(constraints) == 1
    c = constraints[0]
    assert c.kind == "block_shift_set_choice"
    assert c.params["block_size"] == 2
    assert c.params["choices"] == [["NCC1", "Swing"], ["NCC2", "Swing"]]
    assert c.params["allow_none"] is True


def test_prerequisite():
    rule = {
        "name": "NCC Junior: NCC Before Swing",
        "type": "prerequisite",
        "groups": ["NCC_JR"],
        "prerequisite_shifts": ["NCC1", "NCC2"],
        "target_shifts": ["Swing"],
        "min_prerequisite_weeks": 4,
        "strength": "hard",
        "active": True,
    }
    constraints = palette_rule_to_constraints(rule)
    assert len(constraints) == 1
    c = constraints[0]
    assert c.kind == "prerequisite"
    assert c.params["prerequisite_shifts"] == ["NCC1", "NCC2"]
    assert c.params["target_shifts"] == ["Swing"]
    assert c.params["min_prerequisite_weeks"] == 4


def test_windowed_balance():
    rule = {
        "name": "Half-Year Balance",
        "type": "windowed_balance",
        "groups": ["NCC_JR", "NCC_SR"],
        "shifts": ["MICU"],
        "window_a": [0, 26],
        "window_b": [26, 52],
        "max_difference": 4,
        "strength": "soft",
        "active": True,
    }
    constraints = palette_rule_to_constraints(rule)
    assert len(constraints) == 1
    c = constraints[0]
    assert c.kind == "windowed_balance"
    assert c.params["window_a"] == [0, 26]
    assert c.params["window_b"] == [26, 52]
    assert c.params["max_difference"] == 4
