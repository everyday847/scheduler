"""Bridge between the web API / annual YAML and the RoundingSat schedule solver."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict

import yaml

from .annual_rules import constraints_from_config as annual_constraints_from_config
from .night_call_solver import NightSolverConfig, CountMultiset
from .palette_rules import palette_rule_to_constraints
from .palette_derivations import derive_forbidden_shifts
from .standing_rules import constraints_from_config as standing_constraints_from_config
from .weekend_call_solver import WeekendSolverConfig

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

    night_config = _build_night_config(raw_request, fellow_groups)
    weekend_config = _build_weekend_config(raw_request, fellow_groups)

    return ScheduleSolverConfig(
        fellow_groups=fellow_groups,
        shifts=shifts,
        constraints=constraints,
        night_config=night_config,
        weekend_config=weekend_config,
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
        constraints.extend(palette_rule_to_constraints(
            rule, lifecycle=ConstraintLifecycle.ANNUAL_RULE,
        ))

    # Derive forbidden shifts from shift_total rules
    all_rules = standing_config.get("rules", []) + annual_rules
    constraints.extend(derive_forbidden_shifts(all_rules, shifts, fellow_groups))

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
        parts = horizon_start.split("-")
        horizon = date(int(parts[0]), int(parts[1]), int(parts[2]))
    else:
        horizon = date(2026, 6, 29)

    return NightSolverConfig(
        total_nights=total_nights or None,
        friday_nights=friday_nights or None,
        total_night_multisets=tuple(total_multisets),
        friday_night_multisets=tuple(friday_multisets),
        ccm_fellows=ccm_fellows,
        holiday_dates=parsed_holidays,
        horizon_start_date=horizon,
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

    return WeekendSolverConfig(
        ncc_totals=ncc_totals or None,
        stroke_totals=stroke_totals or None,
        stroke_cohort=(),
        stroke_cohort_total=None,
        stroke_cohort_min=0,
        stroke_cohort_max=52,
        ccm_fellows=frozenset(fellow_groups.get("CCM", [])),
        always_stroke_eligible=frozenset(stroke_eligible),
        telestroke_stroke_eligible=frozenset(),
        stroke_only_eligible=frozenset(),
    )


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
        parts = value.split("-")
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    if isinstance(value, (list, tuple)):
        return date(*value)
    if isinstance(value, date):
        return value
    raise ValueError(f"Cannot parse date: {value}")
