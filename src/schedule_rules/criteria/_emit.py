"""Shared encode primitives for night criteria.

The recurring shape is: a criterion fires when a NIGHT var and a CONDITION
(gating) var are both true. HARD ⇒ forbid the pair; SOFT ⇒ a fresh indicator
equal to (night AND condition) carries a penalty weight. Term-first emission
order (condition before night) matches the solver's prior helpers so relocating
a criterion onto this primitive is byte-for-byte neutral.
"""

from __future__ import annotations

from schedule_rules.sink import ConstraintSink
from schedule_rules.strength import HARD, Strength


def emit_pair(
    sink: ConstraintSink,
    *,
    condition_var: int,
    night_var: int,
    strength: Strength,
    weight: int,
) -> None:
    """Emit: (condition AND night) is a violation."""
    if strength is HARD:
        sink.at_most_k([condition_var, night_var], 1)
    else:
        ind = sink.new_var()
        sink.weighted_sum_at_most([(condition_var, 1), (night_var, 1), (-ind, 1)], 2)
        sink.weighted_sum_at_least([(condition_var, 1), (-ind, 1)], 1)
        sink.weighted_sum_at_least([(night_var, 1), (-ind, 1)], 1)
        sink.soft(ind, weight)


def emit_or(sink: ConstraintSink, term_vars: list[int]) -> int:
    """Allocate and return a var equal to OR(term_vars).

    Mirrors the encoder's prior inline construction: for each term, or_var =>
    term is NOT asserted (the relationship is or_var >= each term via the
    pairwise >=1 clauses below) and or_var <= sum(terms). With a single term the
    var IS that term (no aux), matching the prior code's len==1 short-circuit.
    """
    if len(term_vars) == 1:
        return term_vars[0]
    or_var = sink.new_var()
    for tv in term_vars:
        sink.weighted_sum_at_least([(or_var, 1), (-tv, 1)], 1)
    sink.weighted_sum_at_least([(tv, 1) for tv in term_vars] + [(-or_var, 1)], 1)
    return or_var
