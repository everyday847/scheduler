from dataclasses import dataclass

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
