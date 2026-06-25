"""No-3xCCM: every day >=1 active call role is held by a NON-CCM fellow.

config.nf_no_triple_ccm. A Stroke/NCC fellow is always on NCC service, so call
must never be entirely CCM-held. Active roles: weekday NCC1/NCC2/NF; weekend NCC1/NF.

The fixture needs >=3 CCM so a weekday's three roles CAN all be CCM-held (that is
exactly the situation the rule must forbid). Uses a custom fellow_groups.
"""
from __future__ import annotations

from _nf_helpers import make_nf_config, build, runner_or_skip

# 3 CCM (can cover all weekday roles) + 2 NCC_SR + 2 NCC_JR (the non-CCM that the
# rule requires on call). SR/CCM are week-0-night-unrestricted so the fixture solves.
_GROUPS = {
    "CCM":    ["C1", "C2", "C3"],
    "NCC_SR": ["S1", "S2"],
    "NCC_JR": ["J1", "J2"],
}
_WEEKDAY = 16   # Wednesday, week 2


def _non_ccm_indices(vm):
    ccm = set(_GROUPS["CCM"])
    return [vm.fellow_names.index(n) for n in vm.fellow_names if n not in ccm]


def test_flag_off_emits_no_extra_constraints_flag_on_does():
    off = make_nf_config(num_days=28, fellow_groups=_GROUPS, nf_no_triple_ccm=False)
    on = make_nf_config(num_days=28, fellow_groups=_GROUPS, nf_no_triple_ccm=True)
    opb_off, _ = build(off)
    opb_on, _ = build(on)
    assert opb_on.num_constraints > opb_off.num_constraints


def test_all_ccm_day_is_unsat():
    """RED->GREEN: forbid every non-CCM fellow any call role on a weekday. Coverage
    then forces all three roles onto CCM fellows; nf_no_triple_ccm makes that UNSAT."""
    cfg = make_nf_config(num_days=28, fellow_groups=_GROUPS, nf_no_triple_ccm=True)
    opb, vm = build(cfg)
    for f in _non_ccm_indices(vm):
        for r in ("NCC1", "NCC2", "NF"):
            opb.add_unit(-vm.call[_WEEKDAY][f][r])
    res = runner_or_skip().solve(opb, timeout=120)
    assert not res.satisfiable


def test_all_ccm_day_is_sat_without_rule():
    """Counterpart: SAME pins SAT when the rule is off (CCM-only coverage is allowed)."""
    cfg = make_nf_config(num_days=28, fellow_groups=_GROUPS, nf_no_triple_ccm=False)
    opb, vm = build(cfg)
    for f in _non_ccm_indices(vm):
        for r in ("NCC1", "NCC2", "NF"):
            opb.add_unit(-vm.call[_WEEKDAY][f][r])
    res = runner_or_skip().solve(opb, timeout=120)
    assert res.satisfiable
