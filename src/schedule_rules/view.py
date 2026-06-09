"""ScheduleView — the read seam for a Rule's evaluate manifestation.

Symmetric to ConstraintSink: encode() WRITES to a ConstraintSink, evaluate()
READS from a ScheduleView. The model package's parsed schedule satisfies this
Protocol; tests supply a dict-backed fake. Keeping it a Protocol in this leaf is
what lets evaluate() run against ANY Schedule (solver output, an Imported
fellow's frozen weeks, a hand-edited or externally-supplied schedule) without
schedule_rules depending on the model package.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ScheduleView(Protocol):
    @property
    def num_weeks(self) -> int:
        ...

    def weekday_service(self, week: int, fellow: str) -> str:
        """The fellow's weekday Shift that week, or "" if none/out of range."""
        ...

    def weekend_role_holder(self, week: int, role: str) -> str | None:
        """The fellow holding *role* that week, or None."""
        ...

    def fellows_on_shift(self, week: int, shift: str) -> list[str]:
        """All fellows on *shift* that week (for multiplicity checks)."""
        ...
