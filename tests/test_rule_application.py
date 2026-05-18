from dataclasses import dataclass

from z3 import Bool, Optimize, sat, unsat

from scheduler.main import _rule_handlers
from scheduler.rule_application import RuleApplicationContext, apply_constraints
from scheduler.semantic_constraints import (
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
    SemanticConstraint,
)


@dataclass
class FakeFellow:
    index: int


class FakeFellowMapping:
    total_fellows = 3
    all_fellow_indices = [0, 1, 2]

    def get_fellow_index(self, name):
        return {"NCC Raya": 0, "NCC David": 1}[name]

    def get_fellow(self, name):
        if name == "Missing Fellow":
            return None
        return FakeFellow(self.get_fellow_index(name))

    def get_fellow_indices_by_groups(self, *groups):
        by_group = {
            "NCC_JR": [0],
            "NCC_SR": [1],
            "STROKE": [2],
            "CCM": [0],
        }
        return [
            index
            for group in groups
            for index in by_group.get(group, [])
        ]


def test_apply_constraints_dispatches_active_constraints_with_resolved_fellows():
    calls = []
    constraint = SemanticConstraint(
        kind="full_assignment",
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=ConstraintStrength.HARD,
        fellows=FellowSelector.by_groups("NCC_JR", "NCC_SR"),
    )

    def handler(optimizer, variables, context, semantic_constraint, fellow_indices):
        calls.append((optimizer, variables, context, semantic_constraint, fellow_indices))

    context = RuleApplicationContext(FakeFellowMapping(), shifts=["MICU"], week_count=52)

    apply_constraints(
        optimizer="optimizer",
        variables="x",
        constraints=[constraint],
        context=context,
        handlers={"full_assignment": handler},
    )

    assert calls == [("optimizer", "x", context, constraint, [0, 1])]


def test_apply_constraints_resolves_named_fellows_and_ignores_missing_names():
    calls = []
    constraint = SemanticConstraint(
        kind="specific_assignment",
        lifecycle=ConstraintLifecycle.ANNUAL_RULE,
        strength=ConstraintStrength.HARD,
        fellows=FellowSelector.by_names("NCC Raya", "Missing Fellow"),
    )
    context = RuleApplicationContext(FakeFellowMapping(), shifts=["MICU"], week_count=52)

    apply_constraints(
        optimizer="optimizer",
        variables="x",
        constraints=[constraint],
        context=context,
        handlers={
            "specific_assignment": lambda *args: calls.append(args),
        },
    )

    assert calls[0][-1] == [0]


def test_apply_constraints_rejects_unknown_rule_kinds():
    constraint = SemanticConstraint(
        kind="not_supported",
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=ConstraintStrength.HARD,
    )
    context = RuleApplicationContext(FakeFellowMapping(), shifts=["MICU"], week_count=52)

    try:
        apply_constraints("optimizer", "x", [constraint], context, handlers={})
    except ValueError as exc:
        assert "Unsupported rule kind: not_supported" in str(exc)
    else:
        raise AssertionError("Expected unsupported rule kind to raise")


def test_service_profile_forbids_zero_shifts():
    optimizer, variables, context = _small_z3_context()
    constraint = _ccm_service_profile()

    apply_constraints(optimizer, variables, [constraint], context, _rule_handlers())
    optimizer.add(variables[0, 0, "MICU"])

    assert optimizer.check() == unsat


def test_service_profile_enforces_total_shift_counts():
    optimizer, variables, context = _small_z3_context()
    constraint = _ccm_service_profile()

    apply_constraints(optimizer, variables, [constraint], context, _rule_handlers())
    optimizer.add(variables[0, 0, "NCC1"])
    optimizer.add(variables[0, 1, "NCC1"])
    optimizer.add(variables[0, 2, "NCC2"])
    optimizer.add(variables[0, 3, "Swing"])

    assert optimizer.check() == sat

    optimizer, variables, context = _small_z3_context()
    apply_constraints(optimizer, variables, [constraint], context, _rule_handlers())
    optimizer.add(variables[0, 0, "NCC1"])
    optimizer.add(variables[0, 1, "NCC1"])
    optimizer.add(variables[0, 2, "NCC2"])
    optimizer.add(variables[0, 3, "NCC2"])
    optimizer.add(variables[0, 3, "Swing"])

    assert optimizer.check() == unsat


def test_service_profile_requires_service_weeks_to_share_one_active_block():
    optimizer, variables, context = _small_z3_context()
    constraint = _ccm_service_profile()

    apply_constraints(optimizer, variables, [constraint], context, _rule_handlers())
    optimizer.add(variables[0, 0, "NCC1"])
    optimizer.add(variables[0, 4, "Swing"])

    assert optimizer.check() == unsat


def _small_z3_context():
    shifts = ["NCC1", "NCC2", "Swing", "MICU"]
    variables = {
        (0, week, shift): Bool(f"x_0_{week}_{shift}")
        for week in range(8)
        for shift in shifts
    }
    return Optimize(), variables, RuleApplicationContext(FakeFellowMapping(), shifts=shifts, week_count=8)


def _ccm_service_profile():
    return SemanticConstraint(
        kind="service_profile",
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=ConstraintStrength.HARD,
        fellows=FellowSelector.by_groups("CCM"),
        params={
            "name": "ccm_service_profile",
            "zero_shifts": ["MICU"],
            "totals": [
                {"shifts": ["NCC1", "NCC2"], "relation": "exactly", "weeks": 3},
                {"shifts": ["Swing"], "relation": "exactly", "weeks": 1},
            ],
            "active_blocks": [
                {
                    "name": "ccm_ncc_block",
                    "block_size": 4,
                    "trigger_shifts": ["NCC1", "NCC2", "Swing"],
                    "counts": [
                        {"shifts": ["NCC1", "NCC2", "Swing"], "relation": "exactly", "weeks": 4},
                        {"shifts": ["Swing"], "relation": "exactly", "weeks": 1},
                    ],
                }
            ],
        },
    )
