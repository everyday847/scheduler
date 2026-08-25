"""The windowed_count_band archetype — a relation (at_least / at_most / exactly)
on the COUNT of a shift-set within a window, over one var group.

Two weekly kinds share this shape:
  * shift_total       — group = one fellow's shift vars across the window.
  * staffing_per_week — group = all selected fellows' shift vars in one week.

encode reproduces the solver's `_add_cardinality_constraint` against the sink:
  HARD ⇒ one native cardinality line (at_least / at_most / exactly_k).
  SOFT ⇒ a DEVIATION-SCALED penalty — one penalized unary slack per unit of
  allowed deviation, so cost == weight * |count - target| (the optimizer is
  pulled toward the target, not charged a flat per-constraint fee). `exactly`
  penalizes both directions (shortfall and excess).

evaluate counts the shift-set in the window and reports a violation when the
relation is broken. SOFT here is co-located so the contract test can pin
forced-slack count == evaluate's |count - target| (the deviation magnitude).
"""

from __future__ import annotations

from schedule_rules.sink import ConstraintSink
from schedule_rules.strength import HARD, Strength
from schedule_rules.view import ScheduleView


def _relation_violated(relation: str, actual: int, bound: int) -> bool:
    if relation == "at_least":
        return actual < bound
    if relation == "at_most":
        return actual > bound
    if relation == "exactly":
        return actual != bound
    raise ValueError(f"Unknown relation: {relation}")


class WindowedCountBandCriterion:
    name = "windowed_count_band"

    def count(self, view: ScheduleView, fellow: str, shifts, window: range) -> int:
        target = set(shifts)
        return sum(
            1 for w in window
            for s in view.all_services(w, fellow) if s in target
        )

    def evaluate(
        self,
        view: ScheduleView,
        *,
        groups: list[tuple[str, list[str]]],
        shifts,
        window: range,
        relation: str,
        count: int,
    ) -> list[tuple[str, int]]:
        """*groups* is a list of (label, fellows) — for shift_total each group is
        one fellow over the window; for staffing_per_week each group is all
        fellows in one week. Returns (label, actual) for each violated group."""
        target = set(shifts)
        out: list[tuple[str, int]] = []
        for label, fellows in groups:
            total = sum(
                1 for w in window for f in fellows
                for s in view.all_services(w, f) if s in target
            )
            if _relation_violated(relation, total, count):
                out.append((label, total))
        return out

    def encode(
        self,
        sink: ConstraintSink,
        *,
        vars: list[int],
        relation: str,
        target: int,
        strength: Strength,
        weight: int,
        max_violation: int | None = None,
    ) -> None:
        """Emit a cardinality band over *vars* — faithfully mirroring the solver's
        _add_cardinality_constraint (HARD native line / SOFT deviation slacks)."""
        if not vars:
            return

        if strength is HARD:
            if relation == "exactly":
                sink.exactly_k(vars, target)
            elif relation == "at_least":
                if target <= len(vars):
                    sink.at_least_k(vars, target)
            elif relation == "at_most":
                sink.at_most_k(vars, target)
            return

        n = len(vars)

        def _slacks(k: int) -> list[int]:
            s = [sink.new_var() for _ in range(max(0, k))]
            for v in s:
                sink.soft(v, weight)
            return s

        if relation == "at_least":
            if target <= 0:
                return
            k = target if max_violation is None else min(target, max_violation)
            slacks = _slacks(k)
            if slacks or target <= n:
                sink.weighted_sum_at_least(
                    [(x, 1) for x in vars] + [(s, 1) for s in slacks], target
                )
        elif relation == "at_most":
            if n <= target:
                return  # trivially satisfied
            k = (n - target) if max_violation is None else min(n - target, max_violation)
            slacks = _slacks(k)
            sink.weighted_sum_at_least(
                [(-x, 1) for x in vars] + [(s, 1) for s in slacks], n - target
            )
        elif relation == "exactly":
            if target > 0:
                ku = target if max_violation is None else min(target, max_violation)
                under = _slacks(ku)
                sink.weighted_sum_at_least(
                    [(x, 1) for x in vars] + [(s, 1) for s in under], target
                )
            if n > target:
                ko = (n - target) if max_violation is None else min(n - target, max_violation)
                over = _slacks(ko)
                sink.weighted_sum_at_least(
                    [(-x, 1) for x in vars] + [(s, 1) for s in over], n - target
                )
