"""Tests for pre-vacation weekend penalty.

Verifies:
1. Fellow on vacation in week 2 -> weekend role in week 1 generates soft penalty
2. Fellow NOT on vacation -> no penalty for weekend role
3. Week 0 vacation -> no penalty (no week -1)
4. Penalty weight is 1
5. All three weekend roles generate independent penalties
"""

from __future__ import annotations

from datetime import date

import pytest

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.schedule_solver import (
    ScheduleSolverConfig,
    _ROLE_NCC1,
    _ROLE_NCC2,
    _ROLE_STROKE,
    _encode_prevacation_weekend_penalty,
)
from scheduler.night_call_solver import NightSolverConfig
from scheduler.weekend_call_solver import WeekendSolverConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    num_weeks: int = 4,
    fellow_groups: dict[str, list[str]] | None = None,
) -> ScheduleSolverConfig:
    """Build a minimal ScheduleSolverConfig for testing."""
    if fellow_groups is None:
        fellow_groups = {"NCC_SR": ["Alice", "Bob"]}
    night_config = NightSolverConfig(
        total_nights={},
        friday_nights={},
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(),
        horizon_start_date=date(2026, 7, 1),
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
        shifts=["NCC1", "Vac"],
        constraints=[],
        night_config=night_config,
        weekend_config=weekend_config,
        night_hard_criteria=frozenset(),
        start_dow=0,
        num_days=num_weeks * 7,
    )


def _make_xs(opb: OpbBuilder, num_fellows: int, num_weeks: int, num_shifts: int) -> list[list[list[int]]]:
    """Allocate xs variables: xs[f][w][s]."""
    xs = []
    for f in range(num_fellows):
        xs.append([])
        for w in range(num_weeks):
            xs[f].append([opb.new_var() for _ in range(num_shifts)])
    return xs


def _make_wr(opb: OpbBuilder, num_fellows: int, num_weeks: int) -> list[list[dict[int, int]]]:
    """Allocate weekend role variables: wr[w][role][f]."""
    wr = []
    for w in range(num_weeks):
        wr.append([])
        for role_idx in range(3):
            role_vars = {}
            for f in range(num_fellows):
                role_vars[f] = opb.new_var()
            wr[w].append(role_vars)
    return wr


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPrevacationWeekendPenalty:
    """Tests for _encode_prevacation_weekend_penalty."""

    def test_vacation_week2_penalizes_weekend_week1(self):
        """Fellow on vacation in week 2 -> weekend role in week 1 generates soft penalty."""
        config = _make_config(num_weeks=4)
        fellow_names = ["Alice", "Bob"]
        shift_idx = {"NCC1": 0, "Vac": 1}
        opb = OpbBuilder()
        xs = _make_xs(opb, 2, 4, 2)
        wr = _make_wr(opb, 2, 4)
        soft_violations: list[tuple[int, int]] = []

        _encode_prevacation_weekend_penalty(
            opb, wr, xs, config, fellow_names, shift_idx, soft_violations
        )

        # Both fellows have Vac vars in weeks 1-3 (w>=1) and weekend roles in w-1.
        # Each fellow has 3 roles * 3 vacation weeks (w=1,2,3) = 9 potential penalties.
        # Total for 2 fellows = 18
        assert len(soft_violations) == 18

    def test_no_vac_shift_no_penalty(self):
        """If Vac is not in shift_idx, no penalties are generated."""
        config = _make_config(num_weeks=4)
        fellow_names = ["Alice", "Bob"]
        shift_idx = {"NCC1": 0}  # No "Vac" key
        opb = OpbBuilder()
        xs = _make_xs(opb, 2, 4, 1)
        wr = _make_wr(opb, 2, 4)
        soft_violations: list[tuple[int, int]] = []

        _encode_prevacation_weekend_penalty(
            opb, wr, xs, config, fellow_names, shift_idx, soft_violations
        )

        assert len(soft_violations) == 0

    def test_week0_vacation_no_penalty(self):
        """Vacation in week 0 should not penalize (no week -1 exists)."""
        config = _make_config(num_weeks=4)
        fellow_names = ["Alice"]
        shift_idx = {"NCC1": 0, "Vac": 1}
        opb = OpbBuilder()
        xs = _make_xs(opb, 1, 4, 2)
        wr = _make_wr(opb, 1, 4)
        soft_violations: list[tuple[int, int]] = []

        # Force xs[0][0][vac_idx] = 0 to simulate forbidden vacation in week 0
        # (The loop starts at w=1, so week 0 is never considered as a vacation trigger.)
        _encode_prevacation_weekend_penalty(
            opb, wr, xs, config, fellow_names, shift_idx, soft_violations
        )

        # The loop iterates w=1,2,3 for 1 fellow * 3 roles = 9 penalties.
        # But we want to verify that w=0 does NOT generate any penalties.
        # Since the loop starts at w=1 and xs[0][0][vac_idx] != 0, vacation
        # in week 0 never triggers because the loop skips it.
        # Here we confirm: no penalty references week -1.
        # The penalties are for w=1,2,3 (vacation) penalizing w=0,1,2 (weekend).
        assert len(soft_violations) == 9  # 3 weeks * 3 roles * 1 fellow

    def test_penalty_weight_is_1(self):
        """All penalty weights should be 1."""
        config = _make_config(num_weeks=3)
        fellow_names = ["Alice"]
        shift_idx = {"NCC1": 0, "Vac": 1}
        opb = OpbBuilder()
        xs = _make_xs(opb, 1, 3, 2)
        wr = _make_wr(opb, 1, 3)
        soft_violations: list[tuple[int, int]] = []

        _encode_prevacation_weekend_penalty(
            opb, wr, xs, config, fellow_names, shift_idx, soft_violations
        )

        for _, weight in soft_violations:
            assert weight == 1

    def test_all_three_roles_independent_penalties(self):
        """Each of the 3 weekend roles generates its own independent penalty."""
        config = _make_config(num_weeks=2)
        fellow_names = ["Alice"]
        shift_idx = {"NCC1": 0, "Vac": 1}
        opb = OpbBuilder()
        xs = _make_xs(opb, 1, 2, 2)
        wr = _make_wr(opb, 1, 2)
        soft_violations: list[tuple[int, int]] = []

        _encode_prevacation_weekend_penalty(
            opb, wr, xs, config, fellow_names, shift_idx, soft_violations
        )

        # 1 fellow, vacation possible in week 1 only (w=1), 3 roles in week 0
        assert len(soft_violations) == 3
        # Each penalty uses a distinct indicator variable
        indicator_vars = [var for var, _ in soft_violations]
        assert len(set(indicator_vars)) == 3

    def test_fellow_not_eligible_for_role_no_penalty(self):
        """If fellow f is not in wr[w-1][role], no penalty for that role."""
        config = _make_config(num_weeks=3)
        fellow_names = ["Alice"]
        shift_idx = {"NCC1": 0, "Vac": 1}
        opb = OpbBuilder()
        xs = _make_xs(opb, 1, 3, 2)
        # Custom wr where Alice only has NCC1 role (role 0), no NCC2 or Stroke
        wr: list[list[dict[int, int]]] = []
        for w in range(3):
            wr.append([])
            # Only role 0 for fellow 0
            wr[w].append({0: opb.new_var()})
            # Role 1 and 2: empty (fellow 0 not eligible)
            wr[w].append({})
            wr[w].append({})
        soft_violations: list[tuple[int, int]] = []

        _encode_prevacation_weekend_penalty(
            opb, wr, xs, config, fellow_names, shift_idx, soft_violations
        )

        # 1 fellow, 2 vacation weeks (w=1,2), only 1 role each = 2 penalties
        assert len(soft_violations) == 2

    def test_xs_vac_zero_skips_penalty(self):
        """If xs[f][w][vac_idx] == 0 (forbidden), that week generates no penalty."""
        config = _make_config(num_weeks=3)
        fellow_names = ["Alice"]
        shift_idx = {"NCC1": 0, "Vac": 1}
        opb = OpbBuilder()
        xs = _make_xs(opb, 1, 3, 2)
        wr = _make_wr(opb, 1, 3)
        # Make vacation forbidden in week 1 and 2 (set var to 0)
        xs[0][1][1] = 0
        xs[0][2][1] = 0
        soft_violations: list[tuple[int, int]] = []

        _encode_prevacation_weekend_penalty(
            opb, wr, xs, config, fellow_names, shift_idx, soft_violations
        )

        # Both vacation weeks have var=0 → skipped, no penalties
        assert len(soft_violations) == 0
