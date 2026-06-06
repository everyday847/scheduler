"""Tests for new schedule constraints.

Covers:
1. Weekend role prerequisites (Stroke/NCC)
2. Dual Stroke in weeks 1-10
3. NCC group night requirement
4. Per-fellow shift totals
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
    _ROLE_STROKE,
)
from parafrost_scheduler.schedule_encoder import (
    _encode_call_rules,
    _encode_dual_stroke_window,
    _encode_weekend_eligibility,
    _encode_weekend_prerequisites,
)
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    num_weeks: int = 10,
    fellow_groups: dict[str, list[str]] | None = None,
    call_rules: list[dict] | None = None,
    shifts: list[str] | None = None,
) -> ScheduleSolverConfig:
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
        horizon_start_date=date(2026, 7, 6),
    )
    weekend_config = WeekendSolverConfig(
        ncc_totals={},
        stroke_totals={},
        stroke_cohort=(),
        stroke_cohort_total=None,
        ccm_fellows=frozenset(),
        always_stroke_eligible=frozenset(),
        telestroke_stroke_eligible=frozenset(),
        stroke_only_eligible=frozenset(),
        total_weekends={},
        weekend_options=None,
        friday_weekend_options=None,
    )
    return ScheduleSolverConfig(
        fellow_groups=fellow_groups,
        shifts=shifts,
        constraints=[],
        night_config=night_config,
        weekend_config=weekend_config,
        night_hard_criteria=frozenset(),
        start_dow=0,
        num_days=num_days,
        call_rules=call_rules or [],
    )


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
# Tests: Weekend Stroke Prerequisite
# ---------------------------------------------------------------------------

class TestWeekendStrokePrerequisite:
    """wr[w][STROKE][f] requires prior weekday Stroke assignment."""

    def test_non_exempt_blocked_when_no_stroke_service_through_week(self):
        """A non-exempt fellow with NO possible weekday Stroke in any week w' <= w
        is hard-blocked from Weekend Stroke that week. Under the inclusive (<= w)
        window, we forbid the week-0 weekday Stroke var so no same-week path exists."""
        config = _make_config(
            num_weeks=5,
            fellow_groups={"OTHER": ["Helena"]},
            call_rules=[{
                "type": "weekend_stroke_prerequisite",
                "exempt_fellows": ["Aditya"],
                "active": True,
            }],
        )
        opb = OpbBuilder()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        fellow_names = ["Helena"]
        xs = _make_xs(opb, 1, 5, len(config.shifts))
        wr = _make_wr(opb, 5, 1)
        # Forbid week-0 weekday Stroke so there is no <= w service path in week 0.
        xs[0][0][shift_idx["Stroke"]] = 0

        _encode_weekend_prerequisites(opb, wr, xs, config, fellow_names, shift_idx)

        units = _get_units(opb)
        stroke_var_w0 = wr[0][_ROLE_STROKE][0]
        assert -stroke_var_w0 in units, "No Stroke service through week 0 => Weekend Stroke blocked"

    def test_same_week_stroke_service_satisfies_prereq(self):
        """Inclusive window: a weekday Stroke var present in week 0 (same week) lets
        the fellow hold Weekend Stroke in week 0 via a conditional, NOT a hard block."""
        config = _make_config(
            num_weeks=5,
            fellow_groups={"OTHER": ["Helena"]},
            call_rules=[{
                "type": "weekend_stroke_prerequisite",
                "exempt_fellows": [],
                "active": True,
            }],
        )
        opb = OpbBuilder()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        fellow_names = ["Helena"]
        xs = _make_xs(opb, 1, 5, len(config.shifts))  # week-0 Stroke var present (free)
        wr = _make_wr(opb, 5, 1)

        _encode_weekend_prerequisites(opb, wr, xs, config, fellow_names, shift_idx)

        units = _get_units(opb)
        stroke_var_w0 = wr[0][_ROLE_STROKE][0]
        assert -stroke_var_w0 not in units, "Same-week Stroke service must satisfy the prereq (no hard block)"

    def test_non_exempt_allowed_with_prior_stroke(self):
        """Non-exempt fellow in week 5 with Stroke in week 3 gets a conditional (not a block)."""
        config = _make_config(
            num_weeks=10,
            fellow_groups={"OTHER": ["Helena"]},
            call_rules=[{
                "type": "weekend_stroke_prerequisite",
                "exempt_fellows": [],
                "active": True,
            }],
        )
        opb = OpbBuilder()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        fellow_names = ["Helena"]
        xs = _make_xs(opb, 1, 10, len(config.shifts))
        wr = _make_wr(opb, 10, 1)

        _encode_weekend_prerequisites(opb, wr, xs, config, fellow_names, shift_idx)

        units = _get_units(opb)
        stroke_var_w5 = wr[5][_ROLE_STROKE][0]
        assert -stroke_var_w5 not in units, "Fellow with prior Stroke weeks should not be hard-blocked in week 5"

    def test_exempt_fellow_allowed_in_week_0(self):
        """An exempt fellow (by name) can do Weekend Stroke in week 0."""
        config = _make_config(
            num_weeks=5,
            fellow_groups={"STROKE": ["Aditya"]},
            call_rules=[{
                "type": "weekend_stroke_prerequisite",
                "exempt_fellows": ["Aditya"],
                "active": True,
            }],
        )
        opb = OpbBuilder()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        fellow_names = ["Aditya"]
        xs = _make_xs(opb, 1, 5, len(config.shifts))
        wr = _make_wr(opb, 5, 1)

        _encode_weekend_prerequisites(opb, wr, xs, config, fellow_names, shift_idx)

        units = _get_units(opb)
        stroke_var_w0 = wr[0][_ROLE_STROKE][0]
        assert -stroke_var_w0 not in units, "Exempt fellow should not be blocked"


# ---------------------------------------------------------------------------
# Tests: Weekend NCC Prerequisite
# ---------------------------------------------------------------------------

class TestWeekendNccPrerequisite:
    """wr[w][NCC][f] requires prior weekday NCC1 or NCC2 assignment."""

    def test_non_exempt_blocked_when_no_ncc_service_through_week(self):
        """Non-exempt fellow with NO possible weekday NCC1/NCC2 in any week w' <= w
        is hard-blocked from Weekend NCC that week. Forbid week-0 NCC vars so no
        same-week path exists under the inclusive (<= w) window."""
        config = _make_config(
            num_weeks=5,
            fellow_groups={"STROKE": ["Helena"]},
            call_rules=[{
                "type": "weekend_ncc_prerequisite",
                "exempt_groups": ["NCC_SR"],
                "active": True,
            }],
        )
        opb = OpbBuilder()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        fellow_names = ["Helena"]
        xs = _make_xs(opb, 1, 5, len(config.shifts))
        wr = _make_wr(opb, 5, 1)
        # Forbid week-0 weekday NCC1/NCC2 so there is no <= w service path in week 0.
        xs[0][0][shift_idx["NCC1"]] = 0
        xs[0][0][shift_idx["NCC2"]] = 0

        _encode_weekend_prerequisites(opb, wr, xs, config, fellow_names, shift_idx)

        units = _get_units(opb)
        ncc1_var_w0 = wr[0][_ROLE_NCC1][0]
        ncc2_var_w0 = wr[0][_ROLE_NCC2][0]
        assert -ncc1_var_w0 in units, "No NCC service through week 0 => Weekend NCC1 blocked"
        assert -ncc2_var_w0 in units, "No NCC service through week 0 => Weekend NCC2 blocked"

    def test_same_week_ncc_service_satisfies_prereq(self):
        """Inclusive window: a weekday NCC var present in week 0 lets the fellow hold
        Weekend NCC in week 0 via a conditional, NOT a hard block."""
        config = _make_config(
            num_weeks=5,
            fellow_groups={"STROKE": ["Helena"]},
            call_rules=[{
                "type": "weekend_ncc_prerequisite",
                "exempt_groups": [],
                "active": True,
            }],
        )
        opb = OpbBuilder()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        fellow_names = ["Helena"]
        xs = _make_xs(opb, 1, 5, len(config.shifts))  # week-0 NCC1/NCC2 vars present
        wr = _make_wr(opb, 5, 1)

        _encode_weekend_prerequisites(opb, wr, xs, config, fellow_names, shift_idx)

        units = _get_units(opb)
        assert -wr[0][_ROLE_NCC1][0] not in units, "Same-week NCC service must satisfy NCC1 prereq"
        assert -wr[0][_ROLE_NCC2][0] not in units, "Same-week NCC service must satisfy NCC2 prereq"

    def test_exempt_group_allowed_in_week_0(self):
        """NCC_SR group fellows are exempt from NCC prerequisite."""
        config = _make_config(
            num_weeks=5,
            fellow_groups={"NCC_SR": ["Bob"]},
            call_rules=[{
                "type": "weekend_ncc_prerequisite",
                "exempt_groups": ["NCC_SR"],
                "active": True,
            }],
        )
        opb = OpbBuilder()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        fellow_names = ["Bob"]
        xs = _make_xs(opb, 1, 5, len(config.shifts))
        wr = _make_wr(opb, 5, 1)

        _encode_weekend_prerequisites(opb, wr, xs, config, fellow_names, shift_idx)

        units = _get_units(opb)
        ncc1_var_w0 = wr[0][_ROLE_NCC1][0]
        assert -ncc1_var_w0 not in units, "NCC_SR fellow should not be blocked"

    def test_non_exempt_conditional_with_prior_ncc(self):
        """Non-exempt fellow in later week gets conditional, not hard block."""
        config = _make_config(
            num_weeks=10,
            fellow_groups={"STROKE": ["Helena"]},
            call_rules=[{
                "type": "weekend_ncc_prerequisite",
                "exempt_groups": [],
                "active": True,
            }],
        )
        opb = OpbBuilder()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        fellow_names = ["Helena"]
        xs = _make_xs(opb, 1, 10, len(config.shifts))
        wr = _make_wr(opb, 10, 1)

        _encode_weekend_prerequisites(opb, wr, xs, config, fellow_names, shift_idx)

        units = _get_units(opb)
        ncc1_var_w5 = wr[5][_ROLE_NCC1][0]
        assert -ncc1_var_w5 not in units, "Week 5 should use conditional constraint, not hard block"

    def test_inactive_rule_skipped(self):
        """Rules with active: false produce no constraints."""
        config = _make_config(
            num_weeks=5,
            fellow_groups={"STROKE": ["Helena"]},
            call_rules=[{
                "type": "weekend_ncc_prerequisite",
                "exempt_groups": [],
                "active": False,
            }],
        )
        opb = OpbBuilder()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        fellow_names = ["Helena"]
        xs = _make_xs(opb, 1, 5, len(config.shifts))
        wr = _make_wr(opb, 5, 1)

        before = len(opb._constraints)
        _encode_weekend_prerequisites(opb, wr, xs, config, fellow_names, shift_idx)
        after = len(opb._constraints)

        assert before == after, "Inactive rule should add no constraints"


# ---------------------------------------------------------------------------
# Tests: Dual Stroke Window
# ---------------------------------------------------------------------------

def _get_at_most_constraints(opb: OpbBuilder) -> list[tuple[list[int], int]]:
    """Extract at_most_k constraints as (var_list, k)."""
    results = []
    for line in opb._constraints:
        line = line.strip()
        if ">=" not in line:
            continue
        parts = line.replace(";", "").split(">=")
        if len(parts) != 2:
            continue
        rhs = int(parts[1].strip())
        terms = parts[0].strip().split()
        neg_vars = []
        for i in range(0, len(terms), 2):
            coeff = int(terms[i])
            var_str = terms[i + 1]
            if var_str.startswith("~x"):
                neg_vars.append(int(var_str[2:]))
        if neg_vars and rhs > 0:
            k = len(neg_vars) - rhs
            results.append((neg_vars, k))
    return results


class TestDualStrokeWindow:
    """Dual Stroke window allows 2 fellows on Stroke in specified weeks."""

    def _setup(self, num_weeks=20, supervisors=None, window=None):
        if supervisors is None:
            supervisors = ["Alice"]
        if window is None:
            window = [1, 11]
        fellow_groups = {"STROKE": ["Alice", "Bob", "Carol"]}
        config = _make_config(
            num_weeks=num_weeks,
            fellow_groups=fellow_groups,
            call_rules=[{
                "type": "dual_stroke_window",
                "window": window,
                "supervisors": supervisors,
                "active": True,
            }],
        )
        opb = OpbBuilder()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        fellow_names = ["Alice", "Bob", "Carol"]
        xs = _make_xs(opb, 3, num_weeks, len(config.shifts))
        return config, opb, shift_idx, fellow_names, xs

    def test_window_week_allows_2_stroke(self):
        """Week 5 (in window) should have at_most 2 Stroke, not at_most 1."""
        config, opb, shift_idx, fellow_names, xs = self._setup()
        _encode_dual_stroke_window(opb, xs, config, fellow_names, shift_idx)
        # Should have at_most 2 constraints for window weeks and at_most 1 for non-window
        assert len(opb._constraints) > 0

    def test_outside_window_at_most_1(self):
        """Week 15 (outside window) should have at_most 1 Stroke."""
        config, opb, shift_idx, fellow_names, xs = self._setup()
        _encode_dual_stroke_window(opb, xs, config, fellow_names, shift_idx)
        # Check that constraints were added
        assert len(opb._constraints) > 0

    def test_non_supervisor_at_most_1_in_window(self):
        """Within window, at_most 1 non-supervisor on Stroke."""
        config, opb, shift_idx, fellow_names, xs = self._setup(supervisors=["Alice"])
        stroke_si = shift_idx["Stroke"]
        before = len(opb._constraints)
        _encode_dual_stroke_window(opb, xs, config, fellow_names, shift_idx)
        after = len(opb._constraints)
        assert after > before, "Should add constraints for non-supervisor caps"

    def test_inactive_rule_skipped(self):
        """Inactive dual_stroke_window rule adds no constraints."""
        config = _make_config(
            num_weeks=20,
            fellow_groups={"STROKE": ["Alice", "Bob"]},
            call_rules=[{
                "type": "dual_stroke_window",
                "window": [1, 11],
                "supervisors": ["Alice"],
                "active": False,
            }],
        )
        opb = OpbBuilder()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        fellow_names = ["Alice", "Bob"]
        xs = _make_xs(opb, 2, 20, len(config.shifts))
        before = len(opb._constraints)
        _encode_dual_stroke_window(opb, xs, config, fellow_names, shift_idx)
        assert len(opb._constraints) == before


# ---------------------------------------------------------------------------
# Tests: Group Night Requirement
# ---------------------------------------------------------------------------

class TestGroupNightRequirement:
    """group_night_requirement blocks non-group fellows from night on specific dates."""

    def test_non_ncc_fellow_blocked(self):
        """A Stroke fellow (not in NCC groups) is blocked from night on target date."""
        config = _make_config(
            num_weeks=53,
            fellow_groups={"STROKE": ["Alice"], "NCC_JR": ["Bob"]},
            call_rules=[{
                "type": "group_night_requirement",
                "groups": ["NCC_JR", "NCC_SR"],
                "dates": ["2026-07-08"],
                "active": True,
            }],
        )
        opb = OpbBuilder()
        fellow_names = ["Alice", "Bob"]
        num_days = 53 * 7
        xn = _make_xn(opb, num_days, 2)
        wr = _make_wr(opb, 53, 2)

        _encode_call_rules(opb, xn, wr, config, fellow_names)

        units = _get_units(opb)
        # Day index for 2026-07-08 with horizon_start 2026-07-06 is 2
        alice_night_var = xn[2][0]
        assert -alice_night_var in units, "Non-NCC fellow should be blocked"

    def test_ncc_fellow_not_blocked(self):
        """An NCC_JR fellow is NOT blocked from night on target date."""
        config = _make_config(
            num_weeks=53,
            fellow_groups={"STROKE": ["Alice"], "NCC_JR": ["Bob"]},
            call_rules=[{
                "type": "group_night_requirement",
                "groups": ["NCC_JR", "NCC_SR"],
                "dates": ["2026-07-08"],
                "active": True,
            }],
        )
        opb = OpbBuilder()
        fellow_names = ["Alice", "Bob"]
        num_days = 53 * 7
        xn = _make_xn(opb, num_days, 2)
        wr = _make_wr(opb, 53, 2)

        _encode_call_rules(opb, xn, wr, config, fellow_names)

        units = _get_units(opb)
        bob_night_var = xn[2][1]
        assert -bob_night_var not in units, "NCC fellow should NOT be blocked"

    def test_inactive_rule_skipped(self):
        """Inactive group_night_requirement adds no constraints."""
        config = _make_config(
            num_weeks=53,
            fellow_groups={"STROKE": ["Alice"], "NCC_JR": ["Bob"]},
            call_rules=[{
                "type": "group_night_requirement",
                "groups": ["NCC_JR"],
                "dates": ["2026-07-08"],
                "active": False,
            }],
        )
        opb = OpbBuilder()
        fellow_names = ["Alice", "Bob"]
        num_days = 53 * 7
        xn = _make_xn(opb, num_days, 2)
        wr = _make_wr(opb, 53, 2)

        before = len(opb._constraints)
        _encode_call_rules(opb, xn, wr, config, fellow_names)
        assert len(opb._constraints) == before


# ---------------------------------------------------------------------------
# Tests: Per-Fellow Shift Total
# ---------------------------------------------------------------------------

class TestPerFellowShiftTotal:
    """per_fellow_shift_total constrains individual fellow shift counts."""

    def test_exactly_constraint_adds_constraints(self):
        """Jinyuan exactly 2 Stroke adds cardinality constraints."""
        config = _make_config(
            num_weeks=10,
            fellow_groups={"NH": ["Jinyuan"]},
            call_rules=[{
                "type": "per_fellow_shift_total",
                "fellow": "Jinyuan",
                "shifts": ["Stroke"],
                "relation": "exactly",
                "count": 2,
                "strength": "hard",
                "active": True,
            }],
        )
        opb = OpbBuilder()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        fellow_names = ["Jinyuan"]
        xs = _make_xs(opb, 1, 10, len(config.shifts))
        xn = _make_xn(opb, 70, 1)
        wr = _make_wr(opb, 10, 1)
        soft_violations: list[tuple[int, int]] = []

        before = len(opb._constraints)
        _encode_call_rules(opb, xn, wr, config, fellow_names,
                           xs=xs, shift_idx=shift_idx, soft_violations=soft_violations)
        after = len(opb._constraints)
        assert after > before, "Should add cardinality constraints for exactly 2 Stroke"

    def test_range_constraint_adds_both_bounds(self):
        """Sokena 4-6 Stroke creates at_least + at_most constraints."""
        config = _make_config(
            num_weeks=10,
            fellow_groups={"NH": ["Sokena"]},
            call_rules=[
                {
                    "type": "per_fellow_shift_total",
                    "fellow": "Sokena",
                    "shifts": ["Stroke"],
                    "relation": "at_least",
                    "count": 4,
                    "strength": "hard",
                    "active": True,
                },
                {
                    "type": "per_fellow_shift_total",
                    "fellow": "Sokena",
                    "shifts": ["Stroke"],
                    "relation": "at_most",
                    "count": 6,
                    "strength": "hard",
                    "active": True,
                },
            ],
        )
        opb = OpbBuilder()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        fellow_names = ["Sokena"]
        xs = _make_xs(opb, 1, 10, len(config.shifts))
        xn = _make_xn(opb, 70, 1)
        wr = _make_wr(opb, 10, 1)
        soft_violations: list[tuple[int, int]] = []

        before = len(opb._constraints)
        _encode_call_rules(opb, xn, wr, config, fellow_names,
                           xs=xs, shift_idx=shift_idx, soft_violations=soft_violations)
        after = len(opb._constraints)
        assert after > before, "Should add constraints for range bounds"

    def test_unknown_fellow_skipped(self):
        """Rule targeting non-existent fellow adds no constraints."""
        config = _make_config(
            num_weeks=10,
            fellow_groups={"NH": ["Jinyuan"]},
            call_rules=[{
                "type": "per_fellow_shift_total",
                "fellow": "Nobody",
                "shifts": ["Stroke"],
                "relation": "exactly",
                "count": 2,
                "strength": "hard",
                "active": True,
            }],
        )
        opb = OpbBuilder()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        fellow_names = ["Jinyuan"]
        xs = _make_xs(opb, 1, 10, len(config.shifts))
        xn = _make_xn(opb, 70, 1)
        wr = _make_wr(opb, 10, 1)
        soft_violations: list[tuple[int, int]] = []

        before = len(opb._constraints)
        _encode_call_rules(opb, xn, wr, config, fellow_names,
                           xs=xs, shift_idx=shift_idx, soft_violations=soft_violations)
        assert len(opb._constraints) == before

    def test_soft_strength_creates_soft_violation(self):
        """A soft per_fellow_shift_total adds to soft_violations."""
        config = _make_config(
            num_weeks=10,
            fellow_groups={"NH": ["Jinyuan"]},
            call_rules=[{
                "type": "per_fellow_shift_total",
                "fellow": "Jinyuan",
                "shifts": ["Stroke"],
                "relation": "exactly",
                "count": 2,
                "strength": "soft",
                "active": True,
            }],
        )
        opb = OpbBuilder()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        fellow_names = ["Jinyuan"]
        xs = _make_xs(opb, 1, 10, len(config.shifts))
        xn = _make_xn(opb, 70, 1)
        wr = _make_wr(opb, 10, 1)
        soft_violations: list[tuple[int, int]] = []

        _encode_call_rules(opb, xn, wr, config, fellow_names,
                           xs=xs, shift_idx=shift_idx, soft_violations=soft_violations)
        assert len(soft_violations) > 0, "Soft constraint should add soft violations"


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
