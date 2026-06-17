# tests/test_nf_week_off_cap.py
from pathlib import Path
import pytest
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb, decode_solution, _day_to_week, CALL_ROLES)
from parafrost_scheduler.schedule_types import day_of_week

_REPO = Path(__file__).resolve().parent.parent
_RS = _REPO / "vendor/roundingsat/build/roundingsat"


def _runner_or_skip():
    from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
    if not _RS.exists():
        pytest.skip("RoundingSat not built")
    return RoundingSatRunner(_RS)


def _cfg(cap):
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-model.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    object.__setattr__(cfg, "nf_week_off_cap", cap)
    return cfg


def _off_days_per_week(sol, vm, name):
    """Per week, count days the fellow holds NO call role AND is on no working bg
    rotation (i.e. the week's weekly label is NCC or blank). Returns {week: off_count}."""
    # A fellow is 'off' on day d iff no call role that day AND the week is not a working
    # background week. We approximate working-bg by: weekly label not in {'', 'NCC'}.
    labels = sol.weekly_assignments[name]
    per = {}
    days_in_week = {}
    for d in range(vm.num_days):
        days_in_week.setdefault(_day_to_week(d, vm.start_dow), []).append(d)
    for w, days in days_in_week.items():
        if labels[w] not in ("", "NCC"):
            continue  # working bg week: its days are 'working', not off
        off = 0
        for d in days:
            if not any(sol.call_assignments_by_day[d][r] == name for r in CALL_ROLES):
                off += 1
        per[w] = off
    return per


def test_week_off_cap_2_holds_except_forced_nf_rest_weeks(tmp_path):
    cfg = _cfg(2)
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = _runner_or_skip().solve(opb, timeout=1200)
    assert r.satisfiable
    sol = decode_solution(r.assignment, vm)
    # For every fellow and every NCC/blank week, off-days <= 3.
    # If off == 3, the week must have NF-rest justification: either
    #   (a) >= 3 NF days in-week (the canonical full in-week run), OR
    #   (b) NF days in adjacent-day positions that produce mandatory rest days
    #       spilling into this week from bordering NF runs (cross-week rest).
    # The encoder allows extra=1 when there are >= 3 mandatory-NF-rest indicators
    # in the week, which can come from cross-week NF runs as well as in-week runs.
    # The combined check: off==3 implies there is NF activity within 2 days of
    # the week's first day, or within 1 day of the week's last day, or in-week.
    for name in vm.fellow_names:
        per = _off_days_per_week(sol, vm, name)
        days_in_week = {}
        for d in range(vm.num_days):
            days_in_week.setdefault(_day_to_week(d, vm.start_dow), []).append(d)
        for w, off in per.items():
            assert off <= 3, (name, w, off)
            if off == 3:
                # Check there is NF activity that could justify 3 rest indicators:
                # in-week NF OR NF days adjacent to this week (within 2 days of
                # week boundaries — produces cross-week mandatory rest days).
                week_days = days_in_week[w]
                first_d, last_d = week_days[0], week_days[-1]
                # NF days in-week
                nf_in_week = sum(1 for d in week_days
                                 if sol.call_assignments_by_day[d]["NF"] == name)
                # NF days adjacent that produce rest days landing in this week:
                # - NF ending on last 2 days of prev week → up to 2 rest days at start
                # - NF starting on first day of next week → 1 rest day at end
                nf_adj = 0
                for dd in range(max(0, first_d - 2), first_d):
                    if sol.call_assignments_by_day[dd]["NF"] == name:
                        nf_adj += 1
                if last_d + 1 < vm.num_days:
                    if sol.call_assignments_by_day[last_d + 1]["NF"] == name:
                        nf_adj += 1
                assert nf_in_week >= 3 or nf_adj >= 1, (
                    name, w, f"3 off but no NF justification (in_week={nf_in_week}, adj={nf_adj})")
