"""Orchestrator: high-level entry point for the full schedule solver.

Provides a clean API that accepts structured domain parameters and returns
a FullScheduleSolution.  This is the only module that should import solver
internals (schedule_optimizer, schedule_encoder, roundingsat_runner);
consumers such as solver_bridge, conflict_diagnosis, web_app, and
experiment route through it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict

from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb
from parafrost_scheduler.schedule_optimizer import (
    solve_full_schedule,
    solve_full_schedule_progressive,
)
from parafrost_scheduler.schedule_types import (
    FullScheduleSolution,
    ScheduleSolverConfig,
)
from scheduler.night_call_types import CountMultiset, NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig

ROUNDINGSAT_BINARY = (
    Path(__file__).resolve().parent.parent.parent
    / "vendor"
    / "roundingsat"
    / "build"
    / "roundingsat"
)


def get_runner() -> RoundingSatRunner:
    """Return a RoundingSatRunner pointed at the vendored binary."""
    return RoundingSatRunner(ROUNDINGSAT_BINARY)


def solve_schedule(
    config: ScheduleSolverConfig,
    runner: RoundingSatRunner | None = None,
    *,
    max_soft: int | None = None,
    emit_progress: bool = True,
) -> FullScheduleSolution | None:
    """Solve a full schedule from a ScheduleSolverConfig.

    Creates a runner if none is provided, then delegates to
    solve_full_schedule for the actual optimization.
    """
    if runner is None:
        runner = get_runner()
    return solve_full_schedule(
        config,
        runner,
        max_soft=max_soft,
        emit_progress=emit_progress,
    )


def check_schedule_feasibility(
    config: ScheduleSolverConfig,
    runner: RoundingSatRunner | None = None,
    *,
    timeout: float = 5.0,
) -> bool:
    """Check if a ScheduleSolverConfig is feasible (SAT check, no optimization).

    Builds an OPB encoding and runs a single SAT probe.  Returns True if the
    config is satisfiable within *timeout* seconds.
    """
    if runner is None:
        runner = get_runner()
    opb, var_map = build_full_schedule_opb(config, soft_bound=None)
    upper = sum(w for _, w in var_map.soft_violations)
    opb_probe, _ = build_full_schedule_opb(config, soft_bound=upper)
    result = runner.solve(opb_probe, timeout=timeout)
    return result.satisfiable


def solve_schedule_progressive(
    config: ScheduleSolverConfig,
    runner: RoundingSatRunner | None = None,
    *,
    preview_seconds: tuple[float, ...] = (8.0, 25.0),
    max_seconds: float = 180.0,
):
    """Stream a schedule solve via progressive optimization.

    Yields status/solution/done/error dict events suitable for SSE streaming.
    """
    if runner is None:
        runner = get_runner()
    yield from solve_full_schedule_progressive(
        config,
        runner,
        preview_seconds=preview_seconds,
        max_seconds=max_seconds,
    )


# ---------------------------------------------------------------------------
# Night config builder  (was in solver_bridge._build_night_config)
# ---------------------------------------------------------------------------

def build_night_config_from_request(
    request: Dict[str, Any],
    fellow_groups: Dict[str, list[str]],
) -> NightSolverConfig:
    """Build NightSolverConfig from the annual config's night_call section."""
    night_call = request.get("night_call", [])
    holiday_dates_raw = request.get("holiday_dates", [])
    horizon_start = request.get("horizon_start", "2026-06-29")

    total_nights: dict[str, int] = {}
    friday_nights: dict[str, int] = {}
    total_multisets: list[CountMultiset] = []
    friday_multisets: list[CountMultiset] = []

    for entry in night_call:
        group = entry.get("group")
        if group not in fellow_groups:
            continue
        fellows = fellow_groups[group]
        group_size = len(fellows)
        if group_size == 0:
            continue

        group_total = entry.get("total_nights", 0)
        if group_total > 0:
            base = group_total // group_size
            remainder = group_total % group_size
            if remainder == 0:
                for f in fellows:
                    total_nights[f] = base
            else:
                values = tuple([base + 1] * remainder + [base] * (group_size - remainder))
                total_multisets.append(CountMultiset(names=tuple(fellows), values=values))

        group_friday = entry.get("friday_nights", 0)
        if group_friday > 0:
            base_f = group_friday // group_size
            remainder_f = group_friday % group_size
            if remainder_f == 0:
                for f in fellows:
                    friday_nights[f] = base_f
            else:
                values_f = tuple([base_f + 1] * remainder_f + [base_f] * (group_size - remainder_f))
                friday_multisets.append(CountMultiset(names=tuple(fellows), values=values_f))

    ccm_fellows = frozenset(fellow_groups.get("CCM", []))
    parsed_holidays = tuple(_parse_date(d) for d in holiday_dates_raw)

    if isinstance(horizon_start, str):
        horizon = date.fromisoformat(horizon_start)
    else:
        horizon = date(2026, 6, 29)

    night_rules_config = _apply_night_rules(request)
    _validate_night_config(night_rules_config)

    return NightSolverConfig(
        total_nights=total_nights or None,
        friday_nights=friday_nights or None,
        total_night_multisets=tuple(total_multisets),
        friday_night_multisets=tuple(friday_multisets),
        ccm_fellows=ccm_fellows,
        holiday_dates=parsed_holidays,
        horizon_start_date=horizon,
        **night_rules_config,
    )


# ---------------------------------------------------------------------------
# Weekend config builder  (was in solver_bridge._build_weekend_config)
# ---------------------------------------------------------------------------

_STROKE_SERVICE_SHIFTS = frozenset({"Stroke", "Telestroke/Clinic"})


def _stroke_service_fellows(
    request: Dict[str, Any],
    fellow_groups: Dict[str, list[str]],
) -> set[str]:
    """Fellows who can be assigned weekday Stroke/Telestroke service."""
    result: set[str] = set()

    def grants_stroke(rule: dict) -> bool:
        if not rule.get("active", True):
            return False
        if not (_STROKE_SERVICE_SHIFTS & set(rule.get("shifts", []))):
            return False
        return rule.get("relation") in ("at_least", "exactly") and rule.get("count", 0) > 0

    for rule in request.get("rules", []):
        if rule.get("type") == "shift_total" and grants_stroke(rule):
            for g in rule.get("groups", []):
                result.update(fellow_groups.get(g, []))
            # S1: after the per_fellow_shift_total cutover a single-fellow
            # stroke grant lives in `rules` as a shift_total carrying a
            # `fellow:` (or a `fellows:`/`fellow_groups:` selector) instead of
            # `groups:`. Recognize those forms too — additively, so the legacy
            # group form above still works.
            for g in rule.get("fellow_groups", []):
                result.update(fellow_groups.get(g, []))
            for f in rule.get("fellows", []):
                result.add(f)
            single_fellow = rule.get("fellow")
            if single_fellow:
                result.add(single_fellow)

    for rule in request.get("call_rules", []):
        if rule.get("type") == "per_fellow_shift_total" and grants_stroke(rule):
            fellow = rule.get("fellow")
            if fellow:
                result.add(fellow)

    return result


def build_weekend_config_from_request(
    request: Dict[str, Any],
    fellow_groups: Dict[str, list[str]],
) -> WeekendSolverConfig:
    """Build WeekendSolverConfig from the annual config's weekend_call section."""
    weekend_call = request.get("weekend_call", [])

    ncc_totals: dict[str, int] = {}
    stroke_totals: dict[str, int] = {}
    stroke_eligible: set[str] = set()
    ncc_ranges: dict[str, tuple[int, int]] = {}
    ncc_group_sums: list[tuple[tuple[str, ...], int]] = []
    every_other_weekend_fellows: set[str] = set()

    for entry in weekend_call:
        group = entry.get("group")
        if group not in fellow_groups:
            continue
        fellows = fellow_groups[group]
        group_size = len(fellows)
        if group_size == 0:
            continue

        if entry.get("every_other_weekend"):
            every_other_weekend_fellows.update(fellows)

        group_ncc = entry.get("ncc_total", 0)
        entry_ranges = entry.get("ncc_ranges")
        if entry_ranges:
            lo_sum = hi_sum = 0
            for f in fellows:
                rng = entry_ranges.get(f)
                if rng is None:
                    raise ValueError(
                        f"weekend_call group {group!r} has ncc_ranges but is "
                        f"missing fellow {f!r}"
                    )
                lo, hi = int(rng[0]), int(rng[1])
                if lo > hi:
                    raise ValueError(f"ncc_range for {f!r} has lo > hi: {rng}")
                ncc_ranges[f] = (lo, hi)
                lo_sum += lo
                hi_sum += hi
            if not (lo_sum <= group_ncc <= hi_sum):
                raise ValueError(
                    f"weekend_call group {group!r} ncc_total {group_ncc} is "
                    f"outside the summed ranges [{lo_sum}, {hi_sum}]"
                )
            ncc_group_sums.append((tuple(fellows), group_ncc))
        elif group_ncc > 0:
            base = group_ncc // group_size
            remainder = group_ncc % group_size
            for i, f in enumerate(fellows):
                ncc_totals[f] = base + (1 if i < remainder else 0)

        if "stroke_total" in entry:
            group_stroke = entry.get("stroke_total", 0)
            base_s = group_stroke // group_size
            remainder_s = group_stroke % group_size
            for i, f in enumerate(fellows):
                fellow_stroke = base_s + (1 if i < remainder_s else 0)
                stroke_totals[f] = fellow_stroke
                if fellow_stroke > 0:
                    stroke_eligible.add(f)

    weekend_rules_config = _apply_weekend_rules(request)
    _validate_weekend_config(weekend_rules_config)

    stroke_group = set(fellow_groups.get("STROKE", []))
    service_eligible = _stroke_service_fellows(request, fellow_groups)
    always_eligible = (stroke_eligible | service_eligible) & stroke_group
    conditionally_eligible = (stroke_eligible | service_eligible) - stroke_group

    return WeekendSolverConfig(
        ncc_totals=ncc_totals or None,
        stroke_totals=stroke_totals or None,
        ncc_ranges=ncc_ranges or None,
        ncc_group_sums=tuple(ncc_group_sums),
        every_other_weekend_fellows=frozenset(every_other_weekend_fellows),
        stroke_cohort=(),
        stroke_cohort_total=None,
        stroke_cohort_min=0,
        stroke_cohort_max=52,
        ccm_fellows=frozenset(fellow_groups.get("CCM", [])),
        always_stroke_eligible=frozenset(always_eligible),
        telestroke_stroke_eligible=frozenset(conditionally_eligible),
        stroke_only_eligible=frozenset(),
        **weekend_rules_config,
    )


# ---------------------------------------------------------------------------
# Rules helpers (moved from solver_bridge)
# ---------------------------------------------------------------------------

def _apply_night_rules(request: Dict[str, Any]) -> dict:
    """Extract NightSolverConfig fields from night_rules section."""
    result: dict[str, Any] = {}
    night_rules = request.get("night_rules", [])
    if not night_rules:
        return result

    for rule in night_rules:
        if not rule.get("active", True):
            continue
        rule_type = rule.get("type")
        if rule_type == "night_spacing":
            result["spacing_max_nights"] = rule.get("max_nights", 1)
            result["spacing_window_days"] = rule.get("window_days", 3)
        elif rule_type == "night_blocked_services":
            result["blocking_exact_services"] = tuple(rule.get("exact_services", []))
            result["blocking_substring_services"] = tuple(rule.get("substring_services", []))
        elif rule_type == "night_holiday_eligibility":
            result["holiday_allowed_services"] = tuple(rule.get("allowed_services", []))
        elif rule_type == "night_penalties":
            result["penalty_weights"] = dict(rule.get("weights", {}))
        elif rule_type == "night_sunday_following":
            result["sunday_preferred_services"] = tuple(rule.get("preferred_services", []))

    return result


def _validate_night_config(config_dict: dict) -> None:
    if "spacing_max_nights" in config_dict and config_dict["spacing_max_nights"] < 1:
        raise ValueError("spacing_max_nights must be >= 1")
    if "spacing_window_days" in config_dict and config_dict["spacing_window_days"] < 2:
        raise ValueError("spacing_window_days must be >= 2")


def _apply_weekend_rules(request: Dict[str, Any]) -> dict:
    """Extract WeekendSolverConfig fields from weekend_rules section."""
    result: dict[str, Any] = {}
    weekend_rules = request.get("weekend_rules", [])
    if not weekend_rules:
        return result

    for rule in weekend_rules:
        if not rule.get("active", True):
            continue
        rule_type = rule.get("type")
        if rule_type == "weekend_spacing":
            result["spacing_max_weekends"] = rule.get("max_weekends", 1)
            result["spacing_window_weekends"] = rule.get("window_weekends", 2)
        elif rule_type == "weekend_blocked_services":
            result["blocking_exact_services"] = tuple(rule.get("exact_services", []))
            result["blocking_substring_services"] = tuple(rule.get("substring_services", []))
        elif rule_type == "weekend_stroke_eligibility":
            result["stroke_eligible_services"] = tuple(rule.get("eligible_services", []))
        elif rule_type == "weekend_penalties":
            result["penalty_weights"] = dict(rule.get("weights", {}))

    return result


def _validate_weekend_config(config_dict: dict) -> None:
    if "spacing_max_weekends" in config_dict and config_dict["spacing_max_weekends"] < 1:
        raise ValueError("spacing_max_weekends must be >= 1")
    if "spacing_window_weekends" in config_dict and config_dict["spacing_window_weekends"] < 2:
        raise ValueError("spacing_window_weekends must be >= 2")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(value: Any) -> date:
    if isinstance(value, str):
        return date.fromisoformat(value)
    if isinstance(value, (list, tuple)):
        return date(*value)
    if isinstance(value, date):
        return value
    raise TypeError(f"Cannot parse date from {type(value).__name__}: {value!r}")
