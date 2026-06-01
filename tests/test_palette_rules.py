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
