import pytest

from scheduler.annual_rules import named_assignment
from scheduler.main import optimize_schedule


def test_optimize_schedule_checks_hard_constraints_before_optimization():
    with pytest.raises(ValueError, match="Hard schedule constraints returned unsat"):
        optimize_schedule(
            fellow_groups={"NCC_JR": ["NCC Raya"]},
            shifts=["MICU", "NCC1"],
            fellow_week_pairs={},
            constraints=[
                named_assignment("NCC Raya", week=0, shift="MICU"),
                named_assignment("NCC Raya", week=0, shift="NCC1"),
            ],
        )
