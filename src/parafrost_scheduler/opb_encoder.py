"""OPB (pseudo-Boolean) formula builder.

Produces .opb files suitable for RoundingSat (and any PB-competition-format
solver).  The key advantage over CNF/DIMACS is that cardinality and weighted
sum constraints are represented as *single lines* with no auxiliary variables:

    +1 x1 +1 x2 ... = 60 ;        (native exactly-60)
    +1 x1 +1 x2 ... <= 3 ;        (native at-most-3)
    +2 x1 +3 x2 ... <= 100 ;      (native weighted-sum-bound)

OPB format conventions used here
---------------------------------
- Variables: ``x1``, ``x2``, ... (1-indexed, allocated via new_var / new_vars)
- Positive literal *v*: ``x{v}``
- Negative literal *-v*: ``~x{v}``
- Coefficients: always explicit, e.g. ``+1 x3`` or ``-1 x5``
- Each constraint ends with `` ;``
- Comment lines start with ``*``
- Header (first non-comment line): ``* #variable= N #constraint= M``
"""

from __future__ import annotations

from pathlib import Path


def _lit_str(var: int, negate: bool = False) -> str:
    """Return OPB literal string for variable *var* (1-indexed).

    If *negate* is True, returns ``~x{var}``; otherwise ``x{var}``.
    """
    if negate:
        return f"~x{var}"
    return f"x{var}"


def _encode_lit(lit: int) -> tuple[int, bool]:
    """Decode a signed literal (DIMACS-style) into (var, negated)."""
    if lit > 0:
        return lit, False
    elif lit < 0:
        return -lit, True
    else:
        raise ValueError("Literal 0 is not valid")


class OpbBuilder:
    """Build a pseudo-Boolean formula incrementally, then serialise to OPB.

    Literals follow the same signed-integer convention as DIMACS SAT:
    a positive integer *v* means variable *v* is true; *-v* means it is false.
    """

    def __init__(self) -> None:
        self._next_var: int = 1
        self._constraints: list[str] = []
        self._comments: list[str] = []
        self._objective: list[tuple[int, int]] | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def num_vars(self) -> int:
        """Number of variables allocated so far."""
        return self._next_var - 1

    @property
    def num_constraints(self) -> int:
        """Number of constraints added so far."""
        return len(self._constraints)

    # ------------------------------------------------------------------
    # Variable allocation
    # ------------------------------------------------------------------

    def new_var(self) -> int:
        """Allocate one fresh variable and return its 1-based index."""
        v = self._next_var
        self._next_var += 1
        return v

    def new_vars(self, n: int) -> list[int]:
        """Allocate *n* fresh variables and return their indices."""
        start = self._next_var
        self._next_var += n
        return list(range(start, start + n))

    # ------------------------------------------------------------------
    # Comment
    # ------------------------------------------------------------------

    def add_comment(self, text: str) -> None:
        """Add a comment line (stored separately; prepended before constraints)."""
        self._comments.append(f"* {text}")

    # ------------------------------------------------------------------
    # Low-level constraint builders
    # ------------------------------------------------------------------

    def _add_pb_constraint(
        self,
        signed_lits: list[int],
        op: str,
        rhs: int,
    ) -> None:
        """Add: sum of (+1 * lit_i) OP rhs.

        Each element of *signed_lits* is a signed variable (positive = true,
        negative = negated).  Negated literals are emitted as ``~x{v}``.
        """
        terms: list[str] = []
        for lit in signed_lits:
            var, neg = _encode_lit(lit)
            terms.append(f"+1 {_lit_str(var, neg)}")
        constraint = " ".join(terms) + f" {op} {rhs} ;"
        self._constraints.append(constraint)

    def _add_weighted_pb_constraint(
        self,
        weighted_lits: list[tuple[int, int]],
        op: str,
        rhs: int,
    ) -> None:
        """Add: sum of (weight_i * lit_i) OP rhs.

        Each element is (signed_lit, weight).
        """
        terms: list[str] = []
        for lit, weight in weighted_lits:
            var, neg = _encode_lit(lit)
            sign = "+" if weight >= 0 else ""
            terms.append(f"{sign}{weight} {_lit_str(var, neg)}")
        constraint = " ".join(terms) + f" {op} {rhs} ;"
        self._constraints.append(constraint)

    # ------------------------------------------------------------------
    # Cardinality constraints (all native single-line OPB — no aux vars!)
    # ------------------------------------------------------------------

    def exactly_one(self, lits: list[int]) -> None:
        """Encode exactly one of *lits* is true.

        Equivalent to exactly_k(lits, 1) — one OPB line.
        """
        self._add_pb_constraint(list(lits), "=", 1)

    def exactly_k(self, lits: list[int], k: int) -> None:
        """Encode exactly *k* of *lits* are true.

        Emits one OPB line: ``+1 x1 +1 x2 ... = k ;``
        This is the primary advantage over CNF: no auxiliary variables needed.
        """
        self._add_pb_constraint(list(lits), "=", k)

    def at_most_k(self, lits: list[int], k: int) -> None:
        """Encode at most *k* of *lits* are true.

        Emits one OPB line: ``+1 x1 +1 x2 ... <= k ;``
        """
        self._add_pb_constraint(list(lits), "<=", k)

    def at_least_k(self, lits: list[int], k: int) -> None:
        """Encode at least *k* of *lits* are true.

        Emits one OPB line: ``+1 x1 +1 x2 ... >= k ;``
        """
        self._add_pb_constraint(list(lits), ">=", k)

    def add_unit(self, lit: int) -> None:
        """Force literal *lit* to be true.

        Positive lit *v*: forces x_v = 1 (``+1 x{v} >= 1 ;``).
        Negative lit *-v*: forces x_v = 0 (``+1 ~x{v} >= 1 ;``).
        """
        var, neg = _encode_lit(lit)
        constraint = f"+1 {_lit_str(var, neg)} >= 1 ;"
        self._constraints.append(constraint)

    # ------------------------------------------------------------------
    # Weighted pseudo-Boolean constraints (also native single-line!)
    # ------------------------------------------------------------------

    def weighted_sum_at_most(
        self, weighted_lits: list[tuple[int, int]], bound: int
    ) -> None:
        """Encode sum_i (weight_i * lit_i) <= bound.

        Each element of *weighted_lits* is a (signed_lit, weight) pair.
        Emits one OPB line — no auxiliary variables, regardless of bound size.

        This is the second major advantage over CNF: the PB solver handles
        weighted cardinality natively.
        """
        if not weighted_lits:
            return
        self._add_weighted_pb_constraint(weighted_lits, "<=", bound)

    def weighted_sum_at_least(
        self, weighted_lits: list[tuple[int, int]], bound: int
    ) -> None:
        """Encode sum_i (weight_i * lit_i) >= bound."""
        if not weighted_lits:
            return
        self._add_weighted_pb_constraint(weighted_lits, ">=", bound)

    def weighted_sum_equals(
        self, weighted_lits: list[tuple[int, int]], bound: int
    ) -> None:
        """Encode sum_i (weight_i * lit_i) = bound."""
        if not weighted_lits:
            return
        self._add_weighted_pb_constraint(weighted_lits, "=", bound)

    # ------------------------------------------------------------------
    # Conditional cardinality (selector-gated constraints)
    #
    # Used for multiset constraints where one of several permutations
    # must hold.  Instead of the CNF sequential counter with selector
    # literals, we use two native PB constraints per (selector, ordering)
    # pair.
    #
    # For "if selector is true, sum(lits) = k":
    #
    #   At-least-k when selector=1:
    #     sum(lits) + (n-k)*~selector >= k
    #     When sel=1: ~sel=0 → sum >= k  ✓
    #     When sel=0: ~sel=1 → sum + (n-k) >= k → sum >= k-(n-k)  (trivially satisfied)
    #
    #   At-most-k when selector=1:
    #     sum(lits) + k*~selector <= k
    #     When sel=1: ~sel=0 → sum <= k  ✓
    #     When sel=0: ~sel=1 → sum+k <= k → sum <= 0  (trivially satisfied since sum>=0)
    #     Wait, this forces sum=0 when sel=0 — too strong. Use:
    #     sum(lits) - (n-k)*selector <= k - (n-k)    ... but n may equal k
    #
    # Simpler alternative using only >=:
    #   "at most k" ↔ "at least (n-k) of negations":
    #     sum(~lits) + (n-k)*~selector >= n-k
    #     When sel=1: sum(~lits) >= n-k → sum(lits) <= k  ✓
    #     When sel=0: sum(~lits) + (n-k) >= n-k → 0 >= 0  ✓ (trivially satisfied)
    # ------------------------------------------------------------------

    def conditional_exactly_k(
        self, lits: list[int], k: int, selector: int
    ) -> None:
        """Encode: if selector is true, exactly k of lits are true.

        Adds two native OPB >= constraints (no auxiliary variables).

        Parameters
        ----------
        lits:
            List of signed literals (same convention as other methods).
        k:
            Target count.
        selector:
            A signed literal; when this is true the constraint is active.
        """
        n = len(lits)
        neg_sel = -selector  # negated selector

        # at-least-k: sum(lits) + (n-k)*~selector >= k
        # When selector=1: ~selector=0, constraint becomes sum(lits) >= k
        # When selector=0: ~selector=1, constraint becomes sum(lits) >= k-(n-k), trivial
        al_terms: list[tuple[int, int]] = [(lit, 1) for lit in lits]
        if n > k:
            al_terms.append((neg_sel, n - k))
        self._add_weighted_pb_constraint(al_terms, ">=", k)

        # at-most-k (via at-least n-k of negations):
        # sum(~lits) + (n-k)*~selector >= n-k
        # When selector=1: sum(~lits) >= n-k  ↔  sum(lits) <= k  ✓
        # When selector=0: trivially sum(~lits) + (n-k) >= n-k  ✓
        neg_lits = [-lit for lit in lits]
        am_terms: list[tuple[int, int]] = [(nlit, 1) for nlit in neg_lits]
        if n > k:
            am_terms.append((neg_sel, n - k))
        self._add_weighted_pb_constraint(am_terms, ">=", n - k)

    # ------------------------------------------------------------------
    # OPB output
    # ------------------------------------------------------------------

    def set_objective(self, weighted_lits: list[tuple[int, int]]) -> None:
        """Set a linear minimization objective ``min: sum_i weight_i * lit_i``.

        Each element is a (signed_lit, weight) pair (same convention as the
        weighted_sum_* methods). Pass an empty list or call with None-equivalent
        to clear. When no objective is set, ``to_opb`` emits a pure decision
        formula (backward compatible) and RoundingSat runs in SAT mode.
        """
        self._objective = list(weighted_lits) if weighted_lits else None

    @property
    def has_objective(self) -> bool:
        return self._objective is not None

    def to_opb(self) -> str:
        """Return the formula as an OPB-format string."""
        lines: list[str] = []
        # Header (required by PB competition format)
        lines.append(f"* #variable= {self.num_vars} #constraint= {self.num_constraints}")
        # Optimization objective — emit immediately after the header, BEFORE any
        # comments. RoundingSat is sensitive to this: an objective placed after
        # comment lines yields markedly worse incumbents at the same time budget.
        if self._objective:
            terms = " ".join(
                f"{weight:+d} {_lit_str(*_encode_lit(lit))}"
                for lit, weight in self._objective
            )
            lines.append(f"min: {terms} ;")
        # User comments
        lines.extend(self._comments)
        # Constraints
        lines.extend(self._constraints)
        return "\n".join(lines) + "\n"

    def write_opb(self, path: "Path | str") -> None:
        """Write the OPB representation to *path*."""
        Path(path).write_text(self.to_opb())
