"""Contract test for the WindowedSupervisionCriterion archetype (S5 migration).

The "may" supervision shape: on a low-supply service (Stroke), during a window
a SUPERVISED pair may cover (≤1 supervisor + ≤1 non-supervisor); outside the
window only one fellow may be on the service. Hard-only (at-most-k caps; no soft
penalty). This replaced the hardcoded `_encode_dual_stroke_window` call_rules
branch; the kind `dual_stroke_window` now routes through the weekly registry.

encode: pin a concrete Stroke pattern into PB vars, encode the cap, solve with
vendored RoundingSat, assert the cap is enforced (a 2-non-supervisor pattern is
UNSAT in-window; a supervisor+non-supervisor pair is SAT in-window; any 2 is
UNSAT out-of-window). evaluate: check a concrete schedule against the cap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from schedule_rules.criteria.windowed_supervision import WindowedSupervisionCriterion
from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.constraint_sink import OpbConstraintSink
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner


ROUNDINGSAT_BINARY = (
    Path(__file__).resolve().parent.parent / "vendor" / "roundingsat" / "build" / "roundingsat"
)


@pytest.fixture
def runner():
    if not ROUNDINGSAT_BINARY.exists():
        pytest.skip("RoundingSat binary not built")
    return RoundingSatRunner(ROUNDINGSAT_BINARY)


class DictScheduleView:
    """weekday[w] = {fellow: shift}. fellows_on_shift inverts it."""
    def __init__(self, weekday):
        self._weekday = weekday

    @property
    def num_weeks(self) -> int:
        return len(self._weekday)

    def weekday_service(self, week, fellow):
        if 0 <= week < len(self._weekday):
            return self._weekday[week].get(fellow, "")
        return ""

    def all_services(self, week, fellow):
        s = self.weekday_service(week, fellow)
        return [s] if s else []

    def fellows_on_shift(self, week, shift):
        if 0 <= week < len(self._weekday):
            return [f for f, s in self._weekday[week].items() if s == shift]
        return []

    def weekend_role_holder(self, week, role):
        return None

    def night_holder(self, day):
        return None


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------
class TestEvaluate:
    def setup_method(self):
        self.crit = WindowedSupervisionCriterion()
        self.supers = {"Sup1", "Sup2"}

    def _view(self, week0):
        return DictScheduleView([week0])

    def test_in_window_supervised_pair_ok(self):
        # one supervisor + one non-supervisor in-window → no violation.
        v = self._view({"Sup1": "Stroke", "Junior": "Stroke"})
        hits = self.crit.evaluate(v, supervisors=self.supers, shift="Stroke",
                                  window=(0, 5), num_weeks=1)
        assert hits == []

    def test_in_window_two_non_supervisors_flags(self):
        v = self._view({"JuniorA": "Stroke", "JuniorB": "Stroke"})
        hits = self.crit.evaluate(v, supervisors=self.supers, shift="Stroke",
                                  window=(0, 5), num_weeks=1)
        assert len(hits) == 1 and hits[0][0] == 0

    def test_in_window_two_supervisors_flags(self):
        v = self._view({"Sup1": "Stroke", "Sup2": "Stroke"})
        hits = self.crit.evaluate(v, supervisors=self.supers, shift="Stroke",
                                  window=(0, 5), num_weeks=1)
        assert len(hits) == 1

    def test_out_of_window_pair_flags(self):
        # outside the window, even a supervised pair exceeds the ≤1-total cap.
        v = self._view({"Sup1": "Stroke", "Junior": "Stroke"})
        hits = self.crit.evaluate(v, supervisors=self.supers, shift="Stroke",
                                  window=(0, 0), num_weeks=1)
        assert len(hits) == 1

    def test_single_fellow_never_flags(self):
        for window in ((0, 5), (0, 0)):
            v = self._view({"Junior": "Stroke"})
            assert self.crit.evaluate(v, supervisors=self.supers, shift="Stroke",
                                      window=window, num_weeks=1) == []


# ---------------------------------------------------------------------------
# encode (solved with RoundingSat)
# ---------------------------------------------------------------------------
class TestEncode:
    def _solve_two_pinned(self, runner, in_window, both_non_supervisor):
        """Pin two fellows ON the Stroke service (force their vars true) and apply
        the cap. Returns whether the result is SAT. With both vars forced true,
        the cap is satisfiable only when the pattern respects it."""
        opb = OpbBuilder()
        sup_var = opb.new_var()
        other_var = opb.new_var()
        opb.add_unit(sup_var)      # both fellows pinned onto Stroke
        opb.add_unit(other_var)
        crit = WindowedSupervisionCriterion()
        if both_non_supervisor:
            sup_vars, non_sup_vars = [], [sup_var, other_var]
        else:
            sup_vars, non_sup_vars = [sup_var], [other_var]
        crit.encode(OpbConstraintSink(opb, []), supervisor_vars=sup_vars,
                    non_supervisor_vars=non_sup_vars, in_window=in_window)
        return runner.solve(opb).satisfiable

    def test_in_window_supervised_pair_sat(self, runner):
        # 1 supervisor + 1 non-supervisor pinned, in-window → SAT.
        assert self._solve_two_pinned(runner, in_window=True, both_non_supervisor=False)

    def test_in_window_two_non_supervisors_unsat(self, runner):
        # 2 non-supervisors pinned, in-window → violates ≤1-non-supervisor → UNSAT.
        assert not self._solve_two_pinned(runner, in_window=True, both_non_supervisor=True)

    def test_out_of_window_pair_unsat(self, runner):
        # 2 fellows pinned, out-of-window → violates ≤1-total → UNSAT.
        assert not self._solve_two_pinned(runner, in_window=False, both_non_supervisor=False)
