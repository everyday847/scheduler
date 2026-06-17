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
