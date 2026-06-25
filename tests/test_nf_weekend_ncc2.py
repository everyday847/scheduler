"""Weekend NCC2 coverage + NCC2-specific min-run (no lone NCC2 day).

config.nf_weekend_ncc2: when True, weekend NCC2 is COVERED (exactly one holder) instead
of forbidden. config.nf_min_ncc2_run_days: per fellow, NCC2 runs >= k (no single-day NCC2).
Day 12 = Saturday, 13 = Sunday (start_dow=0). Weekday block: 14=Mon..18=Fri.
"""
from __future__ import annotations

from _nf_helpers import make_nf_config, build, runner_or_skip

_SAT, _SUN = 12, 13


def _weekend_ncc2_holder_count(assignment, vm, d):
    return sum(1 for f in range(vm.num_fellows)
               if assignment.get(vm.call[d][f]["NCC2"], False))


# --- weekend NCC2 coverage --------------------------------------------------

def test_weekend_ncc2_forbidden_by_default():
    """Default (flag off): no fellow may hold NCC2 on a weekend day."""
    cfg = make_nf_config(num_days=28, nf_weekend_ncc2=False)
    opb, vm = build(cfg)
    # pin someone to weekend NCC2 -> UNSAT (forbidden)
    opb.add_unit(vm.call[_SAT][1]["NCC2"])
    res = runner_or_skip().solve(opb, timeout=60)
    assert not res.satisfiable


def test_weekend_ncc2_covered_when_enabled():
    """Flag on: weekend NCC2 is covered (exactly one holder each weekend day)."""
    cfg = make_nf_config(num_days=28, nf_weekend_ncc2=True)
    opb, vm = build(cfg)
    res = runner_or_skip().solve(opb, timeout=60)
    assert res.satisfiable
    a = res.assignment
    assert _weekend_ncc2_holder_count(a, vm, _SAT) == 1
    assert _weekend_ncc2_holder_count(a, vm, _SUN) == 1


def test_weekend_ncc2_flag_changes_constraint_count():
    off = make_nf_config(num_days=28, nf_weekend_ncc2=False)
    on = make_nf_config(num_days=28, nf_weekend_ncc2=True)
    opb_off, _ = build(off)
    opb_on, _ = build(on)
    # enabling coverage replaces N forbid-units with exactly-one lines; counts differ
    assert opb_on.num_constraints != opb_off.num_constraints


# --- NCC2-specific min run --------------------------------------------------

def test_lone_ncc2_day_is_unsat():
    """RED->GREEN: a single isolated NCC2 day (NCC2 at weekday 16, not at 15 or 17 for
    that fellow) is forbidden when nf_min_ncc2_run_days=2. Weekend NCC2 enabled so the
    geometry is unconstrained by the weekend forbiddance."""
    cfg = make_nf_config(num_days=28, nf_weekend_ncc2=True, nf_min_ncc2_run_days=2)
    opb, vm = build(cfg)
    f = 1
    opb.add_unit(vm.call[16][f]["NCC2"])
    opb.add_unit(-vm.call[15][f]["NCC2"])
    opb.add_unit(-vm.call[17][f]["NCC2"])
    res = runner_or_skip().solve(opb, timeout=60)
    assert not res.satisfiable


def test_lone_ncc2_day_is_sat_without_rule():
    """Counterpart: SAME pins SAT when nf_min_ncc2_run_days is off."""
    cfg = make_nf_config(num_days=28, nf_weekend_ncc2=True, nf_min_ncc2_run_days=0)
    opb, vm = build(cfg)
    f = 1
    opb.add_unit(vm.call[16][f]["NCC2"])
    opb.add_unit(-vm.call[15][f]["NCC2"])
    opb.add_unit(-vm.call[17][f]["NCC2"])
    res = runner_or_skip().solve(opb, timeout=60)
    assert res.satisfiable


def test_two_day_ncc2_run_is_allowed():
    """A 2-day NCC2 run (days 15,16) stays SAT under min run = 2."""
    cfg = make_nf_config(num_days=28, nf_weekend_ncc2=True, nf_min_ncc2_run_days=2)
    opb, vm = build(cfg)
    f = 1
    opb.add_unit(vm.call[15][f]["NCC2"])
    opb.add_unit(vm.call[16][f]["NCC2"])
    res = runner_or_skip().solve(opb, timeout=60)
    assert res.satisfiable
