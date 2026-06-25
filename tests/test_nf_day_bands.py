"""Absolute day-budget anchors: per-group service-day band (generalized to all groups),
per-group NF-day band, and CCM per-block NF cap. Fast 28-day fixture only (low memory)."""
from __future__ import annotations

from _nf_helpers import make_nf_config, build, runner_or_skip

_G = {"CCM": ["C1", "C2", "C3"], "NCC_SR": ["S1", "S2"],
      "NCC_JR": ["J1", "J2", "J3"], "Stroke": ["K1", "K2"]}


# --- NF-day band (per group, all groups) ------------------------------------

def test_nf_day_band_emits_constraints():
    off = make_nf_config(num_days=28, fellow_groups=_G)
    on = make_nf_config(num_days=28, fellow_groups=_G, nf_nf_day_band={"Stroke": [2, 5]})
    opb_off, _ = build(off)
    opb_on, _ = build(on)
    assert opb_on.num_constraints > opb_off.num_constraints


def test_nf_day_band_upper_bite():
    """A Stroke fellow pinned to a valid 4-day NF run (days 14..17) violates an NF band
    hi=3 -> UNSAT; SAT without. (4-day run at 14..17 is the proven-feasible pattern from
    test_nf_stroke_nf_cap.)"""
    f_pin = lambda opb, vm: [opb.add_unit(vm.call[d][vm.fellow_names.index("K1")]["NF"])
                             for d in range(14, 18)]
    on = make_nf_config(num_days=28, fellow_groups=_G, nf_nf_day_band={"Stroke": [0, 3]})
    opb, vm = build(on); f_pin(opb, vm)
    assert not runner_or_skip().solve(opb, timeout=60).satisfiable
    off = make_nf_config(num_days=28, fellow_groups=_G)
    opb2, vm2 = build(off); f_pin(opb2, vm2)
    assert runner_or_skip().solve(opb2, timeout=60).satisfiable


def test_nf_day_band_lower_bite():
    """A Stroke fellow forbidden ALL NF violates an NF band lo=2 -> UNSAT; SAT without."""
    def pin(opb, vm):
        f = vm.fellow_names.index("K1")
        for d in range(vm.num_days):
            opb.add_unit(-vm.call[d][f]["NF"])
    on = make_nf_config(num_days=28, fellow_groups=_G, nf_nf_day_band={"Stroke": [2, 12]})
    opb, vm = build(on); pin(opb, vm)
    assert not runner_or_skip().solve(opb, timeout=60).satisfiable
    off = make_nf_config(num_days=28, fellow_groups=_G)
    opb2, vm2 = build(off); pin(opb2, vm2)
    assert runner_or_skip().solve(opb2, timeout=60).satisfiable


# --- generalized service-day band (now applies to Stroke too) ---------------

def test_service_band_applies_to_stroke():
    """Service band on Stroke: pin K1 to >hi service days -> UNSAT. K1 on NCC1 days 14..20
    (7 days) violates a service band hi=5."""
    def pin(opb, vm):
        f = vm.fellow_names.index("K1")
        for d in range(14, 21):
            opb.add_unit(vm.call[d][f]["NCC1"])
    on = make_nf_config(num_days=28, fellow_groups=_G, nf_service_day_band={"Stroke": [0, 5]})
    opb, vm = build(on); pin(opb, vm)
    assert not runner_or_skip().solve(opb, timeout=60).satisfiable
    off = make_nf_config(num_days=28, fellow_groups=_G)
    opb2, vm2 = build(off); pin(opb2, vm2)
    assert runner_or_skip().solve(opb2, timeout=60).satisfiable


# --- CCM per-block NF cap ---------------------------------------------------

def test_ccm_block_nf_cap_emits_and_bites():
    """A CCM fellow pinned to a valid 6-day NF run in the first block violates a small
    per-block cap -> UNSAT; SAT without. 35-day (5-week) horizon = one block [0,5); base=3
    -> first-block cap 3+2*1=5, and a 6-day run (6 NF) > 5. (7 days would exceed the max
    run length itself, so use 6 to isolate the cap.)"""
    def pin(opb, vm):
        f = vm.fellow_names.index("C1")
        for d in range(0, 6):                 # valid 6-day NF run (max run length) in block 0
            opb.add_unit(vm.call[d][f]["NF"])
    on = make_nf_config(num_days=35, fellow_groups=_G, nf_ccm_block_nf_cap=3)
    opb, vm = build(on); pin(opb, vm)
    assert not runner_or_skip().solve(opb, timeout=90).satisfiable
    off = make_nf_config(num_days=35, fellow_groups=_G)
    opb2, vm2 = build(off); pin(opb2, vm2)
    assert runner_or_skip().solve(opb2, timeout=90).satisfiable
