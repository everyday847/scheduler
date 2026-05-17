from z3 import sat, unsat

from scheduler.annual_rules import named_assignment
from scheduler.semantic_constraints import (
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
    SemanticConstraint,
    ShiftSet,
    WeekSpan,
)
from scheduler.z3_adapter import Z3ScheduleAdapter


def test_compile_specific_assignment_forces_named_fellow_shift():
    adapter = Z3ScheduleAdapter(
        fellows={"NCC Raya": "NCC_JR"},
        num_weeks=2,
        shifts=["MICU", "NCC1"],
    )

    adapter.compile([named_assignment("NCC Raya", week=1, shift="MICU")])

    assert adapter.check() == sat
    assert adapter.model_value("NCC Raya", 1, "MICU") is True


def test_compile_at_most_one_shift_per_week_rejects_double_assignment():
    adapter = Z3ScheduleAdapter(
        fellows={"NCC Raya": "NCC_JR"},
        num_weeks=1,
        shifts=["MICU", "NCC1"],
    )
    invariant = SemanticConstraint(
        kind="at_most_one_shift_per_week",
        lifecycle=ConstraintLifecycle.SOLVER_INVARIANT,
        strength=ConstraintStrength.HARD,
        fellows=FellowSelector.by_types("NCC_JR"),
        weeks=WeekSpan(0, 1),
        shifts=ShiftSet("all", ("MICU", "NCC1")),
    )

    adapter.compile([
        invariant,
        named_assignment("NCC Raya", week=0, shift="MICU"),
        named_assignment("NCC Raya", week=0, shift="NCC1"),
    ])

    assert adapter.check() == unsat


def test_compile_soft_assignment_does_not_make_conflicting_schedule_unsat():
    adapter = Z3ScheduleAdapter(
        fellows={"NCC Raya": "NCC_JR"},
        num_weeks=1,
        shifts=["MICU", "NCC1"],
    )
    invariant = SemanticConstraint(
        kind="at_most_one_shift_per_week",
        lifecycle=ConstraintLifecycle.SOLVER_INVARIANT,
        strength=ConstraintStrength.HARD,
        fellows=FellowSelector.by_types("NCC_JR"),
        weeks=WeekSpan(0, 1),
        shifts=ShiftSet("all", ("MICU", "NCC1")),
    )

    adapter.compile([
        invariant,
        named_assignment("NCC Raya", week=0, shift="MICU"),
        named_assignment("NCC Raya", week=0, shift="NCC1", hard=False),
    ])

    assert adapter.check() == sat
    assert adapter.model_value("NCC Raya", 0, "MICU") is True
    assert adapter.model_value("NCC Raya", 0, "NCC1") is False


def test_compile_all_or_none_block_fills_rest_of_block():
    adapter = Z3ScheduleAdapter(
        fellows={"NCC Raya": "NCC_JR"},
        num_weeks=2,
        shifts=["MICU", "NCC1"],
    )
    block_rule = SemanticConstraint(
        kind="all_or_none_block",
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=ConstraintStrength.HARD,
        fellows=FellowSelector.by_types("NCC_JR"),
        weeks=WeekSpan(0, 2),
        shifts=ShiftSet.single("MICU"),
        params={"block_size": 2},
    )

    adapter.compile([
        block_rule,
        named_assignment("NCC Raya", week=0, shift="MICU"),
    ])

    assert adapter.check() == sat
    assert adapter.model_value("NCC Raya", 1, "MICU") is True


def test_compile_minimize_uncovered_shift_weeks_assigns_shift_when_possible():
    adapter = Z3ScheduleAdapter(
        fellows={"NCC Raya": "NCC_JR"},
        num_weeks=2,
        shifts=["Swing"],
    )
    minimize_swing_deficit = SemanticConstraint(
        kind="minimize_uncovered_shift_weeks",
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=ConstraintStrength.MINIMIZE,
        fellows=FellowSelector.by_types("NCC_JR"),
        weeks=WeekSpan(0, 2),
        shifts=ShiftSet.single("Swing"),
    )

    adapter.compile([minimize_swing_deficit])

    assert adapter.check() == sat
    assert adapter.model_value("NCC Raya", 0, "Swing") is True
    assert adapter.model_value("NCC Raya", 1, "Swing") is True

