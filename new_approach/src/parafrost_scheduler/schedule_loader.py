"""Config loader for the full joint schedule solver.

Extracted from schedule_solver.py — loads a ScheduleSolverConfig from
annual + standing YAML configuration files.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from scheduler.night_call_types import NightSolverConfig
from scheduler.night_policy_types import (
    CRITERION_ANAESTHESIA,
    CRITERION_FRIDAY_WEEKEND_NCC1,
    NightPolicyWeights,
)
from scheduler.standing_rules import (
    constraints_from_config as standing_constraints_from_config,
)
from scheduler.weekend_call_types import WeekendSolverConfig

from parafrost_scheduler.schedule_types import ScheduleSolverConfig


def load_schedule_config(
    annual_config_path: str | Path,
    standing_config_path: str | Path,
    *,
    night_config: NightSolverConfig | None = None,
    weekend_config: WeekendSolverConfig | None = None,
    night_weights: NightPolicyWeights | None = None,
    night_hard_criteria: frozenset[str] | None = None,
) -> ScheduleSolverConfig:
    """Load schedule solver config from YAML files."""
    annual = yaml.safe_load(Path(annual_config_path).read_text())
    standing = yaml.safe_load(Path(standing_config_path).read_text())

    fellow_groups = annual["fellow_groups"]
    shifts = annual["shifts"]
    constraints = standing_constraints_from_config(standing)

    return ScheduleSolverConfig(
        fellow_groups=fellow_groups,
        shifts=shifts,
        constraints=constraints,
        night_config=night_config or NightSolverConfig(),
        weekend_config=weekend_config or WeekendSolverConfig(),
        night_weights=night_weights or NightPolicyWeights(),
        night_hard_criteria=night_hard_criteria or frozenset(
            {CRITERION_ANAESTHESIA, CRITERION_FRIDAY_WEEKEND_NCC1}
        ),
    )
