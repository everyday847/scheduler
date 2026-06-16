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


def test_micu_week_blocks_call_for_that_fellow():
    cfg = make_nf_config(num_days=14)
    opb, vm = build(cfg)
    f, w, micu = 0, 0, _shift_idx(vm, "MICU")
    # Verify the weekly var is allocated (no forbidden entry for this fellow/week/shift)
    assert vm.xs[f][w][micu] != 0, "xs[0][0][MICU] must be a real var for this test to be meaningful"
    opb.add_unit(vm.xs[f][w][micu])     # pin fellow 0 to MICU in week 0
    res = runner_or_skip().solve(opb, timeout=30)
    assert res.satisfiable              # other fellows still cover call
    a = res.assignment
    for d in range(7):                  # week 0 days
        for r in ("NCC1", "NCC2", "NF"):
            assert not a.get(vm.call[d][f][r], False)


def test_seam_blocks_within_week_when_link_absent():
    """The background->call seam directly prevents a call role when the fellow's
    weekly background is call_blocking — even when the weekly<->day link cannot
    enforce this because the call role is NOT among the weekly shifts.

    Design note: when the call role IS in the shift list (the normal NF model
    config), the link+at-most-one already subsumes the seam.  To isolate the seam
    alone, we use a config where "NF" is absent from shifts, so the link never
    fires for NF.  In that setting, xs[f][0][MICU]=1 + call[0][f][NF]=1 is UNSAT
    only because the seam emits: call[d][f][r] + xs[f][w][MICU] <= 1.

    Non-vacuity: if you disable _encode_call_background_seam in the encoder, this
    test becomes SAT (proven experimentally)."""
    f, w = 0, 0
    # "NF" deliberately absent from shifts so the weekly<->day link is silent for NF.
    cfg = make_nf_config(shifts=("NCC1", "NCC2", "MICU", "Elec", "Vac"), num_days=14)
    opb, vm = build(cfg)
    micu = _shift_idx(vm, "MICU")
    assert vm.xs[f][w][micu] != 0, "xs[0][0][MICU] must be a real var"
    assert vm.call[0][f]["NF"] != 0, "call[0][0][NF] must be a real var"
    assert "NF" not in vm.shifts, "NF must be absent from shifts for the link to be silent"
    opb.add_unit(vm.xs[f][w][micu])      # pin MICU week 0
    opb.add_unit(vm.call[0][f]["NF"])    # force NF call day 0 (same week, same fellow)
    res = runner_or_skip().solve(opb, timeout=30)
    assert not res.satisfiable, (
        "MICU (call_blocking) in week 0 must forbid an NF call day in week 0 "
        "via the seam; this is UNSAT even without the weekly<->day link"
    )


def test_seam_blocked_by_attribute_not_hardcoded_name():
    """The seam must fire based on the call_blocking palette attribute, not a
    hardcoded shift-name set.  When MICU is NOT marked call_blocking, pinning
    xs[f][0][MICU]=1 must NOT prevent an NF call day in week 0 (the seam is silent;
    only the link+at-most-one would block it, but NF is absent from shifts here).

    CONTRAST with test_seam_blocks_within_week_when_link_absent: same scenario,
    only the palette changes — MICU loses call_blocking.  The result flips from
    UNSAT to SAT, proving the attribute, not the shift name, drives the seam."""
    from scheduler.shift_palette import ShiftPalette

    # MICU deliberately absent from call_blocking; all others retained.
    palette_no_micu_block = ShiftPalette.from_config({
        "NS":   ["call_blocking"],
        "SICU": ["call_blocking"],
        "Vac":  ["call_blocking"],
        # MICU omitted — it is a real rotation but must not block call under this palette
    })
    f, w = 0, 0
    # "NF" absent from shifts so the link is silent for NF (mirrors the blocking test).
    cfg = make_nf_config(
        shifts=("NCC1", "NCC2", "MICU", "Elec", "Vac"),
        num_days=14,
        shift_palette=palette_no_micu_block,
    )
    opb, vm = build(cfg)
    micu = _shift_idx(vm, "MICU")
    assert vm.xs[f][w][micu] != 0, "xs[0][0][MICU] must be a real var"
    assert vm.call[0][f]["NF"] != 0, "call[0][0][NF] must be a real var"
    opb.add_unit(vm.xs[f][w][micu])      # pin MICU week 0
    opb.add_unit(vm.call[0][f]["NF"])    # force NF call day 0
    res = runner_or_skip().solve(opb, timeout=30)
    assert res.satisfiable, (
        "MICU without call_blocking must NOT forbid an NF call day — "
        "blocking is attribute-driven; removing the attribute must lift the forbid"
    )


def test_seam_scoped_to_its_own_week():
    """The background->call seam is week-scoped: a call_blocking background in
    week 0 must NOT block the same fellow's call in week 1.  This exercises the
    w = day_to_week(d) scoping in _encode_call_background_seam.

    Config: NF absent from shifts (so the link is silent for NF) to isolate the
    seam.  In week 1 the fellow is free (no MICU pin), so the seam for week 0 MICU
    must not emit any constraint on week-1 day variables."""
    f = 0
    # "NF" absent from shifts so the link is silent for NF.
    cfg = make_nf_config(shifts=("NCC1", "NCC2", "MICU", "Elec", "Vac"), num_days=14)
    opb, vm = build(cfg)
    micu = _shift_idx(vm, "MICU")
    assert vm.xs[f][0][micu] != 0, "xs[0][0][MICU] must be a real var"
    assert vm.call[7][f]["NF"] != 0, "call[7][0][NF] must be a real var (day 7 = week 1)"
    opb.add_unit(vm.xs[f][0][micu])       # pin MICU week 0 for fellow 0
    opb.add_unit(vm.call[7][f]["NF"])     # force NF call on day 7 (week 1) for fellow 0
    res = runner_or_skip().solve(opb, timeout=30)
    assert res.satisfiable, (
        "MICU (call_blocking) in week 0 must NOT block an NF call day in week 1 — "
        "the seam must be scoped to the week of the blocking background"
    )


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
