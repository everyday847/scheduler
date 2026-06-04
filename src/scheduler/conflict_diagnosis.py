"""MUS (Minimal Unsatisfiable Subset) extraction for scheduling rules.

Uses incremental growth + shrink to find the smallest sets of rules
that can't all be satisfied together. Exploits SAT/UNSAT cost asymmetry:
SAT checks are ~1s, UNSAT checks are ~20s+, so we structure the search
to maximize SAT checks and minimize UNSAT timeouts.
"""

from __future__ import annotations

import time
from typing import Any, Generator

from .palette_rules import palette_rule_to_constraints
from .palette_derivations import derive_forbidden_shifts
from .semantic_constraints import (
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
    SemanticConstraint,
)
from .night_call_types import NightSolverConfig
from .weekend_call_types import WeekendSolverConfig

from parafrost_scheduler.schedule_solver import ScheduleSolverConfig, build_full_schedule_opb
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner


def extract_mus_cores(
    config_data: dict[str, Any],
    standing_rules: list[dict[str, Any]],
    annual_rules: list[dict[str, Any]],
    runner: RoundingSatRunner,
    *,
    probe_timeout: float = 5.0,
    max_cores: int = 5,
) -> Generator[dict[str, Any], None, None]:
    """Yield progress events and MUS cores as they're found.

    Events:
      {"type": "progress", "checked": N, "total": M, "phase": "growing|shrinking"}
      {"type": "core_found", "core": [rule, ...], "core_index": K}
      {"type": "done", "cores": [...], "total_probes": N, "elapsed": float}
    """
    t0 = time.time()
    total_probes = 0
    cores_found: list[list[dict]] = []

    fellow_groups = config_data.get("fellow_groups", {})
    shifts = config_data.get("shifts", [])
    num_weeks = config_data.get("num_weeks", 52)
    locked_assignments = config_data.get("locked_assignments", {})

    standing_constraints = _build_standing_constraints(standing_rules)

    active_annual = [r for r in annual_rules if r.get("active", True)]

    included: list[dict] = []
    remaining = list(active_annual)

    while remaining and len(cores_found) < max_cores:
        rule = remaining.pop(0)
        candidate = included + [rule]

        yield {"type": "progress", "checked": total_probes, "total": len(active_annual), "phase": "growing"}

        sat, probes = _check_feasible(
            fellow_groups, shifts, num_weeks,
            standing_constraints, standing_rules, candidate, active_annual,
            runner, probe_timeout,
            locked_assignments=locked_assignments,
        )
        total_probes += probes

        if sat:
            included.append(rule)
        else:
            yield {"type": "progress", "checked": total_probes, "total": len(active_annual), "phase": "shrinking"}

            mus = _shrink_to_mus(
                fellow_groups, shifts, num_weeks,
                standing_constraints, standing_rules, candidate, active_annual,
                runner, probe_timeout,
                locked_assignments=locked_assignments,
            )
            total_probes += mus["probes"]

            cores_found.append(mus["core"])
            yield {
                "type": "core_found",
                "core": mus["core"],
                "core_index": len(cores_found) - 1,
            }

            # Remove ONE rule from the MUS so we can continue growing.
            # Remove the rule that was just added (it triggered the conflict).
            # The other rules in the MUS stay in 'included' — they may
            # participate in other MUSes too.
            # Don't add 'rule' to included.

    yield {
        "type": "done",
        "cores": cores_found,
        "total_probes": total_probes,
        "elapsed": round(time.time() - t0, 1),
    }


def _shrink_to_mus(
    fellow_groups: dict,
    shifts: list[str],
    num_weeks: int,
    standing_constraints: list[SemanticConstraint],
    standing_rules_raw: list[dict],
    candidate: list[dict],
    all_annual: list[dict],
    runner: RoundingSatRunner,
    timeout: float,
    locked_assignments: dict[str, list[str]] | None = None,
) -> dict:
    """Shrink an UNSAT set of rules to a MUS by removing unnecessary rules."""
    probes = 0
    necessary = []

    # The last rule added is definitely in the MUS
    trigger = candidate[-1]
    to_check = candidate[:-1]

    for rule in to_check:
        # Try without this rule
        test_set = necessary + [r for r in to_check if r is not rule] + [trigger]
        sat, p = _check_feasible(
            fellow_groups, shifts, num_weeks,
            standing_constraints, standing_rules_raw, test_set, all_annual,
            runner, timeout,
            locked_assignments=locked_assignments,
        )
        probes += p

        if sat:
            # Removing this rule made it feasible → rule is necessary
            necessary.append(rule)
        # else: still infeasible without this rule → rule is not needed

        # Update to_check to only include unchecked rules
        to_check = [r for r in to_check if r is not rule and r not in necessary]

    return {"core": necessary + [trigger], "probes": probes}


def _check_feasible(
    fellow_groups: dict,
    shifts: list[str],
    num_weeks: int,
    standing_constraints: list[SemanticConstraint],
    standing_rules_raw: list[dict],
    annual_subset: list[dict],
    all_annual: list[dict],
    runner: RoundingSatRunner,
    timeout: float,
    locked_assignments: dict[str, list[str]] | None = None,
) -> tuple[bool, int]:
    """Check if standing + annual_subset is feasible. Returns (sat, probe_count)."""
    constraints = list(standing_constraints)

    for rule in annual_subset:
        if not rule.get("active", True):
            continue
        if rule.get("type") == "vacation_request_policy":
            continue
        try:
            constraints.extend(palette_rule_to_constraints(
                rule, lifecycle=ConstraintLifecycle.ANNUAL_RULE,
            ))
        except ValueError:
            continue

    # Use ALL annual rules for forbidden shift derivation (not just the subset)
    all_rules_for_derivation = standing_rules_raw + all_annual
    constraints.extend(derive_forbidden_shifts(all_rules_for_derivation, shifts, fellow_groups))

    # Disable night/weekend system: diagnosis checks weekly shift feasibility only.
    # Marking all fellows as CCM zeros their night variables, making all
    # night/weekend constraints vacuous (exactly_one, linking, blocking, etc.)
    all_fellow_names = frozenset(
        name for names in fellow_groups.values() for name in names
    )
    config = ScheduleSolverConfig(
        fellow_groups=fellow_groups,
        shifts=shifts,
        constraints=constraints,
        night_config=NightSolverConfig(
            total_nights={},
            friday_nights={},
            total_night_multisets=(),
            friday_night_multisets=(),
            ccm_fellows=all_fellow_names,
            holiday_dates=(),
        ),
        weekend_config=WeekendSolverConfig(
            ncc_totals={},
            stroke_totals={},
            stroke_cohort=(),
            stroke_cohort_total=None,
            ccm_fellows=all_fellow_names,
            always_stroke_eligible=frozenset(),
            telestroke_stroke_eligible=frozenset(),
            stroke_only_eligible=frozenset(),
        ),
        start_dow=0,
        num_days=num_weeks * 7,
        locked_assignments=locked_assignments or {},
    )

    try:
        opb, var_map = build_full_schedule_opb(config, soft_bound=None)
        upper = sum(w for _, w in var_map.soft_violations)
        opb_probe, _ = build_full_schedule_opb(config, soft_bound=upper)
        result = runner.solve(opb_probe, timeout=timeout)
        return result.satisfiable, 1
    except Exception:
        return False, 1


def _build_standing_constraints(
    standing_rules: list[dict[str, Any]],
) -> list[SemanticConstraint]:
    """Convert standing rules to constraints.

    Handles both v1 format (``kind`` key) and v2 palette format (``type`` key).
    V1 rules without a ``type`` key are skipped (they use a different constraint
    system that the diagnosis doesn't support).
    """
    constraints = []
    for rule in standing_rules:
        if not rule.get("active", True):
            continue
        rule_type = rule.get("type") or rule.get("kind")
        if rule_type == "full_assignment":
            groups = rule.get("groups") or rule.get("fellow_groups", [])
            constraints.append(SemanticConstraint(
                kind="full_assignment",
                lifecycle=ConstraintLifecycle.STANDING_RULE,
                strength=ConstraintStrength.HARD,
                fellows=FellowSelector.by_groups(*groups),
                params={"name": rule["name"]},
            ))
        elif "type" in rule:
            try:
                constraints.extend(palette_rule_to_constraints(
                    rule, lifecycle=ConstraintLifecycle.STANDING_RULE,
                ))
            except (ValueError, KeyError):
                continue
    return constraints
