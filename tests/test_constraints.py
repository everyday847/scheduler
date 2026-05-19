from z3 import Optimize, is_false

from scheduler.constraints import ScheduleConstraints
from scheduler.fellow_mapping import FellowMapping


def test_fundamental_constraints_do_not_include_ncc_policy_coverage():
    fellow_mapping = FellowMapping()
    fellow_mapping.add_fellow("NCC Raya", "NCC_JR")
    constraints = ScheduleConstraints(fellow_mapping, num_weeks=1, shifts=["NCC1", "NCC2", "Swing"])
    optimizer = Optimize()

    constraints.add_fundamental_constraints(optimizer)

    # One-rotation-per-week remains an invariant; NCC coverage belongs to Standing Rules.
    assert len(optimizer.assertions()) == 1


def test_forbidden_assignments_are_false_constants_not_variables():
    fellow_mapping = FellowMapping()
    fellow = fellow_mapping.add_fellow("NCC Raya", "NCC_JR")

    constraints = ScheduleConstraints(
        fellow_mapping,
        num_weeks=2,
        shifts=["MICU", "NCC1"],
        forbidden_assignments={(fellow.index, 0, "MICU")},
    )

    assert is_false(constraints.x[fellow.index, 0, "MICU"])
    assert not is_false(constraints.x[fellow.index, 1, "MICU"])
