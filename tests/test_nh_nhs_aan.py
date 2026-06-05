"""Tests for the NH-specific NHS-week and pre-AAN-week night/weekend rules.

NHS week: an NH fellow on NHS may work ONLY Monday + Tuesday night (each a soft
100 penalty); Wed/Thu/Fri/Sat/Sun nights of that week are hard-forbidden.

Pre-AAN week: in the week immediately BEFORE an AAN week, an NH fellow on AAN is
hard-forbidden from any weekend role and from Friday/Saturday/Sunday nights.
"""

from __future__ import annotations

from datetime import date

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.schedule_solver import (
    ScheduleSolverConfig,
    _ROLE_NCC1,
    _ROLE_STROKE,
    _day_of_week,
    _week_day,
    _encode_nhs_week_nights,
    _encode_pre_aan_forbid,
    NHS_NIGHT_PENALTY_WEIGHT,
)
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig


def _config(num_days=21):
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
        fellow_groups={"NH": ["Jin"]}, shifts=["NHS", "AAN", "Elec"], constraints=[],
        night_config=night_config, weekend_config=weekend_config,
        night_hard_criteria=frozenset(), start_dow=0, num_days=num_days,
    )


def _make_xs_xn(opb, config):
    nf = 1
    nw = config.num_weeks
    ns = len(config.shifts)
    xs = [[[opb.new_var() for _ in range(ns)] for _ in range(nw)] for _ in range(nf)]
    xn = [[opb.new_var() for _ in range(nf)] for _ in range(config.num_days)]
    return xs, xn


def _at_most_1(c):  # "+1 x.. +1 x.. <= 1 ;"
    return c.strip().endswith("<= 1 ;")


class TestNhsWeekNights:
    def test_wed_thru_sun_forbidden_mon_tue_penalized(self):
        config = _config(num_days=21)
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs, xn = _make_xs_xn(opb, config)
        soft = []
        _encode_nhs_week_nights(opb, xn, xs, config, ["Jin"], shift_idx, soft)

        nhs = xs[0][1][shift_idx["NHS"]]  # NHS in week 1
        start = config.start_dow
        # Wed/Thu/Fri/Sat/Sun (dow 2..6) of week 1 must be hard-forbidden.
        for dow in (2, 3, 4, 5, 6):
            d = _week_day(1, dow, start)
            xn_var = xn[d][0]
            forbid = [c for c in opb._constraints
                      if f"x{nhs} " in c.replace("~", "") and f"x{xn_var} " in c.replace("~", "")
                      and _at_most_1(c)]
            assert forbid, f"NHS week dow {dow} night must be hard-forbidden"
        # Mon/Tue (dow 0,1) must NOT be hard-forbidden, but DO add a soft penalty.
        for dow in (0, 1):
            d = _week_day(1, dow, start)
            xn_var = xn[d][0]
            forbid = [c for c in opb._constraints
                      if f"x{nhs} " in c.replace("~", "") and f"x{xn_var} " in c.replace("~", "")
                      and _at_most_1(c)]
            assert not forbid, f"NHS week dow {dow} night must NOT be hard-forbidden"

    def test_mon_tue_penalty_weight_is_100(self):
        config = _config(num_days=21)
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs, xn = _make_xs_xn(opb, config)
        soft = []
        _encode_nhs_week_nights(opb, xn, xs, config, ["Jin"], shift_idx, soft)
        # Two penalty slacks (Mon + Tue of each NHS-capable week-fellow), each 100.
        assert NHS_NIGHT_PENALTY_WEIGHT == 100
        assert soft, "Mon/Tue NHS nights must add soft penalties"
        assert all(w == 100 for _, w in soft), "NHS night penalty weight must be 100"


class TestPreAanForbid:
    def _setup(self, aan_week=2, num_days=28):
        config = _config(num_days=num_days)
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs, xn = _make_xs_xn(opb, config)
        nw = config.num_weeks
        # wr[w][role][f] — give Jin all 3 weekend roles each week.
        wr = [[{0: opb.new_var()} for _ in range(3)] for _ in range(nw)]
        _encode_pre_aan_forbid(opb, xn, wr, xs, config, ["Jin"], shift_idx)
        return opb, xs, xn, wr, shift_idx, config

    def test_weekend_roles_forbidden_week_before_aan(self):
        opb, xs, xn, wr, shift_idx, config = self._setup(aan_week=2)
        aan = xs[0][2][shift_idx["AAN"]]  # AAN in week 2
        for role_idx in (_ROLE_NCC1, _ROLE_STROKE):
            wr_var = wr[1][role_idx][0]  # week 1 = week before
            both = [c for c in opb._constraints
                    if f"x{aan} " in c.replace("~", "") and f"x{wr_var} " in c.replace("~", "")
                    and c.strip().endswith("<= 1 ;")]
            assert both, f"week-before-AAN weekend role {role_idx} must be forbidden"

    def test_fri_sat_sun_nights_forbidden_week_before(self):
        opb, xs, xn, wr, shift_idx, config = self._setup(aan_week=2)
        aan = xs[0][2][shift_idx["AAN"]]
        start = config.start_dow
        for dow in (4, 5, 6):  # Fri/Sat/Sun of week 1
            d = _week_day(1, dow, start)
            xn_var = xn[d][0]
            both = [c for c in opb._constraints
                    if f"x{aan} " in c.replace("~", "") and f"x{xn_var} " in c.replace("~", "")
                    and c.strip().endswith("<= 1 ;")]
            assert both, f"week-before-AAN dow {dow} night must be forbidden"

    def test_mon_thu_nights_not_forbidden_week_before(self):
        opb, xs, xn, wr, shift_idx, config = self._setup(aan_week=2)
        aan = xs[0][2][shift_idx["AAN"]]
        start = config.start_dow
        for dow in (0, 1, 2, 3):  # Mon-Thu of week 1 allowed
            d = _week_day(1, dow, start)
            xn_var = xn[d][0]
            both = [c for c in opb._constraints
                    if f"x{aan} " in c.replace("~", "") and f"x{xn_var} " in c.replace("~", "")]
            assert not both, f"week-before-AAN dow {dow} night must be allowed"
