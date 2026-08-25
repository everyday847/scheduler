"""The weekend_role_pin archetype — pin/forbid a fellow in a weekend role.

ONE shape covering two annual call types (S3 migration):

  * specific_weekend_assignment : PIN one role (NCC1/NCC2/Stroke) true for a
    fellow across the given weeks.
  * blocked_weekend             : FORBID all three weekend roles for a fellow
    across the given weeks.

Both are HARD unit pins — there is no soft variant (annual call pins are
absolute). encode receives the ALREADY-resolved role var list (the thin
encoder-side adapter owns wr/eligibility/role expansion); the archetype only
chooses the sign. evaluate reads the ScheduleView's weekend_role_holder:

  * PIN    : the fellow must hold *role_name* each week.
  * FORBID : the fellow must hold NONE of the three weekend roles each week.

The canonical weekend role NAMES (and the 0/1/2 index order) live in the model
package's _WEEKEND_ROLE_NAMES; this leaf never re-hardcodes the literal map —
the adapter resolves names against that tuple and passes the full role string in
for evaluate (see role_name_for_index in the model package).
"""

from __future__ import annotations

from schedule_rules.sink import ConstraintSink
from schedule_rules.view import ScheduleView


# action constants — kept as plain strings so the encoder/adapter and tests can
# pass them without importing an enum.
PIN = "pin"
FORBID = "forbid"


class WeekendRolePin:
    name = "weekend_role_pin"

    def encode(self, sink: ConstraintSink, *, role_vars: list[int], action: str) -> None:
        """Emit one unit per resolved var. PIN forces the var true, FORBID forces
        it false. *role_vars* is pre-filtered to weekend-eligible vars by the
        adapter (the `fi in wr[w][role_idx]` guard), so this just signs them."""
        if action == PIN:
            for v in role_vars:
                sink.add_unit(v)
        elif action == FORBID:
            for v in role_vars:
                sink.add_unit(-v)
        else:
            raise ValueError(f"Unknown weekend_role_pin action: {action!r}")

    def evaluate(
        self,
        view: ScheduleView,
        *,
        fellow: str,
        weeks,
        role_name: str | None,
        all_role_names: tuple[str, ...],
        action: str,
    ) -> list[tuple[int, str]]:
        """Return (week, detail) per violated week.

        PIN    : flags a week where *fellow* does NOT hold *role_name*.
        FORBID : flags a week where *fellow* holds ANY of *all_role_names*.
        Weeks outside the horizon are skipped (mirrors the encode guard)."""
        out: list[tuple[int, str]] = []
        for w in weeks:
            if not (0 <= w < view.num_weeks):
                continue
            if action == PIN:
                if view.weekend_role_holder(w, role_name) != fellow:
                    out.append((w, f"{fellow} not pinned to {role_name} in week {w}"))
            elif action == FORBID:
                held = [r for r in all_role_names
                        if view.weekend_role_holder(w, r) == fellow]
                if held:
                    out.append((w, f"{fellow} holds {', '.join(held)} in week {w} "
                                   f"(weekend blocked)"))
            else:
                raise ValueError(f"Unknown weekend_role_pin action: {action!r}")
        return out
