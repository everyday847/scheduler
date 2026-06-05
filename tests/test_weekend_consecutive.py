"""Tests for the no-consecutive-weekends constraint hardness flag.

A fellow should not work two weekend call roles in back-to-back weeks. This was
historically a soft (deviation-penalized) distribution constraint; the
``weekend_consecutive_hard`` config flag makes it a hard ``at_most 1`` over each
adjacent weekend pair so the optimizer can never place consecutive weekends
(removing the need for a more complex "two straight weekends => mandatory time
off after" rule).
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


def _make_config(*, consecutive_hard: bool, num_days: int = 21) -> ScheduleSolverConfig:
    night_config = NightSolverConfig(
        total_nights={}, friday_nights={},
        total_night_multisets=(), friday_night_multisets=(),
        ccm_fellows=frozenset(), holiday_dates=(),
        horizon_start_date=date(2026, 7, 1),
    )
    weekend_config = WeekendSolverConfig(
        ncc_totals={}, stroke_totals={}, stroke_cohort=(),
        stroke_cohort_total=None, ccm_fellows=frozenset(),
        always_stroke_eligible=frozenset(), telestroke_stroke_eligible=frozenset(),
        stroke_only_eligible=frozenset(), total_weekends={},
        weekend_options=None, friday_weekend_options=None,
    )
    return ScheduleSolverConfig(
        fellow_groups={"NCC_SR": ["Alice"]},
        shifts=["NCC1"],
        constraints=[],
        night_config=night_config,
        weekend_config=weekend_config,
        night_hard_criteria=frozenset(),
        start_dow=0,
        num_days=num_days,
        weekend_consecutive_hard=consecutive_hard,
    )


def _make_wr(opb: OpbBuilder, num_weeks: int) -> list[list[dict[int, int]]]:
    """One fellow (index 0) eligible for all three weekend roles each week."""
    wr = []
    for _ in range(num_weeks):
        wr.append([{0: opb.new_var()} for _ in range(3)])
    return wr


def _make_xs(opb: OpbBuilder, num_weeks: int) -> list[list[list[int]]]:
    return [[[opb.new_var()] for _ in range(num_weeks)]]


def _encode(consecutive_hard: bool):
    config = _make_config(consecutive_hard=consecutive_hard)
    opb = OpbBuilder()
    num_weeks = config.num_weeks
    wr = _make_wr(opb, num_weeks)
    xs = _make_xs(opb, num_weeks)
    shift_idx = {"NCC1": 0}
    soft: list[tuple[int, int]] = []
    mapping = FellowMapping()
    mapping.add_fellow("Alice", "NCC_SR")
    _encode_weekend_constraints(opb, wr, xs, config, mapping, ["Alice"], shift_idx, soft)
    return opb, soft


class TestConsecutiveWeekendDefaults:
    def test_consecutive_hard_by_default(self):
        from scheduler.night_call_types import NightSolverConfig as _NC
        from scheduler.weekend_call_types import WeekendSolverConfig as _WC
        cfg = ScheduleSolverConfig(
            fellow_groups={"NCC_SR": ["A"]}, shifts=["NCC1"], constraints=[],
            night_config=_NC(total_nights={}, friday_nights={},
                             total_night_multisets=(), friday_night_multisets=(),
                             ccm_fellows=frozenset(), holiday_dates=(),
                             horizon_start_date=date(2026, 7, 1)),
            weekend_config=_WC(ncc_totals={}, stroke_totals={}, stroke_cohort=(),
                               stroke_cohort_total=None, ccm_fellows=frozenset(),
                               always_stroke_eligible=frozenset(),
                               telestroke_stroke_eligible=frozenset(),
                               stroke_only_eligible=frozenset(), total_weekends={},
                               weekend_options=None, friday_weekend_options=None),
            num_days=14,
        )
        assert cfg.weekend_consecutive_hard is True


class TestConsecutiveWeekendHardness:
    """The flag only changes the consecutive-weekend constraint, which the
    encoder emits once per adjacent weekend pair. Other weekend soft penalties
    (role mismatch, pre-vacation) are identical in both modes, so the DIFFERENCE
    in soft-violation count between soft and hard mode equals the number of
    adjacent weekend pairs — 2 for a 3-week horizon."""

    def test_hard_mode_emits_fewer_soft_violations_by_adjacent_pairs(self):
        _, soft_hard = _encode(consecutive_hard=True)
        _, soft_soft = _encode(consecutive_hard=False)
        assert len(soft_soft) - len(soft_hard) == 2, (
            f"Expected the consecutive-weekend flag to flip exactly 2 adjacent "
            f"pairs from soft to hard; got soft={len(soft_soft)} hard={len(soft_hard)}"
        )

    def test_hard_mode_emits_hard_at_most_one_constraint(self):
        """In hard mode each adjacent pair (v1, v2) becomes a hard at_most-1
        constraint of the form ``+1 ~v1 +1 ~v2 >= 1`` (i.e. not both)."""
        opb, _ = _encode(consecutive_hard=True)
        # The two adjacent weekend "work" indicators are aux vars; rather than
        # recompute them, assert that the builder contains at_most-style pair
        # constraints that did not exist as soft slacks. The behavioral delta
        # test above is the primary guard; this asserts hard constraints exist.
        pair_constraints = [
            c for c in opb._constraints
            if c.count("~x") == 2 and c.strip().endswith(">= 1 ;")
        ]
        assert pair_constraints, "Hard mode must emit at_most-1 pair constraints"
