"""Tests for the CnfBuilder module.

Tests cover:
- Task 3: Variable allocation, clause management, DIMACS output
- Task 4: exactly_one (ALO + AMO pairwise)
- Task 5: at_most_k (Sinz sequential counter)
- Task 6: at_least_k and exactly_k
- Task 7: weighted_sum_at_most
"""

from itertools import product
from pathlib import Path

import pytest

from parafrost_scheduler.cnf_builder import CnfBuilder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def evaluate_cnf(clauses: list[list[int]], assignment: dict[int, bool]) -> bool:
    """Return True iff every clause is satisfied by the given assignment."""
    for clause in clauses:
        satisfied = False
        for lit in clause:
            var = abs(lit)
            val = assignment[var]
            if (lit > 0 and val) or (lit < 0 and not val):
                satisfied = True
                break
        if not satisfied:
            return False
    return True


def all_satisfying_input_assignments(clauses: list[list[int]], input_vars: list[int]) -> set[tuple[bool, ...]]:
    """
    Return the set of input-variable truth assignments for which SOME assignment
    to the auxiliary variables satisfies all clauses.
    """
    all_vars = set()
    for clause in clauses:
        for lit in clause:
            all_vars.add(abs(lit))
    aux_vars = sorted(all_vars - set(input_vars))

    satisfying = set()
    for input_vals in product([False, True], repeat=len(input_vars)):
        input_assignment = dict(zip(input_vars, input_vals))
        # Try all aux assignments to see if any satisfies
        found = False
        for aux_vals in product([False, True], repeat=len(aux_vars)):
            full_assignment = {**input_assignment, **dict(zip(aux_vars, aux_vals))}
            if evaluate_cnf(clauses, full_assignment):
                found = True
                break
        if found:
            satisfying.add(input_vals)
    return satisfying


# ---------------------------------------------------------------------------
# Task 3: Variable allocation, clause management, DIMACS output
# ---------------------------------------------------------------------------

def test_new_var_starts_at_one_and_increments():
    b = CnfBuilder()
    assert b.num_vars == 0
    v1 = b.new_var()
    assert v1 == 1
    assert b.num_vars == 1
    v2 = b.new_var()
    assert v2 == 2
    assert b.num_vars == 2


def test_new_vars_allocates_batch():
    b = CnfBuilder()
    vs = b.new_vars(5)
    assert vs == [1, 2, 3, 4, 5]
    assert b.num_vars == 5
    # Further allocation continues from correct offset
    v = b.new_var()
    assert v == 6
    assert b.num_vars == 6


def test_new_vars_empty():
    b = CnfBuilder()
    vs = b.new_vars(0)
    assert vs == []
    assert b.num_vars == 0


def test_add_clause_increments_count():
    b = CnfBuilder()
    v1 = b.new_var()
    v2 = b.new_var()
    assert b.num_clauses == 0
    b.add_clause([v1, -v2])
    assert b.num_clauses == 1
    b.add_clause([v2])
    assert b.num_clauses == 2


def test_to_dimacs_basic():
    b = CnfBuilder()
    v1 = b.new_var()
    v2 = b.new_var()
    b.add_clause([v1, v2])
    b.add_clause([-v1, v2])
    dimacs = b.to_dimacs()
    lines = dimacs.strip().split("\n")
    assert lines[0] == "p cnf 2 2"
    # Each clause line ends with 0
    assert lines[1] == "1 2 0"
    assert lines[2] == "-1 2 0"


def test_to_dimacs_empty():
    b = CnfBuilder()
    dimacs = b.to_dimacs()
    lines = dimacs.strip().split("\n")
    assert lines[0] == "p cnf 0 0"


def test_write_dimacs(tmp_path):
    b = CnfBuilder()
    v = b.new_var()
    b.add_clause([v])
    out = tmp_path / "test.cnf"
    b.write_dimacs(out)
    content = out.read_text()
    assert "p cnf 1 1" in content
    assert "1 0" in content


# ---------------------------------------------------------------------------
# Task 4: exactly_one
# ---------------------------------------------------------------------------

def test_exactly_one_produces_at_least_one_and_pairwise():
    b = CnfBuilder()
    lits = b.new_vars(3)
    b.exactly_one(lits)

    clauses = b._clauses  # Access internal for inspection
    # Should have 1 ALO clause + C(3,2) = 3 pairwise clauses = 4 total
    assert b.num_clauses == 4
    # ALO clause contains all literals
    alo = [c for c in clauses if len(c) == 3]
    assert len(alo) == 1
    assert set(alo[0]) == {1, 2, 3}
    # AMO pairwise clauses each have 2 negated literals
    amo = [c for c in clauses if len(c) == 2]
    assert len(amo) == 3
    for clause in amo:
        assert all(lit < 0 for lit in clause)


def test_exactly_one_with_two_vars():
    b = CnfBuilder()
    lits = b.new_vars(2)
    b.exactly_one(lits)
    # ALO: [1, 2], AMO: [-1, -2]
    assert b.num_clauses == 2


def test_exactly_one_correctness_exhaustive():
    """exactly_one([1,2,3]) is satisfied iff exactly one of the vars is true."""
    b = CnfBuilder()
    lits = b.new_vars(3)
    b.exactly_one(lits)
    clauses = b._clauses

    for vals in product([False, True], repeat=3):
        assignment = {i + 1: v for i, v in enumerate(vals)}
        result = evaluate_cnf(clauses, assignment)
        expected = sum(vals) == 1
        assert result == expected, f"vals={vals}: got {result}, expected {expected}"


def test_exactly_one_single_var():
    """exactly_one with a single variable just forces it true."""
    b = CnfBuilder()
    v = b.new_var()
    b.exactly_one([v])
    assert b.num_clauses == 1
    clauses = b._clauses
    for val in [False, True]:
        assignment = {1: val}
        result = evaluate_cnf(clauses, assignment)
        assert result == val, f"val={val}: got {result}"


# ---------------------------------------------------------------------------
# Task 5: at_most_k (Sinz sequential counter)
# ---------------------------------------------------------------------------

def test_at_most_k_trivial_noop_when_k_ge_n():
    b = CnfBuilder()
    lits = b.new_vars(3)
    b.at_most_k(lits, 3)
    assert b.num_clauses == 0
    b.at_most_k(lits, 5)
    assert b.num_clauses == 0


def test_at_most_k_zero_forces_all_false():
    b = CnfBuilder()
    lits = b.new_vars(3)
    b.at_most_k(lits, 0)
    assert b.num_clauses == 3
    clauses = b._clauses
    # Each clause is a unit clause negating one literal
    for clause in clauses:
        assert len(clause) == 1
        assert clause[0] < 0


def test_at_most_k_correctness_exhaustive():
    """at_most_2 of 4 vars: check all 16 input assignments."""
    b = CnfBuilder()
    lits = b.new_vars(4)
    b.at_most_k(lits, 2)
    clauses = b._clauses

    # Determine satisfying input assignments
    satisfying = all_satisfying_input_assignments(clauses, lits)

    for vals in product([False, True], repeat=4):
        expected = sum(vals) <= 2
        actual = vals in satisfying
        assert actual == expected, (
            f"at_most_2 of 4: vals={vals}: got satisfying={actual}, expected={expected}"
        )


def test_at_most_1_pairwise_small():
    """at_most_1 with small n uses pairwise (no auxiliary variables)."""
    b = CnfBuilder()
    lits = b.new_vars(4)
    b.at_most_k(lits, 1)
    # Pairwise: C(4,2) = 6 clauses, no extra vars
    assert b.num_vars == 4
    assert b.num_clauses == 6


def test_at_most_k_correctness_k1():
    """at_most_1 of 4 vars: exactly the no-more-than-one assignments."""
    b = CnfBuilder()
    lits = b.new_vars(4)
    b.at_most_k(lits, 1)
    clauses = b._clauses

    satisfying = all_satisfying_input_assignments(clauses, lits)

    for vals in product([False, True], repeat=4):
        expected = sum(vals) <= 1
        actual = vals in satisfying
        assert actual == expected, (
            f"at_most_1 of 4: vals={vals}: got satisfying={actual}, expected={expected}"
        )


def test_at_most_k_correctness_k3_n5():
    """at_most_3 of 5 vars exhaustive."""
    b = CnfBuilder()
    lits = b.new_vars(5)
    b.at_most_k(lits, 3)
    clauses = b._clauses

    satisfying = all_satisfying_input_assignments(clauses, lits)

    for vals in product([False, True], repeat=5):
        expected = sum(vals) <= 3
        actual = vals in satisfying
        assert actual == expected, (
            f"at_most_3 of 5: vals={vals}: got satisfying={actual}, expected={expected}"
        )


# ---------------------------------------------------------------------------
# Task 6: at_least_k and exactly_k
# ---------------------------------------------------------------------------

def test_at_least_k_noop_when_k_le_0():
    b = CnfBuilder()
    lits = b.new_vars(3)
    b.at_least_k(lits, 0)
    assert b.num_clauses == 0
    b.at_least_k(lits, -1)
    assert b.num_clauses == 0


def test_at_least_k_forces_all_true_when_k_eq_n():
    b = CnfBuilder()
    lits = b.new_vars(3)
    b.at_least_k(lits, 3)
    assert b.num_clauses == 3
    clauses = b._clauses
    for clause in clauses:
        assert len(clause) == 1
        assert clause[0] > 0


def test_at_least_k_correctness_exhaustive():
    """at_least_2 of 4 vars: check all 16 input assignments."""
    b = CnfBuilder()
    lits = b.new_vars(4)
    b.at_least_k(lits, 2)
    clauses = b._clauses

    satisfying = all_satisfying_input_assignments(clauses, lits)

    for vals in product([False, True], repeat=4):
        expected = sum(vals) >= 2
        actual = vals in satisfying
        assert actual == expected, (
            f"at_least_2 of 4: vals={vals}: got satisfying={actual}, expected={expected}"
        )


def test_exactly_k_correctness_exhaustive():
    """exactly_2 of 4 vars: check all 16 input assignments."""
    b = CnfBuilder()
    lits = b.new_vars(4)
    b.exactly_k(lits, 2)
    clauses = b._clauses

    satisfying = all_satisfying_input_assignments(clauses, lits)

    for vals in product([False, True], repeat=4):
        expected = sum(vals) == 2
        actual = vals in satisfying
        assert actual == expected, (
            f"exactly_2 of 4: vals={vals}: got satisfying={actual}, expected={expected}"
        )


def test_exactly_k_zero():
    """exactly_0 of 3 vars forces all false."""
    b = CnfBuilder()
    lits = b.new_vars(3)
    b.exactly_k(lits, 0)
    clauses = b._clauses

    for vals in product([False, True], repeat=3):
        assignment = {i + 1: v for i, v in enumerate(vals)}
        result = evaluate_cnf(clauses, assignment)
        expected = sum(vals) == 0
        assert result == expected, f"exactly_0 of 3: vals={vals}"


def test_exactly_k_all():
    """exactly_n of n vars forces all true."""
    b = CnfBuilder()
    lits = b.new_vars(3)
    b.exactly_k(lits, 3)
    clauses = b._clauses

    for vals in product([False, True], repeat=3):
        assignment = {i + 1: v for i, v in enumerate(vals)}
        result = evaluate_cnf(clauses, assignment)
        expected = sum(vals) == 3
        assert result == expected, f"exactly_3 of 3: vals={vals}"


# ---------------------------------------------------------------------------
# Task 7: weighted_sum_at_most
# ---------------------------------------------------------------------------

def test_weighted_sum_at_most_correctness():
    """weights [2, 3], bound 3: 2*x1 + 3*x2 <= 3."""
    b = CnfBuilder()
    x1 = b.new_var()
    x2 = b.new_var()
    b.weighted_sum_at_most([(x1, 2), (x2, 3)], 3)
    clauses = b._clauses
    input_vars = [x1, x2]

    satisfying = all_satisfying_input_assignments(clauses, input_vars)

    # 2*False+3*False=0 <=3 YES
    # 2*True+3*False=2  <=3 YES
    # 2*False+3*True=3  <=3 YES
    # 2*True+3*True=5   <=3 NO
    expected_satisfying = {
        (False, False),
        (True, False),
        (False, True),
    }
    assert satisfying == expected_satisfying


def test_weighted_sum_at_most_uniform_weights():
    """Uniform weights of 1 should behave like at_most_k."""
    b = CnfBuilder()
    lits = b.new_vars(4)
    b.weighted_sum_at_most([(lit, 1) for lit in lits], 2)
    clauses = b._clauses

    satisfying = all_satisfying_input_assignments(clauses, lits)

    for vals in product([False, True], repeat=4):
        expected = sum(vals) <= 2
        actual = vals in satisfying
        assert actual == expected, (
            f"uniform weight at_most_2 of 4: vals={vals}: got {actual}, expected {expected}"
        )


def test_weighted_sum_at_most_zero_bound():
    """Bound of 0 forces all literals false."""
    b = CnfBuilder()
    lits = b.new_vars(3)
    b.weighted_sum_at_most([(lit, 2) for lit in lits], 0)
    clauses = b._clauses

    for vals in product([False, True], repeat=3):
        expected = sum(vals) == 0  # 2*any_true > 0
        assignment = {i + 1: v for i, v in enumerate(vals)}
        # Need to check with aux var satisfaction
    satisfying = all_satisfying_input_assignments(clauses, lits)
    for vals in product([False, True], repeat=3):
        expected = sum(vals) == 0
        actual = vals in satisfying
        assert actual == expected, f"weighted zero bound: vals={vals}"


def test_weighted_sum_at_most_large_weights():
    """Single lit with weight > bound must be false."""
    b = CnfBuilder()
    x = b.new_var()
    # weight 5, bound 3: x must be false
    b.weighted_sum_at_most([(x, 5)], 3)
    clauses = b._clauses

    satisfying = all_satisfying_input_assignments(clauses, [x])
    assert (True,) not in satisfying
    assert (False,) in satisfying
