"""Slice 2 — ADR-0005 contract tests for the remaining 4 night criteria.

Each criterion (Anaesthesia, Clinic, FridayWeekendNcc1, SundayFollowing) is one
Rule with co-located encode()+evaluate() over a single shared gating geometry.
Like the Stroke contract test, each agreement test pins a concrete schedule into
PB variables, encodes, solves, and asserts the forced soft-indicator count
equals evaluate()'s count.

Geometry being pinned (each mirrors the encoder + the canonical evaluator):
  Anaesthesia      : weekday night (dow 0-4), fellow on Anaesthesia THAT week.
  Clinic           : Tue/Wed night gate same week, Sun night gates next week;
                     fellow on Clinic/Elective in the gating week.
  FridayWeekendNcc1: Friday night, fellow holds Weekend NCC1 that week.
  SundayFollowing  : Sunday night, fellow's NEXT week service is non-preferred
                     (NON_PREFERRED set — the encoder's truth, per the resolved
                     divergence; the old evaluator under-reported NS/SCVMC/Vac).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from schedule_rules.criteria.anaesthesia import AnaesthesiaCriterion
from schedule_rules.criteria.clinic import ClinicCriterion
from schedule_rules.criteria.friday_weekend_ncc1 import FridayWeekendNcc1Criterion
from schedule_rules.criteria.sunday_following import (
    SundayFollowingCriterion,
    NON_PREFERRED_SUNDAY_FOLLOWING,
)
from schedule_rules.strength import HARD, SOFT
from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.constraint_sink import OpbConstraintSink
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner


ROUNDINGSAT_BINARY = (
    Path(__file__).resolve().parent.parent / "vendor" / "roundingsat" / "build" / "roundingsat"
)
_MON, _TUE, _WED, _THU, _FRI, _SAT, _SUN = range(7)


@pytest.fixture
def runner():
    if not ROUNDINGSAT_BINARY.exists():
        pytest.skip("RoundingSat binary not built")
    return RoundingSatRunner(ROUNDINGSAT_BINARY)


class DictScheduleView:
    def __init__(self, weekday: list[dict[str, str]], weekend: list[dict[str, str]] | None = None):
        self._weekday = weekday
        self._weekend = weekend or [{} for _ in weekday]

    @property
    def num_weeks(self) -> int:
        return len(self._weekday)

    def weekday_service(self, week: int, fellow: str) -> str:
        if 0 <= week < len(self._weekday):
            return self._weekday[week].get(fellow, "")
        return ""

    def weekend_role_holder(self, week: int, role: str) -> str | None:
        if 0 <= week < len(self._weekend):
            return self._weekend[week].get(role)
        return None

    def fellows_on_shift(self, week: int, shift: str) -> list[str]:
        if 0 <= week < len(self._weekday):
            return [f for f, s in self._weekday[week].items() if s == shift]
        return []


# ---------------------------------------------------------------------------
# Anaesthesia
# ---------------------------------------------------------------------------
class TestAnaesthesiaEvaluate:
    def setup_method(self):
        self.crit = AnaesthesiaCriterion()
        self.view = DictScheduleView([{"A": "Anaesthesia", "B": "NCC1"}])

    def test_weekday_night_on_anaesthesia_fires(self):
        assert self.crit.evaluate(self.view, 0, _MON, "A") is True

    def test_weekend_night_does_not_fire(self):
        assert self.crit.evaluate(self.view, 0, _SAT, "A") is False
        assert self.crit.evaluate(self.view, 0, _SUN, "A") is False

    def test_non_anaesthesia_fellow_does_not_fire(self):
        assert self.crit.evaluate(self.view, 0, _MON, "B") is False


# ---------------------------------------------------------------------------
# Clinic
# ---------------------------------------------------------------------------
class TestClinicEvaluate:
    def setup_method(self):
        self.crit = ClinicCriterion()

    def test_tuesday_night_same_week_clinic_fires(self):
        view = DictScheduleView([{"C": "Clinic/Elective"}])
        assert self.crit.evaluate(view, 0, _TUE, "C") is True

    def test_wednesday_night_same_week_clinic_fires(self):
        view = DictScheduleView([{"C": "Clinic/Elective"}])
        assert self.crit.evaluate(view, 0, _WED, "C") is True

    def test_sunday_night_gates_next_week(self):
        view = DictScheduleView([{"C": "NCC1"}, {"C": "Clinic/Elective"}])
        assert self.crit.evaluate(view, 0, _SUN, "C") is True

    def test_monday_thursday_friday_do_not_gate(self):
        view = DictScheduleView([{"C": "Clinic/Elective"}])
        assert self.crit.evaluate(view, 0, _MON, "C") is False
        assert self.crit.evaluate(view, 0, _THU, "C") is False
        assert self.crit.evaluate(view, 0, _FRI, "C") is False


# ---------------------------------------------------------------------------
# Friday / Weekend NCC1
# ---------------------------------------------------------------------------
class TestFridayWeekendNcc1Evaluate:
    def setup_method(self):
        self.crit = FridayWeekendNcc1Criterion()
        self.view = DictScheduleView(
            [{"A": "NCC1", "B": "NCC2"}], [{"Weekend NCC1": "A"}]
        )

    def test_friday_night_weekend_ncc1_holder_fires(self):
        assert self.crit.evaluate(self.view, 0, _FRI, "A") is True

    def test_non_holder_does_not_fire(self):
        assert self.crit.evaluate(self.view, 0, _FRI, "B") is False

    def test_non_friday_does_not_fire(self):
        assert self.crit.evaluate(self.view, 0, _THU, "A") is False


# ---------------------------------------------------------------------------
# Sunday following
# ---------------------------------------------------------------------------
class TestSundayFollowingEvaluate:
    def setup_method(self):
        self.crit = SundayFollowingCriterion()

    def test_sunday_before_non_preferred_next_week_fires(self):
        view = DictScheduleView([{"A": "NCC1"}, {"A": "Vac"}])  # Vac is non-preferred
        assert self.crit.evaluate(view, 0, _SUN, "A") is True

    def test_sunday_before_preferred_next_week_does_not_fire(self):
        view = DictScheduleView([{"A": "NCC1"}, {"A": "NCC2"}])  # NCC2 preferred
        assert self.crit.evaluate(view, 0, _SUN, "A") is False

    def test_ns_scvmc_vac_are_non_preferred(self):
        # The resolved divergence: encoder's set wins; these 3 canonical shifts
        # ARE non-preferred (the old evaluator wrongly treated them as preferred).
        for shift in ("NS", "SCVMC Rehab", "Vac"):
            assert shift in NON_PREFERRED_SUNDAY_FOLLOWING

    def test_last_week_sunday_does_not_fire(self):
        view = DictScheduleView([{"A": "NCC1"}])  # no next week
        assert self.crit.evaluate(view, 0, _SUN, "A") is False

    def test_non_sunday_does_not_fire(self):
        view = DictScheduleView([{"A": "NCC1"}, {"A": "Vac"}])
        assert self.crit.evaluate(view, 0, _SAT, "A") is False


# ---------------------------------------------------------------------------
# encode/evaluate agreement (solver-backed)
# ---------------------------------------------------------------------------
class TestEncodeEvaluateAgreeSingleTerm:
    """Anaesthesia, Clinic, Friday: single-gating-term 'pair' criteria."""

    def _agree(self, runner, crit, view, nights, role_for_term=None):
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)

        expected = sum(1 for (w, dow, f) in nights if crit.evaluate(view, w, dow, f))

        # Pin every gating term referenced by the placed nights.
        term_vars: dict[tuple, int] = {}
        night_vars: dict[tuple, int] = {}
        for (w, dow, f) in nights:
            nv = opb.new_var()
            night_vars[(w, dow, f)] = nv
            opb.add_unit(nv)
            for term in crit.gating_terms(w, dow):
                key = (term.week, term.target, term.is_weekend_role, f)
                if key not in term_vars:
                    tv = opb.new_var()
                    term_vars[key] = tv
                    if term.is_weekend_role:
                        holds = view.weekend_role_holder(term.week, term.target) == f
                    else:
                        holds = view.weekday_service(term.week, f) == term.target
                    opb.add_unit(tv if holds else -tv)

        for (w, dow, f) in nights:
            for term in crit.gating_terms(w, dow):
                tv = term_vars[(term.week, term.target, term.is_weekend_role, f)]
                crit.encode(sink, night_var=night_vars[(w, dow, f)], term_var=tv,
                            strength=SOFT, weight=1)

        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced == expected
        return expected

    def test_anaesthesia_agrees(self, runner):
        crit = AnaesthesiaCriterion()
        view = DictScheduleView([{"A": "Anaesthesia", "B": "NCC1"}])
        nights = [(0, _MON, "A"), (0, _SAT, "A"), (0, _MON, "B")]
        assert self._agree(runner, crit, view, nights) == 1

    def test_clinic_agrees(self, runner):
        crit = ClinicCriterion()
        view = DictScheduleView([{"C": "Clinic/Elective"}, {"C": "Clinic/Elective"}])
        nights = [(0, _TUE, "C"), (0, _MON, "C"), (0, _SUN, "C")]
        assert self._agree(runner, crit, view, nights) == 2  # Tue + Sun(next-week clinic)

    def test_friday_agrees(self, runner):
        crit = FridayWeekendNcc1Criterion()
        view = DictScheduleView([{"A": "NCC1", "B": "NCC2"}], [{"Weekend NCC1": "A"}])
        nights = [(0, _FRI, "A"), (0, _FRI, "B")]
        assert self._agree(runner, crit, view, nights) == 1

    def test_friday_hard_forbids_violation(self, runner):
        crit = FridayWeekendNcc1Criterion()
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)
        term = opb.new_var(); opb.add_unit(term)     # holds Weekend NCC1
        night = opb.new_var(); opb.add_unit(night)   # takes Friday night
        crit.encode(sink, night_var=night, term_var=term, strength=HARD, weight=1)
        assert runner.solve(opb).satisfiable is False


class TestSundayFollowingEncodeEvaluateAgree:
    """Sunday: OR-over-non-preferred-shifts then one pair."""

    def test_or_then_pair_agrees(self, runner):
        crit = SundayFollowingCriterion()
        view = DictScheduleView([{"A": "NCC1"}, {"A": "Vac"}])
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)

        # Pin A's week-1 shift vars across the non-preferred set; A is on "Vac".
        shifts = sorted(NON_PREFERRED_SUNDAY_FOLLOWING)
        shift_var = {}
        for s in shifts:
            v = opb.new_var()
            shift_var[s] = v
            opb.add_unit(v if s == "Vac" else -v)
        night = opb.new_var(); opb.add_unit(night)

        expected = 1 if crit.evaluate(view, 0, _SUN, "A") else 0
        term_vars = [shift_var[t.target] for t in crit.gating_terms(0, _SUN)
                     if t.target in shift_var]
        crit.encode(sink, night_var=night, term_vars=term_vars, strength=SOFT, weight=1)

        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced == expected == 1
