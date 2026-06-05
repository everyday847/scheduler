"""Tests for the Backup / Weekend Backup role encoding.

Eligibility: NCC_JR/NCC_SR on Elec, or STROKE on Clinic/Elective or
Telestroke/Clinic. Weekend Backup additionally requires the fellow NOT hold a
weekend call role that week. Coverage is hard (exactly one of each per week). No
fellow is on Backup more than 2 consecutive weeks.
"""

from __future__ import annotations

from datetime import date

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.schedule_solver import (
    ScheduleSolverConfig,
    _BACKUP_WEEKDAY,
    _BACKUP_WEEKEND,
    _ROLE_NCC1,
    _allocate_backup_vars,
    _encode_backup_constraints,
    build_full_schedule_opb,
    decode_solution,
)
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig, BackupScheduleSolution


def _config(fellow_groups, shifts, num_days=21):
    night_config = NightSolverConfig(
        total_nights={}, friday_nights={}, total_night_multisets=(),
        friday_night_multisets=(), ccm_fellows=frozenset(), holiday_dates=(),
        horizon_start_date=date(2026, 7, 1),
    )
    weekend_config = WeekendSolverConfig(
        ncc_totals={}, stroke_totals={}, stroke_cohort=(), stroke_cohort_total=None,
        ccm_fellows=frozenset(), always_stroke_eligible=frozenset(),
        telestroke_stroke_eligible=frozenset(), stroke_only_eligible=frozenset(),
        total_weekends={}, weekend_options=None, friday_weekend_options=None,
    )
    return ScheduleSolverConfig(
        fellow_groups=fellow_groups, shifts=shifts, constraints=[],
        night_config=night_config, weekend_config=weekend_config,
        night_hard_criteria=frozenset(), start_dow=0, num_days=num_days,
    )


def _setup(fellow_groups, shifts, *, num_days=21, weekend_holders=None):
    """Build xs, wr, bk for the given config and encode backup constraints.

    weekend_holders: optional {(week, fellow_idx)} that hold a Weekend NCC1 role.
    Returns (opb, bk, xs, wr, fellow_names, shift_idx, num_weeks).
    """
    config = _config(fellow_groups, shifts, num_days=num_days)
    fellow_names = [f for g in fellow_groups.values() for f in g]
    shift_idx = {s: i for i, s in enumerate(shifts)}
    num_weeks = config.num_weeks
    num_fellows = len(fellow_names)
    opb = OpbBuilder()
    # xs[f][w][s]
    xs = [[[opb.new_var() for _ in shifts] for _ in range(num_weeks)] for _ in range(num_fellows)]
    # wr[w][role][f] — only Weekend NCC1 populated for the listed holders.
    wr = []
    holders = weekend_holders or set()
    for w in range(num_weeks):
        roles = [{}, {}, {}]
        for f in range(num_fellows):
            if (w, f) in holders:
                roles[_ROLE_NCC1][f] = opb.new_var()
        wr.append(roles)
    bk = _allocate_backup_vars(opb, config, fellow_names)
    soft = []
    _encode_backup_constraints(opb, bk, wr, xs, config, fellow_names, shift_idx, soft)
    return opb, bk, xs, wr, fellow_names, shift_idx, num_weeks


class TestBackupEligibilityAllocation:
    def test_ncc_and_stroke_fellows_get_backup_vars(self):
        opb, bk, *_ = _setup(
            {"NCC_SR": ["Alice"], "STROKE": ["Stan"], "CCM": ["Cara"]},
            ["Elec", "Clinic/Elective", "Telestroke/Clinic", "MICU"],
        )
        # Alice (idx 0) and Stan (idx 1) eligible; Cara (idx 2, CCM) NOT.
        assert 0 in bk[0][_BACKUP_WEEKDAY]
        assert 1 in bk[0][_BACKUP_WEEKDAY]
        assert 2 not in bk[0][_BACKUP_WEEKDAY]
        assert 2 not in bk[0][_BACKUP_WEEKEND]


class TestBackupShiftGating:
    def test_ncc_backup_requires_elec(self):
        """Alice's weekday Backup var must imply she is on Elec that week:
        a constraint linking bk var to the Elec shift var exists."""
        opb, bk, xs, wr, names, shift_idx, nw = _setup(
            {"NCC_SR": ["Alice"]}, ["Elec", "MICU"],
        )
        bk_var = bk[0][_BACKUP_WEEKDAY][0]
        elec_var = xs[0][0][shift_idx["Elec"]]
        micu_var = xs[0][0][shift_idx["MICU"]]
        # Some constraint references both bk_var and elec_var (the gating OR).
        gated = [c for c in opb._constraints
                 if f"x{bk_var} " in c.replace("~", "") and f"x{elec_var} " in c.replace("~", "")]
        assert gated, "weekday Backup must be gated on the Elec shift var"
        # And it must NOT be gated on MICU (not an eligible backup shift).
        micu_gate = [c for c in opb._constraints
                     if f"x{bk_var} " in c.replace("~", "") and f"x{micu_var} " in c.replace("~", "")]
        assert not micu_gate, "Backup must not reference MICU"


class TestWeekendBackupExcludesWeekendRole:
    def test_weekend_backup_excludes_weekend_ncc1_holder(self):
        opb, bk, xs, wr, names, shift_idx, nw = _setup(
            {"STROKE": ["Stan"]}, ["Clinic/Elective", "Telestroke/Clinic"],
            weekend_holders={(0, 0)},  # Stan holds Weekend NCC1 in week 0
        )
        wb_var = bk[0][_BACKUP_WEEKEND][0]
        wr_var = wr[0][_ROLE_NCC1][0]
        # A constraint forbids holding both: ...wb_var... wr_var ... <= 1
        both = [c for c in opb._constraints
                if f"x{wb_var} " in c.replace("~", "") and f"x{wr_var} " in c.replace("~", "")]
        assert both, "Weekend Backup must exclude a weekend-role holder"


class TestBackupHardCoverage:
    def test_exactly_one_backup_per_week(self):
        opb, bk, xs, wr, names, shift_idx, nw = _setup(
            {"NCC_SR": ["Alice", "Bob"]}, ["Elec"],
        )
        # exactly_one over week-0 weekday backup vars => a "= 1 ;" constraint
        # covering exactly Alice+Bob's bk weekday vars.
        wd_vars = set(bk[0][_BACKUP_WEEKDAY].values())
        eq1 = [c for c in opb._constraints if c.strip().endswith("= 1 ;")
               and {int(t[1:]) for t in c.replace("~", "").split() if t.startswith("x")} == wd_vars]
        assert eq1, "must emit exactly-one weekday Backup coverage for week 0"


class TestBackupMaxConsecutive:
    def test_three_week_window_bounds_backup_to_two(self):
        """Over weeks 0-2, a fellow's combined backup indicator is at_most 2."""
        opb, bk, xs, wr, names, shift_idx, nw = _setup(
            {"NCC_SR": ["Alice"]}, ["Elec"], num_days=28,  # 4 weeks
        )
        # There should be at least one at_most-2 (<= 2 ;) constraint (the sliding
        # window over Alice's on_backup indicators).
        le2 = [c for c in opb._constraints if c.strip().endswith("<= 2 ;")]
        assert le2, "must emit a max-2-consecutive-backup-weeks window constraint"


class TestBackupDecode:
    def test_decode_populates_backup_solution(self):
        """A solved assignment decodes Backup + Weekend Backup per week."""
        config = _config({"NCC_SR": ["Alice", "Bob"]}, ["Elec", "MICU"], num_days=14)
        opb, vm = build_full_schedule_opb(config, objective=True)
        # Hand-build an assignment: pick the first available weekday + weekend
        # backup var each week as True, everything else False.
        assignment = {}
        for w in range(vm.num_weeks):
            for kind in (_BACKUP_WEEKDAY, _BACKUP_WEEKEND):
                vars_ = list(vm.bk[w][kind].items())
                if vars_:
                    chosen_f, chosen_v = vars_[0]
                    assignment[chosen_v] = True
        sol = decode_solution(assignment, vm)
        assert sol.backup_solution is not None
        assert len(sol.backup_solution.assignments_by_week) == vm.num_weeks
        wk0 = sol.backup_solution.assignments_by_week[0]
        assert "Backup" in wk0 and "Weekend Backup" in wk0
        # The chosen fellow name is one of the NCC_SR fellows (or "" if no var).
        assert wk0["Backup"] in ("Alice", "Bob", "")

    def test_backup_solution_is_dataclass(self):
        sol = BackupScheduleSolution(assignments_by_week=[{"Backup": "X", "Weekend Backup": ""}])
        assert sol.assignments_by_week[0]["Backup"] == "X"
