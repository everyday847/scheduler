"""Tests for the OPB encoder and RoundingSat PB solver backend.

Covers:
1. OpbBuilder unit tests (encoding, format)
2. RoundingSat runner integration tests (requires binary)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner, SolveResult

ROUNDINGSAT_BINARY = (
    Path(__file__).resolve().parent.parent
    / "vendor"
    / "roundingsat"
    / "build"
    / "roundingsat"
)


@pytest.fixture
def runner():
    if not ROUNDINGSAT_BINARY.exists():
        pytest.skip("RoundingSat binary not built")
    return RoundingSatRunner(ROUNDINGSAT_BINARY)


# ---------------------------------------------------------------------------
# OpbBuilder unit tests
# ---------------------------------------------------------------------------

class TestOpbBuilder:
    """Unit tests for the OPB formula builder (no solver needed)."""

    def test_new_var_allocates_sequentially(self):
        opb = OpbBuilder()
        v1 = opb.new_var()
        v2 = opb.new_var()
        v3 = opb.new_var()
        assert v1 == 1
        assert v2 == 2
        assert v3 == 3
        assert opb.num_vars == 3

    def test_new_vars_allocates_batch(self):
        opb = OpbBuilder()
        vs = opb.new_vars(5)
        assert vs == [1, 2, 3, 4, 5]
        assert opb.num_vars == 5

    def test_exactly_one_emits_single_constraint(self):
        opb = OpbBuilder()
        vs = opb.new_vars(3)
        opb.exactly_one(vs)
        assert opb.num_constraints == 1
        text = opb.to_opb()
        assert "= 1 ;" in text
        assert "+1 x1" in text
        assert "+1 x2" in text
        assert "+1 x3" in text

    def test_exactly_k_is_single_line(self):
        opb = OpbBuilder()
        vs = opb.new_vars(10)
        opb.exactly_k(vs, 5)
        assert opb.num_constraints == 1
        text = opb.to_opb()
        assert "= 5 ;" in text
        # No extra variables introduced
        assert opb.num_vars == 10

    def test_at_most_k_is_single_line(self):
        opb = OpbBuilder()
        vs = opb.new_vars(8)
        opb.at_most_k(vs, 3)
        assert opb.num_constraints == 1
        text = opb.to_opb()
        assert "<= 3 ;" in text

    def test_at_least_k_is_single_line(self):
        opb = OpbBuilder()
        vs = opb.new_vars(6)
        opb.at_least_k(vs, 2)
        assert opb.num_constraints == 1
        text = opb.to_opb()
        assert ">= 2 ;" in text

    def test_add_unit_positive_literal(self):
        opb = OpbBuilder()
        v = opb.new_var()
        opb.add_unit(v)
        text = opb.to_opb()
        assert f"+1 x{v} >= 1 ;" in text

    def test_add_unit_negative_literal(self):
        opb = OpbBuilder()
        v = opb.new_var()
        opb.add_unit(-v)
        text = opb.to_opb()
        assert f"+1 ~x{v} >= 1 ;" in text

    def test_weighted_sum_at_most_single_line(self):
        opb = OpbBuilder()
        vs = opb.new_vars(5)
        weighted = list(zip(vs, [5, 5, 1, 1, 1]))
        opb.weighted_sum_at_most(weighted, 10)
        assert opb.num_constraints == 1
        text = opb.to_opb()
        assert "<= 10 ;" in text
        # No aux variables — all 5 primary vars remain
        assert opb.num_vars == 5

    def test_weighted_sum_at_most_noop_on_empty(self):
        opb = OpbBuilder()
        opb.weighted_sum_at_most([], 100)
        assert opb.num_constraints == 0

    def test_header_counts_match_state(self):
        opb = OpbBuilder()
        vs = opb.new_vars(4)
        opb.exactly_k(vs, 2)
        opb.at_most_k(vs, 3)
        text = opb.to_opb()
        first_line = text.splitlines()[0]
        assert f"#variable= {opb.num_vars}" in first_line
        assert f"#constraint= {opb.num_constraints}" in first_line

    def test_negated_literals_encoded_as_tilde(self):
        opb = OpbBuilder()
        vs = opb.new_vars(3)
        # Pass negative literals (negated)
        neg_lits = [-v for v in vs]
        opb.at_least_k(neg_lits, 2)
        text = opb.to_opb()
        assert "~x1" in text
        assert "~x2" in text
        assert "~x3" in text

    def test_conditional_exactly_k_two_constraints(self):
        opb = OpbBuilder()
        vs = opb.new_vars(5)
        sel = opb.new_var()
        opb.conditional_exactly_k(vs, 2, sel)
        # Should produce 2 PB constraints
        assert opb.num_constraints == 2
        # No extra aux variables
        assert opb.num_vars == 6

    def test_write_opb(self, tmp_path):
        opb = OpbBuilder()
        vs = opb.new_vars(3)
        opb.exactly_one(vs)
        path = tmp_path / "test.opb"
        opb.write_opb(path)
        content = path.read_text()
        assert "#variable= 3 #constraint= 1" in content
        assert "= 1 ;" in content


# ---------------------------------------------------------------------------
# RoundingSat runner integration tests
# ---------------------------------------------------------------------------

class TestRoundingSatRunner:
    """Integration tests against the actual RoundingSat binary."""

    def test_trivial_sat(self, runner):
        """x1 must be true, x1+x2+x3=1 → x1 true, others false."""
        opb = OpbBuilder()
        v1, v2, v3 = opb.new_vars(3)
        opb.exactly_one([v1, v2, v3])
        opb.add_unit(v1)  # force x1=1

        result = runner.solve(opb)
        assert result.satisfiable is True
        assert result.assignment is not None
        assert result.assignment[v1] is True
        assert result.assignment[v2] is False
        assert result.assignment[v3] is False

    def test_trivial_unsat(self, runner):
        """x1 must be true AND x1 must be false → UNSAT."""
        opb = OpbBuilder()
        v1 = opb.new_var()
        opb.add_unit(v1)   # x1 = 1
        opb.add_unit(-v1)  # x1 = 0

        result = runner.solve(opb)
        assert result.satisfiable is False
        assert result.assignment is None

    def test_exactly_k_native(self, runner):
        """Exactly 3 of 5 variables true — solved as a single native PB constraint."""
        opb = OpbBuilder()
        vs = opb.new_vars(5)
        opb.exactly_k(vs, 3)

        result = runner.solve(opb)
        assert result.satisfiable is True
        assert result.assignment is not None
        true_count = sum(1 for v in vs if result.assignment.get(v, False))
        assert true_count == 3

    def test_weighted_sum_bound(self, runner):
        """Weighted sum constraint: 5*x1 + 5*x2 + 1*x3 <= 5, x1 and x2 forced true.

        x1=T, x2=T gives weight=10 > 5, so must be UNSAT.
        """
        opb = OpbBuilder()
        v1, v2, v3 = opb.new_vars(3)
        opb.weighted_sum_at_most([(v1, 5), (v2, 5), (v3, 1)], 5)
        opb.add_unit(v1)
        opb.add_unit(v2)

        result = runner.solve(opb)
        assert result.satisfiable is False

    def test_weighted_sum_sat(self, runner):
        """Weighted sum constraint: weight of chosen vars must be <= 10."""
        opb = OpbBuilder()
        vs = opb.new_vars(5)
        weights_list = [1, 2, 3, 4, 5]
        opb.exactly_k(vs, 2)
        opb.weighted_sum_at_most(list(zip(vs, weights_list)), 3)

        result = runner.solve(opb)
        assert result.satisfiable is True
        assert result.assignment is not None
        chosen = [v for v in vs if result.assignment.get(v, False)]
        assert len(chosen) == 2
        total_weight = sum(weights_list[vs.index(v)] for v in chosen)
        assert total_weight <= 3

    def test_runtime_seconds_populated(self, runner):
        opb = OpbBuilder()
        v = opb.new_var()
        opb.add_unit(v)
        result = runner.solve(opb)
        assert result.runtime_seconds >= 0.0

    def test_stdout_captured(self, runner):
        opb = OpbBuilder()
        v = opb.new_var()
        opb.add_unit(v)
        result = runner.solve(opb)
        assert "SATISFIABLE" in result.stdout

