"""Weekend NCC1 is one assignment: the Saturday and Sunday NCC1 holder must match.

config.nf_weekend_ncc1_paired -> NCC1[sat][f] <=> NCC1[sun][f] per fellow/week.
Day 12 = Saturday, day 13 = Sunday (start_dow=0, week 1).
"""
from __future__ import annotations

from _nf_helpers import make_nf_config, build, runner_or_skip

_SAT, _SUN = 12, 13


def test_flag_off_emits_no_extra_constraints_flag_on_does():
    off = make_nf_config(num_days=28, nf_weekend_ncc1_paired=False)
    on = make_nf_config(num_days=28, nf_weekend_ncc1_paired=True)
    opb_off, _ = build(off)
    opb_on, _ = build(on)
    assert opb_on.num_constraints > opb_off.num_constraints


def test_split_weekend_ncc1_is_unsat():
    """RED->GREEN: fellow f on Sat NCC1 but forbidden Sun NCC1. With pairing on, the
    rule forces f to also hold Sun NCC1 -> contradiction -> UNSAT."""
    cfg = make_nf_config(num_days=28, nf_weekend_ncc1_paired=True)
    opb, vm = build(cfg)
    f = 1
    opb.add_unit(vm.call[_SAT][f]["NCC1"])
    opb.add_unit(-vm.call[_SUN][f]["NCC1"])
    res = runner_or_skip().solve(opb, timeout=120)
    assert not res.satisfiable


def test_split_weekend_ncc1_is_sat_without_rule():
    """Counterpart: SAME pins SAT when pairing is off (another fellow takes Sun NCC1)."""
    cfg = make_nf_config(num_days=28, nf_weekend_ncc1_paired=False)
    opb, vm = build(cfg)
    f = 1
    opb.add_unit(vm.call[_SAT][f]["NCC1"])
    opb.add_unit(-vm.call[_SUN][f]["NCC1"])
    res = runner_or_skip().solve(opb, timeout=120)
    assert res.satisfiable


def test_matched_weekend_ncc1_is_sat():
    """Same fellow on both Sat and Sun NCC1 stays SAT under pairing."""
    cfg = make_nf_config(num_days=28, nf_weekend_ncc1_paired=True)
    opb, vm = build(cfg)
    f = 1
    opb.add_unit(vm.call[_SAT][f]["NCC1"])
    opb.add_unit(vm.call[_SUN][f]["NCC1"])
    res = runner_or_skip().solve(opb, timeout=120)
    assert res.satisfiable
