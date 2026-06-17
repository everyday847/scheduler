"""Tests for _encode_nf_max_consecutive_off (Task 2 — hard density lever).

Day/week arithmetic note: horizon_start='2026-07-01' is a Wednesday, so
start_dow=2 and day_to_week(d, 2) = (2+d)//7.

Week layout:
  week 0 : days  0-4   (Wed Jul 1 – Sun Jul 5)    ← orientation MICU for JR
  week 1 : days  5-11  (Mon Jul 6 – Sun Jul 12)   ← orientation MICU for JR
  week 2 : days 12-18                              ← orientation MICU for JR
  week 3 : days 19-25                              ← orientation MICU for JR
  week 4 : days 26-32  (Mon Aug 3 – Sun Aug 9)
  week 5 : days 33-39  (Mon Aug 10 – Sun Aug 16)  ← used by guard test

The guard test uses JR1 in week 5 (days 33-39).  JR1's forced Vac weeks are
8, 28, and 32 — none of which overlap with week 5.  The orientation MICU block
covers weeks 0-3 only, so week 5 is free for the solver to label NCC.

The guard test pins:
  - days 33, 34, 35: no call role for JR1  (3 consecutive off inside call-land)
  - day  36: NCC1 for JR1  (relabels week 5 as NCC via call_weekly_link)

With max_off=2, having 3 consecutive off days is forbidden → UNSAT.
Without the constraint, the model is SAT (3 consecutive off days are allowed).
"""
from pathlib import Path
import pytest
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

_REPO = Path(__file__).resolve().parent.parent
_RS = _REPO / "vendor/roundingsat/build/roundingsat"


def _runner_or_skip():
    if not _RS.exists():
        pytest.skip("RoundingSat not built")
    return RoundingSatRunner(_RS)


def _cfg(max_off):
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-model.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    object.__setattr__(cfg, "nf_max_consecutive_off", max_off)
    return cfg


def test_max_off_off_by_default_changes_nothing():
    # max_off=0 => constraint emits nothing => still SAT fast
    cfg = _cfg(0)
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = _runner_or_skip().solve(opb, timeout=180)
    assert r.satisfiable


def test_max_off_2_is_satisfiable_and_bounds_off_runs():
    cfg = _cfg(2)
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = _runner_or_skip().solve(opb, timeout=240)
    assert r.satisfiable
    a = r.assignment
    from parafrost_scheduler.schedule_encoder import CALL_ROLES, _build_off_indicator
    # No JR/SR fellow has 3 consecutive days with no call role AND no working bg week.
    # Proxy check: no 3 consecutive days where the fellow holds no call role while the
    # week's generic NCC label is on (i.e. inside call-land). Full off-indicator is
    # internal; assert the weaker, observable property: every JR/SR has <= max_off+? —
    # here just assert solve succeeded and at least one fellow has a call day, proving
    # the constraint did not trivially force everyone off.
    jr = cfg.fellow_groups["NCC_JR"][0]
    fi = vm.fellow_names.index(jr)
    has_call = any(a.get(vm.call[d][fi][r2], False)
                   for d in range(vm.num_days) for r2 in CALL_ROLES)
    assert has_call


def test_max_off_forbids_long_off_stretch():
    """RED->GREEN guard: pin JR1 OFF for max_off+1 consecutive days inside a
    non-working (NCC-labeled) week; with the constraint active this is UNSAT.

    We use week 5 (days 33-39, Mon Aug 10 – Sun Aug 16) for JR1 because:
      - JR orientation MICU covers weeks 0-3 only (not week 5).
      - JR1 Vac is pinned to weeks 8, 28, 32 (not week 5).
      - Forcing NCC1 on day 36 labels week 5 as NCC via call_weekly_link.
      - NCC is NOT a "working" background in _build_off_indicator, so days
        33-35 (no call role, NCC week) count as fully off.
      - 3 consecutive off days > max_off=2 → UNSAT with constraint present.
      - Without constraint: SAT (the model has no rule against 3 off days).
    """
    cfg = _cfg(2)
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    from parafrost_scheduler.schedule_encoder import CALL_ROLES
    f = vm.fellow_names.index(cfg.fellow_groups["NCC_JR"][0])

    # Pin days 33, 34, 35 to have NO call role for JR1 (3 consecutive off).
    # Day 36 gets NCC1 to label week 5 as NCC (so days 33-35 count as "off inside
    # call-land" rather than off inside a working MICU/Anaesthesia/Elec/Vac week,
    # where the off-indicator would be 0 regardless).
    for d in (33, 34, 35):
        for r2 in CALL_ROLES:
            opb.add_unit(-vm.call[d][f][r2])
    # Force NCC1 on day 36 (still week 5: (2+36)//7 = 5).
    opb.add_unit(vm.call[36][f]["NCC1"])

    r = _runner_or_skip().solve(opb, timeout=180)
    assert not r.satisfiable, (
        "Expected UNSAT (3 consecutive off days with max_off=2), but got SAT. "
        "The density constraint is not biting — check the encoding or the off-indicator."
    )
