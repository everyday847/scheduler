"""Bridge between the web API / annual YAML and the RoundingSat schedule solver."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict

import yaml

from .annual_rules import constraints_from_config as annual_constraints_from_config
from .palette_rules import palette_rule_to_constraints
from .palette_derivations import derive_forbidden_shifts
from .shift_palette import ShiftPalette
from .standing_rules import constraints_from_config as standing_constraints_from_config

from parafrost_scheduler.orchestrator import (
    build_night_config_from_request,
    build_weekend_config_from_request,
    get_runner as _orchestrator_runner,
)
from parafrost_scheduler.schedule_types import ScheduleSolverConfig

_REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = _REPO / "config"
STANDING_RULE_CONFIG = CONFIG_DIR / "standing" / "stanford-fellowship-v3.yaml"
DEFAULT_ANNUAL_CONFIG = CONFIG_DIR / "annual" / "my-2026-2027-v3.yaml"


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

    night_config = build_night_config_from_request(raw_request, fellow_groups)
    weekend_config = build_weekend_config_from_request(raw_request, fellow_groups)

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

    # Shift Attributes (ADR-0003): the shift_palette block drives the historical
    # night/weekend shift-name frozensets. Prefer the request's copy (it survives
    # the standing_rules override path used by experiment.assemble_config); else
    # take it from the standing config loaded from disk.
    palette_block = raw_request.get("shift_palette")
    if palette_block is None:
        palette_block = standing_config.get("shift_palette")
    shift_palette = ShiftPalette.from_config(palette_block)

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
        shift_palette=shift_palette,
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


def get_runner():
    """Get a RoundingSatRunner instance (delegates to orchestrator)."""
    return _orchestrator_runner()


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


def _require_dict(request: Dict[str, Any], key: str) -> Dict[str, list[str]]:
    value = request.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object.")
    return {k: list(v) for k, v in value.items()}
