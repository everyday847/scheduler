from scheduler.annual_rules import constraints_from_config, named_assignment, vacation_request_constraints
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


def test_vacation_request_constraints_make_later_requests_soft_vacation():
    # Requests beyond hard_request_count stay as Vac but become SOFT — the solver
    # still tries to honor the vacation, rather than substituting an elective.
    constraints = vacation_request_constraints(
        {"NCC Raya": [1, 7, 25, 30]},
        hard_request_count=3,
    )

    soft = constraints[3]
    assert soft.kind == "specific_assignment"
    assert soft.lifecycle is ConstraintLifecycle.ANNUAL_RULE
    assert soft.strength is ConstraintStrength.SOFT
    assert soft.weeks.start == 30
    assert soft.shifts.shifts == ("Vac",)
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


def test_constraints_from_config_builds_vacation_policy_from_request_pairs():
    constraints = constraints_from_config(
        {
            "rules": [
                {
                    "name": "vacation_requests",
                    "kind": "vacation_request_policy",
                    "active": True,
                    "hard_request_count": 2,
                }
            ]
        },
        fellow_week_pairs={"NCC Raya": [1, 7, 25]},
    )

    assert [constraint.weeks.start for constraint in constraints] == [1, 7, 25]
    assert [constraint.strength for constraint in constraints] == [
        ConstraintStrength.HARD,
        ConstraintStrength.HARD,
        ConstraintStrength.SOFT,
    ]


def test_constraints_from_config_builds_specific_assignments_for_groups_or_names():
    constraints = constraints_from_config({
        "rules": [
            {
                "name": "first_week_senior_on_stroke",
                "kind": "specific_assignment",
                "active": True,
                "strength": "hard",
                "fellow_groups": ["NCC_SR"],
                "shift": "Stroke",
                "week": 1,
            },
            {
                "name": "abpn_backup",
                "kind": "specific_assignment",
                "active": True,
                "strength": "soft",
                "fellows": ["NCC Prash", "NCC David"],
                "shift": "Telestroke/Clinic",
                "week": 11,
            },
            {
                "name": "inactive",
                "kind": "specific_assignment",
                "active": False,
                "fellows": ["Stroke Jeff"],
                "shift": "Stroke",
                "week": 11,
            },
        ]
    })

    assert len(constraints) == 2
    assert constraints[0].kind == "specific_assignment"
    assert constraints[0].fellows.groups == ("NCC_SR",)
    assert constraints[0].strength is ConstraintStrength.HARD
    assert constraints[0].weeks.start == 1
    assert constraints[1].fellows.names == ("NCC Prash", "NCC David")
    assert constraints[1].strength is ConstraintStrength.SOFT
