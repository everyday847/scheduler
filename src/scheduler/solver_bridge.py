"""Bridge between the web API request format and the RoundingSat schedule solver."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from .annual_rules import constraints_from_config as annual_constraints_from_config
from .night_call_solver import NightSolverConfig
from .standing_rules import constraints_from_config as standing_constraints_from_config
from .weekend_call_solver import WeekendSolverConfig

from parafrost_scheduler.schedule_solver import ScheduleSolverConfig
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

STANDING_RULE_CONFIG = Path(__file__).resolve().parents[2] / "config" / "standing" / "stanford-fellowship.yaml"
ROUNDINGSAT_BINARY = Path(__file__).resolve().parents[2] / "new_approach" / "vendor" / "roundingsat" / "build" / "roundingsat"

DEFAULT_SHIFTS = [
    "SICU", "MICU", "NS", "Anaesthesia", "NCC1", "NCC2", "Swing",
    "Elec", "Vac", "Stroke", "Telestroke/Clinic", "Clinic/Elective",
    "SCVMC Rehab", "NIR", "ISC",
]


def build_solver_config_from_request(
    raw_request: Dict[str, Any] | None,
    *,
    night_config: NightSolverConfig | None = None,
    weekend_config: WeekendSolverConfig | None = None,
) -> ScheduleSolverConfig:
    """Convert a web API request into a ScheduleSolverConfig for the RoundingSat solver."""
    if raw_request is None:
        raise ValueError("Request body must be JSON.")

    fellow_groups = _fellow_groups(raw_request.get("fellow_groups", {}))
    shifts = _string_list(raw_request.get("shifts", DEFAULT_SHIFTS))
    fellow_week_pairs = raw_request.get("fellow_week_pairs", {})
    annual_rules = raw_request.get("annual_rules")

    standing_config = yaml.safe_load(STANDING_RULE_CONFIG.read_text())
    constraints = [
        *standing_constraints_from_config(standing_config),
        *annual_constraints_from_config(annual_rules, fellow_week_pairs=fellow_week_pairs),
    ]

    return ScheduleSolverConfig(
        fellow_groups=fellow_groups,
        shifts=shifts,
        constraints=constraints,
        night_config=night_config or NightSolverConfig(),
        weekend_config=weekend_config or WeekendSolverConfig(),
    )


def get_runner() -> RoundingSatRunner:
    """Get a RoundingSatRunner instance with the default binary path."""
    return RoundingSatRunner(ROUNDINGSAT_BINARY)


def _fellow_groups(value: Any) -> Dict[str, list[str]]:
    if not isinstance(value, dict):
        raise ValueError("fellow_groups must be an object.")
    groups = {}
    for group_name, fellows in value.items():
        if not isinstance(fellows, list):
            raise ValueError(f"fellow_groups.{group_name} must be a list.")
        groups[group_name.strip()] = [f.strip() for f in fellows if isinstance(f, str) and f.strip()]
    return groups


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return list(DEFAULT_SHIFTS)
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]
