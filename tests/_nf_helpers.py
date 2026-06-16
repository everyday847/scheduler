"""Fixtures for the NF model's day-granular call tier (Phase 1+).

Parallel to tests/_dispatch_helpers.make_dispatch_config, but turns the call tier
ON (call_tier_day_granular=True) and uses a short horizon so solves are fast.
"""
from __future__ import annotations

from datetime import date

from parafrost_scheduler.schedule_types import ScheduleSolverConfig
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig

from _dispatch_helpers import build, runner_or_skip  # noqa: F401 — re-exported for callers


def make_nf_config(
    *,
    fellows=("J1", "J2", "J3", "S1", "S2", "C1"),
    shifts=("NCC1", "NCC2", "NF", "MICU", "Elec", "Vac"),
    num_days: int = 14,
    start_dow: int = 0,
    constraints=(),
    **overrides,
) -> ScheduleSolverConfig:
    """A minimal day-granular-call config (call_tier_day_granular ON)."""
    night_config = NightSolverConfig(
        total_nights={}, friday_nights={},
        total_night_multisets=(), friday_night_multisets=(),
        ccm_fellows=frozenset(), holiday_dates=(),
        horizon_start_date=date(2026, 7, 1))
    weekend_config = WeekendSolverConfig(
        ncc_totals={}, stroke_totals={}, stroke_cohort=(), stroke_cohort_total=None,
        ccm_fellows=frozenset(),
        always_stroke_eligible=frozenset(fellows),
        telestroke_stroke_eligible=frozenset(), stroke_only_eligible=frozenset())
    kwargs = dict(
        fellow_groups={"NCC_JR": list(fellows)},
        shifts=list(shifts),
        constraints=list(constraints),
        night_config=night_config,
        weekend_config=weekend_config,
        start_dow=start_dow,
        num_days=num_days,
        call_tier_day_granular=True,
    )
    kwargs.update(overrides)
    return ScheduleSolverConfig(**kwargs)
