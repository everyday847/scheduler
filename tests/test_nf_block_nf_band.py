"""Per-group, per-block NF-day band (all groups), block-scoped + block-active-gated.
The tractable analogue of the whole-horizon nf_nf_day_band. Fast 35-day fixture (one
5-week block [0,5) under the offset-1 grid) so blocks exist; low memory."""
from __future__ import annotations

from _nf_helpers import make_nf_config, build, runner_or_skip

_G = {"CCM": ["C1", "C2", "C3"], "NCC_SR": ["S1", "S2"],
      "NCC_JR": ["J1", "J2", "J3"], "Stroke": ["K1", "K2"]}


def test_block_band_emits_constraints():
    off = make_nf_config(num_days=35, fellow_groups=_G)
    on = make_nf_config(num_days=35, fellow_groups=_G,
                        nf_block_nf_band={"NCC_JR": [5, 9], "Stroke": [3, 7]})
    opb_off, _ = build(off)
    opb_on, _ = build(on)
    assert opb_on.num_constraints > opb_off.num_constraints


def test_block_band_upper_bite():
    """A JR fellow pinned to a valid 6-day NF run in the first block exceeds a per-block
    hi: with the 5-week first block cap = hi(3)+2 = 5, six NF > 5 -> UNSAT; SAT without."""
    def pin(opb, vm):
        f = vm.fellow_names.index("J1")
        for d in range(0, 6):
            opb.add_unit(vm.call[d][f]["NF"])
    on = make_nf_config(num_days=35, fellow_groups=_G, nf_block_nf_band={"NCC_JR": [0, 3]})
    opb, vm = build(on); pin(opb, vm)
    assert not runner_or_skip().solve(opb, timeout=90).satisfiable
    off = make_nf_config(num_days=35, fellow_groups=_G)
    opb2, vm2 = build(off); pin(opb2, vm2)
    assert runner_or_skip().solve(opb2, timeout=90).satisfiable


def test_block_band_lower_bite_when_on_service():
    """Lower bound (gated): a JR fellow on NCC the whole first block but forbidden ALL NF
    there violates lo=2 -> UNSAT; SAT without the band. Pinning NCC1 across weeks 0..4
    forces blk_active=1, so lo applies."""
    def pin(opb, vm):
        f = vm.fellow_names.index("J1")
        si = vm.shifts.index("NCC")
        for w in range(5):                      # on NCC every week of block [0,5)
            opb.add_unit(vm.xs[f][w][si])
        for d in range(35):                     # but never NF
            opb.add_unit(-vm.call[d][f]["NF"])
    on = make_nf_config(num_days=35, fellow_groups=_G, nf_block_nf_band={"NCC_JR": [2, 12]})
    opb, vm = build(on); pin(opb, vm)
    assert not runner_or_skip().solve(opb, timeout=90).satisfiable
    off = make_nf_config(num_days=35, fellow_groups=_G)
    opb2, vm2 = build(off); pin(opb2, vm2)
    assert runner_or_skip().solve(opb2, timeout=90).satisfiable


def test_block_band_lower_exempt_when_off_service():
    """Lower bound is GATED: a JR fellow OFF service the whole first block (no NCC, hence
    0 NF) must stay SAT under lo=2 (the gate exempts an inactive block). Pin J1 to Elec
    all 5 weeks + no NF -> blk_active=0 -> lo not enforced."""
    cfg = make_nf_config(num_days=35, fellow_groups=_G, nf_block_nf_band={"NCC_JR": [2, 12]})
    opb, vm = build(cfg)
    f = vm.fellow_names.index("J1")
    si_elec = vm.shifts.index("Elec")
    for w in range(5):
        opb.add_unit(vm.xs[f][w][si_elec])      # Elec (not NCC) all block
    for d in range(35):
        opb.add_unit(-vm.call[d][f]["NF"])
    assert runner_or_skip().solve(opb, timeout=90).satisfiable
