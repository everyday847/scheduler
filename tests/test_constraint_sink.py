"""Slice 0 contract: the ConstraintSink seam.

`schedule_rules` is the dependency-free home of the Rule Shape vocabulary. A
Rule's encode manifestation writes to a `ConstraintSink` (a Protocol), never to
the concrete `OpbBuilder`, so `schedule_rules` carries no dependency on the
solver package and the import cycle (solver -> model, model -> solver) cannot
form. The solver package supplies `OpbConstraintSink`, an adapter that:

  * delegates every emit method to an underlying OpbBuilder (verified by output
    equality, not by spying on calls), and
  * owns `soft(lit, weight)` — the soft-penalty channel OpbBuilder lacks today
    (soft violations are an external list), appending (lit, weight).
"""

from __future__ import annotations

import subprocess
import sys

from schedule_rules.sink import ConstraintSink
from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.constraint_sink import OpbConstraintSink


def _direct() -> OpbBuilder:
    return OpbBuilder()


def _via_sink() -> tuple[OpbConstraintSink, OpbBuilder, list[tuple[int, int]]]:
    opb = OpbBuilder()
    soft: list[tuple[int, int]] = []
    return OpbConstraintSink(opb, soft), opb, soft


class TestProtocolConformance:
    def test_adapter_is_a_constraint_sink(self):
        sink, _opb, _soft = _via_sink()
        assert isinstance(sink, ConstraintSink)


class TestEmitDelegation:
    """Driving the sink must produce byte-identical OPB to driving the builder."""

    def test_new_var_allocates_through_builder(self):
        sink, opb, _ = _via_sink()
        v1 = sink.new_var()
        v2 = sink.new_var()
        assert (v1, v2) == (1, 2)
        assert opb.num_vars == 2

    def test_add_unit_matches_direct(self):
        direct = _direct()
        direct.add_unit(-3)
        sink, opb, _ = _via_sink()
        sink.add_unit(-3)
        assert opb.to_opb() == direct.to_opb()

    def test_at_most_k_matches_direct(self):
        direct = _direct()
        direct.at_most_k([1, 2, 3], 1)
        sink, opb, _ = _via_sink()
        sink.at_most_k([1, 2, 3], 1)
        assert opb.to_opb() == direct.to_opb()

    def test_at_least_k_matches_direct(self):
        direct = _direct()
        direct.at_least_k([1, 2, 3], 2)
        sink, opb, _ = _via_sink()
        sink.at_least_k([1, 2, 3], 2)
        assert opb.to_opb() == direct.to_opb()

    def test_weighted_sum_at_most_matches_direct(self):
        direct = _direct()
        direct.weighted_sum_at_most([(1, 1), (2, 1), (-3, 1)], 2)
        sink, opb, _ = _via_sink()
        sink.weighted_sum_at_most([(1, 1), (2, 1), (-3, 1)], 2)
        assert opb.to_opb() == direct.to_opb()

    def test_weighted_sum_at_least_matches_direct(self):
        direct = _direct()
        direct.weighted_sum_at_least([(1, 1), (-2, 1)], 1)
        sink, opb, _ = _via_sink()
        sink.weighted_sum_at_least([(1, 1), (-2, 1)], 1)
        assert opb.to_opb() == direct.to_opb()


class TestSoftChannel:
    """OpbBuilder has no soft penalty concept; the sink owns the list."""

    def test_soft_appends_lit_weight_pair(self):
        sink, _opb, soft = _via_sink()
        ind = sink.new_var()
        sink.soft(ind, 5)
        assert soft == [(ind, 5)]

    def test_soft_does_not_emit_a_hard_constraint(self):
        sink, opb, _soft = _via_sink()
        sink.soft(sink.new_var(), 5)
        assert opb.num_constraints == 0

    def test_multiple_soft_terms_accumulate_in_order(self):
        sink, _opb, soft = _via_sink()
        a, b = sink.new_var(), sink.new_var()
        sink.soft(a, 10)
        sink.soft(b, 1)
        assert soft == [(a, 10), (b, 1)]


class TestRulesHomeIsDependencyFree:
    """The load-bearing invariant: importing schedule_rules must NOT pull in the
    solver package. Checked in a clean subprocess so other tests' imports can't
    mask a violation."""

    def test_importing_schedule_rules_does_not_import_solver_package(self):
        code = (
            "import sys; import schedule_rules.sink; "
            "leaked = [m for m in sys.modules "
            "if m == 'parafrost_scheduler' or m.startswith('parafrost_scheduler.')]; "
            "assert not leaked, leaked; print('OK')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True,
            env={"PYTHONPATH": "src"},
            cwd=_repo_root(),
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout


def _repo_root() -> str:
    import pathlib
    # tests/ is at repo root.
    return str(pathlib.Path(__file__).resolve().parent.parent)
