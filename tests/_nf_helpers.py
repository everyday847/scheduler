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

# Realistic NF-model fellow grouping: 1 CCM + 2 NCC_SR + 3 NCC_JR.
# CCM and NCC_SR are NOT in the week-0 first-night restriction (only NCC_JR/STROKE
# are restricted), so they provide night coverage in week 0 without conflict.
_DEFAULT_FELLOW_GROUPS: dict[str, list[str]] = {
    "CCM":    ["C1"],
    "NCC_SR": ["S1", "S2"],
    "NCC_JR": ["J1", "J2", "J3"],
}
_DEFAULT_FELLOWS: tuple[str, ...] = ("C1", "S1", "S2", "J1", "J2", "J3")


def make_nf_config(
    *,
    fellow_groups: dict[str, list[str]] | None = None,
    fellows: tuple[str, ...] | None = None,
    shifts=("NCC1", "NCC2", "NF", "MICU", "Elec", "Vac"),
    num_days: int = 14,
    start_dow: int = 0,
    constraints=(),
    **overrides,
) -> ScheduleSolverConfig:
    """A minimal day-granular-call config (call_tier_day_granular ON).

    Uses a realistic fellow_groups mix (CCM + NCC_SR + NCC_JR) so that the
    week-0 first-night restriction (which blocks only NCC_JR and STROKE fellows)
    does not render the fixture UNSAT.  CCM and NCC_SR fellows are unrestricted
    in week 0 and can absorb the early-week night coverage demand.
    """
    if fellow_groups is None:
        fellow_groups = _DEFAULT_FELLOW_GROUPS
    all_fellows: tuple[str, ...] = (
        fellows if fellows is not None
        else tuple(f for g in fellow_groups.values() for f in g)
    )
    night_config = NightSolverConfig(
        total_nights={}, friday_nights={},
        total_night_multisets=(), friday_night_multisets=(),
        ccm_fellows=frozenset(), holiday_dates=(),
        horizon_start_date=date(2026, 7, 1))
    weekend_config = WeekendSolverConfig(
        ncc_totals={}, stroke_totals={}, stroke_cohort=(), stroke_cohort_total=None,
        ccm_fellows=frozenset(),
        always_stroke_eligible=frozenset(all_fellows),
        telestroke_stroke_eligible=frozenset(), stroke_only_eligible=frozenset())
    kwargs = dict(
        fellow_groups=fellow_groups,
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
