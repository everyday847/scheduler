"""Byte-equivalence test: the four night-pin kinds, OLD call_rules path vs NEW
config.constraints (night-layer) path, produce an IDENTICAL OPB.

For each of specific_night_assignment / blocked_night / friday_call_assignment /
group_night_requirement we build two configs that differ ONLY in WHERE the rule
lives:

  Config A — the rule as a legacy call_rules dict (encoded by _encode_call_rules).
  Config B — the SAME rule as a SemanticConstraint(kind=<same string>) in
             config.constraints (encoded by the _NIGHT_HANDLERS walk).

build_full_schedule_opb(A) and (B) must yield the same num_vars, num_constraints,
objective terms, AND the same canonical constraint multiset. Equal multiset ⇒
the relocation is byte-neutral, proving the migration is safe.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

import pytest

from parafrost_scheduler.schedule_types import ScheduleSolverConfig, _num_weeks_for
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig
from scheduler.semantic_constraints import (
    SemanticConstraint,
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
)


HORIZON = date(2026, 7, 6)  # Monday
START_DOW = 0
NUM_DAYS = 21
FELLOW_GROUPS = {"NCC_SR": ["Alice", "Bob", "Carol"]}


def _make_config(call_rules, constraints, fellow_groups=None):
    if fellow_groups is None:
        fellow_groups = FELLOW_GROUPS
    night_config = NightSolverConfig(
        total_nights={},
        friday_nights={},
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(),
        horizon_start_date=HORIZON,
    )
    weekend_config = WeekendSolverConfig(
        ncc_totals={},
        stroke_totals={},
        stroke_cohort=(),
        stroke_cohort_total=None,
        ccm_fellows=frozenset(),
        always_stroke_eligible=frozenset({"Alice", "Bob", "Carol"}),
        telestroke_stroke_eligible=frozenset(),
        stroke_only_eligible=frozenset(),
    )
    return ScheduleSolverConfig(
        fellow_groups=fellow_groups,
        shifts=["NCC1"],
        constraints=constraints,
        night_config=night_config,
        weekend_config=weekend_config,
        night_hard_criteria=frozenset(),
        start_dow=START_DOW,
        num_days=NUM_DAYS,
        call_rules=call_rules,
    )


def _canonical_line(line: str) -> str:
    """Canonicalize a constraint line so term ORDER doesn't matter: sort the
    `coeff var` terms, keep op + rhs. (Mirrors the plan's regression gate.)"""
    line = line.strip().rstrip(";").strip()
    parts = line.split()
    # Find the operator (one of <=, >=, =).
    for op in ("<=", ">=", "="):
        if op in parts:
            idx = parts.index(op)
            terms = parts[:idx]
            rhs = parts[idx + 1:]
            pair_terms = [" ".join(terms[i:i + 2]) for i in range(0, len(terms), 2)]
            return " | ".join(sorted(pair_terms)) + f" {op} {' '.join(rhs)}"
    return line


def _fingerprint(opb):
    return (
        opb.num_vars,
        opb.num_constraints,
        tuple(opb._objective) if opb._objective else (),
        Counter(_canonical_line(c) for c in opb._constraints),
    )


def _assert_equivalent(call_rule, constraint, label, fellow_groups=None):
    cfg_a = _make_config(call_rules=[call_rule], constraints=[], fellow_groups=fellow_groups)
    cfg_b = _make_config(call_rules=[], constraints=[constraint], fellow_groups=fellow_groups)
    opb_a, _ = build_full_schedule_opb(cfg_a, objective=True)
    opb_b, _ = build_full_schedule_opb(cfg_b, objective=True)

    fa, fb = _fingerprint(opb_a), _fingerprint(opb_b)
    assert fa[0] == fb[0], f"{label}: num_vars {fa[0]} != {fb[0]}"
    assert fa[1] == fb[1], f"{label}: num_constraints {fa[1]} != {fb[1]}"
    assert fa[2] == fb[2], f"{label}: objective terms differ"
    # The discriminating check: same canonical constraint multiset.
    only_a = fa[3] - fb[3]
    only_b = fb[3] - fa[3]
    assert not only_a and not only_b, (
        f"{label}: constraint multiset diverged.\n"
        f"  only in A (call_rules): {dict(only_a)}\n"
        f"  only in B (constraints): {dict(only_b)}"
    )


def _annual(kind):
    return dict(
        lifecycle=ConstraintLifecycle.ANNUAL_RULE,
        strength=ConstraintStrength.HARD,
    )


# July 6 2026 = Monday = horizon day 0; start_dow=0.
class TestNightPinMigrationEquivalence:
    def test_specific_night_assignment(self):
        call_rule = {
            "type": "specific_night_assignment",
            "name": "Alice covers July 10th",
            "fellow": "Alice",
            "dates": ["2026-07-10"],
            "active": True,
        }
        constraint = SemanticConstraint(
            kind="specific_night_assignment",
            fellows=FellowSelector.by_names("Alice"),
            params={"dates": ["2026-07-10"], "name": "Alice covers July 10th"},
            **_annual("specific_night_assignment"),
        )
        _assert_equivalent(call_rule, constraint, "specific_night_assignment")

    def test_blocked_night(self):
        call_rule = {
            "type": "blocked_night",
            "name": "Bob off July 12th",
            "fellow": "Bob",
            "dates": ["2026-07-12"],
            "active": True,
        }
        constraint = SemanticConstraint(
            kind="blocked_night",
            fellows=FellowSelector.by_names("Bob"),
            params={"dates": ["2026-07-12"], "name": "Bob off July 12th"},
            **_annual("blocked_night"),
        )
        _assert_equivalent(call_rule, constraint, "blocked_night")

    def test_friday_call_assignment(self):
        call_rule = {
            "type": "friday_call_assignment",
            "name": "Carol Friday wk1",
            "fellow": "Carol",
            "weeks": [1],
            "active": True,
        }
        constraint = SemanticConstraint(
            kind="friday_call_assignment",
            fellows=FellowSelector.by_names("Carol"),
            params={"weeks": [1], "dow": 4, "name": "Carol Friday wk1"},
            **_annual("friday_call_assignment"),
        )
        _assert_equivalent(call_rule, constraint, "friday_call_assignment")

    def test_group_night_requirement(self):
        call_rule = {
            "type": "group_night_requirement",
            "name": "Only NCC_SR on these nights",
            "groups": ["NCC_SR"],
            "dates": ["2026-07-08", "2026-07-15"],
            "active": True,
        }
        constraint = SemanticConstraint(
            kind="group_night_requirement",
            fellows=FellowSelector.by_groups("NCC_SR"),
            params={
                "groups": ["NCC_SR"],
                "dates": ["2026-07-08", "2026-07-15"],
                "name": "Only NCC_SR on these nights",
            },
            **_annual("group_night_requirement"),
        )
        _assert_equivalent(call_rule, constraint, "group_night_requirement")

    def test_group_night_requirement_with_excluded_group(self):
        # A wider fellow set so the complement (forbidden) set is non-empty.
        groups = {"NCC_SR": ["Alice", "Bob"], "OTHER": ["Carol", "Dave"]}
        call_rule = {
            "type": "group_night_requirement",
            "name": "Only NCC_SR",
            "groups": ["NCC_SR"],
            "dates": ["2026-07-08"],
            "active": True,
        }
        constraint = SemanticConstraint(
            kind="group_night_requirement",
            fellows=FellowSelector.by_groups("NCC_SR"),
            params={"groups": ["NCC_SR"], "dates": ["2026-07-08"], "name": "Only NCC_SR"},
            **_annual("group_night_requirement"),
        )
        _assert_equivalent(
            call_rule, constraint, "group_night_requirement (excluded)",
            fellow_groups=groups)
