"""Tests for the buffered no-consecutive-weekends constraint.

A consecutive weekend pair (working weekend w and weekend w+1) risks a long
unbroken work stretch. A pair is considered BUFFERED — and so exempt from
penalty / permitted when hard — when:

  * the fellow had NO weekend role in week w-1 (a weekend off before the pair),
    AND
  * a light rotation breaks the run: week w+2 weekday service is light, OR the
    MIDDLE week w+1 weekday service is itself light
    (CONSECUTIVE_WEEKEND_BUFFER_SHIFTS = Vac/ISC/ABPN/NHS/AAN/NCS 2026).

The w+1-light case matters because a light middle week means the fellow isn't
working those weekdays, so the run is already broken without a w+2 buffer.

Config flag ``weekend_consecutive_hard`` (default False):
  * False — SOFT: each UN-buffered consecutive pair adds one penalty slack;
    buffered pairs add none (the optimizer can dodge the penalty by buffering).
  * True  — HARD: an un-buffered consecutive pair is forbidden. A blanket hard
    rule (and the buffered-hard variant) were proven infeasible on workbook6, so
    the shipped default is soft-with-exemption.

Blank cells never count as light: a locked fellow's blank week has all shift
vars forbidden, so it offers no buffer var to satisfy the light condition.
"""

from __future__ import annotations

from datetime import date

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.schedule_types import (
    CONSECUTIVE_WEEKEND_BUFFER_SHIFTS,
    ScheduleSolverConfig,
)
from parafrost_scheduler.schedule_encoder import _encode_weekend_constraints
from scheduler.fellow_mapping import FellowMapping
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig


def _make_config(*, consecutive_hard: bool, shifts: list[str], num_days: int = 35) -> ScheduleSolverConfig:
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
        shifts=shifts,
        constraints=[],
        night_config=night_config,
        weekend_config=weekend_config,
        night_hard_criteria=frozenset(),
        start_dow=0,
        num_days=num_days,
        weekend_consecutive_hard=consecutive_hard,
    )


def _encode(*, consecutive_hard: bool, shifts=("NCC1", "ISC")):
    """Encode weekend constraints for one fellow, only Weekend NCC1 eligible, so
    each week's "work" indicator IS the role var (no aux)."""
    shifts = list(shifts)
    config = _make_config(consecutive_hard=consecutive_hard, shifts=shifts)
    opb = OpbBuilder()
    num_weeks = config.num_weeks
    shift_idx = {s: i for i, s in enumerate(shifts)}
    wr = [[{0: opb.new_var()}, {}, {}] for _ in range(num_weeks)]
    xs = [[[opb.new_var() for _ in shifts] for _ in range(num_weeks)]]
    soft: list[tuple[int, int]] = []
    mapping = FellowMapping()
    mapping.add_fellow("Alice", "NCC_SR")
    _encode_weekend_constraints(opb, wr, xs, config, mapping, ["Alice"], shift_idx, soft)
    return opb, soft, wr, xs, shift_idx, num_weeks


def _constraints_with(opb: OpbBuilder, *var_ids: int) -> list[str]:
    out = []
    for c in opb._constraints:
        if all((f"x{v} " in c or f"~x{v} " in c) for v in var_ids):
            out.append(c)
    return out


class TestBufferShiftConstant:
    def test_buffer_shifts_are_the_agreed_light_set(self):
        assert CONSECUTIVE_WEEKEND_BUFFER_SHIFTS == frozenset(
            {"Vac", "ISC", "ABPN", "NHS", "AAN", "NCS 2026"}
        )


class TestConsecutiveWeekendDefaults:
    def test_consecutive_soft_by_default(self):
        """Hard forms (blanket + buffered) were proven infeasible on workbook6,
        so the shipped default is soft-with-exemption."""
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
        assert cfg.weekend_consecutive_hard is False


class TestSoftExemption:
    """Soft mode: each consecutive pair couples its penalty to a buffer
    indicator, so a buffered pair can avoid the penalty."""

    def test_soft_adds_one_slack_per_adjacent_pair(self):
        """Soft mode adds exactly one consecutive-penalty slack per adjacent
        pair beyond the unrelated (mismatch / pre-vacation) soft penalties."""
        _, soft_soft, *_ = _encode(consecutive_hard=False)
        _, soft_hard, *_ = _encode(consecutive_hard=True)
        # num_days=35, Monday start => 5 weeks => 4 adjacent pairs.
        assert len(soft_soft) - len(soft_hard) == 4, (
            f"soft={len(soft_soft)} hard={len(soft_hard)}; expected delta 4"
        )

    def test_soft_pair_penalty_references_light_weeks(self):
        """An interior pair's buffer indicator is bounded by the light-shift
        vars of week w+1 (middle) and w+2 (after)."""
        opb, soft, wr, xs, shift_idx, num_weeks = _encode(consecutive_hard=False)
        w = 1  # interior pair (1,2); middle=w+1=2, after=w+2=3
        light_mid = xs[0][w + 1][shift_idx["ISC"]]
        light_after = xs[0][w + 2][shift_idx["ISC"]]
        # The buffer indicator constraint references BOTH the middle and after
        # light vars (the OR bound). At least one constraint mentions each.
        assert _constraints_with(opb, light_mid), "buffer must consider week w+1 light"
        assert _constraints_with(opb, light_after), "buffer must consider week w+2 light"


class TestHardForbidsUnbuffered:
    """Hard mode: an un-buffered consecutive pair is forbidden (no penalty
    slack); the pair is coupled to a buffer indicator forced true when active."""

    def test_hard_adds_no_consecutive_slack(self):
        _, soft_hard, *_ = _encode(consecutive_hard=True)
        _, soft_soft, *_ = _encode(consecutive_hard=False)
        assert len(soft_soft) > len(soft_hard), (
            "hard mode must not add the per-pair consecutive penalty slacks"
        )

    def test_hard_pair_coupled_to_prior_weekend_off(self):
        """Hard interior pair must reference week w-1's weekend var (no
        three-in-a-row / weekend-off-before)."""
        opb, _, wr, xs, shift_idx, num_weeks = _encode(consecutive_hard=True)
        w = 1
        v_prev = wr[w - 1][0][0]
        v_w = wr[w][0][0]
        v_w1 = wr[w + 1][0][0]
        assert _constraints_with(opb, v_prev, v_w) or _constraints_with(opb, v_prev, v_w1), (
            "hard buffered pair must couple to the prior weekend var"
        )
