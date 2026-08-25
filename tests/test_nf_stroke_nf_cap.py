"""Each Stroke fellow holds <= config.nf_stroke_nf_cap NF days total (hard).

A direct per-fellow at_most over NF day-vars. Fixture includes a Stroke group;
a small cap + a pinned NF run that exceeds it -> UNSAT.
"""
from __future__ import annotations

from _nf_helpers import make_nf_config, build, runner_or_skip

_GROUPS = {
    "CCM":    ["C1"],
    "NCC_SR": ["S1", "S2"],
    "NCC_JR": ["J1", "J2"],
    "Stroke": ["K1", "K2"],
}


def test_flag_off_emits_no_extra_constraints_flag_on_does():
    off = make_nf_config(num_days=28, fellow_groups=_GROUPS, nf_stroke_nf_cap=0)
    on = make_nf_config(num_days=28, fellow_groups=_GROUPS, nf_stroke_nf_cap=3)
    opb_off, _ = build(off)
    opb_on, _ = build(on)
    assert opb_on.num_constraints > opb_off.num_constraints


def test_stroke_over_cap_is_unsat():
    """RED->GREEN: a Stroke fellow pinned to a 4-day NF run (days 14..17) under a
    cap of 3 -> UNSAT. (4 is a valid run length, so the only thing forbidding it is
    the cap.)"""
    cfg = make_nf_config(num_days=28, fellow_groups=_GROUPS, nf_stroke_nf_cap=3)
    opb, vm = build(cfg)
    f = vm.fellow_names.index("K1")
    for d in range(14, 18):
        opb.add_unit(vm.call[d][f]["NF"])
    res = runner_or_skip().solve(opb, timeout=120)
    assert not res.satisfiable


def test_stroke_over_cap_is_sat_without_rule():
    """Counterpart: SAME 4-day NF run SAT when the cap is off."""
    cfg = make_nf_config(num_days=28, fellow_groups=_GROUPS, nf_stroke_nf_cap=0)
    opb, vm = build(cfg)
    f = vm.fellow_names.index("K1")
    for d in range(14, 18):
        opb.add_unit(vm.call[d][f]["NF"])
    res = runner_or_skip().solve(opb, timeout=120)
    assert res.satisfiable


def test_non_stroke_fellow_uncapped():
    """The cap applies ONLY to Stroke: a NON-Stroke fellow with a 4-day NF run is SAT
    even under a stroke cap of 3 (proving the group filter works)."""
    cfg = make_nf_config(num_days=28, fellow_groups=_GROUPS, nf_stroke_nf_cap=3)
    opb, vm = build(cfg)
    f = vm.fellow_names.index("S1")
    for d in range(14, 18):
        opb.add_unit(vm.call[d][f]["NF"])
    res = runner_or_skip().solve(opb, timeout=120)
    assert res.satisfiable
