"""The ConstraintSink seam.

A Rule's encode manifestation emits pseudo-Boolean form by calling these
methods, never by touching a concrete builder. The solver package supplies an
adapter (OpbConstraintSink) that satisfies this Protocol; tests may supply a
fake. The Protocol surface mirrors the subset of OpbBuilder the Rule Shapes
need, plus `soft` — the soft-penalty channel a plain builder lacks.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ConstraintSink(Protocol):
    def new_var(self) -> int:
        """Allocate one fresh variable and return its 1-based index."""
        ...

    def add_unit(self, lit: int) -> None:
        """Force signed literal *lit* true (positive = var true, negative = false)."""
        ...

    def at_most_k(self, lits: list[int], k: int) -> None:
        """At most *k* of *lits* are true."""
        ...

    def at_least_k(self, lits: list[int], k: int) -> None:
        """At least *k* of *lits* are true."""
        ...

    def weighted_sum_at_most(self, weighted_lits: list[tuple[int, int]], bound: int) -> None:
        """sum_i weight_i * lit_i <= bound."""
        ...

    def weighted_sum_at_least(self, weighted_lits: list[tuple[int, int]], bound: int) -> None:
        """sum_i weight_i * lit_i >= bound."""
        ...

    def soft(self, lit: int, weight: int) -> None:
        """Register *lit* as a soft-violation indicator carrying *weight*.

        Distinct from the emit methods: this adds no hard constraint. The sink
        accumulates (lit, weight) for the solver's objective.
        """
        ...
