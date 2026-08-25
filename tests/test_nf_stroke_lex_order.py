"""Stroke symmetry-break: first NCC-week strictly increasing in roster order.

config.nf_stroke_lex_order. The Stroke fellows are interchangeable; pinning the
order of their first NCC week (A < B < C < D) kills the permutation symmetry.

Fixture: 2 Stroke fellows [K1, K2] over 8 weeks. A violation pins K2's first NCC
week strictly before K1's -> UNSAT with the rule, SAT without.
"""
from __future__ import annotations

from _nf_helpers import make_nf_config, build, runner_or_skip

_GROUPS = {
    "CCM":    ["C1"],
    "NCC_SR": ["S1", "S2"],
    "NCC_JR": ["J1", "J2"],
    "Stroke": ["K1", "K2"],
}


def _ncc(vm):
    return vm.shifts.index("NCC")


def test_flag_off_emits_no_extra_constraints_flag_on_does():
    off = make_nf_config(num_days=56, fellow_groups=_GROUPS, nf_stroke_lex_order=False)
    on = make_nf_config(num_days=56, fellow_groups=_GROUPS, nf_stroke_lex_order=True)
    opb_off, _ = build(off)
    opb_on, _ = build(on)
    assert opb_on.num_constraints > opb_off.num_constraints


def test_reversed_first_ncc_order_is_unsat():
    """RED->GREEN: K2 (later in roster) starts NCC at week 1 while K1 has no NCC in
    weeks 0..2. That makes K2's first NCC week < K1's -> violates A<B -> UNSAT."""
    cfg = make_nf_config(num_days=56, fellow_groups=_GROUPS, nf_stroke_lex_order=True)
    opb, vm = build(cfg)
    ncc = _ncc(vm)
    k1, k2 = vm.fellow_names.index("K1"), vm.fellow_names.index("K2")
    opb.add_unit(vm.xs[k2][1][ncc])
    opb.add_unit(-vm.xs[k2][0][ncc])
    for w in (0, 1, 2):
        opb.add_unit(-vm.xs[k1][w][ncc])
    res = runner_or_skip().solve(opb, timeout=120)
    assert not res.satisfiable


def test_reversed_first_ncc_order_is_sat_without_rule():
    """Counterpart: SAME pins SAT when the rule is off."""
    cfg = make_nf_config(num_days=56, fellow_groups=_GROUPS, nf_stroke_lex_order=False)
    opb, vm = build(cfg)
    ncc = _ncc(vm)
    k1, k2 = vm.fellow_names.index("K1"), vm.fellow_names.index("K2")
    opb.add_unit(vm.xs[k2][1][ncc])
    opb.add_unit(-vm.xs[k2][0][ncc])
    for w in (0, 1, 2):
        opb.add_unit(-vm.xs[k1][w][ncc])
    res = runner_or_skip().solve(opb, timeout=120)
    assert res.satisfiable


def test_correct_first_ncc_order_is_sat():
    """K1 starts NCC at week 0, K2 not until week 1: order A<B respected -> SAT."""
    cfg = make_nf_config(num_days=56, fellow_groups=_GROUPS, nf_stroke_lex_order=True)
    opb, vm = build(cfg)
    ncc = _ncc(vm)
    k1, k2 = vm.fellow_names.index("K1"), vm.fellow_names.index("K2")
    opb.add_unit(vm.xs[k1][0][ncc])
    opb.add_unit(-vm.xs[k2][0][ncc])
    opb.add_unit(vm.xs[k2][1][ncc])
    res = runner_or_skip().solve(opb, timeout=120)
    assert res.satisfiable
