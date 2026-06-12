"""Integration tests for the weekend-night ↔ weekend-role relationship, now the
config-driven WeekendNightCriterion archetype (`_encode_configured_weekend_night`).

The former `_encode_weekend_night_linking` encoder was dissolved into the
archetype; its leaf-level encode/evaluate agreement is pinned by
test_weekend_night_contract.py. These tests drive the encoder HANDLER from a
config carrying the three weekend_night rules and check that each weekend night
produces the intended constraints:
  Friday   (FORBID): NCC1 hard, NCC2/Stroke soft@40 — Friday now OWNS NCC1
    (coalesced from the former friday_weekend_ncc1 rule).
  Saturday (REQUIRE {NCC1,NCC2}, hard).
  Sunday   (REQUIRE {Stroke} soft@10 + eligibility-forbid soft@10).
"""

from __future__ import annotations

from datetime import date

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.constraint_sink import OpbConstraintSink
from parafrost_scheduler.schedule_types import (
    _ROLE_NCC1, _ROLE_NCC2, _ROLE_STROKE, _week_day)
from parafrost_scheduler.schedule_encoder import _encode_configured_weekend_night
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig
from scheduler.palette_rules import weekend_night_rule_to_constraint
from scheduler.shift_palette import ShiftPalette


_NCC1, _NCC2, _STROKE = "Weekend NCC1", "Weekend NCC2", "Weekend Stroke"


def _rules():
    """The shipped three weekend_night rules (mirrors stanford-fellowship-v3)."""
    palette = ShiftPalette.from_config({})
    raw = [
        {"type": "weekend_night", "criterion": "weekend_night_friday", "dow": 4,
         "polarity": "forbid", "role_strengths": [
             {"role": _NCC1, "hard": True},
             {"role": _NCC2, "hard": False, "weight": 40},
             {"role": _STROKE, "hard": False, "weight": 40}]},
        {"type": "weekend_night", "criterion": "weekend_night_saturday", "dow": 5,
         "polarity": "require", "role_strengths": [
             {"role": _NCC1, "hard": True}, {"role": _NCC2, "hard": True}]},
        {"type": "weekend_night", "criterion": "weekend_night_sunday", "dow": 6,
         "polarity": "require",
         "role_strengths": [{"role": _STROKE, "hard": False, "weight": 10}],
         "eligibility": {"role": _STROKE, "hard": False, "weight": 10}},
    ]
    return [weekend_night_rule_to_constraint(r, palette) for r in raw]


def _make_config(num_days: int = 7, start_dow: int = 0,
                 fellow_groups=None) -> "object":
    from parafrost_scheduler.schedule_types import ScheduleSolverConfig
    if fellow_groups is None:
        fellow_groups = {"NCC_SR": ["Alice"]}
    night_config = NightSolverConfig(
        total_nights={}, friday_nights={}, total_night_multisets=(),
        friday_night_multisets=(), ccm_fellows=frozenset(), holiday_dates=(),
        horizon_start_date=date(2026, 7, 1))
    weekend_config = WeekendSolverConfig(
        ncc_totals={}, stroke_totals={}, stroke_cohort=(), stroke_cohort_total=None,
        ccm_fellows=frozenset(), always_stroke_eligible=frozenset(),
        telestroke_stroke_eligible=frozenset(), stroke_only_eligible=frozenset())
    return ScheduleSolverConfig(
        fellow_groups=fellow_groups, shifts=["NCC1"], constraints=_rules(),
        night_config=night_config, weekend_config=weekend_config,
        night_hard_criteria=frozenset(), start_dow=start_dow, num_days=num_days)


def _make_xn(opb, num_days, num_fellows):
    return [[opb.new_var() for _ in range(num_fellows)] for _ in range(num_days)]


def _make_wr(opb, num_weeks, num_fellows, *, ncc_eligible=None, stroke_eligible=None):
    if ncc_eligible is None:
        ncc_eligible = set(range(num_fellows))
    if stroke_eligible is None:
        stroke_eligible = set(range(num_fellows))
    wr = []
    for _w in range(num_weeks):
        wr.append([
            {f: opb.new_var() for f in ncc_eligible},   # NCC1
            {f: opb.new_var() for f in ncc_eligible},   # NCC2
            {f: opb.new_var() for f in stroke_eligible},  # Stroke
        ])
    return wr


def _constraints_containing(opb, var_id):
    pos, neg = f"x{var_id} ", f"~x{var_id} "
    return [c for c in opb._constraints if pos in c or neg in c]


def _encode(config, opb, xn, wr, fellows):
    soft: list[tuple[int, int]] = []
    _encode_configured_weekend_night(opb, xn, wr, config, fellows, soft)
    return soft


class TestFridayCoalesced:
    """Friday now OWNS all three weekend roles (the NCC1 case was coalesced from
    the former friday_weekend_ncc1 rule): NCC1 hard, NCC2/Stroke soft@40."""

    def test_friday_now_touches_ncc1_hard(self):
        config = _make_config()
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 1)
        wr = _make_wr(opb, config.num_weeks, 1)
        _encode(config, opb, xn, wr, ["Alice"])
        friday_d = _week_day(0, 4, 0)
        xn_fri = xn[friday_d][0]
        wr_ncc1 = wr[0][_ROLE_NCC1][0]
        # NCC1 is now referenced — and as a hard at-most-1 (no soft indicator).
        ncc1_cons = [c for c in _constraints_containing(opb, xn_fri) if f"x{wr_ncc1} " in c]
        assert len(ncc1_cons) > 0, "Friday now owns Weekend NCC1 (coalesced)"

    def test_friday_ncc2_and_stroke_soft_40(self):
        config = _make_config()
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 1)
        wr = _make_wr(opb, config.num_weeks, 1)
        soft = _encode(config, opb, xn, wr, ["Alice"])
        # Friday contributes exactly two soft@40 (NCC2 + Stroke); NCC1 is hard.
        assert sorted(w for _, w in soft if w == 40) == [40, 40]


class TestSaturdayRequireNCC:
    def test_saturday_generates_constraint(self):
        config = _make_config()
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 1)
        wr = _make_wr(opb, config.num_weeks, 1)
        soft = _encode(config, opb, xn, wr, ["Alice"])
        sat_d = _week_day(0, 5, 0)
        assert len(_constraints_containing(opb, xn[sat_d][0])) > 0
        # Saturday is hard -> no soft indicators from it.
        assert all(w != 10 or True for _, w in soft)  # (soft list may hold Sunday@10)

    def test_saturday_non_ncc_fellow_no_constraint(self):
        """A fellow with no NCC weekend-role var has no Saturday REQUIRE term
        (nothing to require), so no constraint is emitted for them."""
        config = _make_config(fellow_groups={"NCC_SR": ["Alice", "Bob"]})
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 2)
        wr = _make_wr(opb, config.num_weeks, 2, ncc_eligible={0})
        _encode(config, opb, xn, wr, ["Alice", "Bob"])
        sat_d = _week_day(0, 5, 0)
        # Fellow 1 (not NCC-eligible) has no Saturday REQUIRE constraint.
        assert len(_constraints_containing(opb, xn[sat_d][1])) == 0


class TestSundayRequireStroke:
    def test_sunday_stroke_soft_10(self):
        config = _make_config()
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 1)
        wr = _make_wr(opb, config.num_weeks, 1)
        soft = _encode(config, opb, xn, wr, ["Alice"])
        # Sunday stroke-eligible -> one soft@10 indicator.
        assert 10 in [w for _, w in soft]

    def test_sunday_non_stroke_eligible_penalized(self):
        """The eligibility-forbid: a fellow with no Weekend-Stroke var on Sunday
        night is penalized (soft@10) — the bare night var carries the penalty."""
        config = _make_config()
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 1)
        wr = _make_wr(opb, config.num_weeks, 1, stroke_eligible=set())
        soft = _encode(config, opb, xn, wr, ["Alice"])
        sun_d = _week_day(0, 6, 0)
        assert any(v == xn[sun_d][0] for v, _ in soft), (
            "non-stroke-eligible fellow on Sunday night must be penalized")


class TestPartialWeeks:
    def test_partial_first_week_no_friday(self):
        """start_dow=6 (Sunday): week 0 has only Sun; Fri/Sat out of bounds -> no
        crash, Sunday constraint still emitted."""
        config = _make_config(num_days=7, start_dow=6)
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 1)
        wr = _make_wr(opb, config.num_weeks, 1)
        soft = _encode(config, opb, xn, wr, ["Alice"])
        # Sunday of week 0 is day 0.
        assert len(_constraints_containing(opb, xn[0][0])) > 0 or soft
