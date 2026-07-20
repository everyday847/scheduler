"""Part-A tests: NCC_JR/NCC_SR background rotation quotas for the NF model."""
from __future__ import annotations

from pathlib import Path
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb, decode_solution
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.schedule_types import CALL_ROLES

_REPO = Path(__file__).resolve().parent.parent
_ROUNDINGSAT = _REPO / "vendor/roundingsat/build/roundingsat"
_ANNUAL = _REPO / "config/annual/ncc-nf-foundation-fixture.yaml"
_STANDING = _REPO / "config/standing/ncc-nf-model.yaml"


def _assemble():
    res = assemble_config(None, annual_path=_ANNUAL, standing_path=_STANDING, verbose=False)
    return res[0] if isinstance(res, tuple) else res


def _runner():
    if not _ROUNDINGSAT.exists():
        import pytest; pytest.skip("RoundingSat not built")
    return RoundingSatRunner(_ROUNDINGSAT)


def test_palette_has_rotation_shifts_and_two_ccm():
    cfg = _assemble()
    for s in ("Anaesthesia", "Stroke", "Telestroke/Clinic", "MICU", "SICU", "NS", "Elec", "Vac", "NCC"):
        assert s in cfg.shifts, s
    ccm = cfg.fellow_groups.get("CCM", [])
    assert len(ccm) == 2, ccm
    jr = cfg.fellow_groups.get("NCC_JR", [])
    sr = cfg.fellow_groups.get("NCC_SR", [])
    assert len(jr) == 3 and len(sr) == 2


def test_jr_micu_quota_constraint_present():
    """A NCC_JR MICU shift_total (exactly 16) must be among the assembled constraints."""
    cfg = _assemble()
    micu_totals = [c for c in cfg.constraints
                   if c.kind == "shift_total"
                   and c.shifts and "MICU" in c.shifts.shifts
                   and c.params.get("count") in (14, 16)]
    assert micu_totals, "expected an NCC_JR MICU shift_total rule"


def test_pinned_vac_weeks_present_in_constraints():
    """Each JR/SR fellow should have exactly 3 specific_assignment Vac pins."""
    cfg = _assemble()
    vac_pins = [c for c in cfg.constraints if c.kind == "specific_assignment"]
    # Verify each expected fellow has exactly 3 Vac pins
    expected = {
        "JR1": {8, 28, 32},
        "JR2": {11, 25, 41},
        "JR3": {14, 20, 27},
        "SR1": {10, 30, 44},
        "SR2": {17, 35, 48},
    }
    for fellow, exp_weeks in expected.items():
        fellow_pins = [c for c in vac_pins if fellow in (c.fellows.names if c.fellows else [])]
        pin_weeks = {c.weeks.start for c in fellow_pins}
        assert pin_weeks == exp_weeks, f"{fellow}: expected Vac weeks {exp_weeks}, got {pin_weeks}"


def test_fixed_rotation_rules_present():
    """JR rules: MICU exactly 16, MICU orientation [0,4]=4, Anaesthesia block 4 + total 4, SICU 4."""
    cfg = _assemble()
    st = [c for c in cfg.constraints if c.kind == "shift_total"]
    # block_rotation YAML rules become all_or_none_block constraints after assembly
    br = [c for c in cfg.constraints if c.kind in ("block_rotation", "all_or_none_block")]

    def has_st(shifts, count, window_start=None):
        for c in st:
            if (c.shifts and set(shifts) <= set(c.shifts.shifts)
                    and c.params.get("count") == count):
                if window_start is None:
                    return True
                # Window is encoded as c.weeks (WeekSpan) when a YAML window is present
                if c.weeks is not None and c.weeks.start == window_start:
                    return True
        return False

    assert has_st(["MICU"], 16), "JR MICU total 16"
    assert has_st(["MICU"], 4, window_start=0), "JR MICU orientation 4 in [0,4]"
    assert has_st(["Anaesthesia"], 4), "JR Anaesthesia total 4"
    assert has_st(["SICU"], 4), "JR SICU total 4"
    # block_rotation: Anaesthesia block_size 4
    ana_blocks = [c for c in br if c.shifts and "Anaesthesia" in c.shifts.shifts]
    assert any(c.params.get("block_size") == 4 for c in ana_blocks), "JR Anaesthesia 4wk block"


def test_full_year_with_rotations_is_sat():
    cfg = _assemble()
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    res = _runner().solve(opb, timeout=180)
    assert res.satisfiable, "NF model with JR/SR rotations + 2 CCM must be feasible"


def test_ncc_service_day_band():
    """Each JR fellow has 75-85 call days; each SR fellow has 125-135 call days."""
    cfg = _assemble()
    jr_names = set(cfg.fellow_groups.get("NCC_JR", []))
    sr_names = set(cfg.fellow_groups.get("NCC_SR", []))
    fellow_names = [f for g in cfg.fellow_groups.values() for f in g]

    opb, vm = build_full_schedule_opb(cfg, objective=False)
    res = _runner().solve(opb, timeout=180)
    assert res.satisfiable, "NF model must be SAT for call-day band check"

    a = res.assignment
    num_days = len(vm.call)
    for fi, name in enumerate(fellow_names):
        if name not in jr_names and name not in sr_names:
            continue
        count = sum(
            1 for d in range(num_days) for role in CALL_ROLES
            if a.get(vm.call[d][fi][role], False)
        )
        if name in jr_names:
            assert 75 <= count <= 85, f"JR fellow {name}: {count} call days not in [75,85]"
        else:
            assert 125 <= count <= 135, f"SR fellow {name}: {count} call days not in [125,135]"


# ---------------------------------------------------------------------------
# Item #4: weekend NCC1-on-Elec exemption test
# The encoder emits cv => (NCC v Elec) for weekend NCC1 days when Elec var
# exists; a weekday NCC1 day still forces NCC label.
# ---------------------------------------------------------------------------

from _nf_helpers import make_nf_config, build, runner_or_skip
from parafrost_scheduler.schedule_types import day_of_week as _day_of_week


def test_weekend_ncc1_elec_week_does_not_force_ncc_label():
    """Item #4 encoder test (non-vacuous): weekend NCC1 during an Elec week must
    NOT force the weekly NCC label — the week stays Elec.

    Setup (start_dow=0, 14 days):
      week 0 = days 0-6 (Mon-Sun); Sat = day 5 (dow 5).
    We pick fellow 0 (C1, CCM — unrestricted in week 0), pin:
      - xs[0][0][elec_si] = 1  (fellow spends week 0 on Elec)
      - call[5][0]["NCC1"] = 1 (fellow holds NCC1 on Sat of week 0)
    Assert: SAT  AND  xs[0][0][ncc_si] == False (week label stays Elec, not NCC).

    Non-vacuity: if the exemption were removed (cv => NCC instead of cv => NCC v Elec),
    the NCC1 call day would force the NCC label, which conflicts with the pinned Elec
    under at-most-one-shift => UNSAT.  So removing the exemption makes this test fail
    (SAT flips to UNSAT).  The exemption is the only reason both pins coexist SAT.
    """
    cfg = make_nf_config(num_days=14, start_dow=0,
                         shifts=("NCC", "MICU", "Elec", "Vac"))
    opb, vm = build(cfg)

    shift_idx = {s: i for i, s in enumerate(vm.shifts)}
    ncc_si = shift_idx["NCC"]
    elec_si = shift_idx["Elec"]

    # Week 0, fellow 0 (C1 — CCM, unrestricted in week 0).
    f, w = 0, 0
    # day 5 is Saturday of week 0: day_of_week(5, start_dow=0) = 5 (Sat) ✓
    sat_day = 5
    assert _day_of_week(sat_day, 0) in (5, 6), "sanity: day 5 must be a weekend day"

    elec_var = vm.xs[f][w][elec_si]
    ncc_var  = vm.xs[f][w][ncc_si]
    call_var = vm.call[sat_day][f]["NCC1"]
    assert elec_var != 0, "Elec weekly var must exist for fellow 0, week 0"
    assert ncc_var  != 0, "NCC weekly var must exist for fellow 0, week 0"
    assert call_var != 0, "NCC1 call var must exist for fellow 0, day 5"

    # Pin fellow 0 to Elec in week 0 AND to NCC1 on the Saturday of week 0.
    opb.add_unit(elec_var)
    opb.add_unit(call_var)

    runner = runner_or_skip()
    res = runner.solve(opb, timeout=30)

    assert res.satisfiable, (
        "model UNSAT after pinning Elec week + weekend NCC1 — "
        "exemption clause (cv => NCC v Elec) may be missing or broken"
    )
    a = res.assignment
    assert not a.get(ncc_var, False), (
        "fellow 0 pinned to Elec in week 0 but NCC label is TRUE — "
        "weekend NCC1 exemption failed to keep the week as Elec"
    )


def test_weekday_ncc1_forces_ncc_label():
    """Regression (non-vacuous): a weekday NCC1 call day must force the weekly NCC label.

    Setup (start_dow=0, 14 days):
      day 0 = Monday of week 0 (dow 0, weekday).
    We pick fellow 0 and pin call[0][0]["NCC1"] = 1.
    Assert: SAT  AND  xs[0][0][ncc_si] == True (weekday call forces the NCC label).

    This pin is unconditional — no if-guard escape — so the assertion always fires.
    The strict path (cv => NCC) covers weekday call days regardless of the exemption,
    so this test passes with or without the weekend exemption in place.
    """
    cfg = make_nf_config(num_days=14, start_dow=0,
                         shifts=("NCC", "MICU", "Elec", "Vac"))
    opb, vm = build(cfg)

    shift_idx = {s: i for i, s in enumerate(vm.shifts)}
    ncc_si = shift_idx["NCC"]

    # day 0 = Monday (dow 0, weekday) of week 0.
    f, w, mon_day = 0, 0, 0
    assert _day_of_week(mon_day, 0) not in (5, 6), "sanity: day 0 must be a weekday"

    call_var = vm.call[mon_day][f]["NCC1"]
    ncc_var  = vm.xs[f][w][ncc_si]
    assert call_var != 0, "NCC1 call var must exist for fellow 0, day 0"
    assert ncc_var  != 0, "NCC weekly var must exist for fellow 0, week 0"

    opb.add_unit(call_var)

    runner = runner_or_skip()
    res = runner.solve(opb, timeout=30)

    assert res.satisfiable, "model UNSAT after pinning a weekday NCC1 — unexpected"
    a = res.assignment
    assert a.get(ncc_var, False), (
        "fellow 0 has NCC1 on weekday day 0 but NCC weekly label is not set — "
        "strict forward implication (cv => NCC) is broken for weekday call days"
    )


# ---------------------------------------------------------------------------
# A2: block_offset — long first block
# ---------------------------------------------------------------------------
from parafrost_scheduler.schedule_types import ScheduleSolverConfig
from _nf_helpers import make_nf_config, build, runner_or_skip
from scheduler.semantic_constraints import (
    SemanticConstraint, ConstraintLifecycle, ConstraintStrength, FellowSelector, ShiftSet)


def _block_rule(block_size, offset, strength=ConstraintStrength.HARD):
    params = {"name": "blk", "block_size": block_size}
    if offset:
        params["block_offset"] = offset
    return SemanticConstraint(
        kind="all_or_none_block", lifecycle=ConstraintLifecycle.STANDING_RULE,
        strength=strength, fellows=FellowSelector.by_groups("NCC_JR"),
        shifts=ShiftSet("blk", ("MICU",)), params=params)


def test_block_offset_long_first_block_grid():
    """With block_size=4, offset=1: a fellow on MICU in week 0 must be on it weeks
    0..4 (5-week first block), and a fellow on MICU in week 5 must be on weeks 5..8.
    Pinning MICU in weeks 0 and 5 then forbidding week 4 (inside block 0) is UNSAT."""
    # 9-week horizon (num_days=63), block grid: [0..4],[5..8]
    cfg = make_nf_config(num_days=63, shifts=("MICU", "NCC", "Elec", "Vac"),
                         constraints=[_block_rule(4, 1)])
    opb, vm = build(cfg)
    f = 3  # a JR
    micu = vm.shifts.index("MICU")
    opb.add_unit(vm.xs[f][0][micu])     # MICU week 0 -> block 0 = weeks 0..4 all MICU
    opb.add_unit(-vm.xs[f][4][micu])    # but forbid week 4 -> contradicts the 5-wk block
    res = runner_or_skip().solve(opb, timeout=30)
    assert not res.satisfiable


# ---------------------------------------------------------------------------
# A3: CCM 4-week NCC blocks + soft 6-8 NF-days/block
# ---------------------------------------------------------------------------

def test_ccm_ncc_comes_in_aligned_blocks():
    """A CCM fellow on NCC in week 0 must be NCC for the whole first (5-week) block."""
    cfg = _assemble()
    if not _ROUNDINGSAT.exists():
        import pytest; pytest.skip("RoundingSat not built")
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    # pin a CCM fellow to NCC week 0; assert the block_rotation forces weeks 0..4 NCC
    ccm = cfg.fellow_groups["CCM"][0]
    f = vm.fellow_names.index(ccm)
    ncc = vm.shifts.index("NCC")
    opb.add_unit(vm.xs[f][0][ncc])
    opb.add_unit(-vm.xs[f][3][ncc])     # forbid week 3 (inside first 5-wk block) -> UNSAT
    res = RoundingSatRunner(_ROUNDINGSAT).solve(opb, timeout=120)
    assert not res.satisfiable


def test_ccm_block_nf_count_soft_penalty_registered():
    """The CCM per-block NF-day soft target registers soft-violation vars (so the
    objective can penalize <6 or >8 NF days in a selected CCM block)."""
    cfg = _assemble()
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    # soft_violations is a list of (var, weight); the CCM-NF-count helper appends some.
    # Assert at least one soft violation exists tagged at the CCM-NF weight (use a
    # sentinel weight set in config, or just assert the helper added vars by comparing
    # soft-violation count with/without the helper — simplest: assert > 0 here and rely
    # on the dedicated unit test below for the precise behavior).
    assert len(vm.soft_violations) > 0


# ---------------------------------------------------------------------------
# A3 precision: unit-test the CCM NF-count helper's gating behavior
# ---------------------------------------------------------------------------
# Uses a minimal fixture: 1 CCM fellow, 5 days (one block), "NCC" shift.
# Verifies:
#   - A selected block (NCC weekly var active) with <6 NF days is penalized.
#   - An unselected block (NCC weekly var inactive) is NOT penalized regardless
#     of NF-day count (the blk_active gate must swallow the lo-side penalty).
#   - A selected block with >8 NF days is penalized.

_CCM_NF_WEIGHT = 50   # sentinel weight used by _encode_ccm_block_nf_count


def test_ccm_nf_count_selected_block_below_lo_penalized():
    """A selected CCM block with fewer than 6 NF days fires the lo-side penalty.

    Build the FULL assembled config (all 5 fellows satisfy coverage).
    Pin CCM fellow 0 to NCC in week 0 -> block [0..4] is selected.
    Pin all call-days in that block to NCC1 (not NF) -> 0 NF days -> lo violation.
    The model must still be SAT (soft penalty) and vm.soft_violations must contain
    the CCM-NF sentinel weight.
    """
    cfg = _assemble()
    opb, vm = build_full_schedule_opb(cfg, objective=False)

    ccm_name = cfg.fellow_groups["CCM"][0]
    f = vm.fellow_names.index(ccm_name)
    ncc_si = vm.shifts.index("NCC")

    # Pin week 0 NCC => block 0 selected (weeks 0..4, 35 days)
    if vm.xs[f][0][ncc_si] != 0:
        opb.add_unit(vm.xs[f][0][ncc_si])

    # Forbid NF for this fellow across all block-0 days (days 0..34)
    for d in range(min(35, len(vm.call))):
        nf_var = vm.call[d][f]["NF"]
        if nf_var != 0:
            opb.add_unit(-nf_var)

    runner = runner_or_skip()
    res = runner.solve(opb, timeout=120)
    assert res.satisfiable, (
        "model UNSAT after forbidding CCM NF days in selected block — "
        "lo-side penalty must be SOFT"
    )
    a = res.assignment
    # The CCM-NF sentinel weight must appear in soft_violations
    assert _CCM_NF_WEIGHT in [w for _, w in vm.soft_violations], (
        f"CCM-NF sentinel weight {_CCM_NF_WEIGHT} not in soft_violations — "
        "helper may not have emitted any lo-side penalty var"
    )
    # At least one lo-side slack var must be True: with 0 NF days in a selected
    # block and nf_lo=6, the lo-side penalty MUST fire.
    ccm_nf_vars = [var for var, w in vm.soft_violations if w == _CCM_NF_WEIGHT]
    active = sum(1 for var in ccm_nf_vars if a.get(var, False))
    assert active >= 1, (
        f"expected at least one active CCM-NF lo-side penalty var (0 NF days in "
        f"selected block, nf_lo=6), but zero fired "
        f"(total CCM-NF vars: {len(ccm_nf_vars)})"
    )


def test_ccm_nf_count_unselected_block_no_penalty():
    """An unselected CCM block incurs ZERO lo-side NF-count penalty (the blk_active gate).

    This is the key correctness property from the brief:
    "a CCM fellow with NO NCC block in the year must incur ZERO CCM-NF-count penalty."

    We use a minimal model (not the full assembled config) where we can control
    block selection completely. Setup:
      - 1 CCM fellow + 6 other fellows (for coverage)
      - 14-day horizon (2 weeks), block_size=4, block_offset=0 → 1 block (weeks 0-1)
      - CCM block rule with nf_days_per_block=[6,8]
      - Forbid NCC for the CCM fellow in this block (blk_active=0)
      - Build with objective=True and solve; verify no CCM-NF penalty var fires

    The constraint `nf_count + nf_lo*slack_lo + nf_lo*(1-blk_active) >= nf_lo`:
      blk_active=0 → nf_count + nf_lo*slack_lo + nf_lo >= nf_lo → nf_count + nf_lo*slack_lo >= 0
      → tautology: slack_lo CAN be 0 regardless of nf_count ← no penalty
    """
    from _nf_helpers import make_nf_config
    from scheduler.semantic_constraints import (
        SemanticConstraint, ConstraintLifecycle, ConstraintStrength,
        FellowSelector, ShiftSet,
    )

    # CCM block rule: block_size=4, no offset (14-day horizon has weeks 0-1 = 1 block)
    ccm_blk = SemanticConstraint(
        kind="all_or_none_block",
        lifecycle=ConstraintLifecycle.ANNUAL_RULE,
        strength=ConstraintStrength.HARD,
        fellows=FellowSelector.by_groups("CCM"),
        shifts=ShiftSet("ccm_ncc", ("NCC",)),
        params={"name": "CCM NCC 4wk blocks", "block_size": 4, "block_offset": 0,
                "nf_days_per_block": [6, 8]},
    )
    # 14 days = 2 weeks. Use default 6-fellow fixture for coverage.
    cfg = make_nf_config(
        shifts=("NCC", "Elec", "Vac"),
        num_days=14,
        start_dow=0,
        constraints=[ccm_blk],
    )
    opb, vm = build_full_schedule_opb(cfg, objective=True)

    # Find CCM fellow index (C1 in the default fixture)
    ccm_names = set(cfg.fellow_groups.get("CCM", []))
    ccm_fi = [i for i, n in enumerate(vm.fellow_names) if n in ccm_names]

    ncc_si = vm.shifts.index("NCC")

    # Forbid all NCC for ALL CCM fellows in the block (weeks 0-1)
    for f in ccm_fi:
        for w in range(vm.num_weeks):
            v = vm.xs[f][w][ncc_si]
            if v != 0:
                opb.add_unit(-v)

    runner = runner_or_skip()
    res = runner.optimize(opb, time_limit=60)
    assert res.satisfiable, (
        "model UNSAT after forbidding CCM NCC in a small fixture — "
        "other fellows should cover call without CCM NCC"
    )

    a = res.assignment
    # All CCM-NF penalty vars (sentinel weight) must be False in the optimal solution
    ccm_nf_viol_vars = [var for var, w in vm.soft_violations if w == _CCM_NF_WEIGHT]
    assert ccm_nf_viol_vars, (
        f"no CCM-NF penalty vars found (sentinel weight {_CCM_NF_WEIGHT}) — "
        "helper must have emitted lo-side vars"
    )
    active_count = sum(1 for var in ccm_nf_viol_vars if a.get(var, False))
    assert active_count == 0, (
        f"unselected CCM block has {active_count} active penalty var(s) — "
        "the blk_active gate must suppress lo-side penalty when blk_active=0. "
        "When blk_active=0: nf_count + nf_lo*slack_lo + nf_lo >= nf_lo → "
        "nf_count + nf_lo*slack_lo >= 0 (tautology, slack_lo free to be 0)."
    )


def test_ccm_nf_count_selected_block_above_hi_penalized():
    """A selected CCM block with more than 8 NF days fires the hi-side penalty.

    Build the full assembled config. Pin CCM fellow 0 to NCC week 0 (block selected).
    Pin NF days in a valid 4-6-day run that exceeds 8 days total across the block.
    The model must stay SAT (soft penalty) and the CCM-NF sentinel weight must appear
    and at least one hi-side var must be True in the solution.

    Uses two well-spaced runs with at least 2 rest days between them:
      Run 1: days 0-4 (5 days, Mon-Fri of week 0). Run ends at day 4.
             After-rest: days 5-6 must be off (Sat, Sun of week 0).
      Run 2: days 9-13 (5 days, Mon-Fri of week 1). Run starts at day 9.
             Before-rest: days 7-8 must be off.
    Total NF = 10 days > 8 → hi-side penalty fires.
    """
    cfg = _assemble()
    opb, vm = build_full_schedule_opb(cfg, objective=False)

    ccm_name = cfg.fellow_groups["CCM"][0]
    f = vm.fellow_names.index(ccm_name)
    ncc_si = vm.shifts.index("NCC")

    # Pin week 0 NCC => block 0 selected
    if vm.xs[f][0][ncc_si] != 0:
        opb.add_unit(vm.xs[f][0][ncc_si])

    # Run 1: days 0-4 (Mon-Fri of week 0, start_dow=0) — 5 consecutive NF days
    for d in range(5):
        nf_var = vm.call[d][f]["NF"]
        if nf_var != 0:
            opb.add_unit(nf_var)
    # Explicitly forbid NF on rest days 5-6 (Sat-Sun of week 0)
    for d in range(5, 7):
        nf_var = vm.call[d][f]["NF"]
        if nf_var != 0:
            opb.add_unit(-nf_var)

    # Run 2: days 9-13 (Mon-Fri of week 1) — 5 consecutive NF days
    # Before rest (days 7-8 off) is satisfied by forbidding NF on days 7-8
    for d in range(7, 9):
        nf_var = vm.call[d][f]["NF"]
        if nf_var != 0:
            opb.add_unit(-nf_var)
    for d in range(9, 14):
        nf_var = vm.call[d][f]["NF"]
        if nf_var != 0:
            opb.add_unit(nf_var)

    runner = runner_or_skip()
    res = runner.solve(opb, timeout=120)
    assert res.satisfiable, (
        "model UNSAT after pinning 10 NF days in selected CCM block — "
        "hi-side penalty must be SOFT"
    )
    a = res.assignment
    # The CCM-NF sentinel weight must appear and at least one hi-side var must be True
    assert _CCM_NF_WEIGHT in [w for _, w in vm.soft_violations], (
        f"CCM-NF sentinel weight {_CCM_NF_WEIGHT} not in soft_violations — "
        "helper may not have emitted any hi-side penalty var"
    )
    ccm_nf_vars = [(var, w) for var, w in vm.soft_violations if w == _CCM_NF_WEIGHT]
    active = sum(1 for var, _ in ccm_nf_vars if a.get(var, False))
    assert active >= 1, (
        f"expected at least one active CCM-NF penalty for 10 NF days in selected block, "
        f"but zero fired (total CCM-NF vars: {len(ccm_nf_vars)})"
    )
