"""Post-cutover guards for the annual call-rule pins/blocks.

These pin/block kinds (specific_night_assignment / blocked_night /
specific_weekend_assignment / blocked_weekend / friday_call_assignment) were
dissolved out of the raw `call_rules` channel into the typed `config.constraints`
pipeline. The old `_encode_call_rules` branches that this file used to unit-test
(by inspecting emitted unit literals) have been gutted — `_encode_call_rules`
now raises for any migrated type — so the original branch-by-branch tests no
longer apply. Their behavioral coverage moved to:

  * the archetypes' own contract tests — tests/test_night_literal_pin_contract.py
    and tests/test_weekend_role_pin_contract.py (encode forces vars true/false
    via the real solver; evaluate flags the right violations);
  * the new-path "it fires" guards — tests/test_night_pins_migration.py and
    tests/test_weekend_pins_migration.py;
  * the live OPB-triple regression gate.

What remains here:
  * TestDateToDayIndex — the pure date->day helper (`_date_to_day_index`) is
    unaffected by the cutover and still merits direct coverage.
  * TestSkipSemanticsNewPath — the SKIP behaviors (inactive / unknown fellow /
    out-of-horizon date / out-of-range week → no constraints) now belong to the
    typed pipeline; these guard that the typed path still no-ops them rather than
    erroring or silently pinning the wrong var.
"""

from __future__ import annotations

from datetime import date

from parafrost_scheduler.schedule_types import (
    ScheduleSolverConfig,
    _date_to_day_index)
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig
from scheduler.semantic_constraints import (
    SemanticConstraint,
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    constraints,
    start_dow: int = 0,
    num_days: int = 21,
    fellow_groups: dict[str, list[str]] | None = None,
    horizon_start: date | None = None) -> ScheduleSolverConfig:
    """Build a minimal full-schedule config for the typed pipeline."""
    if fellow_groups is None:
        fellow_groups = {"NCC_SR": ["Alice", "Bob", "Carol"]}
    if horizon_start is None:
        horizon_start = date(2026, 7, 6)  # Monday
    night_config = NightSolverConfig(
        total_nights={},
        friday_nights={},
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(),
        horizon_start_date=horizon_start)
    weekend_config = WeekendSolverConfig(
        ncc_totals={},
        stroke_totals={},
        stroke_cohort=(),
        stroke_cohort_total=None,
        ccm_fellows=frozenset(),
        always_stroke_eligible=frozenset({"Alice", "Bob", "Carol"}),
        telestroke_stroke_eligible=frozenset(),
        stroke_only_eligible=frozenset())
    return ScheduleSolverConfig(
        fellow_groups=fellow_groups,
        shifts=["NCC1"],
        constraints=constraints,
        night_config=night_config,
        weekend_config=weekend_config,
        night_hard_criteria=frozenset(),
        start_dow=start_dow,
        num_days=num_days,
        call_rules=[])


def _annual(kind, **kw) -> SemanticConstraint:
    return SemanticConstraint(
        kind=kind,
        lifecycle=ConstraintLifecycle.ANNUAL_RULE,
        strength=ConstraintStrength.HARD,
        **kw)


def _num_constraints(constraints, **cfg_kw) -> int:
    opb, _ = build_full_schedule_opb(
        _make_config(constraints, **cfg_kw), objective=True)
    return opb.num_constraints


# ---------------------------------------------------------------------------
# Tests: _date_to_day_index (pure helper, unaffected by the cutover)
# ---------------------------------------------------------------------------

class TestDateToDayIndex:
    def test_same_date_returns_zero(self):
        assert _date_to_day_index("2026-07-06", date(2026, 7, 6)) == 0

    def test_one_day_after(self):
        assert _date_to_day_index("2026-07-07", date(2026, 7, 6)) == 1

    def test_ten_days_after(self):
        assert _date_to_day_index("2026-07-16", date(2026, 7, 6)) == 10

    def test_date_object_input(self):
        assert _date_to_day_index(date(2026, 7, 10), date(2026, 7, 6)) == 4

    def test_date_before_horizon_returns_negative(self):
        assert _date_to_day_index("2026-07-04", date(2026, 7, 6)) == -2


# ---------------------------------------------------------------------------
# Tests: skip semantics through the typed pipeline
#
# These mirror the old call_rules skip cases (inactive / unknown fellow /
# out-of-bounds date / out-of-range week). On the typed path each resolves to
# NO night/weekend vars, so the build emits the same constraint count as the
# no-rule baseline (a clean no-op, not an error or a mis-pin).
# ---------------------------------------------------------------------------

class TestSkipSemanticsNewPath:
    def test_unknown_fellow_skipped(self):
        """A pin whose selector names an unknown fellow resolves to no var → the
        build is identical to the no-rule baseline."""
        rule = _annual(
            "specific_night_assignment",
            fellows=FellowSelector.by_names("NonexistentPerson"),
            params={"dates": ["2026-07-10"], "name": "ghost"})
        assert _num_constraints([rule]) == _num_constraints([])

    def test_out_of_bounds_date_skipped(self):
        """A night pin on a date past the horizon resolves to no var → no-op."""
        rule = _annual(
            "specific_night_assignment",
            fellows=FellowSelector.by_names("Alice"),
            params={"dates": ["2026-09-01"], "name": "far future"})
        assert (_num_constraints([rule], num_days=7)
                == _num_constraints([], num_days=7))

    def test_out_of_bounds_week_skipped(self):
        """A weekend pin in a week past the horizon resolves to no role var → no-op."""
        rule = _annual(
            "specific_weekend_assignment",
            fellows=FellowSelector.by_names("Alice"),
            params={"role": "NCC1", "weeks": [99], "action": "pin"})
        assert (_num_constraints([rule], num_days=7)
                == _num_constraints([], num_days=7))
