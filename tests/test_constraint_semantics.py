"""Per-constraint semantic verification harness.

Each test exercises ONE encoder on a tiny universe and asserts its EXACT
behavior: which assignments it permits (SAT) and which it forbids (UNSAT).

This is the foundation for trusting the encoding. Until every encoder is
proven faithful here, an UNSAT in the full problem is uninterpretable.

Run:
    PYTHONPATH=src pytest tests/test_constraint_semantics.py -v
"""
from __future__ import annotations

from pathlib import Path

import pytest

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.schedule_types import (
    ManagementMode,
    ScheduleSolverConfig,
    _ROLE_NCC1,
    _ROLE_NCC2,
    _ROLE_STROKE,
)
from parafrost_scheduler.schedule_encoder import (
    _encode_shift_total,
    _encode_staffing_per_week,
    _encode_weekend_eligibility,
)
from scheduler.semantic_constraints import (
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
    SemanticConstraint,
    ShiftSet,
    WeekSpan,
)
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig

ROUNDINGSAT = (
    Path(__file__).resolve().parents[1]
    / "vendor" / "roundingsat" / "build" / "roundingsat"
)

pytestmark = pytest.mark.skipif(
    not ROUNDINGSAT.exists(), reason="RoundingSat binary not built"
)


# ---------------------------------------------------------------------------
# Weekly-layer harness
# ---------------------------------------------------------------------------

class WeeklyHarness:
    """A tiny xs[f][w][s] universe for testing one weekly encoder in isolation.

    All shift variables are created (none forbidden). The fundamental
    at-most-one-shift-per-week invariant is added so that pinning behaves
    like the real solver. assert_permits / assert_forbids pin a partial
    assignment and check SAT/UNSAT, restoring the formula afterward so the
    same harness can be reused across many checks.
    """

    def __init__(self, fellows: list[str], shifts: list[str], num_weeks: int):
        self.opb = OpbBuilder()
        self.fellow_names = fellows
        self.shifts = shifts
        self.num_weeks = num_weeks
        self.shift_idx = {s: i for i, s in enumerate(shifts)}
        self.soft_violations: list[tuple[int, int]] = []
        self.runner = RoundingSatRunner(ROUNDINGSAT)

        # xs[f][w][s]
        self.xs: list[list[list[int]]] = []
        for _f in range(len(fellows)):
            self.xs.append([])
            for _w in range(num_weeks):
                self.xs[-1].append([self.opb.new_var() for _s in range(len(shifts))])

        # Solver invariant: at most one shift per fellow per week
        for f in range(len(fellows)):
            for w in range(num_weeks):
                self.opb.at_most_k(self.xs[f][w], 1)

    def config(self, fellow_groups: dict[str, list[str]] | None = None) -> ScheduleSolverConfig:
        if fellow_groups is None:
            fellow_groups = {"G": list(self.fellow_names)}
        return ScheduleSolverConfig(
            fellow_groups=fellow_groups,
            shifts=self.shifts,
            constraints=[],
            night_config=NightSolverConfig(),
            weekend_config=WeekendSolverConfig(),
            num_days=self.num_weeks * 7,
        )

    def fi(self, *names: str) -> list[int]:
        return [self.fellow_names.index(n) for n in names]

    def encode(self, encoder, constraint, fellow_indices, *, config=None,
               locked_fellow_indices=frozenset(), soft_bound=None):
        if config is None:
            config = self.config()
        encoder(
            self.opb, self.xs, constraint, fellow_indices,
            num_weeks=self.num_weeks, num_fellows=len(self.fellow_names),
            shifts=self.shifts, shift_idx=self.shift_idx,
            fellow_names=self.fellow_names,
            soft_violations=self.soft_violations, config=config,
            locked_fellow_indices=locked_fellow_indices,
        )
        self._soft_bound = soft_bound

    def _solve_with_pins(self, pins: dict[tuple[int, int, str], bool]) -> bool:
        snapshot = len(self.opb._constraints)
        for (f, w, s), val in pins.items():
            var = self.xs[f][w][self.shift_idx[s]]
            self.opb.add_unit(var if val else -var)
        # Apply soft bound if the constraint under test is soft
        if getattr(self, "_soft_bound", None) is not None and self.soft_violations:
            self.opb.weighted_sum_at_most(list(self.soft_violations), self._soft_bound)
        result = self.runner.solve(self.opb, timeout=30.0)
        del self.opb._constraints[snapshot:]
        return result.satisfiable

    def assert_permits(self, pins: dict[tuple[int, int, str], bool], msg: str = ""):
        assert self._solve_with_pins(pins), f"Expected SAT (permitted): {msg}"

    def assert_forbids(self, pins: dict[tuple[int, int, str], bool], msg: str = ""):
        assert not self._solve_with_pins(pins), f"Expected UNSAT (forbidden): {msg}"


def _shift_total(shifts, relation, count, *, strength=ConstraintStrength.HARD,
                 groups=("G",), window=None):
    params = {"name": "test", "relation": relation, "count": count}
    weeks = WeekSpan(window[0], window[1]) if window else None
    return SemanticConstraint(
        kind="shift_total", lifecycle=ConstraintLifecycle.ANNUAL_RULE,
        strength=strength, fellows=FellowSelector.by_groups(*groups),
        weeks=weeks, shifts=ShiftSet("s", tuple(shifts)), params=params,
    )


# ---------------------------------------------------------------------------
# shift_total
# ---------------------------------------------------------------------------

class TestShiftTotal:
    def test_exactly_permits_exact_count(self):
        h = WeeklyHarness(["A"], ["Stroke", "Elec"], 4)
        h.encode(_encode_shift_total, _shift_total(["Stroke"], "exactly", 2), h.fi("A"))
        # Exactly 2 Stroke: pin 2 Stroke weeks, 2 Elec weeks
        h.assert_permits({(0,0,"Stroke"): True, (0,1,"Stroke"): True,
                          (0,2,"Elec"): True, (0,3,"Elec"): True},
                         "2 Stroke == exactly 2")

    def test_exactly_forbids_too_many(self):
        h = WeeklyHarness(["A"], ["Stroke", "Elec"], 4)
        h.encode(_encode_shift_total, _shift_total(["Stroke"], "exactly", 2), h.fi("A"))
        h.assert_forbids({(0,0,"Stroke"): True, (0,1,"Stroke"): True, (0,2,"Stroke"): True},
                         "3 Stroke > exactly 2")

    def test_exactly_forbids_too_few(self):
        h = WeeklyHarness(["A"], ["Stroke", "Elec"], 4)
        h.encode(_encode_shift_total, _shift_total(["Stroke"], "exactly", 2), h.fi("A"))
        # Force all 4 weeks to Elec → 0 Stroke < exactly 2
        h.assert_forbids({(0,0,"Elec"): True, (0,1,"Elec"): True,
                          (0,2,"Elec"): True, (0,3,"Elec"): True},
                         "0 Stroke < exactly 2")

    def test_at_least_permits_more(self):
        h = WeeklyHarness(["A"], ["Stroke", "Elec"], 4)
        h.encode(_encode_shift_total, _shift_total(["Stroke"], "at_least", 2), h.fi("A"))
        h.assert_permits({(0,0,"Stroke"): True, (0,1,"Stroke"): True, (0,2,"Stroke"): True},
                         "3 Stroke >= at_least 2")

    def test_at_least_forbids_fewer(self):
        h = WeeklyHarness(["A"], ["Stroke", "Elec"], 4)
        h.encode(_encode_shift_total, _shift_total(["Stroke"], "at_least", 2), h.fi("A"))
        h.assert_forbids({(0,0,"Stroke"): True, (0,1,"Elec"): True,
                          (0,2,"Elec"): True, (0,3,"Elec"): True},
                         "1 Stroke < at_least 2")

    def test_at_most_forbids_more(self):
        h = WeeklyHarness(["A"], ["Stroke", "Elec"], 4)
        h.encode(_encode_shift_total, _shift_total(["Stroke"], "at_most", 2), h.fi("A"))
        h.assert_forbids({(0,0,"Stroke"): True, (0,1,"Stroke"): True, (0,2,"Stroke"): True},
                         "3 Stroke > at_most 2")

    def test_at_most_permits_fewer(self):
        h = WeeklyHarness(["A"], ["Stroke", "Elec"], 4)
        h.encode(_encode_shift_total, _shift_total(["Stroke"], "at_most", 2), h.fi("A"))
        h.assert_permits({(0,0,"Stroke"): True, (0,1,"Elec"): True,
                          (0,2,"Elec"): True, (0,3,"Elec"): True},
                         "1 Stroke <= at_most 2")

    def test_windowed_only_counts_window(self):
        h = WeeklyHarness(["A"], ["Stroke", "Elec"], 6)
        # exactly 1 Stroke in weeks [0,2)
        h.encode(_encode_shift_total,
                 _shift_total(["Stroke"], "exactly", 1, window=(0, 2)), h.fi("A"))
        # 1 Stroke inside window, extra Stroke OUTSIDE window — should be permitted
        h.assert_permits({(0,0,"Stroke"): True, (0,1,"Elec"): True,
                          (0,4,"Stroke"): True, (0,5,"Stroke"): True},
                         "window counts only weeks 0-1")
        # 2 Stroke INSIDE window — forbidden
        h.assert_forbids({(0,0,"Stroke"): True, (0,1,"Stroke"): True},
                         "2 Stroke in window > exactly 1")

    def test_soft_cannot_cause_unsat(self):
        """A soft shift_total must NEVER make the formula UNSAT, even when violated."""
        h = WeeklyHarness(["A"], ["Stroke", "Elec"], 4)
        h.encode(_encode_shift_total,
                 _shift_total(["Stroke"], "exactly", 2, strength=ConstraintStrength.SOFT),
                 h.fi("A"), soft_bound=10_000)
        # Violate it hard (0 Stroke) — must still be SAT because soft
        h.assert_permits({(0,0,"Elec"): True, (0,1,"Elec"): True,
                          (0,2,"Elec"): True, (0,3,"Elec"): True},
                         "soft exactly-2 violated (0 Stroke) is still SAT")


def _staffing(shifts, relation, count, groups, *, strength=ConstraintStrength.HARD):
    return SemanticConstraint(
        kind="staffing_per_week", lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=strength, fellows=FellowSelector.by_groups(*groups),
        shifts=ShiftSet("s", tuple(shifts)),
        params={"name": "test", "relation": relation, "count": count},
    )


# ---------------------------------------------------------------------------
# staffing_per_week (per-week caps and coverage; cross-group locked softening)
# ---------------------------------------------------------------------------

class TestStaffingPerWeek:
    def test_at_most_cap_forbids_overfill(self):
        h = WeeklyHarness(["A", "B"], ["NCC1", "Elec"], 2)
        cfg = h.config({"G": ["A", "B"]})
        h.encode(_encode_staffing_per_week, _staffing(["NCC1"], "at_most", 1, ["G"]),
                 h.fi("A", "B"), config=cfg)
        h.assert_forbids({(0,0,"NCC1"): True, (1,0,"NCC1"): True},
                         "2 on NCC1 in week 0 > at_most 1")
        h.assert_permits({(0,0,"NCC1"): True, (1,0,"Elec"): True},
                         "1 on NCC1 <= at_most 1")

    def test_at_least_coverage_forbids_gap(self):
        h = WeeklyHarness(["A", "B"], ["Stroke", "Elec"], 2)
        cfg = h.config({"G": ["A", "B"]})
        h.encode(_encode_staffing_per_week, _staffing(["Stroke"], "at_least", 1, ["G"]),
                 h.fi("A", "B"), config=cfg)
        h.assert_forbids({(0,0,"Elec"): True, (1,0,"Elec"): True},
                         "nobody on Stroke in week 0 < at_least 1")
        h.assert_permits({(0,0,"Stroke"): True, (1,0,"Elec"): True},
                         "1 on Stroke >= at_least 1")

    def test_locked_other_group_does_not_soften_cap(self):
        """REGRESSION: a locked fellow OUTSIDE the constraint's target group must
        not trigger the locked_count softening. (The 'Only One NH on Core ICU'
        bug: locked NCC fellows on NCC1 were wrongly softening the NH cap.)"""
        # C is NCC (locked to NCC1 wk0); J,S are NH. Cap targets NH only.
        h = WeeklyHarness(["C", "J", "S"], ["NCC1", "Elec"], 2)
        cfg = ScheduleSolverConfig(
            fellow_groups={"NCC": ["C"], "NH": ["J", "S"]},
            shifts=h.shifts, constraints=[],
            night_config=NightSolverConfig(), weekend_config=WeekendSolverConfig(),
            num_days=14,
            locked_assignments={"C": ["NCC1", ""]},  # C locked to NCC1 in week 0
        )
        # "Only One NH on Core ICU": at_most 1 of NH on NCC1, group NH
        h.encode(_encode_staffing_per_week, _staffing(["NCC1"], "at_most", 1, ["NH"]),
                 h.fi("J", "S"), config=cfg)
        # Both NH on NCC1 in week 0 must be FORBIDDEN — C (other group) shouldn't soften it
        h.assert_forbids({(1,0,"NCC1"): True, (2,0,"NCC1"): True},
                         "2 NH on NCC1 must violate cap regardless of locked NCC fellow")

    def test_locked_same_group_softens_when_filling_cap(self):
        """When a locked fellow IN the target group fills the cap, that week's
        cap is softened (the workbook already exceeds it, so we can't also
        enforce it on the remaining fellows)."""
        h = WeeklyHarness(["A", "B"], ["NCC1", "Elec"], 2)
        cfg = ScheduleSolverConfig(
            fellow_groups={"G": ["A", "B"]}, shifts=h.shifts, constraints=[],
            night_config=NightSolverConfig(), weekend_config=WeekendSolverConfig(),
            num_days=14,
            locked_assignments={"A": ["NCC1", ""]},  # A locked to NCC1 wk0, fills cap=1
        )
        h.encode(_encode_staffing_per_week, _staffing(["NCC1"], "at_most", 1, ["G"]),
                 h.fi("A", "B"), config=cfg)
        # Week 0 cap is softened (A fills it) → B may also be on NCC1 without UNSAT
        h.assert_permits({(0,0,"NCC1"): True, (1,0,"NCC1"): True},
                         "week 0 cap softened because locked A fills it")
        # Week 1 has no locked filler → cap stays hard
        h.assert_forbids({(0,1,"NCC1"): True, (1,1,"NCC1"): True},
                         "week 1 cap stays hard (no locked filler)")


# ---------------------------------------------------------------------------
# Cross-layer harness (weekly xs + weekend wr) for eligibility gates
# ---------------------------------------------------------------------------

class WeekendHarness:
    """xs[f][w][s] + wr[w][role][f] universe for testing weekend eligibility.

    Creates weekend-role variables for every fellow in all 3 roles, but does
    NOT add the exactly-one-per-role coverage constraint — so a role may be
    left empty. This isolates the eligibility GATE: we pin a weekday shift and
    a weekend-role assignment, then check whether that pairing is permitted.
    """

    def __init__(self, fellows, shifts, num_weeks, weekend_config):
        self.opb = OpbBuilder()
        self.fellow_names = fellows
        self.shifts = shifts
        self.num_weeks = num_weeks
        self.shift_idx = {s: i for i, s in enumerate(shifts)}
        self.weekend_config = weekend_config
        self.runner = RoundingSatRunner(ROUNDINGSAT)

        self.xs = [[[self.opb.new_var() for _ in shifts] for _ in range(num_weeks)]
                   for _ in fellows]
        for f in range(len(fellows)):
            for w in range(num_weeks):
                self.opb.at_most_k(self.xs[f][w], 1)

        # wr[w][role] = {fi: var} for all fellows, all roles
        self.wr = []
        for w in range(num_weeks):
            self.wr.append([{f: self.opb.new_var() for f in range(len(fellows))}
                            for _ in range(3)])

    def config(self, fellow_groups):
        return ScheduleSolverConfig(
            fellow_groups=fellow_groups, shifts=self.shifts, constraints=[],
            night_config=NightSolverConfig(), weekend_config=self.weekend_config,
            num_days=self.num_weeks * 7,
        )

    def encode_eligibility(self, config):
        _encode_weekend_eligibility(self.opb, self.wr, self.xs, config,
                                    self.fellow_names, self.shift_idx)

    def _solve(self, shift_pins, wr_pins):
        snap = len(self.opb._constraints)
        for (f, w, s), val in shift_pins.items():
            var = self.xs[f][w][self.shift_idx[s]]
            self.opb.add_unit(var if val else -var)
        for (f, w, role), val in wr_pins.items():
            var = self.wr[w][role][f]
            self.opb.add_unit(var if val else -var)
        result = self.runner.solve(self.opb, timeout=30.0)
        del self.opb._constraints[snap:]
        return result.satisfiable

    def assert_weekend_permitted(self, shift_pins, wr_pins, msg=""):
        assert self._solve(shift_pins, wr_pins), f"Expected SAT (permitted): {msg}"

    def assert_weekend_forbidden(self, shift_pins, wr_pins, msg=""):
        assert not self._solve(shift_pins, wr_pins), f"Expected UNSAT (forbidden): {msg}"


def _wc(*, always=(), telestroke=(), stroke_only=()):
    return WeekendSolverConfig(
        always_stroke_eligible=frozenset(always),
        telestroke_stroke_eligible=frozenset(telestroke),
        stroke_only_eligible=frozenset(stroke_only),
    )


class TestWeekendEligibility:
    SHIFTS = ["Stroke", "Telestroke/Clinic", "NCC1", "Elec", "Vac", "MICU", "SICU", "Anaesthesia"]

    def test_blocked_shift_forbids_all_weekend_roles(self):
        # On Vac (weekend-blocking) → cannot do any weekend role
        wc = _wc(always=["A"])
        h = WeekendHarness(["A"], self.SHIFTS, 2, wc)
        h.encode_eligibility(h.config({"STROKE": ["A"]}))
        for role in (_ROLE_NCC1, _ROLE_NCC2, _ROLE_STROKE):
            h.assert_weekend_forbidden(
                {(0,0,"Vac"): True}, {(0,0,role): True},
                f"on Vac cannot do weekend role {role}")

    def test_core_icu_and_anaesthesia_forbid_weekend_roles(self):
        # On MICU, SICU, or Anaesthesia → cannot do any weekend role that week.
        wc = _wc(always=["A"])
        h = WeekendHarness(["A"], self.SHIFTS, 2, wc)
        h.encode_eligibility(h.config({"STROKE": ["A"]}))
        for weekday in ("MICU", "SICU", "Anaesthesia"):
            for role in (_ROLE_NCC1, _ROLE_NCC2, _ROLE_STROKE):
                h.assert_weekend_forbidden(
                    {(0,0,weekday): True}, {(0,0,role): True},
                    f"on {weekday} cannot do weekend role {role}")

    def test_always_eligible_can_stroke_off_stroke_service(self):
        # always_stroke_eligible (STROKE specialist): weekend Stroke even when
        # weekday shift is NCC1 (not Stroke/Tele).
        wc = _wc(always=["A"])
        h = WeekendHarness(["A"], self.SHIFTS, 2, wc)
        h.encode_eligibility(h.config({"STROKE": ["A"]}))
        h.assert_weekend_permitted(
            {(0,0,"NCC1"): True}, {(0,0,_ROLE_STROKE): True},
            "always-eligible can do weekend Stroke while on NCC1 weekday")

    def test_telestroke_eligible_requires_stroke_or_tele(self):
        # telestroke_stroke_eligible (e.g. NCC_SR): weekend Stroke ONLY if on
        # Stroke or Telestroke that week. This is the 'live and pinned' check:
        # pinned onto NCC1 → weekend Stroke must be forbidden.
        wc = _wc(telestroke=["A"])
        h = WeekendHarness(["A"], self.SHIFTS, 2, wc)
        h.encode_eligibility(h.config({"NCC_SR": ["A"]}))
        h.assert_weekend_forbidden(
            {(0,0,"NCC1"): True}, {(0,0,_ROLE_STROKE): True},
            "telestroke-eligible on NCC1 CANNOT do weekend Stroke")
        h.assert_weekend_permitted(
            {(0,0,"Stroke"): True}, {(0,0,_ROLE_STROKE): True},
            "telestroke-eligible on Stroke CAN do weekend Stroke")
        h.assert_weekend_permitted(
            {(0,0,"Telestroke/Clinic"): True}, {(0,0,_ROLE_STROKE): True},
            "telestroke-eligible on Telestroke CAN do weekend Stroke")

    def test_stroke_only_eligible_requires_stroke(self):
        wc = _wc(stroke_only=["A"])
        h = WeekendHarness(["A"], self.SHIFTS, 2, wc)
        h.encode_eligibility(h.config({"NH": ["A"]}))
        h.assert_weekend_forbidden(
            {(0,0,"Telestroke/Clinic"): True}, {(0,0,_ROLE_STROKE): True},
            "stroke-only on Telestroke CANNOT do weekend Stroke")
        h.assert_weekend_permitted(
            {(0,0,"Stroke"): True}, {(0,0,_ROLE_STROKE): True},
            "stroke-only on Stroke CAN do weekend Stroke")


# ---------------------------------------------------------------------------
# Night harness: calls the real _encode_night_constraints
# ---------------------------------------------------------------------------

from parafrost_scheduler.schedule_encoder import _encode_night_constraints


class NightHarness:
    """xs + wr + xn universe that runs the real night encoder.

    Uses start_dow=0 so week w = days [7w, 7w+6] (Mon..Sun). A pool of
    'Cover' fellows (in a full_assignment group) guarantees every night can
    be covered, so we can isolate the behavior of one fellow under test.
    """

    def __init__(self, cover_fellows, managed_fellows, shifts, num_weeks):
        self.opb = OpbBuilder()
        self.fellow_names = list(cover_fellows) + list(managed_fellows)
        self.cover = list(cover_fellows)
        self.managed = list(managed_fellows)
        self.shifts = shifts
        self.num_weeks = num_weeks
        self.num_days = num_weeks * 7
        self.shift_idx = {s: i for i, s in enumerate(shifts)}
        self.soft_violations: list[tuple[int, int]] = []
        self.runner = RoundingSatRunner(ROUNDINGSAT)

        nf = len(self.fellow_names)
        self.xs = [[[self.opb.new_var() for _ in shifts] for _ in range(num_weeks)]
                   for _ in range(nf)]
        for f in range(nf):
            for w in range(num_weeks):
                self.opb.at_most_k(self.xs[f][w], 1)
        # weekend layer (unused but required by signature): empty role dicts
        self.wr = [[{} for _ in range(3)] for _ in range(num_weeks)]
        # night layer
        self.xn = [[self.opb.new_var() for _ in range(nf)] for _ in range(self.num_days)]

        from scheduler.semantic_constraints import (
            SemanticConstraint as SC, ConstraintLifecycle as CL,
            ConstraintStrength as CS, FellowSelector as FS,
        )
        fa = SC(kind="full_assignment", lifecycle=CL.STANDING_RULE, strength=CS.HARD,
                fellows=FS.by_groups("FA"), params={"name": "fa"})
        self.config = ScheduleSolverConfig(
            fellow_groups={"FA": list(cover_fellows), "NH": list(managed_fellows)},
            shifts=shifts, constraints=[fa],
            night_config=NightSolverConfig(), weekend_config=WeekendSolverConfig(),
            start_dow=0, num_days=self.num_days,
            # This harness exercises SERVICE-based night blocking, not weekend
            # role linking, and leaves the weekend (wr) layer empty. The hard
            # Sunday-night-must-be-weekend-Stroke rule would forbid all Sunday
            # coverage here (no fellow holds a weekend-Stroke var), so run that
            # rule in soft mode — its dedicated coverage is in test_weekend_night_linking.
            weekend_night_sunday_hard=False,
        )

    def encode(self):
        _encode_night_constraints(self.opb, self.xn, self.xs, self.wr, self.config,
                                  self.fellow_names, self.shift_idx, self.soft_violations)

    def _solve(self, shift_pins, night_pins):
        snap = len(self.opb._constraints)
        for (fname, w, s), val in shift_pins.items():
            f = self.fellow_names.index(fname)
            var = self.xs[f][w][self.shift_idx[s]]
            self.opb.add_unit(var if val else -var)
        for (fname, d), val in night_pins.items():
            f = self.fellow_names.index(fname)
            var = self.xn[d][f]
            self.opb.add_unit(var if val else -var)
        result = self.runner.solve(self.opb, timeout=30.0)
        del self.opb._constraints[snap:]
        return result.satisfiable

    def permits(self, shift_pins, night_pins, msg=""):
        assert self._solve(shift_pins, night_pins), f"Expected SAT: {msg}"

    def forbids(self, shift_pins, night_pins, msg=""):
        assert not self._solve(shift_pins, night_pins), f"Expected UNSAT: {msg}"


# 4 cover fellows over 1 week (7 nights) is enough to cover under the default
# no-more-than-1-night-per-3-days spacing, so the base formula is SAT and pins
# isolate the behavior under test (avoids vacuous UNSAT from under-resourcing).
class TestNightBlocking:
    SHIFTS = ["MICU", "NCC1", "Stroke", "Elec"]

    def _harness(self):
        return NightHarness(["C1", "C2", "C3", "C4"], [], self.SHIFTS, 1)

    def test_base_is_satisfiable(self):
        h = self._harness()
        h.encode()
        h.permits({}, {}, "base night formula must be SAT (not vacuous)")

    def test_micu_blocks_all_seven_nights(self):
        h = self._harness()
        h.encode()
        for d in range(7):
            h.forbids({("C1", 0, "MICU"): True}, {("C1", d): True},
                      f"MICU blocks night day {d}")

    def test_non_blocking_shift_permits_night(self):
        h = self._harness()
        h.encode()
        h.permits({("C1", 0, "NCC1"): True}, {("C1", 0): True},
                  "NCC1 (non-blocking) permits a Monday night")


class TestWeekdayOnlyNightBlocking:
    """AAN / RWC / NCS 2026 block weekday nights (Sun-of-prev-week through Thu of
    the service week) but allow Fri/Sat (weekend) nights. (NHS is excluded — it
    has a custom rule allowing Mon/Tue night; see test_nh_nhs_aan.py.)

    Uses a 2-week horizon so 'the Sunday before week 1' (day 6 of week 0)
    exists and can be tested. For a week-1 service, the blocked nights are
    days 6 (Sun ending wk0),7(Mon),8(Tue),9(Wed),10(Thu); allowed are days
    11(Fri),12(Sat) of week 1.
    """
    SHIFTS = ["NCC1", "Elec", "NHS", "AAN", "RWC", "NCS 2026"]

    def _harness(self):
        # 8 cover fellows over 2 weeks keeps the base SAT under spacing.
        return NightHarness(["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"],
                            [], self.SHIFTS, 2)

    BLOCKED_WEEKDAY_NIGHTS = [6, 7, 8, 9, 10]   # Sun(prev), Mon, Tue, Wed, Thu
    ALLOWED_WEEKEND_NIGHTS = [11, 12]           # Fri, Sat of the service week

    def test_weekday_services_block_weekday_nights_allow_weekend(self):
        for service in ("AAN", "RWC", "NCS 2026"):
            h = self._harness()
            h.encode()
            for d in self.BLOCKED_WEEKDAY_NIGHTS:
                h.forbids({("C1", 1, service): True}, {("C1", d): True},
                          f"{service} (week 1) must block weekday night day {d}")
            for d in self.ALLOWED_WEEKEND_NIGHTS:
                h.permits({("C1", 1, service): True}, {("C1", d): True},
                          f"{service} (week 1) must ALLOW weekend night day {d}")


class TestNHNightGate:
    SHIFTS = ["NCC1", "Stroke", "Elec"]

    def _harness(self):
        return NightHarness(["C1", "C2", "C3", "C4"], ["NH1"], self.SHIFTS, 1)

    def test_base_is_satisfiable(self):
        h = self._harness()
        h.encode()
        h.permits({}, {}, "base must be SAT")

    def test_no_weekday_shift_forbids_night(self):
        h = self._harness()
        h.encode()
        no_shift = {("NH1", 0, s): False for s in self.SHIFTS}
        h.forbids(no_shift, {("NH1", 0): True},
                  "NH with no weekday shift cannot do a night")

    def test_with_weekday_shift_permits_night(self):
        h = self._harness()
        h.encode()
        h.permits({("NH1", 0, "NCC1"): True}, {("NH1", 0): True},
                  "NH with a weekday shift CAN do a night")


class TestSoftNightCriterionIsNotHard:
    """REGRESSION: a SOFT night criterion must only PENALIZE its pattern, never
    FORBID it. The conjunction-indicator 'ind <= X' implications were encoded
    backwards (X + ind <= 1), turning every soft criterion into a hard forbid —
    which silently blocked Telestroke/Clinic fellows from Sun/Tue/Wed nights and
    was the true cause of the WB2 night UNSAT.
    """
    SHIFTS = ["MICU", "NCC1", "Stroke", "Telestroke/Clinic", "Elec"]

    def _harness(self):
        # 4 cover fellows over 1 week; clinic criterion is soft by default.
        return NightHarness(["C1", "C2", "C3", "C4"], [], self.SHIFTS, 1)

    def test_clinic_night_is_permitted_not_forbidden(self):
        h = self._harness()
        h.encode()
        # Day 1 = Tuesday (start_dow=0). Clinic criterion fires for a Tue night
        # when the fellow is on a clinic shift that week. It is SOFT, so the
        # pairing must be PERMITTED (penalized), not forbidden.
        h.permits({("C1", 0, "Telestroke/Clinic"): True}, {("C1", 1): True},
                  "Telestroke/Clinic fellow CAN take a Tuesday night (soft penalty)")

    def test_clinic_night_other_fellows_still_cover(self):
        # Sanity: base SAT so the permit above isn't vacuous.
        h = self._harness()
        h.encode()
        h.permits({}, {}, "base night formula SAT")


# ---------------------------------------------------------------------------
# Bridge-level: weekend Stroke eligibility derivation
# ---------------------------------------------------------------------------

from parafrost_scheduler.orchestrator import build_weekend_config_from_request as _build_weekend_config


class TestWeekendStrokeServiceEligibility:
    """REGRESSION: weekend-Stroke eligibility must follow Stroke-SERVICE
    capability, NOT the weekend stroke_total TARGET. NH have weekend
    stroke_total 0 but take weekday Stroke service (per-fellow targets), so
    they must be conditionally eligible. Deriving eligibility from the target
    silently excluded NH — no Weekend-Stroke variable was ever created for them.
    """

    def _request(self):
        return {
            "weekend_call": [
                {"group": "STROKE", "ncc_total": 22, "stroke_total": 51},
                {"group": "NCC_SR", "ncc_total": 32, "stroke_total": 2},
                {"group": "NH", "ncc_total": 6, "stroke_total": 0},
            ],
            "rules": [
                {"type": "shift_total", "groups": ["NCC_SR"], "shifts": ["Stroke"],
                 "relation": "exactly", "count": 2, "active": True},
            ],
            "call_rules": [
                {"type": "per_fellow_shift_total", "fellow": "Sokena Zaidi",
                 "shifts": ["Stroke"], "relation": "at_least", "count": 4, "active": True},
            ],
        }

    def _groups(self):
        return {
            "STROKE": ["Aditya", "Cameron"],
            "NCC_SR": ["Raya", "Joseph"],
            "NH": ["Jinyuan Liu", "Sokena Zaidi"],
        }

    def test_nh_with_stroke_service_target_is_conditionally_eligible(self):
        wk = _build_weekend_config(self._request(), self._groups())
        assert "Sokena Zaidi" in wk.telestroke_stroke_eligible, \
            "NH with a per-fellow Stroke target must be conditionally weekend-Stroke eligible"

    def test_ncc_sr_with_group_stroke_rule_is_conditionally_eligible(self):
        wk = _build_weekend_config(self._request(), self._groups())
        assert "Joseph" in wk.telestroke_stroke_eligible
        assert "Raya" in wk.telestroke_stroke_eligible

    def test_stroke_group_is_always_eligible(self):
        wk = _build_weekend_config(self._request(), self._groups())
        assert "Aditya" in wk.always_stroke_eligible
        assert "Cameron" in wk.always_stroke_eligible

    def test_fellow_without_any_stroke_capability_is_not_eligible(self):
        req = self._request()
        groups = {"STROKE": ["Aditya"], "NCC_JR": ["NoStroke"]}
        req["weekend_call"] = [{"group": "STROKE", "stroke_total": 10}]
        wk = _build_weekend_config(req, groups)
        all_elig = (wk.always_stroke_eligible | wk.telestroke_stroke_eligible
                    | wk.stroke_only_eligible)
        assert "NoStroke" not in all_elig


# ---------------------------------------------------------------------------
# Management mode (single source of truth for IMPORTED vs MANAGED)
# ---------------------------------------------------------------------------

class TestManagementMode:
    """The IMPORTED/MANAGED distinction must be resolved in exactly one place
    (ScheduleSolverConfig), so the four sites that used to re-derive 'is this
    fellow locked?' can never drift apart."""

    def _config(self):
        return ScheduleSolverConfig(
            fellow_groups={"NCC": ["Imp1"], "NH": ["Man1", "Man2"]},
            shifts=["NCC1", "Stroke", "Elec"], constraints=[],
            night_config=NightSolverConfig(), weekend_config=WeekendSolverConfig(),
            num_days=21,
            locked_assignments={"Imp1": ["NCC1", "Stroke", ""]},
        )

    def test_imported_vs_managed_classification(self):
        cfg = self._config()
        assert cfg.management_mode("Imp1") is ManagementMode.IMPORTED
        assert cfg.management_mode("Man1") is ManagementMode.MANAGED
        assert cfg.imported_fellow_names == frozenset({"Imp1"})

    def test_imported_indices_follow_canonical_order(self):
        cfg = self._config()
        fellow_names = ["Imp1", "Man1", "Man2"]  # canonical (group concat) order
        assert cfg.imported_fellow_indices(fellow_names) == frozenset({0})

    def test_imported_shift_counts_per_week(self):
        cfg = self._config()
        fellow_names = ["Imp1", "Man1", "Man2"]
        # Imp1 frozen NCC1 in wk0, Stroke in wk1, empty wk2.
        assert cfg.imported_shift_counts(fellow_names, {"NCC1"}) == {0: 1}
        assert cfg.imported_shift_counts(fellow_names, {"Stroke"}) == {1: 1}
        # restrict_to excluding the imported fellow → no counts
        assert cfg.imported_shift_counts(fellow_names, {"NCC1"},
                                         restrict_to=frozenset({1, 2})) == {}

    def test_no_imports_is_all_managed(self):
        cfg = ScheduleSolverConfig(
            fellow_groups={"G": ["A", "B"]}, shifts=["X"], constraints=[],
            night_config=NightSolverConfig(), weekend_config=WeekendSolverConfig(),
            num_days=7,
        )
        assert cfg.imported_fellow_names == frozenset()
        assert cfg.management_mode("A") is ManagementMode.MANAGED
        assert cfg.imported_fellow_indices(["A", "B"]) == frozenset()
