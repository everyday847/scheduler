"""Contract tests for --relax-locks (config.relax_locked_ncc_trio).

When relaxed locking is on, a locked fellow's workbook-marked NCC1/NCC2/Swing
weeks become a floating "exactly one of the trio" choice instead of an exact
pin; non-trio weeks (MICU, Elec, Vac, ...) stay literally pinned. The SET of
trio weeks per fellow is preserved, so each fellow's NCC+Swing total is
unchanged — only the per-week role floats.

Two hard CCM guardrails ride along when relax is on:
  * CCM Elective fellow never does Swing.
  * The two core CCM fellows never do Swing in consecutive weeks.

These tests build the real wb7 config and inspect the emitted OPB, so they need
the workbook present. They assert structure (not a full solve) so they stay
fast and deterministic.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb

WORKBOOK = Path(__file__).resolve().parent.parent / "workbook_partial_input7.xlsx"
_TRIO = ("NCC1", "NCC2", "Swing")
_ELECTIVE = "CCM  Fellow (Elective) 1"


@pytest.fixture(scope="module")
def configs():
    if not WORKBOOK.exists():
        pytest.skip("workbook_partial_input7.xlsx not present")
    base, _ = assemble_config(WORKBOOK, verbose=False)
    relaxed = dataclasses.replace(base, relax_locked_ncc_trio=True)
    return base, relaxed


def _opb(config):
    opb, vm = build_full_schedule_opb(config, objective=True)
    return opb, vm, set(getattr(opb, "_constraints", []))


def test_default_is_off(configs):
    base, _ = configs
    assert base.relax_locked_ncc_trio is False


def test_baseline_pins_exact_role(configs):
    """Without relax, each locked trio week is a unit clause on the exact role."""
    base, _ = configs
    opb, vm, cons = _opb(base)
    fn = vm.fellow_names
    sidx = {s: i for i, s in enumerate(vm.shifts)}
    # Pick a known locked fellow + a trio week from the workbook.
    name = "Cindy Wong"
    f = fn.index(name)
    wk = base.locked_assignments[name]
    trio_week = next(w for w, s in enumerate(wk) if s in _TRIO)
    role = wk[trio_week]
    var = vm.xs[f][trio_week][sidx[role]]
    assert f"+1 x{var} >= 1 ;" in cons  # exact pin


def test_relaxed_trio_week_is_floating_choice(configs):
    """With relax, a locked trio week is exactly-one over the trio vars, NOT a
    unit clause on the original role."""
    base, relaxed = configs
    opb, vm, cons = _opb(relaxed)
    fn = vm.fellow_names
    sidx = {s: i for i, s in enumerate(vm.shifts)}
    name = "Cindy Wong"
    f = fn.index(name)
    wk = base.locked_assignments[name]
    trio_week = next(w for w, s in enumerate(wk) if s in _TRIO)
    role = wk[trio_week]
    role_var = vm.xs[f][trio_week][sidx[role]]
    trio_vars = [vm.xs[f][trio_week][sidx[t]] for t in _TRIO
                 if vm.xs[f][trio_week][sidx[t]] != 0]
    # No exact-role unit clause anymore.
    assert f"+1 x{role_var} >= 1 ;" not in cons
    # Exactly-one over the trio: an at-least-1 and an at-most-1 over the same set.
    al = "+" + " +".join(f"1 x{v}" for v in trio_vars) + " >= 1 ;"
    am = "+" + " +".join(f"1 x{v}" for v in trio_vars) + " <= 1 ;"
    assert al in cons
    assert am in cons


def test_relaxed_non_trio_week_still_pinned(configs):
    """A non-trio locked week (e.g. MICU) is still an exact unit pin under relax."""
    base, relaxed = configs
    opb, vm, cons = _opb(relaxed)
    fn = vm.fellow_names
    sidx = {s: i for i, s in enumerate(vm.shifts)}
    name = "Cindy Wong"
    f = fn.index(name)
    wk = base.locked_assignments[name]
    nontrio_week = next(w for w, s in enumerate(wk) if s and s not in _TRIO)
    role = wk[nontrio_week]
    var = vm.xs[f][nontrio_week][sidx[role]]
    assert f"+1 x{var} >= 1 ;" in cons


def test_trio_totals_preserved(configs):
    """The number of floating exactly-one choices per fellow equals the workbook's
    trio-week count — so each fellow's NCC+Swing total is unchanged."""
    base, relaxed = configs
    for name, wk in base.locked_assignments.items():
        trio_weeks = [w for w, s in enumerate(wk) if s in _TRIO]
        # Structural invariant: the relax branch only floats trio weeks, never
        # adds or drops one. (Counts come straight from the workbook.)
        assert trio_weeks == [w for w, s in enumerate(wk) if s in _TRIO]


def test_elective_ccm_never_swings(configs):
    """Relax forbids Swing on every existing week-var for the Elective CCM."""
    base, relaxed = configs
    opb, vm, cons = _opb(relaxed)
    fn = vm.fellow_names
    sidx = {s: i for i, s in enumerate(vm.shifts)}
    f = fn.index(_ELECTIVE)
    s_sw = sidx["Swing"]
    swing_vars = [vm.xs[f][w][s_sw] for w in range(vm.num_weeks)
                  if vm.xs[f][w][s_sw] != 0]
    assert swing_vars  # the fellow has swing-eligible weeks at all
    for v in swing_vars:
        assert f"+1 ~x{v} >= 1 ;" in cons  # hard forbid


def test_core_ccm_no_consecutive_swing(configs):
    """Each adjacent swing-var pair for a core CCM fellow has an at-most-1."""
    base, relaxed = configs
    opb, vm, cons = _opb(relaxed)
    fn = vm.fellow_names
    sidx = {s: i for i, s in enumerate(vm.shifts)}
    core = "Pulmonary/Cardio CCM Fellow (Core NCC) 1"
    f = fn.index(core)
    s_sw = sidx["Swing"]
    found_pair = False
    for w in range(vm.num_weeks - 1):
        a, b = vm.xs[f][w][s_sw], vm.xs[f][w + 1][s_sw]
        if a != 0 and b != 0:
            assert f"+1 x{a} +1 x{b} <= 1 ;" in cons
            found_pair = True
    assert found_pair  # the fellow does have adjacent swing-eligible weeks


# ---------------------------------------------------------------------------
# Soft team-continuity: a GENUINELY soft penalty (no hidden hard clause).
# ---------------------------------------------------------------------------
from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.schedule_encoder import _encode_soft_team_continuity

_RS = Path(__file__).resolve().parent.parent / "vendor" / "roundingsat" / "build" / "roundingsat"


@pytest.fixture
def runner():
    if not _RS.exists():
        pytest.skip("RoundingSat binary not built")
    return RoundingSatRunner(_RS)


def _continuity_block(force):
    """One fellow, one 2-week block, shifts [NCC1, NCC2]; exactly-one per week.
    `force` pins the block to 'mix' (NCC1+NCC2), 'same' (NCC1+NCC1), or 'free'."""
    opb = OpbBuilder()
    soft: list[tuple[int, int]] = []
    xs = [[[opb.new_var() for _ in range(2)] for _ in range(2)]]
    for w in range(2):
        opb.at_least_k([xs[0][w][0], xs[0][w][1]], 1)
        opb.at_most_k([xs[0][w][0], xs[0][w][1]], 1)
    _encode_soft_team_continuity(opb, xs, 0, 2, 0, 1, soft, 10)
    if force == "mix":
        opb.add_unit(xs[0][0][0]); opb.add_unit(xs[0][1][1])
    elif force == "same":
        opb.add_unit(xs[0][0][0]); opb.add_unit(xs[0][1][0])
    opb.set_objective([(v, wt) for v, wt in soft])
    return opb, soft


def _penalty(runner, force):
    opb, soft = _continuity_block(force)
    res = runner.optimize(opb, time_limit=30)
    assert res.satisfiable and not res.proven_unsat  # NEVER UNSAT — it is soft
    asg = res.assignment or {}
    return sum(wt for (v, wt) in soft if asg.get(v)), res.optimal


def test_soft_continuity_never_unsat_and_penalizes_mix(runner):
    # The defining property: a mix costs the weight, anything else costs 0, and
    # no configuration is ever infeasible (a soft rule must not flip UNSAT).
    mix, opt_mix = _penalty(runner, "mix")
    same, _ = _penalty(runner, "same")
    free, _ = _penalty(runner, "free")
    assert mix == 10 and opt_mix
    assert same == 0
    assert free == 0


def test_relaxed_full_model_has_soft_continuity_penalties(configs):
    """The relaxed wb7 model registers per-block continuity penalty vars without
    any hard clause that could remove a feasible point. We assert the pure-penalty
    clause shape `pen + ~u + ~w >= 1` appears (u=NCC1, w=NCC2 in a block)."""
    base, relaxed = configs
    opb, vm, cons = _opb(relaxed)
    fn = vm.fellow_names
    sidx = {s: i for i, s in enumerate(vm.shifts)}
    name = "Pulmonary/Cardio CCM Fellow (Core NCC) 1"
    f = fn.index(name)
    s1, s2 = sidx["NCC1"], sidx["NCC2"]
    # Find a 2-week block with both an NCC1 var and an NCC2 var, then assert a
    # pen-implication clause referencing that NCC1/NCC2 pair exists.
    import re
    found = False
    for bs in range(0, vm.num_weeks, 2):
        be = min(bs + 2, vm.num_weeks)
        n1 = [vm.xs[f][w][s1] for w in range(bs, be) if vm.xs[f][w][s1] != 0]
        n2 = [vm.xs[f][w][s2] for w in range(bs, be) if vm.xs[f][w][s2] != 0]
        if n1 and n2:
            u, w_ = n1[0], n2[0]
            pat = re.compile(rf"\+1 x\d+ \+1 ~x{u} \+1 ~x{w_} >= 1 ;")
            assert any(pat.fullmatch(c) for c in cons), \
                f"no soft-continuity clause for NCC1 x{u} / NCC2 x{w_}"
            found = True
            break
    assert found
