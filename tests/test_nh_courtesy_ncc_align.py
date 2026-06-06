"""Presence tests for NH AAN/ABPN courtesy penalties and NCC weekend alignment.

These inspect opb._constraints strings (no solve). They mirror the harness in
tests/test_nh_nhs_aan.py.
"""

from __future__ import annotations

from datetime import date

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.schedule_solver import (
    ScheduleSolverConfig,
    _ROLE_NCC1,
    _ROLE_NCC2,
    _ROLE_STROKE,
    _week_day,
    _encode_nh_courtesy_weeks,
    _encode_ncc_weekend_alignment,
)
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig


def _config(num_days=28, nh_aan=100, nh_abpn=100, ncc_align=10,
            fellow_groups=None, shifts=None):
    night_config = NightSolverConfig(
        total_nights={}, friday_nights={}, total_night_multisets=(),
        friday_night_multisets=(), ccm_fellows=frozenset(), holiday_dates=(),
        horizon_start_date=date(2026, 7, 1),
    )
    weekend_config = WeekendSolverConfig(
        ncc_totals={}, stroke_totals={}, stroke_cohort=(), stroke_cohort_total=None,
        ccm_fellows=frozenset(), always_stroke_eligible=frozenset(),
        telestroke_stroke_eligible=frozenset(), stroke_only_eligible=frozenset(),
        total_weekends={}, weekend_options=None, friday_weekend_options=None,
    )
    return ScheduleSolverConfig(
        fellow_groups=fellow_groups or {"NH": ["Jin"]},
        shifts=shifts or ["NCC1", "NCC2", "AAN", "ABPN", "Elec"], constraints=[],
        night_config=night_config, weekend_config=weekend_config,
        night_hard_criteria=frozenset(), start_dow=0, num_days=num_days,
        nh_aan_week_call_penalty=nh_aan, nh_abpn_week_call_penalty=nh_abpn,
        ncc_weekend_misalign_penalty=ncc_align,
    )


def _make_xs_xn_wr(opb, config, fellow_names):
    nf = len(fellow_names)
    nw = config.num_weeks
    ns = len(config.shifts)
    xs = [[[opb.new_var() for _ in range(ns)] for _ in range(nw)] for _ in range(nf)]
    xn = [[opb.new_var() for _ in range(nf)] for _ in range(config.num_days)]
    wr = [[{f: opb.new_var() for f in range(nf)} for _ in range(3)] for _ in range(nw)]
    return xs, xn, wr


def _links(opb, a, b):
    """Constraints mentioning both var a and var b (ignoring ~ negation)."""
    return [c for c in opb._constraints
            if f"x{a} " in c.replace("~", "") and f"x{b} " in c.replace("~", "")]


class TestNhCourtesyWeeks:
    def test_aan_week_night_and_weekend_penalized_for_nh(self):
        config = _config()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs, xn, wr = _make_xs_xn_wr(opb, config, ["Jin"])
        soft = []
        _encode_nh_courtesy_weeks(opb, xn, wr, xs, config, ["Jin"], shift_idx, soft)

        aan = xs[0][2][shift_idx["AAN"]]  # AAN in week 2
        # A night in week 2 (Fri, dow 4) must produce a soft AND-indicator linked to aan.
        d = _week_day(2, 4, config.start_dow)
        assert _links(opb, aan, xn[d][0]), "AAN-week night must be penalized for NH fellow"
        # A weekend role in week 2 must produce a soft AND-indicator linked to aan.
        assert _links(opb, aan, wr[2][_ROLE_NCC1][0]), "AAN-week weekend role must be penalized"
        # All AAN penalties weigh 100.
        assert soft, "AAN week must add soft penalties"
        assert all(w == 100 for _, w in soft), "AAN courtesy weight must be 100"

    def test_abpn_week_penalized_for_nh(self):
        config = _config()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs, xn, wr = _make_xs_xn_wr(opb, config, ["Jin"])
        soft = []
        _encode_nh_courtesy_weeks(opb, xn, wr, xs, config, ["Jin"], shift_idx, soft)
        abpn = xs[0][1][shift_idx["ABPN"]]  # ABPN in week 1
        d = _week_day(1, 2, config.start_dow)  # Wed night, week 1
        assert _links(opb, abpn, xn[d][0]), "ABPN-week night must be penalized for NH fellow"
        assert soft and all(w == 100 for _, w in soft)

    def test_non_nh_fellow_not_penalized(self):
        config = _config(fellow_groups={"NCC_SR": ["Bob"]})
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs, xn, wr = _make_xs_xn_wr(opb, config, ["Bob"])
        soft = []
        _encode_nh_courtesy_weeks(opb, xn, wr, xs, config, ["Bob"], shift_idx, soft)
        assert soft == [], "Non-NH fellow must not get AAN/ABPN courtesy penalties"

    def test_zero_weight_disables(self):
        config = _config(nh_aan=0, nh_abpn=0)
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs, xn, wr = _make_xs_xn_wr(opb, config, ["Jin"])
        soft = []
        _encode_nh_courtesy_weeks(opb, xn, wr, xs, config, ["Jin"], shift_idx, soft)
        assert soft == [], "Zero weights must add no penalties"


class TestNccWeekendAlignment:
    def test_misalignment_penalized_weight_10(self):
        config = _config()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs, xn, wr = _make_xs_xn_wr(opb, config, ["Jin"])
        soft = []
        _encode_ncc_weekend_alignment(opb, wr, xs, config, ["Jin"], shift_idx, soft)
        # Weekday NCC1 (week 0) crossed with Weekend NCC2 (week 0) must be penalized.
        wd_ncc1 = xs[0][0][shift_idx["NCC1"]]
        we_ncc2 = wr[0][_ROLE_NCC2][0]
        assert _links(opb, wd_ncc1, we_ncc2), "weekday-NCC1/weekend-NCC2 must be penalized"
        # Weekday NCC2 crossed with Weekend NCC1 must be penalized.
        wd_ncc2 = xs[0][0][shift_idx["NCC2"]]
        we_ncc1 = wr[0][_ROLE_NCC1][0]
        assert _links(opb, wd_ncc2, we_ncc1), "weekday-NCC2/weekend-NCC1 must be penalized"
        assert soft and all(w == 10 for _, w in soft), "misalign weight must be 10"

    def test_aligned_not_penalized(self):
        config = _config()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs, xn, wr = _make_xs_xn_wr(opb, config, ["Jin"])
        soft = []
        _encode_ncc_weekend_alignment(opb, wr, xs, config, ["Jin"], shift_idx, soft)
        # The aligned pairing weekday-NCC1 + weekend-NCC1 must NOT be linked.
        wd_ncc1 = xs[0][0][shift_idx["NCC1"]]
        we_ncc1 = wr[0][_ROLE_NCC1][0]
        assert not _links(opb, wd_ncc1, we_ncc1), "aligned NCC1/NCC1 must not be penalized"

    def test_zero_weight_disables(self):
        config = _config(ncc_align=0)
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs, xn, wr = _make_xs_xn_wr(opb, config, ["Jin"])
        soft = []
        _encode_ncc_weekend_alignment(opb, wr, xs, config, ["Jin"], shift_idx, soft)
        assert soft == [], "Zero weight must add no penalties"
