"""evaluate_nf(solution, config) — audit a SOLVED NF-model schedule against the NF rules.

The day-granular mirror of the encoder, for the NF model's call tier + rest/density/
continuity rules. Where schedule_rules/validate.py checks the weekly constraint registry
on a ValidationView, this checks the things that live ONLY in the NF day layer and the
NF-specific weekly rules — the conditions whose encoders are easy to get subtly wrong
(a negative OPB coefficient silently dropped, a vacuous relaxation var). A build-only
constraint dump can't catch those; auditing the decoded solution can.

Each check reads `solution.call_assignments_by_day` (list[dict role->fellow]) and
`solution.weekly_assignments` (fellow -> list[label]) and reports concrete Violations.
A check is GATED on the same config flag that gates its encoder, so the evaluator only
asserts what the model was actually asked to enforce.

Usage:
    from parafrost_scheduler.nf_evaluate import evaluate_nf
    res = evaluate_nf(solution, config)
    assert res.ok, res.summary()
"""

from __future__ import annotations

from dataclasses import dataclass, field

from parafrost_scheduler.schedule_types import day_of_week, day_to_week

CALL_ROLES = ("NCC1", "NCC2", "NF")


@dataclass(frozen=True)
class NfViolation:
    rule: str
    detail: str
    fellow: str | None = None
    week: int | None = None
    day: int | None = None


@dataclass
class NfEvalResult:
    violations: list[NfViolation] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def by_rule(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in self.violations:
            out[v.rule] = out.get(v.rule, 0) + 1
        return out

    def summary(self) -> str:
        if self.ok:
            return f"NF evaluate OK ({len(self.checks_run)} checks: {', '.join(self.checks_run)})"
        lines = [f"NF evaluate: {len(self.violations)} violation(s) across "
                 f"{len(self.by_rule())} rule(s):"]
        for rule, n in sorted(self.by_rule().items()):
            lines.append(f"  {rule}: {n}")
            for v in [x for x in self.violations if x.rule == rule][:5]:
                loc = []
                if v.fellow:
                    loc.append(v.fellow)
                if v.week is not None:
                    loc.append(f"wk{v.week}")
                if v.day is not None:
                    loc.append(f"d{v.day}")
                lines.append(f"      [{' '.join(loc)}] {v.detail}")
        return "\n".join(lines)


# --- per-(fellow,day) helpers built once -----------------------------------
def _build_tables(solution, config):
    """Return (fellows, num_days, num_weeks, start_dow, role_holder, day_role, days_in_week).

    role_holder[day][role] = fellow name or "".
    day_role[fellow][day]  = the role that fellow holds that day, or "".
    """
    fellows = list(solution.weekly_assignments.keys())
    num_weeks = len(next(iter(solution.weekly_assignments.values())))
    start_dow = config.start_dow
    role_holder = solution.call_assignments_by_day
    num_days = len(role_holder)

    day_role: dict[str, list[str]] = {f: [""] * num_days for f in fellows}
    for d in range(num_days):
        for role in CALL_ROLES:
            who = role_holder[d].get(role, "")
            if who:
                day_role[who][d] = role

    days_in_week: dict[int, list[int]] = {}
    for d in range(num_days):
        days_in_week.setdefault(day_to_week(d, start_dow), []).append(d)
    return fellows, num_days, num_weeks, start_dow, role_holder, day_role, days_in_week


def _is_off(day_role, weekly, working_bg, f, d, start_dow):
    """A fellow is fully OFF on day d iff they hold no call role that day AND are not on
    a working background rotation that week. The only non-working weekly label is 'NCC'
    (and blank). Mirrors _build_off_indicator."""
    if day_role[f][d]:
        return False
    w = day_to_week(d, start_dow)
    return weekly[f][w] in ("", "NCC")


# --- checks -----------------------------------------------------------------
def _check_coverage(t, res):
    """Hard coverage: weekday NCC1=NCC2=NF=1; weekend NCC1=NF=1, NO weekend NCC2."""
    _, num_days, _, start_dow, role_holder, _, _ = t
    for d in range(num_days):
        weekend = day_of_week(d, start_dow) in (5, 6)
        need = {"NCC1": 1, "NF": 1, "NCC2": 0 if weekend else 1}
        for role, k in need.items():
            held = 1 if role_holder[d].get(role) else 0
            if held != k:
                res.violations.append(NfViolation(
                    "coverage", f"{role}={held} (need {k})", day=d,
                    week=day_to_week(d, start_dow)))


def _check_nf_runs(t, res):
    """Every NF run is 4-6 consecutive days (a run truncated by the horizon end is OK)."""
    fellows, num_days, _, _, _, day_role, _ = t
    for f in fellows:
        d = 0
        while d < num_days:
            if day_role[f][d] == "NF":
                start = d
                while d < num_days and day_role[f][d] == "NF":
                    d += 1
                length = d - start
                truncated = d >= num_days       # run hits the horizon edge
                if not truncated and not (4 <= length <= 6):
                    res.violations.append(NfViolation(
                        "nf_run_length", f"NF run length {length} (need 4-6)",
                        fellow=f, day=start))
            else:
                d += 1


def _check_nf_rest_impl(t, weekly, working_bg, res):
    """Asymmetric rest: >=1 off day before an NF run starts, >=2 off days after it ends.
    off = no call role that day AND not on a working bg rotation that week."""
    fellows, num_days, _, start_dow, _, day_role, _ = t

    def off(f, d):
        return _is_off(day_role, weekly, working_bg, f, d, start_dow)

    for f in fellows:
        nf = [day_role[f][d] == "NF" for d in range(num_days)]
        for d in range(num_days):
            if not nf[d]:
                continue
            run_start = (d == 0) or (not nf[d - 1])
            run_end = (d == num_days - 1) or (not nf[d + 1])
            if run_start and d - 1 >= 0 and not off(f, d - 1):
                res.violations.append(NfViolation(
                    "nf_rest_before", f"day before NF run (d{d}) not off",
                    fellow=f, day=d - 1))
            if run_end:
                for k in (1, 2):
                    if d + k < num_days and not off(f, d + k):
                        res.violations.append(NfViolation(
                            "nf_rest_after", f"day {k} after NF run (d{d}) not off",
                            fellow=f, day=d + k))


def _check_week_off_cap(t, weekly, working_bg, config, res):
    """Rule A: <= nf_week_off_cap off days/week, EXCEPT a week with >= cap+1 mandatory
    NF-rest off days (a full in-week run's forced rest). off counted only in NCC/blank
    weeks (working-bg weeks aren't 'off')."""
    cap = getattr(config, "nf_week_off_cap", 0)
    if cap <= 0:
        return
    fellows, num_days, _, start_dow, _, day_role, days_in_week = t

    def off(f, d):
        return _is_off(day_role, weekly, working_bg, f, d, start_dow)

    for f in fellows:
        nf = [day_role[f][d] == "NF" for d in range(num_days)]
        for w, days in days_in_week.items():
            offs = [d for d in days if off(f, d)]
            if len(offs) <= cap:
                continue
            # count mandatory NF-rest off days in this week (mirrors the encoder's rest[d]:
            # off & nf[d+1] [day before a start] OR off & nf[d-1] [1st after end]
            # OR off & ~nf[d-1] & nf[d-2] [2nd after end]).
            rest = 0
            for d in offs:
                before = (d + 1 < num_days) and nf[d + 1]
                after1 = (d - 1 >= 0) and nf[d - 1]
                after2 = (d - 2 >= 0) and nf[d - 2] and not ((d - 1 >= 0) and nf[d - 1])
                if before or after1 or after2:
                    rest += 1
            if len(offs) > cap and rest < cap + 1:
                res.violations.append(NfViolation(
                    "week_off_cap",
                    f"{len(offs)} off days (cap {cap}); only {rest} are NF-rest",
                    fellow=f, week=w))


def _check_max_consecutive_call(t, config, res):
    """Rule B: no more than nf_max_consecutive_call_days consecutive call days."""
    cap = getattr(config, "nf_max_consecutive_call_days", 0)
    if cap <= 0:
        return
    fellows, num_days, _, _, _, day_role, _ = t
    for f in fellows:
        run = 0
        for d in range(num_days):
            run = run + 1 if day_role[f][d] else 0
            if run > cap:
                res.violations.append(NfViolation(
                    "max_consecutive_call",
                    f"{run} consecutive call days (cap {cap})", fellow=f, day=d))
                break


def _check_ncc1_continuity(t, config, res):
    """NCC1 contiguity: weekday => NCC1 constant Mon-Fri; fullweek => constant all 7."""
    mode = getattr(config, "nf_ncc1_continuity", "off")
    if mode not in ("weekday", "fullweek"):
        return
    fellows, num_days, _, start_dow, _, _, days_in_week = t
    role_holder = t[4]
    for w, days in days_in_week.items():
        sel = [d for d in sorted(days)
               if mode == "fullweek" or day_of_week(d, start_dow) < 5]
        holders = {role_holder[d].get("NCC1", "") for d in sel if role_holder[d].get("NCC1")}
        if len(holders) > 1:
            res.violations.append(NfViolation(
                "ncc1_continuity",
                f"{mode} NCC1 held by {sorted(holders)} within the week", week=w))


def _check_min_ncc_block_impl(weekly, fellows, num_weeks, config, res):
    """Rule 11: every interior NCC-labeled week has an NCC neighbor week (>=2-wk blocks)."""
    if not getattr(config, "nf_min_ncc_block_weeks", False):
        return
    for f in fellows:
        labels = weekly[f]
        for w in range(1, num_weeks - 1):
            if labels[w] == "NCC" and labels[w - 1] != "NCC" and labels[w + 1] != "NCC":
                res.violations.append(NfViolation(
                    "min_ncc_block", "lone NCC week (no NCC neighbor)", fellow=f, week=w))


def _check_ccm_no_bridge(t, weekly, config, res):
    """Rule C: a CCM fellow's NF run may not bridge a 4-week block boundary (offset-1)."""
    if not getattr(config, "nf_ccm_no_bridge_blocks", False):
        return
    from parafrost_scheduler.schedule_encoder import _block_starts_grid
    fellows, num_days, num_weeks, start_dow, _, day_role, days_in_week = t
    ccm = set(config.fellow_groups.get("CCM", []))
    starts = [bs for bs, _ in _block_starts_grid(num_weeks, 4, 1)][1:]
    for f in fellows:
        if f not in ccm:
            continue
        nf = [day_role[f][d] == "NF" for d in range(num_days)]
        for wk in starts:
            ds = days_in_week.get(wk, [])
            if not ds:
                continue
            b = min(ds)
            if b - 1 >= 0 and nf[b - 1] and nf[b]:
                res.violations.append(NfViolation(
                    "ccm_no_bridge", f"CCM NF run bridges block boundary at d{b}",
                    fellow=f, day=b))


def evaluate_nf(solution, config) -> NfEvalResult:
    """Audit a solved NF schedule. Only runs checks whose feature is active in config
    (gated like the encoder), plus the always-on structural checks (coverage, NF runs,
    NF rest). Returns NfEvalResult; res.ok is True iff no violations."""
    res = NfEvalResult()
    if not getattr(config, "call_tier_day_granular", False):
        return res  # not an NF-model solution; nothing to evaluate here
    if not solution.call_assignments_by_day:
        return res

    t = _build_tables(solution, config)
    fellows, num_days, num_weeks, start_dow, role_holder, day_role, days_in_week = t
    weekly = solution.weekly_assignments
    working_bg = None  # the off-helper uses the weekly LABEL, not shift indices

    # always-on structural checks
    _check_coverage(t, res); res.checks_run.append("coverage")
    _check_nf_runs(t, res); res.checks_run.append("nf_run_length")
    _check_nf_rest_impl(t, weekly, working_bg, res)
    res.checks_run += ["nf_rest_before", "nf_rest_after"]

    # gated checks (one per NF rule)
    _check_week_off_cap(t, weekly, working_bg, config, res)
    if getattr(config, "nf_week_off_cap", 0) > 0:
        res.checks_run.append("week_off_cap")
    _check_max_consecutive_call(t, config, res)
    if getattr(config, "nf_max_consecutive_call_days", 0) > 0:
        res.checks_run.append("max_consecutive_call")
    _check_ncc1_continuity(t, config, res)
    if getattr(config, "nf_ncc1_continuity", "off") in ("weekday", "fullweek"):
        res.checks_run.append("ncc1_continuity")
    _check_min_ncc_block_impl(weekly, fellows, num_weeks, config, res)
    if getattr(config, "nf_min_ncc_block_weeks", False):
        res.checks_run.append("min_ncc_block")
    _check_ccm_no_bridge(t, weekly, config, res)
    if getattr(config, "nf_ccm_no_bridge_blocks", False):
        res.checks_run.append("ccm_no_bridge")
    return res
