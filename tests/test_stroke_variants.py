"""Tests for the experimental Stroke variant constraints:

- ABPN night-block (abpn_night_block flag): ABPN blocks prior-Sun..Thu nights.
- Helena dual-stroke soft penalty (dual_stroke_helena flag): two-tier penalty.
- Stroke wk26/27 on/off toggle (stroke_wk2627_toggle flag).
"""

from __future__ import annotations

from datetime import date

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.schedule_types import (
    ScheduleSolverConfig,
    STROKE_WK2627_ON_SHIFTS,
)
from parafrost_scheduler.schedule_encoder import (
    _encode_stroke_wk2627_toggle,
    _encode_dual_stroke_helena,
)
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig


def _config(*, num_days=28 * 7, dual_stroke_helena=None, stroke_wk2627_toggle="off",
            fellow_groups=None, shifts=None):
    night_config = NightSolverConfig(
        total_nights={}, friday_nights={}, total_night_multisets=(),
        friday_night_multisets=(), ccm_fellows=frozenset(), holiday_dates=(),
        horizon_start_date=date(2026, 7, 1),
    )
    weekend_config = WeekendSolverConfig(
        ncc_totals={}, stroke_totals={}, stroke_cohort=(), stroke_cohort_total=None,
        ccm_fellows=frozenset(), always_stroke_eligible=frozenset(),
        telestroke_stroke_eligible=frozenset(), stroke_only_eligible=frozenset(),
    )
    return ScheduleSolverConfig(
        fellow_groups=fellow_groups or {"STROKE": ["Helena", "Aditya"]},
        shifts=shifts or ["Stroke", "Telestroke/Clinic", "Swing", "NCC1", "NCC2", "Elec"],
        constraints=[], night_config=night_config, weekend_config=weekend_config,
        night_hard_criteria=frozenset(), start_dow=0, num_days=num_days,
        dual_stroke_helena=dual_stroke_helena, stroke_wk2627_toggle=stroke_wk2627_toggle,
    )


def _make_xs(opb, config, fellow_names):
    nf = len(fellow_names)
    nw = config.num_weeks
    ns = len(config.shifts)
    return [[[opb.new_var() for _ in range(ns)] for _ in range(nw)] for _ in range(nf)]


class TestWk2627Toggle:
    def test_hard_forbids_on_both_holiday_weeks(self):
        config = _config(stroke_wk2627_toggle="hard")
        fellows = ["Helena", "Aditya"]
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs = _make_xs(opb, config, fellows)
        soft = []
        _encode_stroke_wk2627_toggle(opb, xs, config, fellows, shift_idx, soft)
        # Each STROKE fellow gets a hard at_most-1 over their two on-indicators
        # (one per holiday week). With multiple on-shifts, the indicator is an
        # aux var, so the <= 1 pairs aux(wk25) with aux(wk26).
        le1 = [c for c in opb._constraints if c.strip().endswith("<= 1 ;")]
        assert len(le1) == 2, f"expected one at_most-1 per STROKE fellow, got {len(le1)}"
        # The on-indicator OR constraints must reference week-25 and week-26
        # Stroke shift vars (proof the toggle is built from those weeks).
        on_si = [shift_idx[s] for s in STROKE_WK2627_ON_SHIFTS if s in shift_idx]
        w25_stroke = xs[0][25][shift_idx["Stroke"]]
        w26_stroke = xs[0][26][shift_idx["Stroke"]]
        refs25 = any(f"x{w25_stroke} " in c for c in opb._constraints)
        refs26 = any(f"x{w26_stroke} " in c for c in opb._constraints)
        assert refs25 and refs26, "toggle must build on-indicators from wk25 & wk26"

    def test_off_emits_nothing(self):
        config = _config(stroke_wk2627_toggle="off")
        fellows = ["Helena", "Aditya"]
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs = _make_xs(opb, config, fellows)
        soft = []
        _encode_stroke_wk2627_toggle(opb, xs, config, fellows, shift_idx, soft)
        assert not opb._constraints and not soft

    def test_on_shift_set(self):
        assert STROKE_WK2627_ON_SHIFTS == frozenset(
            {"Stroke", "Telestroke/Clinic", "Swing", "NCC1", "NCC2"})


class TestDualStrokeHelena:
    def test_penalizes_dual_without_helena(self):
        config = _config(dual_stroke_helena="Helena")
        fellows = ["Helena", "Aditya"]
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs = _make_xs(opb, config, fellows)
        soft = []
        _encode_dual_stroke_helena(opb, xs, config, fellows, shift_idx, soft)
        # Some soft penalties must be created (dual-stroke base + no-Helena tiers).
        assert soft, "dual_stroke_helena must add soft penalties"

    def test_off_when_none(self):
        config = _config(dual_stroke_helena=None)
        fellows = ["Helena", "Aditya"]
        shift_idx = {s: i for i, s in enumerate(config.shifts)}
        opb = OpbBuilder()
        xs = _make_xs(opb, config, fellows)
        soft = []
        _encode_dual_stroke_helena(opb, xs, config, fellows, shift_idx, soft)
        assert not soft


class TestShippedDualStrokeWindowActive:
    """Guard: the shipped supervised-dual-Stroke rule must stay ACTIVE so Helena's
    early Stroke weeks get a senior co-fellow (Aditya/Cameron/Harneet), not an
    NH/NCC_SR junior. Guards against an accidental revert to active:false."""

    def test_supervised_dual_stroke_rule_is_active(self):
        import yaml
        from pathlib import Path
        annual = yaml.safe_load(
            Path("/cv/scratch/u/watkina6/scheduler/config/annual/my-2026-2027-v3.yaml").read_text()
        )
        rules = [r for r in annual.get("call_rules", [])
                 if r.get("type") == "dual_stroke_window"]
        assert rules, "expected a dual_stroke_window call_rule in the shipped config"
        rule = rules[0]
        assert rule.get("active") is True, "Supervised dual Stroke window must be active"
        # Supervisors must be exactly the three seniors (STROKE minus Helena).
        assert set(rule.get("supervisors", [])) == {
            "Aditya Srivatsan", "Cameron Schmidt", "Harneet Dhillon"
        }
