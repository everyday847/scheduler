"""OpbConstraintSink — the solver-side adapter satisfying schedule_rules.ConstraintSink.

Delegates the emit methods to an underlying OpbBuilder and owns the soft-penalty
channel the builder lacks (soft violations are an external (lit, weight) list
fed to the objective). This is the seam that lets the encoder hand a Rule its
builder without the rules package depending on the builder.
"""

from __future__ import annotations

from parafrost_scheduler.opb_encoder import OpbBuilder


class OpbConstraintSink:
    def __init__(self, opb: OpbBuilder, soft_violations: list[tuple[int, int]]) -> None:
        self._opb = opb
        self._soft = soft_violations

    def new_var(self) -> int:
        return self._opb.new_var()

    def add_unit(self, lit: int) -> None:
        self._opb.add_unit(lit)

    def at_most_k(self, lits: list[int], k: int) -> None:
        self._opb.at_most_k(lits, k)

    def at_least_k(self, lits: list[int], k: int) -> None:
        self._opb.at_least_k(lits, k)

    def exactly_k(self, lits: list[int], k: int) -> None:
        self._opb.exactly_k(lits, k)

    def weighted_sum_at_most(self, weighted_lits: list[tuple[int, int]], bound: int) -> None:
        self._opb.weighted_sum_at_most(weighted_lits, bound)

    def weighted_sum_at_least(self, weighted_lits: list[tuple[int, int]], bound: int) -> None:
        self._opb.weighted_sum_at_least(weighted_lits, bound)

    def soft(self, lit: int, weight: int) -> None:
        self._soft.append((lit, weight))
