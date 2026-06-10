"""Slice 4 — validate(schedule): check an arbitrary Schedule against the rules.

The capability: take ANY concrete Schedule (solver output, an Imported fellow's
frozen weeks, a hand-edited or externally-supplied schedule) and report which
rules it violates. Hard violations make the Schedule invalid; soft violations
sum to a penalty. This is the evaluate-side mirror of build_full_schedule_opb.

These tests exercise the driver + the evaluate() manifestation of the archetypes
a bad import/hand-edit realistically breaks:
  * FullAssignment  — each fellow exactly one shift/week (the Solver Invariant;
                      the "someone gave Aditya two shifts by hand" case).
  * PinForbid       — specific_assignment (pin) / zero_shifts (forbid).
  * WindowedCountBand — shift_total / staffing_per_week (over/under counts).
"""

from __future__ import annotations

from schedule_rules.validate import validate, ValidationResult
from schedule_rules.strength import HARD, SOFT
from scheduler.semantic_constraints import (
    SemanticConstraint,
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
    WeekSpan,
    ShiftSet,
)


class DictScheduleView:
    """Concrete Schedule keyed by fellow name. weekday[w] = {fellow: shift}."""

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
        """Every shift the fellow holds that week — normally 0 or 1, but a
        hand-edit may put 2+ (the overload case). Returns a list."""
        if 0 <= week < len(self._weekday):
            v = self._weekday[week].get(fellow, "")
            if isinstance(v, list):
                return [s for s in v if s]
            return [v] if v else []
        return []

    def weekend_role_holder(self, week, role):
        return None

    def night_holder(self, day):
        return None

    def fellows_on_shift(self, week, shift):
        out = []
        if 0 <= week < len(self._weekday):
            for f in self._weekday[week]:
                if shift in self.all_services(week, f):
                    out.append(f)
        return out


_GROUPS = {"NCC_JR": ["Aditya", "Cameron"], "STROKE": ["Raya"]}


def _c(kind, *, strength=ConstraintStrength.HARD, fellows=None, shifts=None,
       weeks=None, params=None) -> SemanticConstraint:
    return SemanticConstraint(
        kind=kind,
        lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=strength,
        fellows=fellows,
        weeks=weeks,
        shifts=shifts,
        params=params or {},
    )


# ---------------------------------------------------------------------------
# FullAssignment (Solver Invariant)
# ---------------------------------------------------------------------------
class TestFullAssignment:
    def test_clean_schedule_no_violations(self):
        view = DictScheduleView([{"Aditya": "NCC1", "Cameron": "NCC2"}])
        rules = [_c("full_assignment", fellows=FellowSelector.by_groups("NCC_JR"))]
        result = validate(view, rules, _GROUPS)
        assert result.is_valid
        assert result.hard_violations == []

    def test_double_booked_fellow_is_invalid(self):
        # Aditya hand-edited onto TWO shifts in week 0.
        view = DictScheduleView([{"Aditya": ["NCC1", "MICU"], "Cameron": "NCC2"}])
        rules = [_c("full_assignment", fellows=FellowSelector.by_groups("NCC_JR"))]
        result = validate(view, rules, _GROUPS)
        assert not result.is_valid
        assert any("Aditya" in v.detail and v.week == 0 for v in result.hard_violations)

    def test_unassigned_fellow_is_invalid(self):
        view = DictScheduleView([{"Aditya": "", "Cameron": "NCC2"}])
        rules = [_c("full_assignment", fellows=FellowSelector.by_groups("NCC_JR"))]
        result = validate(view, rules, _GROUPS)
        assert not result.is_valid


# ---------------------------------------------------------------------------
# PinForbid
# ---------------------------------------------------------------------------
class TestPinForbid:
    def test_specific_assignment_satisfied(self):
        view = DictScheduleView([{"Raya": "ISC"}])
        rules = [_c("specific_assignment", fellows=FellowSelector.by_names("Raya"),
                    shifts=ShiftSet.single("ISC"), weeks=WeekSpan(0, 1))]
        assert validate(view, rules, _GROUPS).is_valid

    def test_specific_assignment_violated(self):
        view = DictScheduleView([{"Raya": "Stroke"}])
        rules = [_c("specific_assignment", fellows=FellowSelector.by_names("Raya"),
                    shifts=ShiftSet.single("ISC"), weeks=WeekSpan(0, 1))]
        assert not validate(view, rules, _GROUPS).is_valid

    def test_zero_shifts_forbidden_violated(self):
        view = DictScheduleView([{"Raya": "MICU"}])
        rules = [_c("zero_shifts", fellows=FellowSelector.by_names("Raya"),
                    shifts=ShiftSet("forbidden", ("MICU", "SICU")))]
        assert not validate(view, rules, _GROUPS).is_valid

    def test_zero_shifts_respected(self):
        view = DictScheduleView([{"Raya": "Stroke"}])
        rules = [_c("zero_shifts", fellows=FellowSelector.by_names("Raya"),
                    shifts=ShiftSet("forbidden", ("MICU", "SICU")))]
        assert validate(view, rules, _GROUPS).is_valid


# ---------------------------------------------------------------------------
# WindowedCountBand
# ---------------------------------------------------------------------------
class TestWindowedCountBand:
    def _view_3wk(self, raya_shifts):
        return DictScheduleView([{"Raya": s} for s in raya_shifts])

    def test_shift_total_at_least_satisfied(self):
        view = self._view_3wk(["Stroke", "Stroke", "Elec"])
        rules = [_c("shift_total", fellows=FellowSelector.by_names("Raya"),
                    shifts=ShiftSet.single("Stroke"),
                    params={"relation": "at_least", "count": 2})]
        assert validate(view, rules, _GROUPS).is_valid

    def test_shift_total_at_least_violated(self):
        view = self._view_3wk(["Stroke", "Elec", "Elec"])
        rules = [_c("shift_total", fellows=FellowSelector.by_names("Raya"),
                    shifts=ShiftSet.single("Stroke"),
                    params={"relation": "at_least", "count": 2})]
        assert not validate(view, rules, _GROUPS).is_valid

    def test_shift_total_at_most_violated(self):
        view = self._view_3wk(["Stroke", "Stroke", "Stroke"])
        rules = [_c("shift_total", fellows=FellowSelector.by_names("Raya"),
                    shifts=ShiftSet.single("Stroke"),
                    params={"relation": "at_most", "count": 2})]
        assert not validate(view, rules, _GROUPS).is_valid

    def test_shift_total_windowed(self):
        # only weeks [0,2) counted -> 2 Stroke in window, ok for at_most 2
        view = self._view_3wk(["Stroke", "Stroke", "Stroke"])
        rules = [_c("shift_total", fellows=FellowSelector.by_names("Raya"),
                    shifts=ShiftSet.single("Stroke"), weeks=WeekSpan(0, 2),
                    params={"relation": "at_most", "count": 2})]
        assert validate(view, rules, _GROUPS).is_valid

    def test_soft_band_produces_penalty_not_invalidity(self):
        view = self._view_3wk(["Stroke", "Elec", "Elec"])
        rules = [_c("shift_total", strength=ConstraintStrength.SOFT,
                    fellows=FellowSelector.by_names("Raya"),
                    shifts=ShiftSet.single("Stroke"),
                    params={"relation": "at_least", "count": 2, "weight": 100})]
        result = validate(view, rules, _GROUPS)
        assert result.is_valid          # soft -> still valid
        assert result.soft_penalty > 0  # but penalized


# ---------------------------------------------------------------------------
# Driver behavior
# ---------------------------------------------------------------------------
class TestDriver:
    def test_unknown_kind_is_reported_not_silently_skipped(self):
        view = DictScheduleView([{"Raya": "Stroke"}])
        rules = [_c("some_unported_kind", fellows=FellowSelector.by_names("Raya"))]
        result = validate(view, rules, _GROUPS)
        assert "some_unported_kind" in result.unevaluated_kinds

    def test_multiple_rules_accumulate(self):
        view = DictScheduleView([{"Aditya": ["NCC1", "MICU"], "Cameron": ""}])
        rules = [
            _c("full_assignment", fellows=FellowSelector.by_groups("NCC_JR")),
            _c("zero_shifts", fellows=FellowSelector.by_names("Aditya"),
               shifts=ShiftSet("f", ("MICU",))),
        ]
        result = validate(view, rules, _GROUPS)
        # Aditya double-booked + on forbidden MICU; Cameron unassigned.
        assert len(result.hard_violations) >= 2
        assert not result.is_valid


class TestRealParsedScheduleView:
    """The capability end-to-end: validate() on the production ParsedScheduleView
    (the adapter over a parsed call schedule), not just the test double."""

    def test_validate_flags_forbidden_shift_on_parsed_schedule(self):
        from scheduler.call_schedule_common import ParsedCallScheduleCsv, WeekRow
        from scheduler.schedule_view import ParsedScheduleView

        parsed = ParsedCallScheduleCsv(
            fellow_names=["Raya"], existing_schedule_columns=(),
            week_rows=[WeekRow(weekday_assignments={"Raya": "MICU"},
                               schedule_assignments={}, raw_row=[])],
            trailing_rows=[],
        )
        view = ParsedScheduleView(parsed)
        rules = [_c("zero_shifts", fellows=FellowSelector.by_names("Raya"),
                    shifts=ShiftSet("forbidden", ("MICU",)))]
        result = validate(view, rules, {"STROKE": ["Raya"]})
        assert not result.is_valid
        assert result.hard_violations[0].kind == "zero_shifts"
