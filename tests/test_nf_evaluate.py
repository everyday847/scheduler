"""Unit tests for the NF schedule evaluator (parafrost_scheduler.nf_evaluate).

Build synthetic solved schedules with known violations and confirm the evaluator catches
them — fast, no solver. This is the meta-guard: it proves the evaluator would have caught
the negative-coefficient Rule A bug (a week with 3 off days and no NF justification).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from parafrost_scheduler.nf_evaluate import evaluate_nf

CALL_ROLES = ("NCC1", "NCC2", "NF")


@dataclass
class FakeSol:
    weekly_assignments: dict
    call_assignments_by_day: list


@dataclass
class FakeCfg:
    fellow_groups: dict
    start_dow: int = 0
    num_days: int = 0
    call_tier_day_granular: bool = True
    nf_week_off_cap: int = 0
    nf_max_consecutive_call_days: int = 0
    nf_ncc1_continuity: str = "off"
    nf_min_ncc_block_weeks: bool = False
    nf_ccm_no_bridge_blocks: bool = False


def _blank_days(num_weeks):
    return [{"NCC1": "", "NCC2": "", "NF": ""} for _ in range(num_weeks * 7)]


def _set(days, d, role, who):
    days[d][role] = who


def test_evaluator_passes_a_clean_minimal_schedule():
    # 2 weeks, one fellow F doing nothing — coverage will complain, but with no fellows
    # to cover it's an empty roster. Use a config with no groups => coverage still checks
    # per-day need. To keep this test about the GATED rules, give a roster that covers.
    nw = 2
    days = _blank_days(nw)
    # cover NCC1/NCC2/NF every weekday, NCC1/NF every weekend, with 3 fellows A,B,C
    for d in range(nw * 7):
        weekend = (d % 7) in (5, 6)
        _set(days, d, "NCC1", "A")
        _set(days, d, "NF", "B")
        if not weekend:
            _set(days, d, "NCC2", "C")
    sol = FakeSol(
        weekly_assignments={"A": ["NCC"] * nw, "B": ["NCC"] * nw, "C": ["NCC"] * nw},
        call_assignments_by_day=days)
    cfg = FakeCfg(fellow_groups={"NCC_JR": ["A", "B", "C"]}, start_dow=0, num_days=nw * 7)
    res = evaluate_nf(sol, cfg)
    # coverage is satisfied; no gated rules on => OK
    assert res.ok, res.summary()
    assert "coverage" in res.checks_run


def test_evaluator_flags_coverage_gap():
    nw = 1
    days = _blank_days(nw)  # nobody assigned -> every day under-covered
    sol = FakeSol(weekly_assignments={"A": ["NCC"]}, call_assignments_by_day=days)
    cfg = FakeCfg(fellow_groups={"NCC_JR": ["A"]}, start_dow=0, num_days=nw * 7)
    res = evaluate_nf(sol, cfg)
    assert not res.ok
    assert any(v.rule == "coverage" for v in res.violations)


def test_evaluator_catches_week_off_cap_violation():
    """THE regression guard for the neg-coefficient Rule A bug: a fellow with 3 off days
    in an NCC week and NO NF that week must be flagged when nf_week_off_cap=2."""
    nw = 1
    days = _blank_days(nw)
    # Fellow A: NCC1 on Mon/Wed/Thu/Fri (days 0,2,3,4), off Tue/Sat/Sun (1,5,6). No NF.
    # A holds NO role on days 1,5,6 (its 3 off days). B and C cover everything else so A
    # is never accidentally given coverage on an off day.
    for d in (0, 2, 3, 4):
        _set(days, d, "NCC1", "A")
    for d in range(nw * 7):
        if not days[d]["NCC1"]:          # A's off days -> B takes NCC1
            _set(days, d, "NCC1", "B")
        _set(days, d, "NF", "C")
        if (d % 7) not in (5, 6):        # weekday NCC2 -> B (never A)
            _set(days, d, "NCC2", "B")
    sol = FakeSol(
        weekly_assignments={"A": ["NCC"], "B": ["NCC"], "C": ["NCC"]},
        call_assignments_by_day=days)
    cfg = FakeCfg(fellow_groups={"NCC_JR": ["A", "B", "C"]},
                  start_dow=0, num_days=nw * 7, nf_week_off_cap=2)
    res = evaluate_nf(sol, cfg)
    offcap = [v for v in res.violations if v.rule == "week_off_cap" and v.fellow == "A"]
    assert offcap, res.summary()


def test_evaluator_catches_max_consecutive_call():
    nw = 3
    days = _blank_days(nw)
    # Fellow A holds NCC1 every single day for 3 weeks = 21 consecutive call days.
    for d in range(nw * 7):
        _set(days, d, "NCC1", "A")
        _set(days, d, "NF", "B")
        if (d % 7) not in (5, 6):
            _set(days, d, "NCC2", "C")
    sol = FakeSol(
        weekly_assignments={"A": ["NCC"] * nw, "B": ["NCC"] * nw, "C": ["NCC"] * nw},
        call_assignments_by_day=days)
    cfg = FakeCfg(fellow_groups={"NCC_JR": ["A", "B", "C"]},
                  start_dow=0, num_days=nw * 7, nf_max_consecutive_call_days=14)
    res = evaluate_nf(sol, cfg)
    assert any(v.rule == "max_consecutive_call" and v.fellow == "A" for v in res.violations)


def test_evaluator_catches_lone_ncc_week():
    # weeks: NCC, Elec, NCC, Elec, NCC -> the interior week 2 (0-indexed) NCC is lone.
    labels = ["NCC", "Elec", "NCC", "Elec", "NCC"]
    nw = len(labels)
    days = _blank_days(nw)
    sol = FakeSol(weekly_assignments={"A": labels}, call_assignments_by_day=days)
    cfg = FakeCfg(fellow_groups={"NCC_JR": ["A"]},
                  start_dow=0, num_days=nw * 7, nf_min_ncc_block_weeks=True)
    res = evaluate_nf(sol, cfg)
    lone = [v for v in res.violations if v.rule == "min_ncc_block" and v.fellow == "A"]
    # week 2 is a lone NCC (neighbors Elec/Elec); weeks 0 and 4 are edges (exempt).
    assert any(v.week == 2 for v in lone), res.summary()
