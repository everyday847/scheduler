from scheduler.annual_rules import named_assignment, vacation_request_constraints
from scheduler.semantic_constraints import ConstraintLifecycle, ConstraintStrength


def test_vacation_request_constraints_make_top_requests_hard_vacation():
    constraints = vacation_request_constraints(
        {"NCC Raya": [1, 7, 25, 30]},
        hard_request_count=3,
    )

    hard = constraints[:3]
    assert [constraint.weeks.start for constraint in hard] == [1, 7, 25]
    assert all(constraint.kind == "specific_assignment" for constraint in hard)
    assert all(constraint.lifecycle is ConstraintLifecycle.ANNUAL_RULE for constraint in hard)
    assert all(constraint.strength is ConstraintStrength.HARD for constraint in hard)
    assert all(constraint.fellows.names == ("NCC Raya",) for constraint in hard)
    assert all(constraint.shifts.shifts == ("Vac",) for constraint in hard)


def test_vacation_request_constraints_make_later_requests_soft_elective():
    constraints = vacation_request_constraints(
        {"NCC Raya": [1, 7, 25, 30]},
        hard_request_count=3,
    )

    soft = constraints[3]
    assert soft.kind == "specific_assignment"
    assert soft.lifecycle is ConstraintLifecycle.ANNUAL_RULE
    assert soft.strength is ConstraintStrength.SOFT
    assert soft.weeks.start == 30
    assert soft.shifts.shifts == ("Elec",)
    assert soft.params["request_rank"] == 4


def test_named_assignment_builds_hard_or_soft_annual_rule():
    hard = named_assignment("Stroke Jeff", week=11, shift="Stroke")
    soft = named_assignment("NCC Prash", week=11, shift="Telestroke/Clinic", hard=False)

    assert hard.kind == "specific_assignment"
    assert hard.lifecycle is ConstraintLifecycle.ANNUAL_RULE
    assert hard.strength is ConstraintStrength.HARD
    assert hard.fellows.names == ("Stroke Jeff",)
    assert hard.weeks.start == 11
    assert hard.weeks.end == 12
    assert hard.shifts.shifts == ("Stroke",)

    assert soft.strength is ConstraintStrength.SOFT
    assert soft.fellows.names == ("NCC Prash",)
    assert soft.shifts.shifts == ("Telestroke/Clinic",)

