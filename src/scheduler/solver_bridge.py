"""Bridge between the web API / annual YAML and the RoundingSat schedule solver."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict

import yaml

from .annual_rules import constraints_from_config as annual_constraints_from_config
from .night_call_types import NightSolverConfig, CountMultiset
from .palette_rules import palette_rule_to_constraints
from .palette_derivations import derive_forbidden_shifts
from .standing_rules import constraints_from_config as standing_constraints_from_config
from .weekend_call_types import WeekendSolverConfig

from parafrost_scheduler.schedule_solver import ScheduleSolverConfig
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
STANDING_RULE_CONFIG = CONFIG_DIR / "standing" / "stanford-fellowship.yaml"
DEFAULT_ANNUAL_CONFIG = CONFIG_DIR / "annual" / "my-2025-2026.yaml"
ROUNDINGSAT_BINARY = Path(__file__).resolve().parents[2] / "new_approach" / "vendor" / "roundingsat" / "build" / "roundingsat"


def build_solver_config_from_request(
    raw_request: Dict[str, Any] | None,
    *,
    annual_path: Path | None = None,
    standing_path: Path | None = None,
) -> ScheduleSolverConfig:
    """Build a ScheduleSolverConfig from web request JSON.

    The request can be:
    - A full annual config (loaded from disk via the API, then edited by the user)
    - Or a minimal request with just fellow_groups + shifts + fellow_week_pairs

    Standing rules are always loaded from disk.
    """
    if raw_request is None:
        raise ValueError("Request body must be JSON.")

    fellow_groups = _require_dict(raw_request, "fellow_groups")
    shifts = raw_request.get("shifts", [])
    if not shifts:
        raise ValueError("shifts must be a non-empty list.")
    fellow_week_pairs = raw_request.get("fellow_week_pairs", {})
    annual_rules = raw_request.get("annual_rules")

    # Standing rules: use overrides from request if provided, else load from disk
    standing_rules_override = raw_request.get("standing_rules")
    if standing_rules_override is not None:
        standing_config = {"rules": standing_rules_override}
    else:
        standing_path = standing_path or STANDING_RULE_CONFIG
        standing_config = yaml.safe_load(standing_path.read_text())

    # Detect palette v2 format: rules have "type" key instead of "kind"
    standing_rules_list = standing_config.get("rules", [])
    is_palette_format = (
        standing_rules_list
        and isinstance(standing_rules_list[0], dict)
        and "type" in standing_rules_list[0]
    )

    if is_palette_format:
        constraints = _build_palette_constraints(
            standing_config, raw_request, fellow_groups, shifts, fellow_week_pairs,
        )
    else:
        constraints = [
            *standing_constraints_from_config(standing_config),
            *annual_constraints_from_config(annual_rules, fellow_week_pairs=fellow_week_pairs),
        ]

    all_fellows = {name: group for group, names in fellow_groups.items() for name in names}

    # Merge night_rules and weekend_rules from standing config and raw_request
    # (annual overrides standing, same pattern as regular rules)
    if is_palette_format:
        standing_night_rules = standing_config.get("night_rules", [])
        annual_night_rules = raw_request.get("night_rules", [])
        merged_night_rules = annual_night_rules if annual_night_rules else standing_night_rules
        if merged_night_rules:
            raw_request = {**raw_request, "night_rules": merged_night_rules}

        standing_weekend_rules = standing_config.get("weekend_rules", [])
        annual_weekend_rules = raw_request.get("weekend_rules", [])
        merged_weekend_rules = annual_weekend_rules if annual_weekend_rules else standing_weekend_rules
        if merged_weekend_rules:
            raw_request = {**raw_request, "weekend_rules": merged_weekend_rules}

    night_config = _build_night_config(raw_request, fellow_groups)
    weekend_config = _build_weekend_config(raw_request, fellow_groups)

    locked_assignments = raw_request.get("locked_assignments", {})
    call_rules = raw_request.get("call_rules", [])

    # Compute calendar model from horizon_start
    horizon_start_str = raw_request.get("horizon_start", "2026-07-01")
    if isinstance(horizon_start_str, str):
        horizon_date = date.fromisoformat(horizon_start_str)
    else:
        horizon_date = date(2026, 7, 1)

    start_dow = horizon_date.weekday()  # 0=Mon, 6=Sun

    # Academic year: from horizon_start to one year later minus one day
    horizon_end = date(horizon_date.year + 1, horizon_date.month, horizon_date.day) - timedelta(days=1)
    num_days = (horizon_end - horizon_date).days + 1  # 365 or 366

    return ScheduleSolverConfig(
        fellow_groups=fellow_groups,
        shifts=shifts,
        constraints=constraints,
        night_config=night_config,
        weekend_config=weekend_config,
        locked_assignments=locked_assignments,
        call_rules=call_rules,
        start_dow=start_dow,
        num_days=num_days,
    )


def load_annual_config(path: Path | None = None) -> Dict[str, Any]:
    """Load an annual config YAML file and return as a dict."""
    path = path or DEFAULT_ANNUAL_CONFIG
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping.")
    return data


def list_configs() -> Dict[str, list[str]]:
    """List available config files."""
    annual_dir = CONFIG_DIR / "annual"
    standing_dir = CONFIG_DIR / "standing"
    return {
        "annual": sorted(f.name for f in annual_dir.glob("*.yaml") if not f.name.startswith(".")),
        "standing": sorted(f.name for f in standing_dir.glob("*.yaml") if not f.name.startswith(".")),
    }


def get_runner() -> RoundingSatRunner:
    """Get a RoundingSatRunner instance."""
    return RoundingSatRunner(ROUNDINGSAT_BINARY)


# ---------------------------------------------------------------------------
# Palette v2 constraint builder
# ---------------------------------------------------------------------------

def _build_palette_constraints(
    standing_config: Dict[str, Any],
    request: Dict[str, Any],
    fellow_groups: Dict[str, list[str]],
    shifts: list[str],
    fellow_week_pairs: Dict[str, list[int]],
) -> list:
    """Build constraints from palette-format (v2) configs."""
    from .semantic_constraints import ConstraintLifecycle, ConstraintStrength, FellowSelector, SemanticConstraint, ShiftSet, WeekSpan
    from .annual_rules import vacation_request_constraints

    constraints = []

    # Standing rules (palette format)
    for rule in standing_config.get("rules", []):
        if not rule.get("active", True):
            continue
        if rule.get("type") == "full_assignment":
            constraints.append(SemanticConstraint(
                kind="full_assignment",
                lifecycle=ConstraintLifecycle.STANDING_RULE,
                strength=ConstraintStrength.HARD,
                fellows=FellowSelector.by_groups(*rule["groups"]),
                params={"name": rule["name"]},
            ))
            continue
        constraints.extend(palette_rule_to_constraints(
            rule, lifecycle=ConstraintLifecycle.STANDING_RULE,
        ))

    # Annual rules (palette format)
    annual_rules = request.get("rules", [])
    if not annual_rules:
        annual_section = request.get("annual_rules")
        if annual_section and isinstance(annual_section, dict):
            annual_rules = annual_section.get("rules", [])

    for rule in annual_rules:
        if not rule.get("active", True):
            continue
        if rule.get("type") == "vacation_request_policy":
            constraints.extend(vacation_request_constraints(
                fellow_week_pairs,
                hard_request_count=rule.get("hard_request_count", 3),
            ))
            continue
        if rule.get("type") == "specific_assignment" and rule.get("fellow"):
            from .annual_rules import named_assignment
            constraints.append(named_assignment(
                rule["fellow"],
                week=rule["week"],
                shift=rule["shift"],
                hard=rule.get("strength", "hard") == "hard",
                params={"name": rule.get("name", "specific_assignment")},
            ))
            continue
        constraints.extend(palette_rule_to_constraints(
            rule, lifecycle=ConstraintLifecycle.ANNUAL_RULE,
        ))

    # Derive forbidden shifts from shift_total rules (+ per_fellow call_rules)
    all_rules = standing_config.get("rules", []) + annual_rules
    constraints.extend(derive_forbidden_shifts(all_rules, shifts, fellow_groups,
                                               call_rules=request.get("call_rules", [])))

    return constraints


# ---------------------------------------------------------------------------
# Night config builder
# ---------------------------------------------------------------------------

def _build_night_config(
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

        # Total nights distribution
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

        # Friday nights distribution
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

    # CCM fellows blocked from nights
    ccm_fellows = frozenset(fellow_groups.get("CCM", []))

    # Parse holiday dates
    parsed_holidays = tuple(_parse_date(d) for d in holiday_dates_raw)

    # Parse horizon start
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
# Weekend config builder
# ---------------------------------------------------------------------------

def _build_weekend_config(
    request: Dict[str, Any],
    fellow_groups: Dict[str, list[str]],
) -> WeekendSolverConfig:
    """Build WeekendSolverConfig from the annual config's weekend_call section."""
    weekend_call = request.get("weekend_call", [])

    ncc_totals: dict[str, int] = {}
    stroke_totals: dict[str, int] = {}
    stroke_eligible: set[str] = set()

    for entry in weekend_call:
        group = entry.get("group")
        if group not in fellow_groups:
            continue
        fellows = fellow_groups[group]
        group_size = len(fellows)
        if group_size == 0:
            continue

        # NCC distribution (NCC1 + NCC2 combined)
        group_ncc = entry.get("ncc_total", 0)
        if group_ncc > 0:
            base = group_ncc // group_size
            remainder = group_ncc % group_size
            for i, f in enumerate(fellows):
                ncc_totals[f] = base + (1 if i < remainder else 0)

        # Stroke distribution
        group_stroke = entry.get("stroke_total", 0)
        if group_stroke > 0:
            base_s = group_stroke // group_size
            remainder_s = group_stroke % group_size
            for i, f in enumerate(fellows):
                fellow_stroke = base_s + (1 if i < remainder_s else 0)
                if fellow_stroke > 0:
                    stroke_totals[f] = fellow_stroke
                    stroke_eligible.add(f)

    weekend_rules_config = _apply_weekend_rules(request)
    _validate_weekend_config(weekend_rules_config)

    # STROKE group fellows are always eligible for Weekend Stroke (they're
    # Stroke specialists). Other fellows with stroke_totals (NCC_SR, NH) are
    # only eligible when on weekday Stroke or Telestroke that week.
    stroke_group = set(fellow_groups.get("STROKE", []))
    always_eligible = stroke_eligible & stroke_group
    conditionally_eligible = stroke_eligible - stroke_group

    return WeekendSolverConfig(
        ncc_totals=ncc_totals or None,
        stroke_totals=stroke_totals or None,
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
# Night rules helper
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


# ---------------------------------------------------------------------------
# Weekend rules helper
# ---------------------------------------------------------------------------

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

def _require_dict(request: Dict[str, Any], key: str) -> Dict[str, list[str]]:
    value = request.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object.")
    return {k: list(v) for k, v in value.items()}


def _parse_date(value: Any) -> date:
    if isinstance(value, str):
        return date.fromisoformat(value)
    if isinstance(value, (list, tuple)):
        return date(*value)
    if isinstance(value, date):
        return value
    raise ValueError(f"Cannot parse date: {value}")
