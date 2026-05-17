import pytest

from scheduler.semantic_constraints import (
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
    SemanticConstraint,
    ShiftSet,
    WeekSpan,
)


def test_week_span_contains_half_open_range():
    span = WeekSpan(start=2, end=5)

    assert list(span.weeks()) == [2, 3, 4]
    assert 2 in span
    assert 4 in span
    assert 5 not in span


def test_week_span_rejects_empty_or_backwards_ranges():
    with pytest.raises(ValueError, match="end must be greater"):
        WeekSpan(start=4, end=4)

    with pytest.raises(ValueError, match="start must be non-negative"):
        WeekSpan(start=-1, end=2)


def test_fellow_selector_can_target_type_names_or_specific_fellows():
    by_type = FellowSelector.by_types("NCC_JR", "NCC_SR")
    by_name = FellowSelector.by_names("NCC Raya", "NCC Joseph")

    assert by_type.types == ("NCC_JR", "NCC_SR")
    assert by_type.names == ()
    assert by_name.names == ("NCC Raya", "NCC Joseph")
    assert by_name.types == ()


def test_fellow_selector_requires_one_target_kind():
    with pytest.raises(ValueError, match="exactly one"):
        FellowSelector(names=("NCC Raya",), types=("NCC_JR",))

    with pytest.raises(ValueError, match="exactly one"):
        FellowSelector()


def test_shift_set_preserves_named_shift_group():
    shifts = ShiftSet("NCC", ("NCC1", "NCC2", "Swing"))

    assert shifts.name == "NCC"
    assert shifts.shifts == ("NCC1", "NCC2", "Swing")


def test_shift_set_rejects_empty_shift_list():
    with pytest.raises(ValueError, match="at least one"):
        ShiftSet("empty", ())


def test_semantic_constraint_preserves_lifecycle_strength_and_params():
    constraint = SemanticConstraint(
        kind="specific_assignment",
        lifecycle=ConstraintLifecycle.ANNUAL_RULE,
        strength=ConstraintStrength.HARD,
        fellows=FellowSelector.by_names("Stroke Jeff"),
        weeks=WeekSpan(11, 12),
        shifts=ShiftSet("stroke", ("Stroke",)),
        params={"reason": "ABPN"},
    )

    assert constraint.kind == "specific_assignment"
    assert constraint.lifecycle is ConstraintLifecycle.ANNUAL_RULE
    assert constraint.strength is ConstraintStrength.HARD
    assert constraint.params == {"reason": "ABPN"}
