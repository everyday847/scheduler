"""Phase-1 foundation tests for the NF model's day-granular call tier.

The whole tier activates iff config.call_tier_day_granular is True, set only from
a new config's solver_options. Default-off keeps the wb7 path byte-identical.
"""
from __future__ import annotations

from scheduler.solver_bridge import build_solver_config_from_request


def _minimal_request(**solver_options):
    """A tiny no-workbook request: 2 fellows, 3 shifts, no standing rules."""
    return {
        "fellow_groups": {"NCC_JR": ["A", "B"]},
        "shifts": ["NCC1", "NCC2", "Elec"],
        "standing_rules": [],  # bypass disk standing config
        "horizon_start": "2026-07-01",
        "num_weeks": 2,
        "solver_options": dict(solver_options),
    }


def test_flag_defaults_false_when_absent():
    cfg = build_solver_config_from_request(_minimal_request())
    assert cfg.call_tier_day_granular is False


def test_flag_threads_from_solver_options():
    cfg = build_solver_config_from_request(
        _minimal_request(call_tier_day_granular=True))
    assert cfg.call_tier_day_granular is True


from _nf_helpers import make_nf_config, build
from parafrost_scheduler.schedule_types import CALL_ROLES


def test_call_vars_exist_per_day_fellow_role_when_flag_on():
    cfg = make_nf_config(num_days=14)
    opb, vm = build(cfg)
    assert len(vm.call) == 14                       # one per day
    assert len(vm.call[0]) == vm.num_fellows        # one per fellow
    assert set(vm.call[0][0].keys()) == set(CALL_ROLES)
    sample = vm.call[0][0]["NCC1"]
    assert isinstance(sample, int) and sample > 0


def test_call_vars_absent_when_flag_off():
    cfg = make_nf_config(num_days=14, call_tier_day_granular=False)
    opb, vm = build(cfg)
    assert vm.call == []   # no day-granular call layer on the wb7-style path


from _nf_helpers import runner_or_skip
from parafrost_scheduler.schedule_types import day_of_week


def _role_holders(assignment, vm, d, role):
    return [f for f in range(vm.num_fellows)
            if assignment.get(vm.call[d][f][role], False)]


def test_coverage_is_satisfiable_and_exactly_one_each():
    cfg = make_nf_config(num_days=14)   # 2 weeks, 6 fellows
    opb, vm = build(cfg)
    res = runner_or_skip().solve(opb, timeout=30)
    assert res.satisfiable
    a = res.assignment
    for d in range(14):
        dow = day_of_week(d, vm.start_dow)
        assert len(_role_holders(a, vm, d, "NCC1")) == 1
        assert len(_role_holders(a, vm, d, "NF")) == 1
        if dow in (5, 6):   # weekend: no NCC2
            assert len(_role_holders(a, vm, d, "NCC2")) == 0
        else:               # weekday: exactly one NCC2
            assert len(_role_holders(a, vm, d, "NCC2")) == 1


def test_one_call_role_per_fellow_per_day():
    cfg = make_nf_config(num_days=14)
    opb, vm = build(cfg)
    res = runner_or_skip().solve(opb, timeout=30)
    assert res.satisfiable
    a = res.assignment
    for d in range(14):
        for f in range(vm.num_fellows):
            held = [r for r in CALL_ROLES if a.get(vm.call[d][f][r], False)]
            assert len(held) <= 1


def _shift_idx(vm, name):
    return vm.shifts.index(name)


def test_blocking_via_link_micu_week_excludes_call():
    """Verify that the weekly<->day link (not the removed seam) enforces blocking:
    pinning xs[f][0][MICU]=1 must prevent the fellow from holding any call role
    on days 0..6.

    Mechanism: call[d][f][role]=1 => xs[f][w][role_shift_idx]=1 (forward link).
    At-most-one-shift-per-week prevents xs[f][w][MICU]=1 and xs[f][w][NCC1]=1
    simultaneously.  Therefore any call day in week 0 for this fellow is UNSAT
    when MICU is pinned.

    Non-vacuity: this test would FAIL if _encode_call_weekly_link's forward
    implication (call[d][f][role] => xs[f][w][role_si]) were removed, because
    the link is the only remaining mechanism that collapses the call day into the
    weekly slot (the seam was deleted as dead code — the link fully subsumes it).

    No palette/call_blocking needed: the link operates on shift indices alone."""
    cfg = make_nf_config(num_days=14)
    opb, vm = build(cfg)
    f, w, micu = 0, 0, _shift_idx(vm, "MICU")
    assert vm.xs[f][w][micu] != 0, "xs[0][0][MICU] must be a real var for this test to be meaningful"
    opb.add_unit(vm.xs[f][w][micu])     # pin fellow 0 to MICU in week 0
    res = runner_or_skip().solve(opb, timeout=30)
    assert res.satisfiable              # other fellows still cover call
    a = res.assignment
    for d in range(7):                  # week 0 days
        for r in ("NCC1", "NCC2", "NF"):
            assert not a.get(vm.call[d][f][r], False)


def test_legacy_night_layer_absent_under_flag():
    cfg = make_nf_config(num_days=14)
    opb, vm = build(cfg)
    assert vm.xn == [] or all(all(v == 0 for v in day) for day in vm.xn)


def test_weekly_label_reflects_call_day():
    cfg = make_nf_config(num_days=14)
    opb, vm = build(cfg)
    f, d = 0, 0
    opb.add_unit(vm.call[d][f]["NF"])      # force fellow 0 onto NF day 0
    res = runner_or_skip().solve(opb, timeout=30)
    assert res.satisfiable
    nf_si = vm.shifts.index("NF")
    assert res.assignment.get(vm.xs[f][0][nf_si], False), \
        "weekly NF label must be set when the fellow has an NF call day that week"


def test_decode_surfaces_call_assignments():
    from parafrost_scheduler.schedule_encoder import decode_solution
    cfg = make_nf_config(num_days=14)
    opb, vm = build(cfg)
    res = runner_or_skip().solve(opb, timeout=30)
    assert res.satisfiable
    sol = decode_solution(res.assignment, vm)
    assert len(sol.call_assignments_by_day) == 14
    day0 = sol.call_assignments_by_day[0]
    assert day0["NCC1"] != "" and day0["NF"] != ""


def test_forbidden_weekly_call_role_forbids_that_call_at_day_tier():
    """A fellow forbidden their WEEKLY NF rotation must not hold NF CALL that week.

    Guards the weekly<->day link's handling of a forbidden weekly call-role slot —
    the latent Phase-2 trap where a hard weekly rule could be bypassed at the day
    tier. Holds via two routes that this test covers together: (a) when the weekly
    var is allocated-but-forced-false (zero_shifts here), the link's forward
    implication call=>weekly-label propagates the forbid; (b) when the weekly var
    is UNALLOCATED (==0), `_encode_call_weekly_link` now emits add_unit(-call) for
    those days. Either way the call role must be off."""
    from scheduler.semantic_constraints import (
        SemanticConstraint, ConstraintLifecycle, ConstraintStrength, FellowSelector)
    forbid_nf = SemanticConstraint(
        kind="zero_shifts", lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=ConstraintStrength.HARD, fellows=FellowSelector.by_names("J1"),
        params={"zero_shifts": ["NF"]})
    cfg = make_nf_config(num_days=14, constraints=[forbid_nf])
    opb, vm = build(cfg)
    f = vm.fellow_names.index("J1")
    opb.add_unit(vm.call[0][f]["NF"])     # try to put J1 on NF call despite the forbid
    res = runner_or_skip().solve(opb, timeout=30)
    assert not res.satisfiable


# ---------------------------------------------------------------------------
# Task 6: Config-file integration — assemble via real YAML configs
# ---------------------------------------------------------------------------

from pathlib import Path
from parafrost_scheduler.experiment import assemble_config

_REPO = Path(__file__).resolve().parent.parent


def test_nf_model_configs_assemble_with_flag_on():
    cfg, _ = assemble_config(
        None,
        annual_path=_REPO / "config/annual/ncc-nf-model.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml",
        verbose=False)
    assert cfg.call_tier_day_granular is True
    assert "Swing" not in cfg.shifts
    assert {"NCC1", "NCC2", "NF"} <= set(cfg.shifts)
    all_fellows = [f for g in cfg.fellow_groups.values() for f in g]
    assert len(all_fellows) == 6   # 3 JR + 2 SR + 1 CCM


# ---------------------------------------------------------------------------
# Task 8: Integration gate — full-year NF model must solve (not UNSAT)
# ---------------------------------------------------------------------------

from _dispatch_helpers import solve_sat


def test_full_year_nf_model_is_satisfiable():
    """Integration gate: the real full-year ncc-nf-model config must SOLVE.
    Regression for the instant-UNSAT from the legacy weekend layer (wb7 fellows)
    + hard backup coverage forcing a weekly Elec fellow the lean roster can't spare."""
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-model.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    from parafrost_scheduler.schedule_encoder import build_full_schedule_opb
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    assert solve_sat(opb, timeout=120), "full-year NF model must be feasible"


def test_legacy_weekend_layer_absent_under_flag():
    cfg = make_nf_config(num_days=14)
    opb, vm = build(cfg)
    assert vm.wr == [] or all(all(len(rm) == 0 for rm in week) for week in vm.wr)


# ---------------------------------------------------------------------------
# Task 8b: Weekend Backup excludes weekend call roles (re-link after wr gated off)
# ---------------------------------------------------------------------------

from parafrost_scheduler.schedule_types import _BACKUP_WEEKEND


def test_weekend_backup_excludes_weekend_call_role():
    """Invariant: a Weekend Backup fellow must not also hold a weekend call role
    (NCC1/NF) on that weekend.

    NOTE: this property currently holds via TWO independent mechanisms — the
    explicit `_encode_call_weekend_backup_exclusion` (added when the legacy
    wr-based exclusion was gated off for the NF model), AND transitively
    (backup => Elec week, call-day => call-label, at-most-one-shift-per-week). So
    this is an invariant check, not a red->green guard for the explicit clause
    alone; the explicit clause is defense-in-depth that survives a future change
    to backup eligibility (e.g. if a call-compatible shift were ever made
    backup-eligible, the transitive argument would break but this would not).

    Index verification (empirically confirmed):
      - start_dow=0 (Monday), so week w Saturday = w*7+5.
      - f=1 (S1, NCC_SR) is in the weekend backup group (f=0/C1 is not).
      - _BACKUP_WEEKEND kind index = 1.
      - Week 1 Saturday = day 12; vm.bk[1][1].get(1) and vm.call[12][1]["NF"]
        are both real positive vars.
    """
    cfg = make_nf_config(num_days=14)
    opb, vm = build(cfg)
    f = 1                   # S1 (NCC_SR) — in weekend backup group
    w = 1                   # week 1 (week 0 is backup-forbidden)
    sat = w * 7 + 5         # day 12, Saturday of week 1 (start_dow=0)
    wb = vm.bk[w][_BACKUP_WEEKEND].get(f, 0)
    nf = vm.call[sat][f]["NF"]
    assert wb != 0 and nf != 0, (
        f"need both vars allocated for a meaningful test: wb={wb}, nf={nf}"
    )
    opb.add_unit(wb)
    opb.add_unit(nf)
    res = runner_or_skip().solve(opb, timeout=30)
    assert not res.satisfiable
