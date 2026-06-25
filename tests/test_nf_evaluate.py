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
    nf_min_ncc_run_days: int = 0
    nf_weekend_ncc1_paired: bool = False
    nf_no_triple_ccm: bool = False
    nf_stroke_lex_order: bool = False
    nf_stroke_nf_cap: int = 0
    nf_weekend_ncc2: bool = False
    nf_min_ncc2_run_days: int = 0
    nf_one_third_nf_band: bool = False
    nf_one_third_nf_weight: int = 0
    nf_nf_day_band: dict | None = None
    nf_ccm_block_nf_cap: int = 0
    nf_service_day_band: object = None
    nf_block_nf_band: dict | None = None


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


# ---------------------------------------------------------------------------
# New NF rules (2026-06-18): min NCC run, weekend NCC1 pairing, no-3xCCM,
# Stroke NF cap, Stroke lex-order.
# ---------------------------------------------------------------------------

def _cover_clean(days, nw, *, exclude=()):
    """Cover NCC1/NCC2/NF every day with A/B/C unless a day's role is pre-set."""
    for d in range(nw * 7):
        weekend = (d % 7) in (5, 6)
        if not days[d]["NCC1"] and "NCC1" not in exclude:
            _set(days, d, "NCC1", "A")
        if not days[d]["NF"] and "NF" not in exclude:
            _set(days, d, "NF", "B")
        if not weekend and not days[d]["NCC2"] and "NCC2" not in exclude:
            _set(days, d, "NCC2", "C")


def test_evaluator_catches_lone_ncc_service_run():
    """A lone 1-day NCC-service stint (NCC2 a single weekday, isolated) is flagged when
    nf_min_ncc_run_days=4."""
    nw = 2
    days = _blank_days(nw)
    # Fellow D holds NCC2 on day 2 (Wed wk0) ONLY — a length-1 service run.
    _set(days, 2, "NCC2", "D")
    _cover_clean(days, nw)
    # day 2 NCC2 was taken by D; make sure C doesn't also hold it (coverage = exactly 1).
    sol = FakeSol(
        weekly_assignments={"A": ["NCC"] * nw, "B": ["NCC"] * nw,
                            "C": ["NCC"] * nw, "D": ["NCC"] * nw},
        call_assignments_by_day=days)
    cfg = FakeCfg(fellow_groups={"NCC_JR": ["A", "B", "C", "D"]},
                  start_dow=0, num_days=nw * 7, nf_min_ncc_run_days=4)
    res = evaluate_nf(sol, cfg)
    lone = [v for v in res.violations if v.rule == "min_ncc_run" and v.fellow == "D"]
    assert lone, res.summary()


def test_evaluator_catches_split_weekend_ncc1():
    """Sat NCC1 holder != Sun NCC1 holder is flagged when nf_weekend_ncc1_paired."""
    nw = 1
    days = _blank_days(nw)
    # Sat=day5, Sun=day6. Different NCC1 holders.
    _set(days, 5, "NCC1", "A")
    _set(days, 6, "NCC1", "B")
    _cover_clean(days, nw)
    sol = FakeSol(
        weekly_assignments={"A": ["NCC"], "B": ["NCC"], "C": ["NCC"]},
        call_assignments_by_day=days)
    cfg = FakeCfg(fellow_groups={"NCC_JR": ["A", "B", "C"]},
                  start_dow=0, num_days=nw * 7, nf_weekend_ncc1_paired=True)
    res = evaluate_nf(sol, cfg)
    split = [v for v in res.violations if v.rule == "weekend_ncc1_paired" and v.day == 5]
    assert split, res.summary()


def test_evaluator_catches_all_ccm_day():
    """A day where every active call role is held by CCM fellows is flagged."""
    nw = 1
    days = _blank_days(nw)
    # Day 0 (Mon): all three roles to CCM fellows X, Y, Z.
    _set(days, 0, "NCC1", "X")
    _set(days, 0, "NCC2", "Y")
    _set(days, 0, "NF", "Z")
    _cover_clean(days, nw)
    sol = FakeSol(
        weekly_assignments={"X": ["NCC"], "Y": ["NCC"], "Z": ["NCC"],
                            "A": ["NCC"], "B": ["NCC"], "C": ["NCC"]},
        call_assignments_by_day=days)
    cfg = FakeCfg(fellow_groups={"CCM": ["X", "Y", "Z"], "NCC_JR": ["A", "B", "C"]},
                  start_dow=0, num_days=nw * 7, nf_no_triple_ccm=True)
    res = evaluate_nf(sol, cfg)
    triple = [v for v in res.violations if v.rule == "no_triple_ccm" and v.day == 0]
    assert triple, res.summary()


def test_evaluator_catches_stroke_over_nf_cap():
    """A Stroke fellow with > cap NF days is flagged."""
    nw = 2
    days = _blank_days(nw)
    # Stroke fellow K does NF on days 7..12 (6 days) — over a cap of 4.
    for d in range(7, 13):
        _set(days, d, "NF", "K")
    _cover_clean(days, nw)
    sol = FakeSol(
        weekly_assignments={"K": ["NCC"] * nw, "A": ["NCC"] * nw,
                            "B": ["NCC"] * nw, "C": ["NCC"] * nw},
        call_assignments_by_day=days)
    cfg = FakeCfg(fellow_groups={"Stroke": ["K"], "NCC_JR": ["A", "B", "C"]},
                  start_dow=0, num_days=nw * 7, nf_stroke_nf_cap=4)
    res = evaluate_nf(sol, cfg)
    over = [v for v in res.violations if v.rule == "stroke_nf_cap" and v.fellow == "K"]
    assert over, res.summary()


def test_evaluator_catches_stroke_lex_order_violation():
    """Stroke fellows whose first-NCC week is not strictly increasing are flagged."""
    # K1 first NCC at week 2, K2 first NCC at week 0 -> not increasing.
    labels_k1 = ["Elec", "Elec", "NCC", "NCC"]
    labels_k2 = ["NCC", "NCC", "Elec", "Elec"]
    nw = 4
    days = _blank_days(nw)
    sol = FakeSol(
        weekly_assignments={"K1": labels_k1, "K2": labels_k2},
        call_assignments_by_day=days)
    cfg = FakeCfg(fellow_groups={"Stroke": ["K1", "K2"]},
                  start_dow=0, num_days=nw * 7, nf_stroke_lex_order=True)
    res = evaluate_nf(sol, cfg)
    assert any(v.rule == "stroke_lex_order" for v in res.violations), res.summary()


def test_evaluator_catches_lone_ncc2_run():
    """A single isolated NCC2 day (NCC2 a weekday, flanked by non-NCC2) is flagged when
    nf_min_ncc2_run_days=2."""
    nw = 2
    days = _blank_days(nw)
    # Fellow D holds NCC2 on day 2 (Wed wk0) ONLY -> a length-1 NCC2 run.
    _set(days, 2, "NCC2", "D")
    _cover_clean(days, nw)
    sol = FakeSol(
        weekly_assignments={"A": ["NCC"] * nw, "B": ["NCC"] * nw,
                            "C": ["NCC"] * nw, "D": ["NCC"] * nw},
        call_assignments_by_day=days)
    cfg = FakeCfg(fellow_groups={"NCC_JR": ["A", "B", "C", "D"]},
                  start_dow=0, num_days=nw * 7, nf_min_ncc2_run_days=2)
    res = evaluate_nf(sol, cfg)
    lone = [v for v in res.violations if v.rule == "min_ncc2_run" and v.fellow == "D"]
    assert lone, res.summary()


def test_evaluator_catches_nf_share_out_of_band():
    """A fellow at 50% NF (over the 40% ceiling) is flagged when nf_one_third_nf_band on;
    a fellow at ~33% is clean."""
    nw = 3
    days = _blank_days(nw)
    # Fellow H: NF on 10 of 20 service days (50%); the other 10 NCC1.
    # Fellow OK: ~1/3 NF. Lay out distinct days per fellow (coverage not required here —
    # the check only reads each fellow's own role days).
    for d in range(10):
        _set(days, d, "NF", "H")
    for d in range(10, 20):
        _set(days, d, "NCC1", "H")
    # OK fellow uses NCC2 column so it doesn't collide with H's NCC1 cells.
    for d in range(7):
        _set(days, d, "NCC2", "OK")          # 7 NCC2 service days...
    for d in range(7, 10):
        _set(days, d, "NF", "OK")            # ...+ 3 NF = 30% NF (in band)
    sol = FakeSol(weekly_assignments={"H": ["NCC"] * nw, "OK": ["NCC"] * nw},
                  call_assignments_by_day=days)
    cfg = FakeCfg(fellow_groups={"NCC_JR": ["H", "OK"]},
                  start_dow=0, num_days=nw * 7, nf_one_third_nf_band=True)
    res = evaluate_nf(sol, cfg)
    bad = [v for v in res.violations if v.rule == "one_third_nf"]
    assert any(v.fellow == "H" for v in bad), res.summary()
    assert not any(v.fellow == "OK" for v in bad), res.summary()


def test_evaluator_catches_nf_day_band_violation():
    """A fellow with 20 NF days is flagged when their group's nf_nf_day_band is [9,12]."""
    nw = 3
    days = _blank_days(nw)
    for d in range(20):
        _set(days, d, "NF", "K1")
    sol = FakeSol(weekly_assignments={"K1": ["NCC"] * nw}, call_assignments_by_day=days)
    cfg = FakeCfg(fellow_groups={"Stroke": ["K1"]}, start_dow=0, num_days=nw * 7,
                  nf_nf_day_band={"Stroke": [9, 12]})
    res = evaluate_nf(sol, cfg)
    assert any(v.rule == "nf_day_band" and v.fellow == "K1" for v in res.violations), res.summary()


def test_evaluator_catches_block_nf_band_violations():
    """Per-block NF band: a JR fellow with 9 NF in the first (5-week) block exceeds hi
    (band [3,7] -> first-block hi=9? no: hi+2=9) ... use band [3,6] -> first-block hi=8,
    so 9 NF > 8 flagged. And a JR on-service block with 1 NF < lo=3 flagged."""
    nw = 5
    days = _blank_days(nw)
    for d in range(9):                         # 9 NF in block [0,5) for over-cap fellow
        _set(days, d, "NF", "OVER")
    # UNDER fellow: on service (NCC1) but only 1 NF in the block — distinct days from OVER.
    _set(days, 10, "NCC1", "UNDER")
    _set(days, 11, "NCC1", "UNDER")
    _set(days, 12, "NF", "UNDER")
    sol = FakeSol(weekly_assignments={"OVER": ["NCC"] * nw, "UNDER": ["NCC"] * nw},
                  call_assignments_by_day=days)
    cfg = FakeCfg(fellow_groups={"NCC_JR": ["OVER", "UNDER"]}, start_dow=0, num_days=nw * 7,
                  nf_block_nf_band={"NCC_JR": [3, 6]})   # first-block hi = 6+2 = 8
    res = evaluate_nf(sol, cfg)
    over = [v for v in res.violations if v.rule == "block_nf_band" and v.fellow == "OVER"]
    under = [v for v in res.violations if v.rule == "block_nf_band" and v.fellow == "UNDER"]
    assert over, res.summary()
    assert under, res.summary()


def test_evaluator_catches_ccm_block_nf_cap_violation():
    """A CCM fellow with 8 NF days in the first (5-week) block is flagged when base cap=3
    (first-block cap = 3 + 2 = 5)."""
    nw = 5
    days = _blank_days(nw)
    for d in range(8):                         # 8 NF days inside block [0,5)
        _set(days, d, "NF", "C1")
    sol = FakeSol(weekly_assignments={"C1": ["NCC"] * nw}, call_assignments_by_day=days)
    cfg = FakeCfg(fellow_groups={"CCM": ["C1"]}, start_dow=0, num_days=nw * 7,
                  nf_ccm_block_nf_cap=3)
    res = evaluate_nf(sol, cfg)
    assert any(v.rule == "ccm_block_nf_cap" and v.fellow == "C1" for v in res.violations), res.summary()


def test_evaluator_weekend_ncc2_coverage_flag():
    """With nf_weekend_ncc2 on, a covered weekend NCC2 is clean; with it off, a covered
    weekend NCC2 is a coverage violation."""
    nw = 1
    # weekend = days 5 (Sat), 6 (Sun). Cover NCC2 on both with a fellow.
    days = _blank_days(nw)
    _set(days, 5, "NCC2", "C")
    _set(days, 6, "NCC2", "C")
    _cover_clean(days, nw)
    sol = FakeSol(
        weekly_assignments={"A": ["NCC"], "B": ["NCC"], "C": ["NCC"]},
        call_assignments_by_day=days)
    # flag ON -> weekend NCC2 covered is fine
    cfg_on = FakeCfg(fellow_groups={"NCC_JR": ["A", "B", "C"]},
                     start_dow=0, num_days=nw * 7, nf_weekend_ncc2=True)
    res_on = evaluate_nf(sol, cfg_on)
    assert not any(v.rule == "coverage" and v.day in (5, 6) for v in res_on.violations), res_on.summary()
    # flag OFF -> weekend NCC2 present is a coverage violation
    cfg_off = FakeCfg(fellow_groups={"NCC_JR": ["A", "B", "C"]},
                      start_dow=0, num_days=nw * 7, nf_weekend_ncc2=False)
    res_off = evaluate_nf(sol, cfg_off)
    assert any(v.rule == "coverage" and v.day in (5, 6) for v in res_off.violations), res_off.summary()
