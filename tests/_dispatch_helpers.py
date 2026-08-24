"""Shared helpers for DISPATCH-LEVEL strength-flow tests.

Why this module exists
----------------------
The ~13 ``tests/test_*contract*.py`` files all call ``crit.encode(sink, ...,
strength=HARD_or_SOFT)`` with strength as a LITERAL argument. They prove a
criterion honors a strength it is *handed* — they prove NOTHING about whether the
encoder DISPATCH (``build_full_schedule_opb``) hands the criterion the strength
the CONFIG declares. A real bug shipped green that way: a configured-hard
``weekend_gating`` rule emitted SOFT because the dispatch hardcoded the strength
(fixed d2f2a50), which also silently invalidated the SAT decision built on it.

The ``tests/test_dispatch_strength_*.py`` family closes that gap: build a minimal
``ScheduleSolverConfig`` carrying ONE target rule, run it through the REAL dispatch,
pin a VIOLATING assignment, solve, and assert hard -> UNSAT / soft -> SAT *and the
penalty (var, weight) was actually registered*. These functions are the shared,
side-effect-free surface those file-disjoint test files consume.

The minimal-config builder is modeled on the proven ~10ms pattern in
``tests/test_prerequisite_migration.py::_make_config`` (no workbook).

DELTA DISCIPLINE (important)
----------------------------
The dispatch ALWAYS emits a floor of baseline hard constraints (night coverage,
weekend-role coverage, backup coverage, "require a weekday assignment", ...). So a
bare config is only SAT with enough fellows/shifts to satisfy that floor — the
defaults here (7 fellows, a ``Elec`` filler shift, 21 days) are chosen to be a
known-SAT baseline. Because the floor is always present, a HARD test must attribute
its UNSAT to the *target rule*, not to the floor. Do that with the delta pattern:
the SAME config + SAME pins must be SAT when the rule is soft/absent and UNSAT only
when the rule is hard. ``assert_baseline_sat`` guards that the pins alone (rule
removed) leave the model SAT, so a hard-variant UNSAT is unambiguously the rule.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from parafrost_scheduler.schedule_encoder import build_full_schedule_opb
from parafrost_scheduler.schedule_types import ScheduleSolverConfig
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig


# --------------------------------------------------------------------------
# Solver binary
# --------------------------------------------------------------------------

ROUNDINGSAT_BINARY = (
    Path(__file__).resolve().parent.parent / "vendor" / "roundingsat" / "build" / "roundingsat"
)


def runner_or_skip() -> RoundingSatRunner:
    """A RoundingSatRunner, or skip the test if the binary is not built.

    Use inside a test (or a thin fixture) so dispatch tests degrade gracefully on
    a machine without the vendored solver, exactly like the contract-test runners.
    """
    if not ROUNDINGSAT_BINARY.exists():
        pytest.skip("RoundingSat binary not built")
    return RoundingSatRunner(ROUNDINGSAT_BINARY)


def solve_or_skip(runner, opb, *, timeout):
    """Solve; if RoundingSat exceeds `timeout` on this host, pytest.skip instead of
    failing. A solve timeout is a time-budget/hardware limit on these slow full-model
    tests, not a correctness failure (the models are verified feasible on Slurm)."""
    import subprocess
    try:
        return runner.solve(opb, timeout=timeout)
    except subprocess.TimeoutExpired:
        pytest.skip(f"solve exceeded {timeout}s on this host (time budget, not a correctness failure)")


# --------------------------------------------------------------------------
# Minimal config builder
# --------------------------------------------------------------------------

# A 7-fellow / filler-shift / 21-day baseline that satisfies the dispatch's floor
# of baseline hard constraints (coverage, "require weekday assignment", etc.) with
# slack. Fewer fellows or no filler shift makes even an empty config UNSAT.
_DEFAULT_FELLOWS = ("F0", "F1", "F2", "F3", "F4", "F5", "F6")
_DEFAULT_SHIFTS = ("Stroke", "NCC1", "NCC2", "Vac", "Elec")


def make_dispatch_config(
    *,
    constraints,
    fellows=_DEFAULT_FELLOWS,
    shifts=_DEFAULT_SHIFTS,
    num_days: int = 21,
    start_dow: int = 0,
    night_hard_criteria=frozenset(),
    weekend_always_stroke_eligible=None,
    ccm_fellows=frozenset(),
    night_weights=None,
    **config_overrides,
) -> ScheduleSolverConfig:
    """Build a minimal ScheduleSolverConfig for a dispatch-level strength test.

    Carries empty Night/Weekend sub-configs (so only the rules in *constraints*
    bear behavior) with every fellow stroke-eligible by default (so the weekend
    role vars ``wr[w][role][f]`` actually exist and a weekend-role violation is
    pinnable).

    Levers callers use to drive a target rule's strength through the dispatch:
      - ``night_hard_criteria`` — the frozenset ``_strength_for`` consults for the
        ``night_gating`` mechanism (membership => HARD). Constraint ``.strength`` is
        irrelevant for that kind.
      - per-constraint ``.strength`` — the weekly and weekend_gating mechanisms.
      - per-role ``role_strengths`` in a ``weekend_night`` constraint's params — the
        weekend_night mechanism (constraint ``.strength`` is inert there by design).
      - ``night_weights`` — a NightPolicyWeights override so a soft night_gating
        penalty has a known, assertable weight.

    ``config_overrides`` pass straight through to ScheduleSolverConfig (e.g.
    ``weekend_mismatch_weight=...`` or ``ncc_weekend_misalign_penalty=...``).
    """
    fellows = list(fellows)
    if weekend_always_stroke_eligible is None:
        weekend_always_stroke_eligible = frozenset(fellows)

    night_config = NightSolverConfig(
        total_nights={}, friday_nights={},
        total_night_multisets=(), friday_night_multisets=(),
        ccm_fellows=frozenset(ccm_fellows), holiday_dates=(),
        horizon_start_date=date(2026, 7, 6))
    weekend_config = WeekendSolverConfig(
        ncc_totals={}, stroke_totals={}, stroke_cohort=(), stroke_cohort_total=None,
        ccm_fellows=frozenset(ccm_fellows),
        always_stroke_eligible=frozenset(weekend_always_stroke_eligible),
        telestroke_stroke_eligible=frozenset(), stroke_only_eligible=frozenset())

    kwargs = dict(
        fellow_groups={"NCC_SR": fellows},
        shifts=list(shifts),
        constraints=list(constraints),
        night_config=night_config,
        weekend_config=weekend_config,
        start_dow=start_dow,
        num_days=num_days,
        call_rules=[],
        night_hard_criteria=frozenset(night_hard_criteria),
    )
    if night_weights is not None:
        kwargs["night_weights"] = night_weights
    kwargs.update(config_overrides)
    return ScheduleSolverConfig(**kwargs)


def build(config: ScheduleSolverConfig, *, objective: bool = False):
    """Run the REAL dispatch. Returns ``(opb, var_map)``.

    ``var_map.soft_violations`` is the live list the build appended to, so reading
    it after the build reflects every soft penalty the dispatch emitted.
    """
    return build_full_schedule_opb(config, objective=objective)


# --------------------------------------------------------------------------
# Pin helpers — force a specific assignment on the built OPB.
# Each asserts the looked-up var is real (!= 0); a 0 var means the geometry the
# test assumed does not exist, which would make the test silently vacuous.
# --------------------------------------------------------------------------

def _fellow_index(vm, fellow) -> int:
    return fellow if isinstance(fellow, int) else vm.fellow_names.index(fellow)


def pin_shift(opb, vm, fellow, week: int, shift, *, value: bool = True) -> int:
    """Force fellow's weekday ``shift`` in ``week`` on/off. Returns the var."""
    f = _fellow_index(vm, fellow)
    s = shift if isinstance(shift, int) else vm.shifts.index(shift)
    var = vm.xs[f][week][s]
    assert var != 0, f"no weekly var for fellow={fellow} week={week} shift={shift}"
    opb.add_unit(var if value else -var)
    return var


def pin_role(opb, vm, fellow, week: int, role_idx: int, *, value: bool = True) -> int:
    """Force fellow's weekend role (0=NCC1,1=NCC2,2=Stroke) in ``week``. Returns var."""
    f = _fellow_index(vm, fellow)
    var = vm.wr[week][role_idx].get(f, 0)
    assert var != 0, f"no weekend-role var for fellow={fellow} week={week} role={role_idx}"
    opb.add_unit(var if value else -var)
    return var


def pin_night(opb, vm, fellow, day: int, *, value: bool = True) -> int:
    """Force fellow's night on absolute ``day`` on/off. Returns the var."""
    f = _fellow_index(vm, fellow)
    var = vm.xn[day][f]
    assert var != 0, f"no night var for fellow={fellow} day={day}"
    opb.add_unit(var if value else -var)
    return var


def role_var_present(vm, fellow, week: int, role_idx: int) -> bool:
    """Whether a weekend-role var exists (i.e. the violation is pinnable)."""
    f = _fellow_index(vm, fellow)
    return vm.wr[week][role_idx].get(f, 0) != 0


# --------------------------------------------------------------------------
# Solve + classify
# --------------------------------------------------------------------------

def solve_sat(opb, *, timeout: float = 30.0) -> bool:
    """True iff the OPB is satisfiable. Skips if the solver binary is missing."""
    return runner_or_skip().solve(opb, timeout=timeout).satisfiable


def soft_weights(vm) -> list[int]:
    """The weights of every soft penalty the dispatch registered."""
    return [w for (_var, w) in vm.soft_violations]


def has_soft_weight(vm, weight: int) -> bool:
    """Whether the dispatch registered at least one soft penalty of *weight*.

    The load-bearing half of a soft assertion: a SAT result alone is worthless
    (a dropped rule is also SAT). This proves the rule actually emitted a penalty.
    """
    return weight in soft_weights(vm)


def assert_baseline_sat(
    pin_fn, *, fellows=_DEFAULT_FELLOWS, shifts=_DEFAULT_SHIFTS,
    num_days: int = 21, timeout: float = 30.0, **config_kwargs,
) -> None:
    """Guard that *pin_fn*'s pins alone (NO target rule) leave the model SAT.

    A HARD-variant test asserts ``not solve_sat(opb)``; this proves that UNSAT is
    caused by the target rule and not by the pins colliding with the baseline floor
    (or with each other). Build a config with ``constraints=[]`` (rule removed),
    apply the same pins via *pin_fn(opb, vm)*, and assert SAT.
    """
    cfg = make_dispatch_config(
        constraints=[], fellows=fellows, shifts=shifts, num_days=num_days,
        **config_kwargs)
    opb, vm = build(cfg)
    pin_fn(opb, vm)
    assert solve_sat(opb, timeout=timeout), (
        "baseline (rule removed) is UNSAT under these pins — a hard-variant UNSAT "
        "would not be attributable to the target rule")
