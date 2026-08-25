from __future__ import annotations

from scheduler.palette_derivations import derive_forbidden_shifts
from scheduler.semantic_constraints import ConstraintStrength


ALL_SHIFTS = ["SICU", "MICU", "NS", "Anaesthesia", "NCC1", "NCC2", "Swing",
              "Elec", "Vac", "Stroke", "Telestroke/Clinic", "Clinic/Elective",
              "SCVMC Rehab", "NIR", "ISC"]

FELLOW_GROUPS = {
    "NCC_JR": ["NCC Alaric", "NCC Bertram"],
    "NCC_SR": ["NCC Durga", "NCC Eos"],
    "STROKE": ["Stroke Gabi", "Stroke Jeff"],
    "CCM": ["CCM Generic"],
    "NH": ["NH Adam"],
}


def test_derives_forbidden_from_shift_totals():
    rules = [
        {"name": "JR MICU", "type": "shift_total", "groups": ["NCC_JR"],
         "shifts": ["MICU"], "relation": "exactly", "count": 20,
         "strength": "hard", "active": True},
        {"name": "JR NCC", "type": "shift_total", "groups": ["NCC_JR"],
         "shifts": ["NCC1", "NCC2", "Swing"], "relation": "exactly", "count": 12,
         "strength": "hard", "active": True},
    ]
    constraints = derive_forbidden_shifts(rules, ALL_SHIFTS, FELLOW_GROUPS)

    # Should have one constraint for NCC_JR
    assert len(constraints) == 1
    c = constraints[0]
    assert c.fellows.groups == ("NCC_JR",)
    assert c.strength == ConstraintStrength.HARD

    # Forbidden = everything NOT in {MICU, NCC1, NCC2, Swing}
    forbidden = set(c.params["zero_shifts"])
    expected_forbidden = set(ALL_SHIFTS) - {"MICU", "NCC1", "NCC2", "Swing"}
    assert forbidden == expected_forbidden


def test_group_with_no_shift_totals_gets_no_forbidden():
    rules = [
        {"name": "JR MICU", "type": "shift_total", "groups": ["NCC_JR"],
         "shifts": ["MICU"], "relation": "exactly", "count": 20,
         "strength": "hard", "active": True},
    ]
    constraints = derive_forbidden_shifts(rules, ALL_SHIFTS, FELLOW_GROUPS)

    groups_with_forbidden = [c.fellows.groups[0] for c in constraints]
    assert "CCM" not in groups_with_forbidden
    assert "NH" not in groups_with_forbidden
    assert "STROKE" not in groups_with_forbidden


def test_inactive_rules_ignored():
    rules = [
        {"name": "JR MICU", "type": "shift_total", "groups": ["NCC_JR"],
         "shifts": ["MICU"], "relation": "exactly", "count": 20,
         "strength": "hard", "active": False},
    ]
    constraints = derive_forbidden_shifts(rules, ALL_SHIFTS, FELLOW_GROUPS)
    assert constraints == []


def test_multiple_groups_each_get_forbidden():
    rules = [
        {"name": "JR MICU", "type": "shift_total", "groups": ["NCC_JR"],
         "shifts": ["MICU", "NCC1", "NCC2", "Swing", "SICU", "Anaesthesia", "Elec", "Vac"],
         "relation": "exactly", "count": 52, "strength": "hard", "active": True},
        {"name": "Stroke Service", "type": "shift_total", "groups": ["STROKE"],
         "shifts": ["Stroke", "NCC1", "NCC2", "Swing", "Telestroke/Clinic",
                    "Clinic/Elective", "SCVMC Rehab", "NIR", "ISC", "Elec", "Vac"],
         "relation": "exactly", "count": 52, "strength": "hard", "active": True},
    ]
    constraints = derive_forbidden_shifts(rules, ALL_SHIFTS, FELLOW_GROUPS)

    groups = {c.fellows.groups[0] for c in constraints}
    assert "NCC_JR" in groups
    assert "STROKE" in groups


def test_non_shift_total_rules_ignored():
    rules = [
        {"name": "Max Consec", "type": "max_consecutive", "groups": ["NCC_JR"],
         "shifts": ["NCC1"], "max_weeks": 6, "strength": "hard", "active": True},
    ]
    constraints = derive_forbidden_shifts(rules, ALL_SHIFTS, FELLOW_GROUPS)
    assert constraints == []


def test_unknown_group_in_rule_skipped():
    rules = [
        {"name": "PEM MICU", "type": "shift_total", "groups": ["PEM"],
         "shifts": ["MICU"], "relation": "exactly", "count": 8,
         "strength": "hard", "active": True},
    ]
    constraints = derive_forbidden_shifts(rules, ALL_SHIFTS, FELLOW_GROUPS)
    assert constraints == []


def test_migrated_single_fellow_shift_total_folds_into_group():
    """S1: after the per_fellow_shift_total cutover a single-fellow rule lives
    in `rules` as a shift_total with `fellow:`. Its shifts must fold into the
    owning group's budget — exactly like the per_fellow_shift_total call_rules
    form does (additive into an already-owned group)."""
    rules = [
        {"name": "JR base", "type": "shift_total", "groups": ["NCC_JR"],
         "shifts": ["MICU"], "relation": "exactly", "count": 20,
         "strength": "hard", "active": True},
        {"name": "Alaric extra Stroke", "type": "shift_total",
         "fellow": "NCC Alaric", "shifts": ["Stroke"], "relation": "at_least",
         "count": 4, "strength": "hard", "active": True},
    ]
    constraints = derive_forbidden_shifts(rules, ALL_SHIFTS, FELLOW_GROUPS)
    assert len(constraints) == 1
    forbidden = set(constraints[0].params["zero_shifts"])
    # Stroke is now budgeted for NCC_JR (via the single-fellow rule), so it must
    # NOT be forbidden.
    assert "Stroke" not in forbidden
    assert "MICU" not in forbidden


def test_migrated_inactive_single_fellow_shift_total_ignored():
    rules = [
        {"name": "JR base", "type": "shift_total", "groups": ["NCC_JR"],
         "shifts": ["MICU"], "relation": "exactly", "count": 20,
         "strength": "hard", "active": True},
        {"name": "Alaric extra Stroke", "type": "shift_total",
         "fellow": "NCC Alaric", "shifts": ["Stroke"], "relation": "at_least",
         "count": 4, "strength": "hard", "active": False},
    ]
    constraints = derive_forbidden_shifts(rules, ALL_SHIFTS, FELLOW_GROUPS)
    assert len(constraints) == 1
    # Inactive single-fellow rule contributes nothing → Stroke stays forbidden.
    assert "Stroke" in set(constraints[0].params["zero_shifts"])
