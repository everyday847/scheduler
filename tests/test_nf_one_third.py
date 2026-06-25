"""Per-fellow one-third-NF balance: hard band [27%,40%] + soft pull to 1/3.

config.nf_one_third_nf_band (hard band on NF/(NCC1+NCC2+NF) per fellow) and
config.nf_one_third_nf_weight (soft deviation penalty toward exactly 1/3).
Encoded at the day layer. Fast build-only vacuity + pinned red->green guards.
"""
from __future__ import annotations

from _nf_helpers import make_nf_config, build, runner_or_skip
from _dispatch_helpers import has_soft_weight


def test_band_flag_emits_constraints():
    off = make_nf_config(num_days=28)
    on = make_nf_config(num_days=28, nf_one_third_nf_band=True)
    opb_off, _ = build(off)
    opb_on, _ = build(on)
    assert opb_on.num_constraints > opb_off.num_constraints


def test_soft_weight_registers_penalty():
    cfg = make_nf_config(num_days=28, nf_one_third_nf_weight=10)
    opb, vm = build(cfg)
    assert has_soft_weight(vm, 10)


def test_all_nf_fellow_violates_band_unsat():
    """RED->GREEN: pin a fellow to NF on a 5-day run (days 7..11) and forbid them any
    NCC1/NCC2 all horizon. Then NF-share = 100% (> 40%) -> UNSAT with the band on."""
    cfg = make_nf_config(num_days=28, nf_one_third_nf_band=True)
    opb, vm = build(cfg)
    f = 1
    for d in range(7, 12):
        opb.add_unit(vm.call[d][f]["NF"])
    for d in range(vm.num_days):
        opb.add_unit(-vm.call[d][f]["NCC1"])
        opb.add_unit(-vm.call[d][f]["NCC2"])
    res = runner_or_skip().solve(opb, timeout=60)
    assert not res.satisfiable


def test_all_nf_fellow_sat_without_band():
    """Counterpart: SAME pins are SAT when the band is off (100% NF allowed)."""
    cfg = make_nf_config(num_days=28, nf_one_third_nf_band=False)
    opb, vm = build(cfg)
    f = 1
    for d in range(7, 12):
        opb.add_unit(vm.call[d][f]["NF"])
    for d in range(vm.num_days):
        opb.add_unit(-vm.call[d][f]["NCC1"])
        opb.add_unit(-vm.call[d][f]["NCC2"])
    res = runner_or_skip().solve(opb, timeout=60)
    assert res.satisfiable


def test_zero_nf_fellow_violates_band_unsat():
    """Lower-bound bite: a fellow forbidden ALL NF but pinned to several NCC1 days has
    NF-share 0% (< 27%) -> UNSAT with the band on. (At least one NCC service day must
    exist for the lower bound to be active; pin 4 NCC1 days.)"""
    cfg = make_nf_config(num_days=28, nf_one_third_nf_band=True)
    opb, vm = build(cfg)
    f = 1
    for d in range(vm.num_days):
        opb.add_unit(-vm.call[d][f]["NF"])           # never NF
    for d in (14, 15, 16, 17):                        # weekday NCC1 block
        opb.add_unit(vm.call[d][f]["NCC1"])
    res = runner_or_skip().solve(opb, timeout=60)
    assert not res.satisfiable


def test_zero_nf_fellow_sat_without_band():
    cfg = make_nf_config(num_days=28, nf_one_third_nf_band=False)
    opb, vm = build(cfg)
    f = 1
    for d in range(vm.num_days):
        opb.add_unit(-vm.call[d][f]["NF"])
    for d in (14, 15, 16, 17):
        opb.add_unit(vm.call[d][f]["NCC1"])
    res = runner_or_skip().solve(opb, timeout=60)
    assert res.satisfiable


# --- per-group dict band (parametrized tightness, group-scoped) -------------
# nf_one_third_nf_band may be a {group: [lo_pct, hi_pct]} dict: each named group's
# fellows get a hard NF-share band [lo%, hi%]; unnamed groups are unconstrained.
# (Default-off bool=False, legacy bool=True -> [27,40] all fellows, both preserved.)
#
# Mid-range NF is not precisely pinnable on this fixture (forbidding a fellow's NF
# everywhere breaks global NF coverage -> spurious UNSAT). So these use the two robust
# extremes — 100% NF (forbid NCC, pin an NF run) and 0% NF (forbid NF, pin NCC) — and
# flip the band number across the share to prove lo/hi are read from config.

def _force_all_nf(vm, opb, f):
    """100% NF share: forbid the fellow all NCC1/NCC2, pin a legal 5-day NF run."""
    for d in range(7, 12):
        opb.add_unit(vm.call[d][f]["NF"])
    for d in range(vm.num_days):
        opb.add_unit(-vm.call[d][f]["NCC1"])
        opb.add_unit(-vm.call[d][f]["NCC2"])


def _force_zero_nf(vm, opb, f):
    """0% NF share: forbid the fellow all NF, pin a 4-day NCC1 block (nonzero service)."""
    for d in range(vm.num_days):
        opb.add_unit(-vm.call[d][f]["NF"])
    for d in (14, 15, 16, 17):
        opb.add_unit(vm.call[d][f]["NCC1"])


def test_dict_band_emits_constraints():
    off = make_nf_config(num_days=28)
    on = make_nf_config(num_days=28, nf_one_third_nf_band={"NCC_SR": [32, 34]})
    opb_off, _ = build(off)
    opb_on, _ = build(on)
    assert opb_on.num_constraints > opb_off.num_constraints


def test_dict_band_binds_named_group():
    """S1 (NCC_SR, banded) forced to 100% NF -> UNSAT (upper bound bites)."""
    cfg = make_nf_config(num_days=28, nf_one_third_nf_band={"NCC_SR": [27, 40]})
    opb, vm = build(cfg)
    _force_all_nf(vm, opb, 1)                    # f=1 is S1
    assert not runner_or_skip().solve(opb, timeout=60).satisfiable


def test_dict_band_excludes_unnamed_group():
    """C1 (CCM, NOT in the dict) forced to 100% NF -> SAT: band does not apply to it."""
    cfg = make_nf_config(num_days=28, nf_one_third_nf_band={"NCC_SR": [27, 40]})
    opb, vm = build(cfg)
    _force_all_nf(vm, opb, 0)                    # f=0 is C1 (CCM)
    assert runner_or_skip().solve(opb, timeout=60).satisfiable


def test_dict_band_upper_is_config_driven():
    """100% NF: UNSAT when hi=40, SAT when hi=100 — the ceiling comes from config."""
    tight = make_nf_config(num_days=28, nf_one_third_nf_band={"NCC_SR": [27, 40]})
    opb, vm = build(tight)
    _force_all_nf(vm, opb, 1)
    assert not runner_or_skip().solve(opb, timeout=60).satisfiable
    loose = make_nf_config(num_days=28, nf_one_third_nf_band={"NCC_SR": [27, 100]})
    opb, vm = build(loose)
    _force_all_nf(vm, opb, 1)
    assert runner_or_skip().solve(opb, timeout=60).satisfiable


def test_dict_band_lower_is_config_driven():
    """0% NF: UNSAT when lo=27, SAT when lo=0 — the floor comes from config."""
    tight = make_nf_config(num_days=28, nf_one_third_nf_band={"NCC_SR": [27, 40]})
    opb, vm = build(tight)
    _force_zero_nf(vm, opb, 1)
    assert not runner_or_skip().solve(opb, timeout=60).satisfiable
    loose = make_nf_config(num_days=28, nf_one_third_nf_band={"NCC_SR": [0, 40]})
    opb, vm = build(loose)
    _force_zero_nf(vm, opb, 1)
    assert runner_or_skip().solve(opb, timeout=60).satisfiable
