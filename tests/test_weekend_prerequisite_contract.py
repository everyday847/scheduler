"""Contract tests for the WeekendRolePrerequisite archetype (S4).

A weekend role (Stroke / NCC1 / NCC2) may be held in week w only if the fellow
served the qualifying weekday shift in some week w' <= w (inclusive). This
exercises the co-located Rule's two manifestations:

  evaluate : flags a weekend-role holder with NO prior qualifying service, and
             passes one WITH prior service.
  encode   : HARD ⇒ a role-without-prior-service cell is UNSAT;
             SOFT ⇒ SAT, paying one penalty per such cell.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from schedule_rules.criteria.weekend_role_prerequisite import (
    WeekendRolePrerequisiteCriterion,
)
from schedule_rules.strength import HARD, SOFT
from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.constraint_sink import OpbConstraintSink
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner


ROUNDINGSAT_BINARY = (
    Path(__file__).resolve().parent.parent / "vendor" / "roundingsat" / "build" / "roundingsat"
)
_NCC1, _NCC2, _STROKE = "Weekend NCC1", "Weekend NCC2", "Weekend Stroke"

STROKE_CRIT = WeekendRolePrerequisiteCriterion(
    role_names=("Weekend Stroke",), prereq_shifts=("Stroke",))
NCC_CRIT = WeekendRolePrerequisiteCriterion(
    role_names=(_NCC1, _NCC2), prereq_shifts=("NCC1", "NCC2"))


@pytest.fixture
def runner():
    if not ROUNDINGSAT_BINARY.exists():
        pytest.skip("RoundingSat binary not built")
    return RoundingSatRunner(ROUNDINGSAT_BINARY)


class DictScheduleView:
    def __init__(self, weekday, weekend):
        self._weekday = weekday
        self._weekend = weekend

    @property
    def num_weeks(self) -> int:
        return len(self._weekday)

    def weekday_service(self, week: int, fellow: str) -> str:
        if 0 <= week < len(self._weekday):
            return self._weekday[week].get(fellow, "")
        return ""

    def weekend_role_holder(self, week: int, role: str):
        if 0 <= week < len(self._weekend):
            return self._weekend[week].get(role)
        return None

    def fellows_on_shift(self, week, shift):
        if 0 <= week < len(self._weekday):
            return [f for f, s in self._weekday[week].items() if s == shift]
        return []

    def night_holder(self, day):
        return None


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------
class TestStrokePrereqEvaluate:
    def test_no_prior_service_flags(self):
        # A holds Weekend Stroke week 0 but never served weekday Stroke -> flag.
        view = DictScheduleView([{"A": "Elec"}], [{_STROKE: "A"}])
        hits = STROKE_CRIT.evaluate(view, fellows=["A"])
        assert hits == [("A", 0, _STROKE)]

    def test_same_week_service_satisfies(self):
        # Weekday Stroke in the SAME week (inclusive window) satisfies the prereq.
        view = DictScheduleView([{"A": "Stroke"}], [{_STROKE: "A"}])
        assert STROKE_CRIT.evaluate(view, fellows=["A"]) == []

    def test_earlier_week_service_satisfies(self):
        view = DictScheduleView(
            [{"A": "Stroke"}, {"A": "Elec"}],
            [{}, {_STROKE: "A"}],
        )
        assert STROKE_CRIT.evaluate(view, fellows=["A"]) == []

    def test_later_service_does_not_satisfy(self):
        # Service AFTER the weekend (week 1) does not count for the week-0 role.
        view = DictScheduleView(
            [{"A": "Elec"}, {"A": "Stroke"}],
            [{_STROKE: "A"}, {}],
        )
        assert STROKE_CRIT.evaluate(view, fellows=["A"]) == [("A", 0, _STROKE)]

    def test_exempt_fellow_skipped(self):
        view = DictScheduleView([{"A": "Elec"}], [{_STROKE: "A"}])
        assert STROKE_CRIT.evaluate(view, fellows=["A"], exempt={"A"}) == []


class TestNccPrereqEvaluate:
    def test_ncc1_or_ncc2_service_satisfies(self):
        # Prior weekday NCC2 satisfies a Weekend NCC1 role (shift-set, not exact).
        view = DictScheduleView([{"A": "NCC2"}], [{_NCC1: "A"}])
        assert NCC_CRIT.evaluate(view, fellows=["A"]) == []

    def test_no_prior_ncc_flags_both_roles(self):
        view = DictScheduleView([{"A": "Elec", "B": "Elec"}], [{_NCC1: "A", _NCC2: "B"}])
        hits = NCC_CRIT.evaluate(view, fellows=["A", "B"])
        assert ("A", 0, _NCC1) in hits and ("B", 0, _NCC2) in hits


# ---------------------------------------------------------------------------
# encode (solver-backed): HARD forbids, SOFT pays a penalty
# ---------------------------------------------------------------------------
class TestStrokePrereqEncode:
    def test_hard_no_prior_is_unsat(self, runner):
        # role var forced true, no prior service -> HARD add_unit(-role) -> UNSAT.
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)
        role_var = opb.new_var()
        opb.add_unit(role_var)  # force the fellow to hold the weekend role
        STROKE_CRIT.encode(sink, role_var=role_var, prior_vars=[],
                           strength=HARD, weight=20)
        result = runner.solve(opb)
        assert result.satisfiable is False

    def test_soft_no_prior_is_sat_and_registers_slack(self, runner):
        # SOFT relaxes the hard forbid into a penalized slack: SAT, and one
        # weighted indicator is registered for the objective. (Reproducing the
        # old encoding EXACTLY: the slack rides `role + slack <= 1`, so an
        # optimizer drives it to 0 — the contract here is SAT + registration,
        # matching the old soft_violations.append behavior byte-for-byte.)
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)
        role_var = opb.new_var()
        opb.add_unit(role_var)
        STROKE_CRIT.encode(sink, role_var=role_var, prior_vars=[],
                           strength=SOFT, weight=20)
        result = runner.solve(opb)
        assert result.satisfiable is True
        assert len(soft) == 1
        (ind, w) = soft[0]
        assert w == 20

    def test_with_prior_service_is_sat_no_penalty(self, runner):
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)
        role_var = opb.new_var()
        prior = opb.new_var()
        opb.add_unit(role_var)
        opb.add_unit(prior)  # the fellow DID serve weekday Stroke
        STROKE_CRIT.encode(sink, role_var=role_var, prior_vars=[prior],
                           strength=HARD, weight=20)
        result = runner.solve(opb)
        assert result.satisfiable is True
        assert soft == []  # non-empty prior never registers a slack

    def test_role_with_free_prior_forces_prior_true(self, runner):
        # The discriminating case: role forced true, prior left FREE. The
        # implication role_var <= sum(prior) must FORCE prior=1. A tautological
        # encoding (bound 0) would leave prior free and let prior=0 stand — the
        # exact bug that let a non-exempt fellow hold Weekend Stroke with no
        # prior weekday Stroke. Pin prior=0 and assert UNSAT.
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)
        role_var = opb.new_var()
        prior = opb.new_var()
        opb.add_unit(role_var)   # hold the weekend role
        opb.add_unit(-prior)     # but DID NOT serve weekday Stroke
        STROKE_CRIT.encode(sink, role_var=role_var, prior_vars=[prior],
                           strength=HARD, weight=20)
        result = runner.solve(opb)
        assert result.satisfiable is False


# ---------------------------------------------------------------------------
# validate.py driver wiring (both kinds register an _EVALUATORS entry)
# ---------------------------------------------------------------------------
class TestValidateDriver:
    def _con(self, kind, strength):
        from scheduler.semantic_constraints import (
            SemanticConstraint, ConstraintLifecycle, ConstraintStrength)
        return SemanticConstraint(
            kind=kind, lifecycle=ConstraintLifecycle.STANDING_RULE,
            strength=strength, params={})

    def test_hard_violation_when_no_prior_service(self):
        from schedule_rules.validate import validate
        from scheduler.semantic_constraints import ConstraintStrength
        view = DictScheduleView([{"Alice": "Elec"}], [{_STROKE: "Alice"}])
        con = self._con("weekend_stroke_prerequisite", ConstraintStrength.HARD)
        res = validate(view, [con], {"NCC_SR": ["Alice"]})
        assert res.is_valid is False
        assert len(res.hard_violations) == 1
        assert res.hard_violations[0].week == 0
        assert res.unevaluated_kinds == []

    def test_no_violation_with_prior_service(self):
        from schedule_rules.validate import validate
        from scheduler.semantic_constraints import ConstraintStrength
        view = DictScheduleView([{"Alice": "Stroke"}], [{_STROKE: "Alice"}])
        con = self._con("weekend_stroke_prerequisite", ConstraintStrength.HARD)
        res = validate(view, [con], {"NCC_SR": ["Alice"]})
        assert res.is_valid is True

    def test_soft_violation_carries_weight(self):
        from schedule_rules.validate import validate
        from scheduler.semantic_constraints import (
            SemanticConstraint, ConstraintLifecycle, ConstraintStrength)
        view = DictScheduleView([{"Bob": "Elec"}], [{_NCC1: "Bob"}])
        con = SemanticConstraint(
            kind="weekend_ncc_prerequisite",
            lifecycle=ConstraintLifecycle.STANDING_RULE,
            strength=ConstraintStrength.SOFT, params={"weight": 7})
        res = validate(view, [con], {"NCC_SR": ["Bob"]})
        assert res.is_valid is True  # soft → not a hard violation
        assert res.soft_penalty == 7

    def test_exempt_fellow_not_flagged(self):
        from schedule_rules.validate import validate
        from scheduler.semantic_constraints import (
            SemanticConstraint, ConstraintLifecycle, ConstraintStrength)
        view = DictScheduleView([{"Alice": "Elec"}], [{_STROKE: "Alice"}])
        con = SemanticConstraint(
            kind="weekend_stroke_prerequisite",
            lifecycle=ConstraintLifecycle.STANDING_RULE,
            strength=ConstraintStrength.HARD,
            params={"exempt_fellows": ["Alice"]})
        res = validate(view, [con], {"NCC_SR": ["Alice"]})
        assert res.is_valid is True
