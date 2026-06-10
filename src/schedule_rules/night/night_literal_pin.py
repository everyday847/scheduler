"""The night_literal_pin archetype — pin (force true) or forbid (force false) a
set of night literals at resolved day coordinates.

FOUR night-layer kinds collapse onto this one shape (all unit constraints on the
per-day night variables ``xn``):

  * specific_night_assignment — PIN one fellow's night on each given date.
  * blocked_night             — FORBID one fellow's night on each given date.
  * friday_call_assignment    — PIN one fellow's Friday night for each given week.
  * group_night_requirement   — FORBID every NON-member's night on each given
                                date (i.e. only fellows in the allowed groups may
                                hold those nights).

The shape is "pin / forbid a set of night literals at resolved coordinates."
Coordinate resolution (date->day, week->friday, group-complement) happens in the
thin registry adapter that has ``config``/``fellow_names``/``xn``; this archetype
receives an already-resolved var list + an action, mirroring how
group_count_balance / windowed_count_band take pre-built var lists.

encode emits one unit per resolved var: PIN ⇒ add_unit(+v), FORBID ⇒ add_unit(-v).

evaluate reads a concrete schedule via view.night_holder(day):
  * fellow-specific PIN    — the fellow MUST hold each coordinate's night.
  * fellow-specific FORBID — the fellow must NOT hold any coordinate's night.
  * group_night_requirement — the holder of each coordinate's night must be in
    the allowed-fellow set (a non-member holding it is a violation).
"""

from __future__ import annotations

from schedule_rules.sink import ConstraintSink
from schedule_rules.view import ScheduleView

PIN = "pin"
FORBID = "forbid"


class NightLiteralPin:
    name = "night_literal_pin"

    def encode(
        self,
        sink: ConstraintSink,
        *,
        night_vars: list[int],
        action: str,
    ) -> None:
        """Force each var in *night_vars* per *action*: PIN ⇒ +v, FORBID ⇒ -v.

        The adapter passes only the live, in-bounds vars it has already resolved
        (date->day, week->friday, group complement), so this stays dead simple —
        no coordinate or eligibility logic here."""
        if action == PIN:
            for v in night_vars:
                sink.add_unit(v)
        elif action == FORBID:
            for v in night_vars:
                sink.add_unit(-v)
        else:
            raise ValueError(f"Unknown night-pin action: {action!r}")

    def evaluate(
        self,
        view: ScheduleView,
        *,
        days: list[int],
        action: str,
        fellow: str | None = None,
        allowed_fellows: frozenset[str] | None = None,
    ) -> list[tuple[int, str | None]]:
        """Return the violating (day, holder) coordinates for this rule.

        * action == PIN with *fellow*: every day whose night holder is not
          *fellow* is a violation (the named fellow must hold it).
        * action == FORBID with *fellow*: every day whose night holder IS
          *fellow* is a violation (the named fellow must not hold it).
        * action == FORBID with *allowed_fellows* (group_night_requirement):
          every day whose night holder is a non-member (and not None) is a
          violation — only allowed-group fellows may hold these nights.
        """
        out: list[tuple[int, str | None]] = []
        if action == PIN:
            for d in days:
                if view.night_holder(d) != fellow:
                    out.append((d, view.night_holder(d)))
        elif action == FORBID:
            for d in days:
                holder = view.night_holder(d)
                if allowed_fellows is not None:
                    # group_night_requirement: a non-member holding the night is
                    # the violation; an unassigned (None) night is fine.
                    if holder is not None and holder not in allowed_fellows:
                        out.append((d, holder))
                elif holder == fellow:
                    out.append((d, holder))
        else:
            raise ValueError(f"Unknown night-pin action: {action!r}")
        return out
