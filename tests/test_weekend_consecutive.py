"""Tests for the buffered no-consecutive-weekends constraint.

A fellow should not work weekend call in two back-to-back weeks UNLESS the run is
buffered on both sides so they never work 15+ days straight. The
``weekend_consecutive_hard`` config flag (default True) makes this a hard rule:

  A consecutive weekend pair (w, w+1) is permitted iff
    * the fellow had NO weekend role in week w-1 (a weekend off before the pair),
      AND
    * the fellow's weekday service in week w+2 is "light"
      (Vac / ISC / APBN / NHS / AAN / NCS 2026 — a rotation that gives a break
      right after the pair).

A blanket hard "no two consecutive weekends" was proven INFEASIBLE on workbook6
(Slurm SAT bisect, 2026-06-05); the buffered rule is the feasible relaxation.

With the flag off the constraint degrades to a soft per-pair distribution
penalty (the historical behavior, kept as a comparison point).
"""

from __future__ import annotations

from datetime import date

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.schedule_solver import (
    CONSECUTIVE_WEEKEND_BUFFER_SHIFTS,
    ScheduleSolverConfig,
    _encode_weekend_constraints,
)
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


def _encode(*, consecutive_hard: bool, single_role: bool = True):
    """Encode weekend constraints for one fellow eligible every week.

    single_role=True gives the fellow only Weekend NCC1, so each week's "work"
    indicator IS the role var (no aux), making constraints easy to trace.
    """
    shifts = ["NCC1", "ISC"]  # ISC is a buffer (light) shift; NCC1 is not.
    config = _make_config(consecutive_hard=consecutive_hard, shifts=shifts)
    opb = OpbBuilder()
    num_weeks = config.num_weeks
    shift_idx = {s: i for i, s in enumerate(shifts)}

    # wr[w][role][f]; role 0 = NCC1. Only role 0 populated if single_role.
    wr = []
    for _ in range(num_weeks):
        if single_role:
            wr.append([{0: opb.new_var()}, {}, {}])
        else:
            wr.append([{0: opb.new_var()} for _ in range(3)])
    # xs[f][w][s]
    xs = [[[opb.new_var() for _ in shifts] for _ in range(num_weeks)]]

    soft: list[tuple[int, int]] = []
    mapping = FellowMapping()
    mapping.add_fellow("Alice", "NCC_SR")
    _encode_weekend_constraints(opb, wr, xs, config, mapping, ["Alice"], shift_idx, soft)
    return opb, soft, wr, xs, shift_idx, num_weeks


def _constraints_with(opb: OpbBuilder, *var_ids: int) -> list[str]:
    """Constraints referencing ALL of the given var ids (pos or neg)."""
    out = []
    for c in opb._constraints:
        if all((f"x{v} " in c or f"~x{v} " in c) for v in var_ids):
            out.append(c)
    return out


class TestBufferShiftConstant:
    def test_buffer_shifts_are_the_agreed_light_set(self):
        assert CONSECUTIVE_WEEKEND_BUFFER_SHIFTS == frozenset(
            {"Vac", "ISC", "APBN", "NHS", "AAN", "NCS 2026"}
        )


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


class TestBufferedHardRule:
    """Hard mode: an interior consecutive pair (w, w+1) is coupled to BOTH the
    prior-weekend-off condition (week w-1) and the light-week condition
    (week w+2)."""

    def test_pair_requires_light_week_after(self):
        """Combo B: work[w] AND work[w+1] => light[w+2]. The constraint for an
        interior pair must reference week w+2's buffer (ISC) shift var."""
        opb, _, wr, xs, shift_idx, num_weeks = _encode(consecutive_hard=True)
        w = 1  # interior pair (1, 2); w-1=0, w+2=3 both exist
        work_w = wr[w][0][0]
        work_w1 = wr[w + 1][0][0]
        light_w2 = xs[0][w + 2][shift_idx["ISC"]]
        # Some emitted constraint ties the pair to the following light week.
        coupling = _constraints_with(opb, work_w, work_w1, light_w2)
        assert coupling, (
            "Buffered hard rule must couple the consecutive pair to the "
            "light-shift var of week w+2"
        )

    def test_pair_forbids_prior_weekend(self):
        """Combo A: work[w-1] AND work[w] AND work[w+1] is forbidden."""
        opb, _, wr, xs, shift_idx, num_weeks = _encode(consecutive_hard=True)
        w = 1
        work_prev = wr[w - 1][0][0]
        work_w = wr[w][0][0]
        work_w1 = wr[w + 1][0][0]
        triple = _constraints_with(opb, work_prev, work_w, work_w1)
        assert triple, (
            "Buffered hard rule must forbid three consecutive weekends "
            "(no weekend-off before the pair)"
        )

    def test_hard_mode_adds_no_consecutive_soft_penalty(self):
        """Hard buffered mode is a hard constraint — it must not add per-pair
        soft violation slacks beyond the unrelated mismatch/pre-vacation ones."""
        _, soft_hard, *_ = _encode(consecutive_hard=True)
        _, soft_soft, *_ = _encode(consecutive_hard=False)
        # Soft mode adds one slack per adjacent pair; hard mode adds none. The
        # difference equals the number of adjacent pairs (num_weeks - 1).
        # num_days=35, start Mon => 5 weeks => 4 pairs.
        assert len(soft_soft) - len(soft_hard) == 4, (
            f"soft={len(soft_soft)} hard={len(soft_hard)}; expected delta 4"
        )
