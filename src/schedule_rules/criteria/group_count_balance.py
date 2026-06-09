"""The group_count_balance archetype — comparable per-fellow counts across a group.

"The per-fellow counts of shift-set S within window W, across fellows in a group,
differ pairwise by at most max_difference." Distinct from windowed_balance, which
balances ONE fellow across TWO windows; this balances counts ACROSS fellows in
ONE window. Soft by default — a violation per fellow pair whose counts differ by
more than max_difference.

Motivating instance: STROKE fellows should have comparable on-service
(Stroke/NCC1/NCC2/Swing) loads before ABPN (weeks [0,11)).

encode emits, per fellow pair (i, j), the two-direction bound
|sum(vars_i) - sum(vars_j)| <= max_difference, mirroring the solver's existing
_encode_balance_constraint (kept here against the ConstraintSink so the leaf
carries no solver-package dependency).
"""

from __future__ import annotations

from itertools import combinations

from schedule_rules.sink import ConstraintSink
from schedule_rules.strength import HARD, Strength
from schedule_rules.view import ScheduleView


class GroupCountBalanceCriterion:
    name = "group_count_balance"

    def _count(self, view: ScheduleView, fellow: str, shifts, window) -> int:
        target = set(shifts)
        lo, hi = window
        return sum(
            1 for w in range(lo, min(hi, view.num_weeks))
            for s in view.all_services(w, fellow) if s in target
        )

    def evaluate(
        self,
        view: ScheduleView,
        *,
        fellows: list[str],
        shifts,
        window: tuple[int, int],
        max_difference: int,
    ) -> list[tuple[str, str]]:
        """Return the fellow pairs whose in-window counts differ by more than
        max_difference (empty ⇒ balanced)."""
        counts = {f: self._count(view, f, shifts, window) for f in fellows}
        out = []
        for a, b in combinations(fellows, 2):
            if abs(counts[a] - counts[b]) > max_difference:
                out.append((a, b))
        return out

    def encode(
        self,
        sink: ConstraintSink,
        *,
        fellow_var_lists: list[list[int]],
        max_difference: int,
        strength: Strength,
        weight: int,
    ) -> None:
        """fellow_var_lists[i] = the on-service vars for fellow i within the
        window. Emits |sum(i) - sum(j)| <= max_difference per pair."""
        for vars_a, vars_b in combinations(fellow_var_lists, 2):
            self._emit_pair_balance(sink, vars_a, vars_b, max_difference, strength, weight)

    def _emit_pair_balance(self, sink, vars_a, vars_b, max_diff, strength, weight):
        if not vars_a and not vars_b:
            return
        n_a, n_b = len(vars_a), len(vars_b)
        if strength is HARD:
            # sum(a) - sum(b) <= max_diff  ⟺  sum(a) + sum(~b) <= max_diff + n_b
            sink.weighted_sum_at_most(
                [(v, 1) for v in vars_a] + [(-v, 1) for v in vars_b], max_diff + n_b)
            sink.weighted_sum_at_most(
                [(v, 1) for v in vars_b] + [(-v, 1) for v in vars_a], max_diff + n_a)
        else:
            ind = sink.new_var()
            big_m = max(n_a, n_b)
            # ind bypasses both directions when the pair is allowed to exceed.
            sink.weighted_sum_at_most(
                [(v, 1) for v in vars_a] + [(-v, 1) for v in vars_b] + [(-ind, big_m)],
                max_diff + n_b + big_m)
            sink.weighted_sum_at_most(
                [(v, 1) for v in vars_b] + [(-v, 1) for v in vars_a] + [(-ind, big_m)],
                max_diff + n_a + big_m)
            sink.soft(ind, weight)
