"""Tests for the Shift-Attribute table (ADR-0003).

The heart of these tests pins that ``shifts_with_attribute(flag)`` reproduces
the historical hardcoded frozensets that used to live in
``parafrost_scheduler.schedule_types``. The expected sets below are the literal
values those frozensets carried before the refactor.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scheduler.shift_palette import KNOWN_FLAGS, ShiftPalette


# The shipped Stanford standing config carries the production palette.
_STANDING = (
    Path(__file__).resolve().parents[1]
    / "config" / "standing" / "stanford-fellowship-v3.yaml"
)


# Historical frozenset literals (pre-refactor schedule_types.py values).
_HISTORICAL_NIGHT_BLOCKED = frozenset(
    {"SICU", "MICU", "Vac", "NS", "SCVMC Rehab", "AAN", "RWC", "NCS 2026"}
)
_HISTORICAL_NIGHT_BLOCKED_ALL_WEEK = frozenset({"SICU", "MICU", "Vac", "NS"})
_HISTORICAL_WEEKEND_BLOCKED = frozenset({"SICU", "MICU", "NS", "Anaesthesia", "Vac"})
_HISTORICAL_HOLIDAY_ELIGIBLE = frozenset({"NCC1", "NCC2", "Stroke"})
_HISTORICAL_CONSEC_WEEKEND_BUFFER = frozenset(
    {"Vac", "ISC", "ABPN", "NHS", "AAN", "NCS 2026"}
)
_HISTORICAL_ANAESTHESIA = frozenset({"Anaesthesia"})
_HISTORICAL_CLINIC = frozenset({"Clinic/Elective"})
_HISTORICAL_STROKE_WK2627_ON = frozenset(
    {"Stroke", "Telestroke/Clinic", "Swing", "NCC1", "NCC2"}
)


def _shipped_palette() -> ShiftPalette:
    standing = yaml.safe_load(_STANDING.read_text())
    return ShiftPalette.from_config(standing.get("shift_palette"))


class TestProjectionsReproduceFrozensets:
    """Each projection over the shipped palette equals its historical frozenset."""

    def test_night_blocked_all_week(self):
        assert (
            _shipped_palette().shifts_with_attribute("night_blocked_all_week")
            == _HISTORICAL_NIGHT_BLOCKED_ALL_WEEK
        )

    def test_night_blocked_union(self):
        # The derived union must equal the historical NIGHT_BLOCKED_SHIFTS.
        assert (
            _shipped_palette().shifts_with_attribute("night_blocked")
            == _HISTORICAL_NIGHT_BLOCKED
        )

    def test_night_blocked_weekday_is_union_minus_all_week(self):
        palette = _shipped_palette()
        weekday = palette.shifts_with_attribute("night_blocked_weekday")
        assert weekday == _HISTORICAL_NIGHT_BLOCKED - _HISTORICAL_NIGHT_BLOCKED_ALL_WEEK
        # And the all_week + weekday partition recombines into the full union.
        assert (
            weekday | palette.shifts_with_attribute("night_blocked_all_week")
            == _HISTORICAL_NIGHT_BLOCKED
        )

    def test_weekend_blocked(self):
        assert (
            _shipped_palette().shifts_with_attribute("weekend_blocked")
            == _HISTORICAL_WEEKEND_BLOCKED
        )

    def test_holiday_eligible(self):
        assert (
            _shipped_palette().shifts_with_attribute("holiday_eligible")
            == _HISTORICAL_HOLIDAY_ELIGIBLE
        )

    def test_consec_weekend_buffer(self):
        assert (
            _shipped_palette().shifts_with_attribute("consec_weekend_buffer")
            == _HISTORICAL_CONSEC_WEEKEND_BUFFER
        )

    def test_anaesthesia_gating(self):
        assert (
            _shipped_palette().shifts_with_attribute("anaesthesia_gating")
            == _HISTORICAL_ANAESTHESIA
        )

    def test_clinic_gating(self):
        assert (
            _shipped_palette().shifts_with_attribute("clinic_gating")
            == _HISTORICAL_CLINIC
        )

    def test_stroke_wk2627_on(self):
        assert (
            _shipped_palette().shifts_with_attribute("stroke_wk2627_on")
            == _HISTORICAL_STROKE_WK2627_ON
        )


class TestFailFast:
    def test_unknown_flag_raises(self):
        with pytest.raises(ValueError, match="Unknown shift attribute flag"):
            ShiftPalette.from_config({"MICU": ["night_blocked_all_week", "bogus_flag"]})

    def test_known_flags_vocabulary_is_eight(self):
        # The fixed code vocabulary (the directly-assignable flags).
        assert KNOWN_FLAGS == frozenset({
            "night_blocked_all_week",
            "night_blocked_weekday",
            "weekend_blocked",
            "holiday_eligible",
            "consec_weekend_buffer",
            "anaesthesia_gating",
            "clinic_gating",
            "stroke_wk2627_on",
        })


class TestEmptyPalette:
    def test_no_block_yields_empty_projections(self):
        palette = ShiftPalette.from_config(None)
        for flag in KNOWN_FLAGS:
            assert palette.shifts_with_attribute(flag) == frozenset()
        # The derived union is empty too.
        assert palette.shifts_with_attribute("night_blocked") == frozenset()

    def test_empty_dict_yields_empty_projections(self):
        palette = ShiftPalette.from_config({})
        assert palette.shifts_with_attribute("weekend_blocked") == frozenset()

    def test_default_construction_is_empty(self):
        palette = ShiftPalette()
        assert palette.shifts_with_attribute("holiday_eligible") == frozenset()
