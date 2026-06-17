# tests/test_nf_ncc1_continuity.py
from pathlib import Path
import pytest
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb, decode_solution, _day_to_week, _day_of_week)
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

_REPO = Path(__file__).resolve().parent.parent
_RS = _REPO / "vendor/roundingsat/build/roundingsat"


def _runner_or_skip():
    if not _RS.exists():
        pytest.skip("RoundingSat not built")
    return RoundingSatRunner(_RS)


def _cfg(mode):
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-model.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    object.__setattr__(cfg, "nf_ncc1_continuity", mode)
    return cfg


def test_fullweek_makes_ncc1_week_constant():
    cfg = _cfg("fullweek")
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = _runner_or_skip().solve(opb, timeout=240)
    assert r.satisfiable
    sol = decode_solution(r.assignment, vm)
    # For each week, the set of NCC1 holders across its days is <= 1 fellow.
    days_in_week = {}
    for d in range(vm.num_days):
        days_in_week.setdefault(_day_to_week(d, vm.start_dow), []).append(d)
    for w, days in days_in_week.items():
        holders = {sol.call_assignments_by_day[d]["NCC1"]
                   for d in days if sol.call_assignments_by_day[d]["NCC1"]}
        assert len(holders) <= 1, (w, holders)


def test_weekday_allows_weekend_ncc1_to_differ():
    cfg = _cfg("weekday")
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = _runner_or_skip().solve(opb, timeout=240)
    assert r.satisfiable
    sol = decode_solution(r.assignment, vm)
    # Weekday (Mon-Fri) NCC1 holders within a week are <= 1 fellow; weekend may differ.
    days_in_week = {}
    for d in range(vm.num_days):
        days_in_week.setdefault(_day_to_week(d, vm.start_dow), []).append(d)
    for w, days in days_in_week.items():
        wkday = {sol.call_assignments_by_day[d]["NCC1"]
                 for d in days
                 if _day_of_week(d, vm.start_dow) < 5 and sol.call_assignments_by_day[d]["NCC1"]}
        assert len(wkday) <= 1, (w, wkday)
