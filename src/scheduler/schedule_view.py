"""ParsedScheduleView — the model-package adapter satisfying schedule_rules.ScheduleView.

Lets a Rule's evaluate manifestation read a concrete Schedule (a parsed call
schedule, optionally overlaid with a solved weekend solution) without the
schedule_rules leaf depending on this package. Symmetric to OpbConstraintSink on
the encode side.

Weekend-role reads prefer the SOLVED weekend solution when supplied, falling
back to the imported schedule_assignments on the parsed row — mirroring the
prior inline evaluator behavior.
"""

from __future__ import annotations

from .call_schedule_common import NIGHT_ROLES, ParsedCallScheduleCsv


class ParsedScheduleView:
    def __init__(
        self,
        parsed: ParsedCallScheduleCsv,
        *,
        weekend_solution=None,
        night_solution=None,
    ) -> None:
        self._parsed = parsed
        self._weekend_solution = weekend_solution
        self._night_solution = night_solution

    @property
    def num_weeks(self) -> int:
        return len(self._parsed.week_rows)

    def weekday_service(self, week: int, fellow: str) -> str:
        if 0 <= week < len(self._parsed.week_rows):
            return self._parsed.week_rows[week].weekday_assignments.get(fellow, "")
        return ""

    def all_services(self, week: int, fellow: str) -> list[str]:
        """Every weekday shift the fellow holds that week. A parsed CSV stores
        one service per fellow/week, so this is [service] or []; the list shape
        lets validate() detect double-bookings from richer (e.g. spreadsheet)
        import sources that can place a fellow on two shifts."""
        svc = self.weekday_service(week, fellow)
        return [svc] if svc else []

    def weekend_role_holder(self, week: int, role: str) -> str | None:
        if not (0 <= week < len(self._parsed.week_rows)):
            return None
        if self._weekend_solution is not None:
            return self._weekend_solution.assignments_by_week[week].get(role)
        return self._parsed.week_rows[week].schedule_assignments.get(role)

    def fellows_on_shift(self, week: int, shift: str) -> list[str]:
        if 0 <= week < len(self._parsed.week_rows):
            return [
                f for f, s in self._parsed.week_rows[week].weekday_assignments.items()
                if s == shift
            ]
        return []

    def night_holder(self, day: int) -> str | None:
        """The fellow holding the night on absolute *day* index (0 = horizon
        start), inverting day -> (week, dow). None when no night solution was
        supplied, or the day/week is out of range."""
        if self._night_solution is None:
            return None
        if day < 0:
            return None
        week, dow = divmod(day, 7)
        weeks = self._night_solution.assignments_by_week
        if not (0 <= week < len(weeks)):
            return None
        return weeks[week].get(NIGHT_ROLES[dow])
