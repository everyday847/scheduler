"""Tests for night_solver.py — domain encoder, binary search, and CLI."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scheduler.call_schedule_common import (
    ParsedCallScheduleCsv,
    WEEKEND_ROLES,
    WeekRow,
)
from scheduler.night_call_types import (
    NightScheduleSolution,
    NightSolverConfig,
    CountMultiset,
)
from scheduler.night_policy_types import (
    ALL_POLICY_CRITERIA,
    CRITERION_STROKE,
    NightPolicyCounts,
    NightPolicyWeights,
    NightPolicySolveResult,
    criteria_counts_for_solution,
)
from parafrost_scheduler.cnf_builder import CnfBuilder
from parafrost_scheduler.night_solver import (
    NightCnfVarMap,
    build_night_cnf,
    extract_solution,
    solve_night_schedule_parafrost_at_limit,
    solve_night_schedule_parafrost_incremental,
    weighted_upper_bound,
)
from parafrost_scheduler.parafrost_runner import ParaFrostRunner
from parafrost_scheduler.cli import main as cli_main

PARAFROST_BINARY = (
    Path(__file__).resolve().parent.parent
    / "vendor"
    / "ParaFROST"
    / "build"
    / "cpu"
    / "bin"
    / "parafrost"
)


@pytest.fixture
def runner():
    if not PARAFROST_BINARY.exists():
        pytest.skip("ParaFROST binary not built")
    return ParaFrostRunner(PARAFROST_BINARY)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_simple_parsed() -> ParsedCallScheduleCsv:
    """1-week, 7-fellow schedule with all-Elective services."""
    fellow_names = ["A", "B", "C", "D", "E", "F", "G"]
    return ParsedCallScheduleCsv(
        fellow_names=fellow_names,
        existing_schedule_columns=WEEKEND_ROLES,
        week_rows=[
            WeekRow(
                weekday_assignments={f: "Elective" for f in fellow_names}
                | {"A": "NCC1", "B": "NCC2"},
                schedule_assignments={
                    "Weekend NCC1": "A",
                    "Weekend NCC2": "B",
                    "Weekend Stroke": "C",
                },
                raw_row=["NCC1", "NCC2", "Elective", "Elective", "Elective", "Elective", "Elective", "A", "B", "C"],
            )
        ],
        trailing_rows=[],
    )


def _simple_config_one_night_each() -> NightSolverConfig:
    """Each of the 7 fellows gets exactly 1 total night, 0 Fridays.

    1 week × 7 days = 7 nights → 7 fellows × 1 = 7. Consistent.
    No multisets, no friday-night constraints.
    """
    fellow_names = ["A", "B", "C", "D", "E", "F", "G"]
    return NightSolverConfig(
        total_nights={f: 1 for f in fellow_names},
        friday_nights={},
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(),
    )


def _make_two_week_parsed() -> ParsedCallScheduleCsv:
    """2-week, 11-fellow schedule for the hard-stroke test."""
    fellow_names = ["A", "B", "Clinic", "Stroke", "FridayWeekend", "SundayBad", "D", "E", "F", "G", "H"]

    # Week 1: Stroke fellow is on "Stroke" service; SundayBad on "MSICU" (night-blocked)
    w1_assignments = {f: "Elective" for f in fellow_names}
    w1_assignments["Stroke"] = "Stroke"
    w1_assignments["SundayBad"] = "MSICU"  # blocked

    # Week 2: SundayBad back to something blocked; everyone else Elective
    w2_assignments = {f: "Elective" for f in fellow_names}
    w2_assignments["SundayBad"] = "MSICU"  # still blocked

    return ParsedCallScheduleCsv(
        fellow_names=fellow_names,
        existing_schedule_columns=WEEKEND_ROLES,
        week_rows=[
            WeekRow(
                weekday_assignments=w1_assignments,
                schedule_assignments={
                    "Weekend NCC1": "FridayWeekend",
                    "Weekend NCC2": "B",
                    "Weekend Stroke": "C",
                },
                raw_row=list(w1_assignments.values()) + ["FridayWeekend", "B", "C"],
            ),
            WeekRow(
                weekday_assignments=w2_assignments,
                schedule_assignments={
                    "Weekend NCC1": "A",
                    "Weekend NCC2": "B",
                    "Weekend Stroke": "C",
                },
                raw_row=list(w2_assignments.values()) + ["A", "B", "C"],
            ),
        ],
        trailing_rows=[],
    )


def _two_week_config(fellow_names: list[str]) -> NightSolverConfig:
    """2 weeks × 7 days = 14 nights total across 9 non-blocked fellows.

    Stroke gets 0 nights. SundayBad is always blocked (MSICU). That leaves
    9 eligible fellows. We distribute 14 nights: give some fellows 2 and
    some 1 so they sum to 14.
    """
    # SundayBad is always MSICU-blocked and will be handled by block constraints.
    # Stroke gets 0 nights (enforced by hard criterion test).
    # Remaining 9 fellows: distribute 14 = 7×2 - 0 is wrong;
    # 14 / 9 ≈ 1.55 → mix of 1s and 2s: 5 fellows get 2, 4 fellows get 1 (5*2+4*1=14)
    eligible = [f for f in fellow_names if f not in ("Stroke", "SundayBad")]
    # Give first 5 two nights, rest one night
    totals = {}
    for i, f in enumerate(eligible):
        totals[f] = 2 if i < 5 else 1
    totals["Stroke"] = 0
    # SundayBad: blocked in both weeks so 0
    totals["SundayBad"] = 0

    return NightSolverConfig(
        total_nights=totals,
        friday_nights={},
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_build_night_cnf_produces_valid_dimacs():
    """CNF builder produces a formula with enough variables and clauses."""
    parsed = _make_simple_parsed()
    config = _simple_config_one_night_each()
    weights = NightPolicyWeights(stroke=5)

    cnf, var_map = build_night_cnf(
        parsed,
        config=config,
        hard_criteria=frozenset(),
        weights=weights,
        soft_bound=None,
    )

    # 1 week × 7 days × 7 fellows = 49 primary variables minimum
    assert cnf.num_vars >= 49
    assert cnf.num_clauses > 0
    assert var_map.num_days == 7
    assert var_map.num_fellows == 7

    # Should produce valid DIMACS
    dimacs = cnf.to_dimacs()
    assert dimacs.startswith("p cnf")
    header = dimacs.splitlines()[0]
    parts = header.split()
    assert int(parts[2]) == cnf.num_vars
    assert int(parts[3]) == cnf.num_clauses


def test_solve_at_limit_finds_valid_solution(runner):
    """Solve with exactly-one-night config; verify 7 unique assignments."""
    parsed = _make_simple_parsed()
    config = _simple_config_one_night_each()
    weights = NightPolicyWeights()

    result = solve_night_schedule_parafrost_at_limit(
        parsed,
        config=config,
        hard_criteria=frozenset(),
        weights=weights,
        runner=runner,
        emit_summary=False,
    )

    assert result.solution is not None
    assert len(result.solution.assignments_by_week) == 1
    week_assignments = result.solution.assignments_by_week[0]
    assert len(week_assignments) == 7

    # All 7 roles should be assigned, each to a distinct fellow
    fellows_assigned = list(week_assignments.values())
    assert len(set(fellows_assigned)) == 7, f"Expected 7 unique assignments, got: {fellows_assigned}"
    assert set(fellows_assigned) == set(parsed.fellow_names)


def test_solve_incremental_optimizes(runner):
    """Binary search returns optimized=True and a valid solution."""
    parsed = _make_simple_parsed()
    config = _simple_config_one_night_each()
    weights = NightPolicyWeights()

    result = solve_night_schedule_parafrost_incremental(
        parsed,
        config=config,
        hard_criteria=frozenset(),
        weights=weights,
        runner=runner,
        emit_summary=False,
    )

    assert result.optimized is True
    assert result.solution is not None
    assert len(result.solution.assignments_by_week) == 1
    assert "parafrost-incremental-soft<=" in result.tier


def test_hard_stroke_criteria_blocks_stroke_fellow(runner):
    """With CRITERION_STROKE hard, no Stroke-service fellow should be assigned nights."""
    parsed = _make_two_week_parsed()
    config = _two_week_config(parsed.fellow_names)
    weights = NightPolicyWeights(stroke=5)

    result = solve_night_schedule_parafrost_at_limit(
        parsed,
        config=config,
        hard_criteria=frozenset({CRITERION_STROKE}),
        weights=weights,
        runner=runner,
        emit_summary=False,
    )

    all_assigned = [
        fellow
        for week in result.solution.assignments_by_week
        for fellow in week.values()
    ]
    # "Stroke" fellow has weekday_assignment "Stroke" in week 1 → CRITERION_STROKE applies
    # With hard CRITERION_STROKE, they must never be assigned
    assert "Stroke" not in all_assigned, (
        f"Stroke-service fellow was assigned nights: "
        f"{[f for f in all_assigned if f == 'Stroke']}"
    )


def test_end_to_end_constraint_satisfaction(runner):
    """Verify total nights and friday nights match config exactly."""
    parsed = _make_simple_parsed()
    config = _simple_config_one_night_each()
    weights = NightPolicyWeights()

    result = solve_night_schedule_parafrost_at_limit(
        parsed,
        config=config,
        hard_criteria=frozenset(),
        weights=weights,
        runner=runner,
        emit_summary=False,
    )

    # Count total nights per fellow
    total_by_fellow: dict[str, int] = {f: 0 for f in parsed.fellow_names}
    friday_by_fellow: dict[str, int] = {f: 0 for f in parsed.fellow_names}

    from scheduler.call_schedule_common import NIGHT_ROLES
    for week_index, week_assignments in enumerate(result.solution.assignments_by_week):
        for day_of_week, role in enumerate(NIGHT_ROLES):
            fellow = week_assignments[role]
            total_by_fellow[fellow] += 1
            if day_of_week == 4:  # Friday
                friday_by_fellow[fellow] += 1

    # Check total nights match config
    for fellow_name, expected_total in config.total_nights.items():
        assert total_by_fellow[fellow_name] == expected_total, (
            f"{fellow_name}: expected {expected_total} nights, got {total_by_fellow[fellow_name]}"
        )

    # Check friday nights (config has none required in this fixture)
    for fellow_name, expected_fridays in config.friday_nights.items():
        assert friday_by_fellow[fellow_name] == expected_fridays, (
            f"{fellow_name}: expected {expected_fridays} Fridays, got {friday_by_fellow[fellow_name]}"
        )


def test_cli_reports_progress(tmp_path, capsys, monkeypatch):
    """CLI main prints 'Parsed' and 'Wrote' to stdout."""
    output_csv = tmp_path / "out.csv"

    # Build minimal fixture objects
    fellow_names = ["A", "B", "C", "D", "E", "F", "G"]
    fake_parsed = ParsedCallScheduleCsv(
        fellow_names=fellow_names,
        existing_schedule_columns=WEEKEND_ROLES,
        week_rows=[
            WeekRow(
                weekday_assignments={f: "Elective" for f in fellow_names},
                schedule_assignments={
                    "Weekend NCC1": "A",
                    "Weekend NCC2": "B",
                    "Weekend Stroke": "C",
                },
                raw_row=["Elective"] * 7 + ["A", "B", "C"],
            )
        ],
        trailing_rows=[],
    )

    fake_solution = NightScheduleSolution(
        assignments_by_week=[{role: "A" for role in ["Night Mon", "Night Tue", "Night Wed", "Night Thu", "Night Fri", "Night Sat", "Night Sun"]}]
    )
    fake_counts = NightPolicyCounts(
        by_criterion={c: 0 for c in ALL_POLICY_CRITERIA},
        weighted_total=0,
    )
    fake_result = NightPolicySolveResult(
        tier="parafrost-incremental-soft<=0",
        solution=fake_solution,
        counts=fake_counts,
        hard_criteria=frozenset(),
        optimized=True,
    )

    with (
        patch("parafrost_scheduler.cli.parse_night_call_csv", return_value=fake_parsed),
        patch("parafrost_scheduler.cli.ParaFrostRunner") as mock_runner_cls,
        patch(
            "parafrost_scheduler.cli.solve_night_schedule_parafrost_incremental",
            return_value=fake_result,
        ),
        patch("parafrost_scheduler.cli.write_night_schedule_csv"),
    ):
        # ParaFrostRunner constructor: accept any path
        mock_runner_cls.return_value = MagicMock()
        exit_code = cli_main([
            "input.csv",
            str(output_csv),
            "--parafrost-path",
            str(PARAFROST_BINARY if PARAFROST_BINARY.exists() else tmp_path / "fake_parafrost"),
        ])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Parsed" in captured.out
    assert "Wrote" in captured.out
