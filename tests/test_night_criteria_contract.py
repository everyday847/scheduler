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

from schedule_rules.criteria.night_gating import NightGatingCriterion, NightTermSpec
from schedule_rules.strength import HARD, SOFT
from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.constraint_sink import OpbConstraintSink
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner


ROUNDINGSAT_BINARY = (
    Path(__file__).resolve().parent.parent / "vendor" / "roundingsat" / "build" / "roundingsat"
)
_MON, _TUE, _WED, _THU, _FRI, _SAT, _SUN = range(7)

# The four night criteria are now instances of the NightGatingCriterion archetype
# (config supplies the gating table + targets; no shift name is hardcoded in the
# rule code). These mirror the standing config's night_gating entries.
NON_PREFERRED_SUNDAY_FOLLOWING = frozenset(
    {"Anaesthesia", "Clinic/Elective", "Telestroke/Clinic", "Vac", "NS", "NIR", "SICU", "SCVMC Rehab"}
)
_ANAESTHESIA_TARGETS = {"anaesthesia_gating": frozenset({"Anaesthesia"})}
_CLINIC_TARGETS = {"clinic_gating": frozenset({"Clinic/Elective"})}
_SUNDAY_TARGETS = {"sunday_nonpref": NON_PREFERRED_SUNDAY_FOLLOWING}


def _anaesthesia_criterion() -> NightGatingCriterion:
    return NightGatingCriterion("anaesthesia", terms=(
        NightTermSpec(dows=(0, 1, 2, 3, 4), week_offset=0, target_key="anaesthesia_gating"),))


def _clinic_criterion() -> NightGatingCriterion:
    return NightGatingCriterion("clinic", terms=(
        NightTermSpec(dows=(1, 2), week_offset=0, target_key="clinic_gating"),
        NightTermSpec(dows=(6,), week_offset=1, target_key="clinic_gating"),))


# (The former friday_weekend_ncc1 criterion was coalesced into the Friday
# WeekendNightCriterion instance — see test_weekend_night_contract.py.)


def _sunday_criterion() -> NightGatingCriterion:
    return NightGatingCriterion("sunday_following", terms=(
        NightTermSpec(dows=(6,), week_offset=1, target_key="sunday_nonpref"),))


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

    def night_holder(self, day: int) -> str | None:
        return None


# ---------------------------------------------------------------------------
# Anaesthesia
# ---------------------------------------------------------------------------
class TestAnaesthesiaEvaluate:
    def setup_method(self):
        self.crit = _anaesthesia_criterion()
        self.view = DictScheduleView([{"A": "Anaesthesia", "B": "NCC1"}])

    def _eval(self, w, dow, f):
        return self.crit.evaluate(self.view, w, dow, f, resolved_targets=_ANAESTHESIA_TARGETS)

    def test_weekday_night_on_anaesthesia_fires(self):
        assert self._eval(0, _MON, "A") is True

    def test_weekend_night_does_not_fire(self):
        assert self._eval(0, _SAT, "A") is False
        assert self._eval(0, _SUN, "A") is False

    def test_non_anaesthesia_fellow_does_not_fire(self):
        assert self._eval(0, _MON, "B") is False


# ---------------------------------------------------------------------------
# Clinic
# ---------------------------------------------------------------------------
class TestClinicEvaluate:
    def setup_method(self):
        self.crit = _clinic_criterion()

    def _eval(self, view, w, dow, f):
        return self.crit.evaluate(view, w, dow, f, resolved_targets=_CLINIC_TARGETS)

    def test_tuesday_night_same_week_clinic_fires(self):
        view = DictScheduleView([{"C": "Clinic/Elective"}])
        assert self._eval(view, 0, _TUE, "C") is True

    def test_wednesday_night_same_week_clinic_fires(self):
        view = DictScheduleView([{"C": "Clinic/Elective"}])
        assert self._eval(view, 0, _WED, "C") is True

    def test_sunday_night_gates_next_week(self):
        view = DictScheduleView([{"C": "NCC1"}, {"C": "Clinic/Elective"}])
        assert self._eval(view, 0, _SUN, "C") is True

    def test_monday_thursday_friday_do_not_gate(self):
        view = DictScheduleView([{"C": "Clinic/Elective"}])
        assert self._eval(view, 0, _MON, "C") is False
        assert self._eval(view, 0, _THU, "C") is False
        assert self._eval(view, 0, _FRI, "C") is False


# ---------------------------------------------------------------------------
# Sunday following
# ---------------------------------------------------------------------------
class TestSundayFollowingEvaluate:
    def setup_method(self):
        self.crit = _sunday_criterion()

    def _eval(self, view, w, dow, f):
        return self.crit.evaluate(view, w, dow, f, resolved_targets=_SUNDAY_TARGETS)

    def test_sunday_before_non_preferred_next_week_fires(self):
        view = DictScheduleView([{"A": "NCC1"}, {"A": "Vac"}])  # Vac is non-preferred
        assert self._eval(view, 0, _SUN, "A") is True

    def test_sunday_before_preferred_next_week_does_not_fire(self):
        view = DictScheduleView([{"A": "NCC1"}, {"A": "NCC2"}])  # NCC2 preferred
        assert self._eval(view, 0, _SUN, "A") is False

    def test_ns_scvmc_vac_are_non_preferred(self):
        # The resolved divergence: encoder's set wins; these 3 canonical shifts
        # ARE non-preferred (the old evaluator wrongly treated them as preferred).
        for shift in ("NS", "SCVMC Rehab", "Vac"):
            assert shift in NON_PREFERRED_SUNDAY_FOLLOWING

    def test_last_week_sunday_does_not_fire(self):
        view = DictScheduleView([{"A": "NCC1"}])  # no next week
        assert self._eval(view, 0, _SUN, "A") is False

    def test_non_sunday_does_not_fire(self):
        view = DictScheduleView([{"A": "NCC1"}, {"A": "Vac"}])
        assert self._eval(view, 0, _SAT, "A") is False


# ---------------------------------------------------------------------------
# encode/evaluate agreement (solver-backed)
# ---------------------------------------------------------------------------
class TestEncodeEvaluateAgreeSingleTerm:
    """Anaesthesia, Clinic, Friday: single-gating-term 'pair' criteria."""

    def _agree(self, runner, crit, view, nights, resolved_targets):
        opb = OpbBuilder()
        soft: list[tuple[int, int]] = []
        sink = OpbConstraintSink(opb, soft)

        expected = sum(1 for (w, dow, f) in nights
                       if crit.evaluate(view, w, dow, f, resolved_targets=resolved_targets))

        # Pin every gating term referenced by the placed nights. A weekday-shift
        # term resolves to its set of shift vars (target_key -> resolved shifts);
        # a weekend-role term resolves to the single role var.
        term_vars: dict[tuple, int] = {}
        night_vars: dict[tuple, int] = {}

        def _resolve(term, f) -> list[int]:
            if term.is_weekend_role:
                key = (term.week, term.target_key, True, f)
                if key not in term_vars:
                    tv = opb.new_var()
                    term_vars[key] = tv
                    holds = view.weekend_role_holder(term.week, term.target_key) == f
                    opb.add_unit(tv if holds else -tv)
                return [term_vars[key]]
            out = []
            for shift in sorted(resolved_targets.get(term.target_key, ())):
                key = (term.week, shift, False, f)
                if key not in term_vars:
                    tv = opb.new_var()
                    term_vars[key] = tv
                    opb.add_unit(tv if view.weekday_service(term.week, f) == shift else -tv)
                out.append(term_vars[key])
            return out

        for (w, dow, f) in nights:
            nv = opb.new_var()
            night_vars[(w, dow, f)] = nv
            opb.add_unit(nv)
            for term in crit.gating_terms(w, dow):
                _resolve(term, f)

        for (w, dow, f) in nights:
            for term in crit.gating_terms(w, dow):
                tvs = _resolve(term, f)
                crit.encode(sink, night_var=night_vars[(w, dow, f)], term_vars=tvs,
                            exempt_var=None, strength=SOFT, weight=1)

        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced == expected
        return expected

    def test_anaesthesia_agrees(self, runner):
        crit = _anaesthesia_criterion()
        view = DictScheduleView([{"A": "Anaesthesia", "B": "NCC1"}])
        nights = [(0, _MON, "A"), (0, _SAT, "A"), (0, _MON, "B")]
        assert self._agree(runner, crit, view, nights, _ANAESTHESIA_TARGETS) == 1

    def test_clinic_agrees(self, runner):
        crit = _clinic_criterion()
        view = DictScheduleView([{"C": "Clinic/Elective"}, {"C": "Clinic/Elective"}])
        nights = [(0, _TUE, "C"), (0, _MON, "C"), (0, _SUN, "C")]
        assert self._agree(runner, crit, view, nights, _CLINIC_TARGETS) == 2  # Tue + Sun(next-week clinic)

    # (Friday agreement is now covered by test_weekend_night_contract.py, where
    # the coalesced Friday rule lives as a WeekendNightCriterion instance.)


class TestSundayFollowingEncodeEvaluateAgree:
    """Sunday: OR-over-non-preferred-shifts then one pair."""

    def test_or_then_pair_agrees(self, runner):
        crit = _sunday_criterion()
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

        expected = 1 if crit.evaluate(view, 0, _SUN, "A", resolved_targets=_SUNDAY_TARGETS) else 0
        # The single sunday term's resolved targets are the non-preferred shifts
        # present in week 1; collect the pinned vars for those shifts.
        term = crit.gating_terms(0, _SUN)[0]
        term_vars = [shift_var[s] for s in sorted(_SUNDAY_TARGETS[term.target_key])
                     if s in shift_var]
        crit.encode(sink, night_var=night, term_vars=term_vars, exempt_var=None,
                    strength=SOFT, weight=1)

        result = runner.solve(opb)
        assert result.satisfiable
        forced = sum(1 for (ind, _w) in soft if result.assignment.get(ind, False))
        assert forced == expected == 1
