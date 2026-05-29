from pathlib import Path
import pytest
from parafrost_scheduler.cnf_builder import CnfBuilder
from parafrost_scheduler.parafrost_runner import ParaFrostRunner, SolveResult


PARAFROST_BINARY = Path(__file__).resolve().parent.parent / "vendor" / "ParaFROST" / "build" / "cpu" / "bin" / "parafrost"


@pytest.fixture
def runner():
    if not PARAFROST_BINARY.exists():
        pytest.skip("ParaFROST binary not built")
    return ParaFrostRunner(PARAFROST_BINARY)


def test_solve_satisfiable(runner):
    cnf = CnfBuilder()
    x1, x2 = cnf.new_vars(2)
    cnf.add_clause([x1, x2])   # x1 OR x2
    cnf.add_clause([-x1, x2])  # NOT x1 OR x2 (forces x2=true)

    result = runner.solve(cnf)

    assert result.satisfiable is True
    assert result.assignment is not None
    assert result.assignment[2] is True  # x2 must be true


def test_solve_unsatisfiable(runner):
    cnf = CnfBuilder()
    x1 = cnf.new_var()
    cnf.add_clause([x1])    # x1
    cnf.add_clause([-x1])   # NOT x1

    result = runner.solve(cnf)

    assert result.satisfiable is False
    assert result.assignment is None


def test_solve_with_cardinality(runner):
    """Test that a formula with exactly_one works end-to-end."""
    cnf = CnfBuilder()
    lits = cnf.new_vars(3)
    cnf.exactly_one(lits)

    result = runner.solve(cnf)

    assert result.satisfiable is True
    assert result.assignment is not None
    true_count = sum(1 for v in lits if result.assignment.get(v, False))
    assert true_count == 1


def test_solve_returns_timing(runner):
    cnf = CnfBuilder()
    x1 = cnf.new_var()
    cnf.add_clause([x1])

    result = runner.solve(cnf)

    assert result.runtime_seconds > 0
    assert isinstance(result.stdout, str)
    assert isinstance(result.stderr, str)
