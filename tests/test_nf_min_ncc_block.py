"""Task 11: NCC call blocks >= 2 consecutive weeks (no lone NCC week)."""
from pathlib import Path
import pytest
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb, decode_solution

_REPO = Path(__file__).resolve().parent.parent
_RS = _REPO / "vendor/roundingsat/build/roundingsat"


def _cfg(flag):
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-foundation-fixture.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    object.__setattr__(cfg, "nf_min_ncc_block_weeks", flag)
    return cfg


def _runner_or_skip():
    from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
    if not _RS.exists():
        pytest.skip("RoundingSat not built")
    return RoundingSatRunner(_RS)


def test_pinned_lone_ncc_week_is_unsat():
    """RED->GREEN guard (fast — small UNSAT, no full optimize). Pin fellow JR1 to a lone
    NCC week: NCC at week 20, NOT-NCC at weeks 19 and 21. With the constraint active this
    is UNSAT; remove the call site and it would be SAT. JR1 week 20 is interior, past the
    weeks-0-3 MICU orientation, and not a pinned-Vac week (JR1 Vac = 8,28,32)."""
    cfg = _cfg(True)
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    si = vm.shifts.index("NCC")
    f = vm.fellow_names.index("JR1")
    opb.add_unit(vm.xs[f][20][si])       # week 20 = NCC
    opb.add_unit(-vm.xs[f][19][si])      # week 19 != NCC
    opb.add_unit(-vm.xs[f][21][si])      # week 21 != NCC  -> lone NCC week, must be UNSAT
    r = _runner_or_skip().solve(opb, timeout=600)
    assert not r.satisfiable


def test_two_week_ncc_block_is_allowed():
    """Counterpart: a 2-week NCC block (weeks 20-21 NCC, 19 and 22 not) stays SAT, proving
    the constraint forbids only LONE weeks, not all NCC blocks."""
    cfg = _cfg(True)
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    si = vm.shifts.index("NCC")
    f = vm.fellow_names.index("JR1")
    opb.add_unit(vm.xs[f][20][si])
    opb.add_unit(vm.xs[f][21][si])
    opb.add_unit(-vm.xs[f][19][si])
    r = _runner_or_skip().solve(opb, timeout=600)
    assert r.satisfiable


def test_no_lone_ncc_week_in_full_solution():
    """Structural (SLURM — full solve). With the constraint on, no fellow has a lone NCC
    week (an NCC week whose both in-horizon neighbors are non-NCC)."""
    cfg = _cfg(True)
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = _runner_or_skip().solve(opb, timeout=2400)
    assert r.satisfiable
    sol = decode_solution(r.assignment, vm)
    nw = vm.num_weeks
    for name in vm.fellow_names:
        labels = sol.weekly_assignments[name]
        for w in range(1, nw - 1):
            if labels[w] == "NCC":
                assert labels[w - 1] == "NCC" or labels[w + 1] == "NCC", (name, w)
