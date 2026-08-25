"""NCC-service runs must be >= N consecutive days (config.nf_min_ncc_run_days).

A "service day" = the fellow holds NCC1 OR NCC2; NF/off breaks a run. A lone 1-day
NCC stint between NF runs is the bug this closes. Mirrors the NF run-length tests:
fast build-only vacuity + pinned red->green UNSAT guard on the short fixture.

Day indices use start_dow=0 (day 0 = Monday). Weekday days only for NCC2 pins
(NCC2 is forbidden on weekends by the call-tier coverage).
"""
from __future__ import annotations

from _nf_helpers import make_nf_config, build, runner_or_skip

# Weekday block in week 2: 14=Mon 15=Tue 16=Wed 17=Thu 18=Fri.
_LONE = 16
_NEIGHBORS = (13, 14, 15, 17, 18, 19)   # surround the lone day with non-service days


def test_flag_off_emits_no_extra_constraints_flag_on_does():
    """Build-only vacuity: turning nf_min_ncc_run_days on emits new constraints."""
    off = make_nf_config(num_days=28, nf_min_ncc_run_days=0)
    on = make_nf_config(num_days=28, nf_min_ncc_run_days=4)
    opb_off, _ = build(off)
    opb_on, _ = build(on)
    assert opb_on.num_constraints > opb_off.num_constraints


def test_pinned_lone_ncc_day_is_unsat():
    """RED->GREEN guard: a length-1 NCC-service day (NCC2 at a weekday, with the
    surrounding days NOT service for that fellow) is forbidden when min run = 4."""
    cfg = make_nf_config(num_days=28, nf_min_ncc_run_days=4)
    opb, vm = build(cfg)
    f = 1
    opb.add_unit(vm.call[_LONE][f]["NCC2"])
    for d in _NEIGHBORS:
        opb.add_unit(-vm.call[d][f]["NCC1"])
        opb.add_unit(-vm.call[d][f]["NCC2"])
    res = runner_or_skip().solve(opb, timeout=120)
    assert not res.satisfiable


def test_pinned_lone_ncc_day_is_sat_without_rule():
    """Counterpart: the SAME pins are SAT when the rule is OFF, proving the UNSAT
    above is attributable to the min-run rule and not to the pins themselves."""
    cfg = make_nf_config(num_days=28, nf_min_ncc_run_days=0)
    opb, vm = build(cfg)
    f = 1
    opb.add_unit(vm.call[_LONE][f]["NCC2"])
    for d in _NEIGHBORS:
        opb.add_unit(-vm.call[d][f]["NCC1"])
        opb.add_unit(-vm.call[d][f]["NCC2"])
    res = runner_or_skip().solve(opb, timeout=120)
    assert res.satisfiable


def test_four_day_ncc_run_is_allowed():
    """A 4-day NCC-service run (NCC2 days 14..17, all weekdays) stays SAT under min
    run = 4, proving the rule forbids only short runs, not all NCC service."""
    cfg = make_nf_config(num_days=28, nf_min_ncc_run_days=4)
    opb, vm = build(cfg)
    f = 1
    for d in range(14, 18):
        opb.add_unit(vm.call[d][f]["NCC2"])
    res = runner_or_skip().solve(opb, timeout=120)
    assert res.satisfiable
