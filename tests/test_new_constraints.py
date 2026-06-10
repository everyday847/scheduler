"""Tests for new schedule constraints.

Covers the one encoder path that still lives outside a co-located archetype:
weekend role requires a weekday shift (the NH wb2 bug guard, via
_encode_weekend_eligibility).

The former call_rules tests (prerequisite / group-night / per-fellow /
dual-stroke-window) were removed when those rules dissolved into the typed
config.constraints pipeline; they are now covered by the co-located archetype
contract tests (test_weekend_prerequisite_contract.py,
test_night_literal_pin_contract.py, test_per_fellow_shift_total_migration.py,
test_windowed_supervision_contract.py).
"""

from __future__ import annotations

from dataclasses import field
from datetime import date

import pytest

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.schedule_types import (
    ScheduleSolverConfig,
    _ROLE_NCC1,
    _ROLE_NCC2,
    _ROLE_STROKE)
from parafrost_scheduler.schedule_encoder import _encode_weekend_eligibility
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    num_weeks: int = 10,
    fellow_groups: dict[str, list[str]] | None = None,
    call_rules: list[dict] | None = None,
    shifts: list[str] | None = None) -> ScheduleSolverConfig:
    if fellow_groups is None:
        fellow_groups = {"STROKE": ["Alice"], "NCC_SR": ["Bob"], "NCC_JR": ["Carol"]}
    if shifts is None:
        shifts = ["NCC1", "NCC2", "Stroke", "Elec"]
    num_fellows = sum(len(v) for v in fellow_groups.values())
    num_days = num_weeks * 7
    night_config = NightSolverConfig(
        total_nights={},
        friday_nights={},
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(),
        horizon_start_date=date(2026, 7, 6))
    weekend_config = WeekendSolverConfig(
        ncc_totals={},
        stroke_totals={},
        stroke_cohort=(),
        stroke_cohort_total=None,
        ccm_fellows=frozenset(),
        always_stroke_eligible=frozenset(),
        telestroke_stroke_eligible=frozenset(),
        stroke_only_eligible=frozenset())
    return ScheduleSolverConfig(
        fellow_groups=fellow_groups,
        shifts=shifts,
        constraints=[],
        night_config=night_config,
        weekend_config=weekend_config,
        night_hard_criteria=frozenset(),
        start_dow=0,
        num_days=num_days,
        call_rules=call_rules or [])


def _make_xs(opb: OpbBuilder, num_fellows: int, num_weeks: int, num_shifts: int) -> list[list[list[int]]]:
    xs = []
    for f in range(num_fellows):
        xs.append([])
        for w in range(num_weeks):
            xs[f].append([opb.new_var() for _ in range(num_shifts)])
    return xs


def _make_wr(opb: OpbBuilder, num_weeks: int, num_fellows: int) -> list[list[dict[int, int]]]:
    wr = []
    for w in range(num_weeks):
        week_roles = []
        for role_idx in range(3):
            role_vars = {}
            for f in range(num_fellows):
                role_vars[f] = opb.new_var()
            week_roles.append(role_vars)
        wr.append(week_roles)
    return wr


def _make_xn(opb: OpbBuilder, num_days: int, num_fellows: int) -> list[list[int]]:
    xn = []
    for d in range(num_days):
        row = []
        for f in range(num_fellows):
            row.append(opb.new_var())
        xn.append(row)
    return xn


def _get_units(opb: OpbBuilder) -> list[int]:
    units = []
    for line in opb._constraints:
        line = line.strip()
        parts = line.split()
        if len(parts) == 5 and parts[0] == "+1" and parts[2] == ">=" and parts[3] == "1" and parts[4] == ";":
            var_str = parts[1]
            if var_str.startswith("~x"):
                var_idx = int(var_str[2:])
                units.append(-var_idx)
            elif var_str.startswith("x"):
                var_idx = int(var_str[1:])
                units.append(var_idx)
    return units


# ---------------------------------------------------------------------------
# Tests: No weekend role without a weekday shift (NH bug)
# ---------------------------------------------------------------------------

class TestWeekendRequiresWeekdayShift:
    """A fellow must never get a weekend role in a week where they have NO
    weekday shift (the wb2 NH bug)."""

    def test_empty_weekday_week_blocks_all_weekend_roles(self):
        """A week with zero weekday shift vars hard-forbids every weekend role."""
        config = _make_config(num_weeks=4, fellow_groups={"NH": ["Jinyuan"]})
        opb = OpbBuilder()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        fellow_names = ["Jinyuan"]
        xs = _make_xs(opb, 1, 4, len(config.shifts))
        wr = _make_wr(opb, 4, 1)
        # Week 2: fellow has no possible weekday shift (all forbidden).
        xs[0][2] = [0] * len(config.shifts)

        _encode_weekend_eligibility(opb, wr, xs, config, fellow_names, shift_idx)

        units = _get_units(opb)
        for role_idx in (_ROLE_NCC1, _ROLE_NCC2, _ROLE_STROKE):
            wr_var = wr[2][role_idx][0]
            assert -wr_var in units, (
                f"Empty weekday week must hard-block weekend role {role_idx}"
            )

    def test_weekend_role_implies_some_weekday_shift(self):
        """With weekday shifts available, a weekend role implies one is taken
        (an implication constraint, not a unit block)."""
        config = _make_config(num_weeks=4, fellow_groups={"NH": ["Jinyuan"]})
        opb = OpbBuilder()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        fellow_names = ["Jinyuan"]
        xs = _make_xs(opb, 1, 4, len(config.shifts))
        wr = _make_wr(opb, 4, 1)

        _encode_weekend_eligibility(opb, wr, xs, config, fellow_names, shift_idx)

        # Week 1 has weekday shift vars, so the NCC1 role is NOT unit-blocked.
        units = _get_units(opb)
        wr_ncc1 = wr[1][_ROLE_NCC1][0]
        assert -wr_ncc1 not in units, "Role must not be hard-blocked when weekday shifts exist"
        # And an implication constraint referencing the role exists.
        role_constraints = [
            c for c in opb._constraints
            if f"~x{wr_ncc1} " in c and ">= 1" in c and "+1 x" in c
        ]
        assert role_constraints, "Expected wr -> OR(weekday shifts) implication"
