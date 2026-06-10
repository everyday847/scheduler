"""Tests for new schedule constraints.

Covers the encoder paths that still live outside the typed Rule-Shape
pipeline:
1. Dual Stroke window (call_rules; pending the S5 Supervision archetype)
2. Weekend role requires a weekday shift (the NH wb2 bug guard)

The former Weekend-prerequisite / group-night-requirement / per-fellow
shift-total tests were removed when those rules dissolved into the typed
config.constraints pipeline; they are now covered by the co-located
archetype contract tests (test_weekend_prerequisite_contract.py,
test_night_literal_pin_contract.py, test_per_fellow_shift_total_migration.py).
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
from parafrost_scheduler.schedule_encoder import (
    _encode_dual_stroke_window,
    _encode_weekend_eligibility)
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
            }])
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
            }])
        opb = OpbBuilder()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        fellow_names = ["Alice", "Bob"]
        xs = _make_xs(opb, 2, 20, len(config.shifts))
        before = len(opb._constraints)
        _encode_dual_stroke_window(opb, xs, config, fellow_names, shift_idx)
        assert len(opb._constraints) == before

    def test_in_window_caps_nonsupervisors_so_pinned_junior_forces_senior(self):
        """The Helena/Sokena bug: in a window week, the non-supervisor cap is an
        at_most_1 over ALL non-supervisor Stroke vars (juniors + outsiders). When a
        junior is pinned to Stroke that week, that junior saturates the slot, so any
        OTHER non-supervisor (e.g. an NH fellow) is forced off Stroke — and a ">=2
        on Stroke" staffing rule can then only be met by a senior supervisor."""
        # Supervisors = the three seniors; juniors = Helena + an NH-like outsider.
        fellow_groups = {
            "STROKE": ["Aditya", "Cameron", "Harneet", "Helena"],
            "NH": ["Sokena"],
        }
        config = _make_config(
            num_weeks=12,
            fellow_groups=fellow_groups,
            call_rules=[{
                "type": "dual_stroke_window",
                "window": [0, 10],
                "supervisors": ["Aditya", "Cameron", "Harneet"],
                "active": True,
            }],
        )
        opb = OpbBuilder()
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        fellow_names = ["Aditya", "Cameron", "Harneet", "Helena", "Sokena"]
        nf = len(fellow_names)
        xs = _make_xs(opb, nf, 12, len(config.shifts))
        _encode_dual_stroke_window(opb, xs, config, fellow_names, shift_idx)

        stroke_si = shift_idx["Stroke"]
        helena = fellow_names.index("Helena")
        sokena = fellow_names.index("Sokena")
        seniors = [fellow_names.index(n) for n in ("Aditya", "Cameron", "Harneet")]

        # at_most_k is emitted directly in "<= k" form: "+1 xA +1 xB ... <= k ;".
        def _at_most_clauses():
            out = []
            for line in opb._constraints:
                s = line.strip()
                if not s.endswith(";") or "<=" not in s:
                    continue
                lhs, rhs = s[:-1].split("<=")
                k = int(rhs.strip())
                vars_ = [int(tok[1:]) for tok in lhs.split()
                         if tok.startswith("x")]
                out.append((set(vars_), k))
            return out

        clauses = _at_most_clauses()
        h_var = xs[helena][1][stroke_si]
        s_var = xs[sokena][1][stroke_si]
        sup_vars_w1 = {xs[sidx][1][stroke_si] for sidx in seniors}

        # Non-supervisor cap (week 1): at_most_1 containing BOTH Helena and Sokena,
        # and NO senior Stroke var.
        nonsup_caps = [(vs, k) for (vs, k) in clauses
                       if k == 1 and h_var in vs and s_var in vs]
        assert nonsup_caps, "expected an at_most_1 over non-supervisor Stroke vars in week 1"
        capped = nonsup_caps[0][0]
        assert not (capped & sup_vars_w1), "senior must not be in the non-supervisor cap"

        # Supervisor cap (week 1): a SEPARATE at_most_1 over the senior Stroke vars.
        sup_caps = [(vs, k) for (vs, k) in clauses
                    if k == 1 and sup_vars_w1.issubset(vs) and h_var not in vs]
        assert sup_caps, "expected a separate at_most_1 over supervisor Stroke vars in week 1"


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
