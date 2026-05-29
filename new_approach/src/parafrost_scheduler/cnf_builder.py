"""CNF builder with Sinz sequential counter cardinality encodings.

Provides CnfBuilder, a helper for constructing CNF formulae programmatically
and emitting them in DIMACS format for consumption by a SAT solver.
"""

from __future__ import annotations

from pathlib import Path


class CnfBuilder:
    """Build a CNF formula incrementally, then serialise to DIMACS.

    Variable indices follow the DIMACS convention: they are 1-indexed positive
    integers.  A *literal* is a variable index (positive → variable true) or
    its negation (negative → variable false).
    """

    def __init__(self) -> None:
        self._next_var: int = 1
        self._clauses: list[list[int]] = []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def num_vars(self) -> int:
        """Number of variables allocated so far."""
        return self._next_var - 1

    @property
    def num_clauses(self) -> int:
        """Number of clauses added so far."""
        return len(self._clauses)

    # ------------------------------------------------------------------
    # Variable allocation
    # ------------------------------------------------------------------

    def new_var(self) -> int:
        """Allocate one fresh variable and return its index (1-based)."""
        v = self._next_var
        self._next_var += 1
        return v

    def new_vars(self, n: int) -> list[int]:
        """Allocate *n* fresh variables and return their indices as a list."""
        start = self._next_var
        self._next_var += n
        return list(range(start, start + n))

    # ------------------------------------------------------------------
    # Clause management
    # ------------------------------------------------------------------

    def add_clause(self, literals: list[int]) -> None:
        """Add a disjunctive clause (logical OR of the given literals)."""
        self._clauses.append(list(literals))

    # ------------------------------------------------------------------
    # Cardinality constraints
    # ------------------------------------------------------------------

    def exactly_one(self, literals: list[int]) -> None:
        """Encode *exactly one* of *literals* is true.

        Uses:
        - one at-least-one clause (all literals in one clause)
        - pairwise at-most-one binary clauses  [-li, -lj] for i < j
        """
        lits = list(literals)
        # At-least-one
        self.add_clause(lits)
        # At-most-one (pairwise)
        for i in range(len(lits)):
            for j in range(i + 1, len(lits)):
                self.add_clause([-lits[i], -lits[j]])

    def at_most_k(self, literals: list[int], k: int) -> None:
        """Encode at most *k* of *literals* are true.

        Strategy:
        - k >= n: trivially satisfied, no-op
        - k == 0: unit clause forbidding each literal
        - k == 1 and n <= 20: pairwise binary clauses
        - otherwise: Sinz sequential counter encoding
        """
        lits = list(literals)
        n = len(lits)

        if k >= n:
            return

        if k == 0:
            for lit in lits:
                self.add_clause([-lit])
            return

        if k == 1 and n <= 20:
            # Pairwise AMO — no auxiliary variables
            for i in range(n):
                for j in range(i + 1, n):
                    self.add_clause([-lits[i], -lits[j]])
            return

        # --- Sinz sequential counter -----------------------------------
        # register[i][j] means "at least j+1 of lits[0..i+1] are true"
        # i ranges 0..n-2, j ranges 0..k-1
        register = [self.new_vars(k) for _ in range(n - 1)]

        # Row 0
        self.add_clause([-lits[0], register[0][0]])
        for j in range(1, k):
            self.add_clause([-register[0][j]])

        # Rows 1 .. n-2
        for i in range(1, n - 1):
            # If lit true → count >= 1
            self.add_clause([-lits[i], register[i][0]])
            # Propagate count >= 1 from previous row
            self.add_clause([-register[i - 1][0], register[i][0]])
            for j in range(1, k):
                # lit true + count >= j → count >= j+1
                self.add_clause([-lits[i], -register[i - 1][j - 1], register[i][j]])
                # Propagate count >= j+1 from previous row
                self.add_clause([-register[i - 1][j], register[i][j]])
            # Overflow check: lit + count >= k is forbidden
            self.add_clause([-lits[i], -register[i - 1][k - 1]])

        # Last literal overflow check
        self.add_clause([-lits[n - 1], -register[n - 2][k - 1]])

    def at_least_k(self, literals: list[int], k: int) -> None:
        """Encode at least *k* of *literals* are true.

        If k <= 0: no-op.
        If k == n: each literal is forced true via a unit clause.
        Otherwise: encode via complement — at_most(n-k) of the negations.
        """
        lits = list(literals)
        n = len(lits)

        if k <= 0:
            return

        if k == n:
            for lit in lits:
                self.add_clause([lit])
            return

        # Complement encoding: ¬x1,...,¬xn satisfy at_most(n-k)
        self.at_most_k([-lit for lit in lits], n - k)

    def exactly_k(self, literals: list[int], k: int) -> None:
        """Encode exactly *k* of *literals* are true."""
        self.at_most_k(literals, k)
        self.at_least_k(literals, k)

    def weighted_sum_at_most(
        self, weighted_lits: list[tuple[int, int]], bound: int
    ) -> None:
        """Encode  sum_i (weight_i * lit_i) <= bound.

        For each (literal, weight) pair, *weight* auxiliary variables are
        created.  Each auxiliary is implied by the literal:

            [-lit, aux_j]   for j in 0..weight-1

        This forces all *weight* auxiliaries true when the literal is true.
        The solver is free to set them false when the literal is false.
        Then  at_most_k(all_auxiliaries, bound)  completes the encoding.
        """
        auxiliaries: list[int] = []
        for lit, weight in weighted_lits:
            aux_vars = self.new_vars(weight)
            for aux in aux_vars:
                self.add_clause([-lit, aux])
            auxiliaries.extend(aux_vars)
        self.at_most_k(auxiliaries, bound)

    # ------------------------------------------------------------------
    # DIMACS output
    # ------------------------------------------------------------------

    def to_dimacs(self) -> str:
        """Return the formula as a DIMACS-format string."""
        lines: list[str] = [f"p cnf {self.num_vars} {self.num_clauses}"]
        for clause in self._clauses:
            lines.append(" ".join(str(lit) for lit in clause) + " 0")
        return "\n".join(lines)

    def write_dimacs(self, path: Path) -> None:
        """Write the DIMACS representation to *path*."""
        Path(path).write_text(self.to_dimacs())
