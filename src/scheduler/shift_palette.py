"""Shift Attributes — a config-driven table that selects scheduling behavior.

A **Shift Attribute** is a code-defined property of a **Shift** that selects a
scheduling behavior (blocks night call, blocks weekend roles, holiday-eligible,
etc.). Per ADR-0003, the attribute *type* vocabulary is code (the fixed set of
flags below); *which shifts carry which attributes* is configuration. The
hardcoded shift-name frozensets that used to live in ``schedule_types`` become
*projections* over this table via :meth:`ShiftPalette.shifts_with_attribute`.

This module lives on the ``scheduler`` (domain) side, peer to
``palette_rules``. It MUST NOT import ``parafrost_scheduler`` (no cycle).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


# ---------------------------------------------------------------------------
# Attribute-type vocabulary — code-defined flags.
# ---------------------------------------------------------------------------
# Each flag names a scheduling behavior the encoder already knows how to apply.
# YAML may only select/parameterize these; it can never invent new behaviors.
NIGHT_BLOCKED_ALL_WEEK = "night_blocked_all_week"
NIGHT_BLOCKED_WEEKDAY = "night_blocked_weekday"
WEEKEND_BLOCKED = "weekend_blocked"
HOLIDAY_ELIGIBLE = "holiday_eligible"
CONSEC_WEEKEND_BUFFER = "consec_weekend_buffer"
ANAESTHESIA_GATING = "anaesthesia_gating"
CLINIC_GATING = "clinic_gating"
STROKE_WK2627_ON = "stroke_wk2627_on"

#: The fixed set of attribute flags a shift may carry directly via config.
KNOWN_FLAGS: frozenset[str] = frozenset({
    NIGHT_BLOCKED_ALL_WEEK,
    NIGHT_BLOCKED_WEEKDAY,
    WEEKEND_BLOCKED,
    HOLIDAY_ELIGIBLE,
    CONSEC_WEEKEND_BUFFER,
    ANAESTHESIA_GATING,
    CLINIC_GATING,
    STROKE_WK2627_ON,
})

#: Derived projection: the night-block UNION (all-week shifts plus the
#: weekday-only shifts). This reproduces the historical ``NIGHT_BLOCKED_SHIFTS``
#: frozenset. It is NOT a directly-assignable flag — config assigns the two
#: component flags; the union is computed on the fly.
NIGHT_BLOCKED = "night_blocked"


@dataclass(frozen=True)
class ShiftPalette:
    """A table mapping shift name -> the set of attribute flags it carries.

    ``shifts_with_attribute`` inverts the table into the historical frozensets
    (a projection over the attributes). An empty palette yields empty
    projections for every flag, so direct-construction call-sites that never
    populate a palette behave as if no shift carries any attribute.
    """

    flags_by_shift: Mapping[str, frozenset[str]] = field(default_factory=dict)

    def shifts_with_attribute(self, attr: str) -> frozenset[str]:
        """Return the set of shift NAMES carrying *attr*.

        For the derived ``night_blocked`` union, compute the all-week shifts
        together with the weekday-only shifts on the fly.
        """
        if attr == NIGHT_BLOCKED:
            return self.shifts_with_attribute(NIGHT_BLOCKED_ALL_WEEK) | \
                self.shifts_with_attribute(NIGHT_BLOCKED_WEEKDAY)
        return frozenset(
            shift for shift, flags in self.flags_by_shift.items() if attr in flags
        )

    @classmethod
    def from_config(
        cls,
        block: Mapping[str, Any] | None,
        *,
        known_flags: frozenset[str] = KNOWN_FLAGS,
    ) -> "ShiftPalette":
        """Build a palette from a YAML map (shift name -> list of flags).

        Fails fast (raises ``ValueError``) on an unknown flag, mirroring the
        fail-fast style of ``palette_rules.palette_rule_to_constraints``.
        A missing/empty block yields an empty palette.
        """
        flags_by_shift: dict[str, frozenset[str]] = {}
        for shift, raw_flags in (block or {}).items():
            flags = frozenset(raw_flags or ())
            for flag in flags:
                if flag not in known_flags:
                    raise ValueError(
                        f"Unknown shift attribute flag: {flag!r} on shift {shift!r}"
                    )
            flags_by_shift[shift] = flags
        return cls(flags_by_shift=flags_by_shift)
