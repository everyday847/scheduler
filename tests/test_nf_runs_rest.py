"""Phase-2 tests: NF runs are 4-6 consecutive days with a 2-day fully-off rest."""
from __future__ import annotations

from _nf_helpers import make_nf_config, build, runner_or_skip


def _nf_holder(assignment, vm, d):
    for f in range(vm.num_fellows):
        if assignment.get(vm.call[d][f]["NF"], False):
            return f
    return None


def _nf_runs(assignment, vm):
    """List of (fellow, start_day, length) for maximal NF runs."""
    runs, cur, start = [], None, None
    for d in range(vm.num_days):
        h = _nf_holder(assignment, vm, d)
        if h != cur:
            if cur is not None:
                runs.append((cur, start, d - start))
            cur, start = h, d
    if cur is not None:
        runs.append((cur, start, vm.num_days - start))
    return runs


def test_max_run_is_six():
    cfg = make_nf_config(num_days=28)
    opb, vm = build(cfg)
    res = runner_or_skip().solve(opb, timeout=60)
    assert res.satisfiable
    runs = _nf_runs(res.assignment, vm)
    assert all(length <= 6 for _, _, length in runs), runs


def test_min_run_is_four_except_horizon_tail():
    cfg = make_nf_config(num_days=28)
    opb, vm = build(cfg)
    res = runner_or_skip().solve(opb, timeout=60)
    assert res.satisfiable
    runs = _nf_runs(res.assignment, vm)
    for fellow, start, length in runs:
        if start + length < vm.num_days - 2:    # not the truncated tail
            assert length >= 4, (fellow, start, length, runs)


def test_pinned_three_day_run_is_unsat():
    """A length-exactly-3 NF run (NF at 7,8,9; not 6, not 10) is forbidden."""
    cfg = make_nf_config(num_days=28)
    opb, vm = build(cfg)
    f = 1
    for d in (7, 8, 9):
        opb.add_unit(vm.call[d][f]["NF"])
    opb.add_unit(-vm.call[6][f]["NF"])
    opb.add_unit(-vm.call[10][f]["NF"])
    res = runner_or_skip().solve(opb, timeout=60)
    assert not res.satisfiable


# ---------------------------------------------------------------------------
# Phase-2 Task 2: symmetric >=2 fully-off rest around every NF run
# ---------------------------------------------------------------------------

def _holds_any_call(assignment, vm, d, f):
    return any(assignment.get(vm.call[d][f][r], False) for r in ("NCC1", "NCC2", "NF"))


def test_rest_after_run_two_days_no_call():
    cfg = make_nf_config(num_days=28)
    opb, vm = build(cfg)
    res = runner_or_skip().solve(opb, timeout=120)
    assert res.satisfiable
    a = res.assignment
    for fellow, start, length in _nf_runs(a, vm):
        end = start + length - 1
        if start + length < vm.num_days - 2:    # skip truncated tail run
            for k in (1, 2):
                d = end + k
                if d < vm.num_days:
                    assert not _holds_any_call(a, vm, d, fellow), (fellow, end, d)


def test_pinned_run_then_immediate_call_is_unsat():
    """NF run ends at day 3; same fellow on NCC1 at day 4 -> UNSAT (rest).

    (Days 7-11 for fellow 1 are infeasible due to coverage constraints unrelated
    to rest; days 0-3 are the shortest provably-feasible run for fellow 1/S1,
    and day 4 is the first rest day that must be off.)
    """
    cfg = make_nf_config(num_days=28)
    opb, vm = build(cfg)
    f = 1                                   # S1 (NCC_SR)
    for d in range(0, 4):                   # NF run days 0..3 (length 4, valid)
        opb.add_unit(vm.call[d][f]["NF"])
    opb.add_unit(-vm.call[4][f]["NF"])
    opb.add_unit(vm.call[4][f]["NCC1"])    # call immediately after run end -> rest violated
    res = runner_or_skip().solve(opb, timeout=120)
    assert not res.satisfiable


def test_pinned_call_then_immediate_run_is_unsat():
    """BEFORE-run rest direction: a call role on the day JUST before an NF run
    starts (d-1) -> UNSAT. Rest is ASYMMETRIC (1 day before, 2 after); the d-1
    before-day must still be off, so this stays UNSAT."""
    cfg = make_nf_config(num_days=28)
    opb, vm = build(cfg)
    f = 1
    for d in range(10, 15):                 # NF run days 10..14 (length 5, valid)
        opb.add_unit(vm.call[d][f]["NF"])
    opb.add_unit(-vm.call[9][f]["NF"])      # run STARTS at day 10 (not NF at 9)
    opb.add_unit(vm.call[9][f]["NCC1"])     # call on day 9 = run-start's d-1 -> before-rest violated
    res = runner_or_skip().solve(opb, timeout=120)
    assert not res.satisfiable


def test_before_rest_is_only_one_day():
    """ASYMMETRY GUARD (user 2026-06-17): only ONE off day is required BEFORE an NF
    run, not two. A call role on day d-2 before a run start must be ALLOWED. Pinning
    NCC1 on day 8 (= d-2 for a run starting day 10), with day 9 (d-1) left off, must
    stay SAT. (Mutation check: reverting _encode_nf_rest's before-side to k in (1,2)
    would force day 8 off and could flip this — guarding the loosening.)"""
    cfg = make_nf_config(num_days=28)
    opb, vm = build(cfg)
    f = 1
    for d in range(10, 15):                 # NF run days 10..14 (length 5, valid)
        opb.add_unit(vm.call[d][f]["NF"])
    opb.add_unit(-vm.call[9][f]["NF"])      # run STARTS at day 10
    opb.add_unit(-vm.call[9][f]["NCC1"])    # d-1 (day 9) kept off (still required)
    opb.add_unit(-vm.call[9][f]["NCC2"])
    opb.add_unit(vm.call[8][f]["NCC1"])     # d-2 (day 8) on call -> allowed under 1-day before-rest
    res = runner_or_skip().solve(opb, timeout=120)
    assert res.satisfiable
