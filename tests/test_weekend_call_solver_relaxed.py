import csv

from scheduler.call_schedule_common import ParsedCallScheduleCsv, WEEKEND_ROLES, WeekRow
from scheduler.weekend_call_solver import WeekendSolverConfig
from scheduler.weekend_call_solver_relaxed import (
    count_core_cross_role_mismatches,
    solve_weekend_schedule_relaxed,
    write_weekend_schedule_csv,
)


def test_relaxed_solver_uses_strict_tier_when_cross_role_matches_are_feasible(capsys):
    parsed = ParsedCallScheduleCsv(
        fellow_names=["A", "B", "C", "D", "E", "F"],
        existing_schedule_columns=(),
        week_rows=[
            WeekRow(
                weekday_assignments={
                    "A": "NCC1",
                    "B": "NCC2",
                    "C": "Stroke",
                    "D": "Vacation",
                    "E": "Vacation",
                    "F": "Vacation",
                },
                schedule_assignments={},
                raw_row=["NCC1", "NCC2", "Stroke", "Vacation", "Vacation", "Vacation"],
            ),
            WeekRow(
                weekday_assignments={
                    "A": "Vacation",
                    "B": "Vacation",
                    "C": "Vacation",
                    "D": "NCC1",
                    "E": "NCC2",
                    "F": "Stroke",
                },
                schedule_assignments={},
                raw_row=["Vacation", "Vacation", "Vacation", "NCC1", "NCC2", "Stroke"],
            ),
        ],
        trailing_rows=[],
    )
    config = WeekendSolverConfig(
        ncc_totals={"A": 1, "B": 1, "D": 1, "E": 1},
        stroke_totals={"C": 1, "F": 1, "A": 0, "B": 0, "D": 0, "E": 0},
        always_stroke_eligible=frozenset({"C", "F"}),
        telestroke_stroke_eligible=frozenset(),
        stroke_only_eligible=frozenset(),
        ccm_fellows=frozenset(),
        stroke_cohort=(),
        ccm_ncc_total=None,
    )

    result = solve_weekend_schedule_relaxed(parsed, config=config)

    assert result.tier == "strict-core-match"
    assert count_core_cross_role_mismatches(parsed, result.solution) == 0
    stdout = capsys.readouterr().out
    assert "Tier: strict-core-match" in stdout
    assert "Core cross-role mismatches: 0" in stdout


def test_relaxed_solver_uses_relaxed_tier_when_strict_core_matches_are_unsat(capsys):
    parsed = ParsedCallScheduleCsv(
        fellow_names=["A", "B", "C", "D", "E", "F", "G"],
        existing_schedule_columns=(),
        week_rows=[
            WeekRow(
                weekday_assignments={"A": "NCC1", "B": "NCC2", "C": "Stroke", "D": "Elective", "E": "Elective", "F": "Vacation", "G": "Vacation"},
                schedule_assignments={},
                raw_row=["NCC1", "NCC2", "Stroke", "Elective", "Elective", "Vacation", "Vacation"],
            ),
            WeekRow(
                weekday_assignments={"A": "Vacation", "B": "Vacation", "C": "Vacation", "D": "NCC1", "E": "Vacation", "F": "Stroke", "G": "Stroke"},
                schedule_assignments={},
                raw_row=["Vacation", "Vacation", "Vacation", "NCC1", "Vacation", "Stroke", "Stroke"],
            ),
        ],
        trailing_rows=[],
    )
    config = WeekendSolverConfig(
        ncc_totals={"A": 1, "B": 1, "D": 1, "F": 1},
        stroke_totals={"C": 1, "G": 1, "A": 0, "B": 0, "D": 0, "E": 0, "F": 0},
        always_stroke_eligible=frozenset({"C", "F", "G"}),
        telestroke_stroke_eligible=frozenset(),
        stroke_only_eligible=frozenset(),
        ccm_fellows=frozenset(),
        stroke_cohort=(),
        ccm_ncc_total=None,
    )

    result = solve_weekend_schedule_relaxed(parsed, config=config, relaxed_mismatch_limit=5)

    assert result.tier == "relaxed-core-match<=5"
    mismatch_count = count_core_cross_role_mismatches(parsed, result.solution)
    assert 0 < mismatch_count <= 5
    stdout = capsys.readouterr().out
    assert "Tier: relaxed-core-match<=5" in stdout
    assert f"Core cross-role mismatches: {mismatch_count}" in stdout


def test_relaxed_solver_falls_back_to_staged_solver_when_relaxed_tier_is_unsat(capsys):
    parsed = ParsedCallScheduleCsv(
        fellow_names=["A", "B", "C", "D", "E", "F"],
        existing_schedule_columns=(),
        week_rows=[
            WeekRow(
                weekday_assignments={"A": "NCC1", "B": "Stroke", "C": "Stroke", "D": "Vacation", "E": "Vacation", "F": "Vacation"},
                schedule_assignments={},
                raw_row=["NCC1", "Stroke", "Stroke", "Vacation", "Vacation", "Vacation"],
            ),
            WeekRow(
                weekday_assignments={"A": "Vacation", "B": "Vacation", "C": "Vacation", "D": "NCC1", "E": "Stroke", "F": "Stroke"},
                schedule_assignments={},
                raw_row=["Vacation", "Vacation", "Vacation", "NCC1", "Stroke", "Stroke"],
            ),
        ],
        trailing_rows=[],
    )
    config = WeekendSolverConfig(
        ncc_totals={"A": 1, "B": 1, "D": 1, "E": 1},
        stroke_totals={"C": 1, "F": 1, "A": 0, "B": 0, "D": 0, "E": 0},
        always_stroke_eligible=frozenset({"B", "C", "E", "F"}),
        telestroke_stroke_eligible=frozenset(),
        stroke_only_eligible=frozenset(),
        ccm_fellows=frozenset(),
        stroke_cohort=(),
        ccm_ncc_total=None,
    )

    result = solve_weekend_schedule_relaxed(parsed, config=config, relaxed_mismatch_limit=1)

    assert result.tier == "staged-fallback"
    assert count_core_cross_role_mismatches(parsed, result.solution) > 1
    stdout = capsys.readouterr().out
    assert "Tier: staged-fallback" in stdout


def test_core_cross_role_mismatches_ignore_non_core_weekday_services():
    parsed = ParsedCallScheduleCsv(
        fellow_names=["A", "B", "C"],
        existing_schedule_columns=(),
        week_rows=[
            WeekRow(
                weekday_assignments={"A": "Elective", "B": "NCC1", "C": "Stroke"},
                schedule_assignments={},
                raw_row=["Elective", "NCC1", "Stroke"],
            )
        ],
        trailing_rows=[],
    )
    solution = type(
        "WeekendSolution",
        (),
        {"assignments_by_week": [{"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "C"}]},
    )()

    assert count_core_cross_role_mismatches(parsed, solution) == 1


def test_relaxed_writer_preserves_standard_weekend_output_shape(tmp_path):
    parsed = ParsedCallScheduleCsv(
        fellow_names=["A", "B", "C"],
        existing_schedule_columns=(),
        week_rows=[
            WeekRow(
                weekday_assignments={"A": "NCC1", "B": "NCC2", "C": "Stroke"},
                schedule_assignments={},
                raw_row=["NCC1", "NCC2", "Stroke"],
            )
        ],
        trailing_rows=[],
    )
    solution = type(
        "WeekendSolution",
        (),
        {"assignments_by_week": [{"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "C"}]},
    )()
    output_path = tmp_path / "weekend_relaxed.csv"

    write_weekend_schedule_csv(parsed, solution, output_path)

    with output_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))

    assert rows[0] == ["A", "B", "C", *WEEKEND_ROLES]
    assert rows[1][-3:] == ["A", "B", "C"]
