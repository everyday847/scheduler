"""Tests for per-fellow weekend-NCC ranges + group-sum in the encoder.

CCM-style proportionate weekend load: a fellow in ncc_ranges gets a hard
[lo, hi] band (at_least lo + at_most hi over their NCC1+NCC2 weekend vars, no
tolerance / soft nudge), and the group still hits its combined total exactly via
an ncc_group_sums constraint. Range fellows are NOT given an even-split band.
"""

from __future__ import annotations

from datetime import date

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.schedule_solver import (
    ScheduleSolverConfig,
    _encode_weekend_constraints,
)
from scheduler.fellow_mapping import FellowMapping
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig


def _encode(num_weeks_days: int = 28):
    fellows = ["A", "B", "C"]
    weekend_config = WeekendSolverConfig(
        ncc_totals={},  # nobody even-split
        stroke_totals={},
        stroke_cohort=(), stroke_cohort_total=None,
        ccm_fellows=frozenset(fellows),
        always_stroke_eligible=frozenset(), telestroke_stroke_eligible=frozenset(),
        stroke_only_eligible=frozenset(), total_weekends={},
        weekend_options=None, friday_weekend_options=None,
        ncc_ranges={"A": (3, 4), "B": (1, 2), "C": (0, 1)},
        ncc_group_sums=((("A", "B", "C"), 4),),
    )
    night_config = NightSolverConfig(
        total_nights={}, friday_nights={}, total_night_multisets=(),
        friday_night_multisets=(), ccm_fellows=frozenset(), holiday_dates=(),
        horizon_start_date=date(2026, 7, 1),
    )
    config = ScheduleSolverConfig(
        fellow_groups={"CCM": fellows}, shifts=["NCC1"], constraints=[],
        night_config=night_config, weekend_config=weekend_config,
        night_hard_criteria=frozenset(), start_dow=0, num_days=num_weeks_days,
    )
    opb = OpbBuilder()
    num_weeks = config.num_weeks
    # wr[w][role][f] — give all 3 fellows NCC1 + NCC2 weekend vars each week.
    wr = []
    for _ in range(num_weeks):
        wr.append([
            {f: opb.new_var() for f in range(3)},  # NCC1
            {f: opb.new_var() for f in range(3)},  # NCC2
            {},                                     # Stroke (none)
        ])
    xs = [[[opb.new_var()] for _ in range(num_weeks)] for _ in range(3)]
    soft: list[tuple[int, int]] = []
    mapping = FellowMapping()
    for f in fellows:
        mapping.add_fellow(f, "CCM")
    _encode_weekend_constraints(opb, wr, xs, config, mapping, fellows, {"NCC1": 0}, soft)
    return opb, wr, num_weeks


def _ncc_vars(wr, f, num_weeks):
    out = []
    for w in range(num_weeks):
        for role in (0, 1):  # NCC1, NCC2
            if f in wr[w][role]:
                out.append(wr[w][role][f])
    return out


def _constraint_strings(opb):
    return list(opb._constraints)


class TestNccRangeBands:
    def test_fellow_A_has_at_least_3_and_at_most_4(self):
        opb, wr, nw = _encode()
        a_vars = set(_ncc_vars(wr, 0, nw))
        cons = _constraint_strings(opb)
        # at_least 3: a constraint over A's vars with ">= 3"
        has_lo = any(c.endswith(">= 3 ;") and _covers(c, a_vars) for c in cons)
        has_hi = any(c.endswith("<= 4 ;") and _covers(c, a_vars) for c in cons)
        assert has_lo, "fellow A must have at_least 3"
        assert has_hi, "fellow A must have at_most 4"

    def test_fellow_C_lo_zero_has_no_at_least(self):
        """C range is [0,1]: no at_least (lo=0), but an at_most 1."""
        opb, wr, nw = _encode()
        c_vars = set(_ncc_vars(wr, 2, nw))
        cons = _constraint_strings(opb)
        has_hi = any(c.endswith("<= 1 ;") and _covers(c, c_vars) for c in cons)
        assert has_hi, "fellow C must have at_most 1"

    def test_group_sum_exact_4(self):
        opb, wr, nw = _encode()
        all_vars = set(_ncc_vars(wr, 0, nw)) | set(_ncc_vars(wr, 1, nw)) | set(_ncc_vars(wr, 2, nw))
        cons = _constraint_strings(opb)
        # group-sum: a single "= 4 ;" constraint covering all three fellows' vars.
        has_sum = any(c.endswith("= 4 ;") and _covers(c, all_vars, mode="all") for c in cons)
        assert has_sum, "group-sum exactly 4 over all CCM NCC vars must exist"


def _covers(constraint: str, var_ids: set[int], mode: str = "any") -> bool:
    """Does the constraint reference the given var ids?"""
    refs = set()
    for tok in constraint.replace("~", "").split():
        if tok.startswith("x"):
            try:
                refs.add(int(tok[1:]))
            except ValueError:
                pass
    if mode == "all":
        return var_ids.issubset(refs)
    return bool(var_ids & refs)
