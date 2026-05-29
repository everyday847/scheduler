import csv
from pathlib import Path

import pytest

from scheduler.call_schedule_common import (
    BLOCK_MARKER,
    ParsedCallScheduleCsv,
    WEEKEND_ROLES,
    WeekRow,
    is_weekend_blocked,
    parse_call_schedule_csv,
    weekend_roles_for_fellow,
)
from scheduler.weekend_call_solver import (
    WeekendSolverConfig,
    count_weekend_role_matches,
    solve_weekend_schedule,
    solve_weekend_schedule_staged,
    summarize_weekend_solution,
    write_weekend_schedule_csv,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_parse_call_schedule_csv_reads_real_input_shape():
    parsed = parse_call_schedule_csv(REPO_ROOT / "weekend_call.csv")

    assert len(parsed.fellow_names) == 13
    assert len(parsed.week_rows) == 53
    assert parsed.existing_schedule_columns == ()
    assert parsed.trailing_rows == []
    assert parsed.week_rows[0].weekday_assignments["Cindy Wong"] == "Resifellow MSICU"


def test_parse_call_schedule_csv_excludes_block_marker(tmp_path):
    path = tmp_path / "weekend_call.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["A", "B"])
        writer.writerow(["NCC1", "Stroke"])
        writer.writerow(["", BLOCK_MARKER])

    parsed = parse_call_schedule_csv(path)

    assert len(parsed.week_rows) == 1
    assert len(parsed.trailing_rows) == 1
    assert BLOCK_MARKER in parsed.trailing_rows[0]


def test_is_weekend_blocked_matches_explicit_service_policy():
    for service in ["Vacation", "Elective/NCS 2026", "SICU", "Elective/SICU", "MSICU", "Resifellow MSICU"]:
        assert is_weekend_blocked(service, is_ccm_fellow=False)

    for service in ["NCC1", "NCC2", "Stroke", "Telestroke", "Telestroke/Clinic", "Elective"]:
        assert not is_weekend_blocked(service, is_ccm_fellow=False)

    assert not is_weekend_blocked("", is_ccm_fellow=False)
    assert is_weekend_blocked("", is_ccm_fellow=True)


def test_weekend_roles_for_fellow_enforces_stroke_eligibility_rules():
    assert weekend_roles_for_fellow("Aditya Srivatsan", "Elective") == {
        "Weekend NCC1",
        "Weekend NCC2",
        "Weekend Stroke",
    }
    assert weekend_roles_for_fellow("Jinyuan Liu", "Stroke") == {
        "Weekend NCC1",
        "Weekend NCC2",
        "Weekend Stroke",
    }
    assert weekend_roles_for_fellow("Jinyuan Liu", "Elective") == {"Weekend NCC1", "Weekend NCC2"}
    assert weekend_roles_for_fellow("Raya Aliakbar", "Stroke") == {
        "Weekend NCC1",
        "Weekend NCC2",
        "Weekend Stroke",
    }
    assert weekend_roles_for_fellow("Raya Aliakbar", "Telestroke/Clinic") == {"Weekend NCC1", "Weekend NCC2"}
    assert weekend_roles_for_fellow("CCM  Fellow (Elective) 1", "") == set()


def test_weekend_roles_constants_define_three_weekend_services():
    assert WEEKEND_ROLES == ("Weekend NCC1", "Weekend NCC2", "Weekend Stroke")


def test_solve_weekend_schedule_joint_solver_satisfies_hard_rules_and_emits_summary(capsys):
    parsed = _small_weekend_fixture()
    config = WeekendSolverConfig(
        ncc_totals={
            "A": 1,
            "B": 1,
            "D": 1,
            "E": 1,
        },
        stroke_totals={
            "C": 1,
            "F": 1,
            "A": 0,
            "B": 0,
            "D": 0,
            "E": 0,
        },
        always_stroke_eligible=frozenset({"C", "F"}),
        telestroke_stroke_eligible=frozenset(),
        stroke_only_eligible=frozenset(),
        ccm_fellows=frozenset(),
        stroke_cohort=(),
        ccm_ncc_total=None,
    )

    solution = solve_weekend_schedule(parsed, config=config)

    assert len(solution.assignments_by_week) == 2
    assert solution.assignments_by_week[0] == {"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "C"}
    assert solution.assignments_by_week[1] == {"Weekend NCC1": "D", "Weekend NCC2": "E", "Weekend Stroke": "F"}
    assert count_weekend_role_matches(parsed, solution) == 6

    stdout = capsys.readouterr().out
    assert "Weekend summary:" in stdout
    assert "total same-service matches: 6" in stdout
    assert "total mismatches: 0" in stdout


def test_joint_weekend_solver_is_not_worse_than_staged_solver_on_same_input():
    parsed = _small_weekend_fixture()
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

    staged = solve_weekend_schedule_staged(parsed, config=config)
    joint = solve_weekend_schedule(parsed, config=config)

    assert count_weekend_role_matches(parsed, joint) >= count_weekend_role_matches(parsed, staged)


def test_summarize_weekend_solution_counts_role_specific_mismatches():
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
    solution = type("WeekendSolution", (), {"assignments_by_week": [{"Weekend NCC1": "B", "Weekend NCC2": "A", "Weekend Stroke": "C"}]})()

    summary = summarize_weekend_solution(parsed, solution)

    assert summary.total_matches == 1
    assert summary.total_mismatches == 2
    assert summary.ncc1_mismatches == 1
    assert summary.ncc2_mismatches == 1
    assert summary.stroke_mismatches == 0


def test_write_weekend_schedule_csv_appends_columns_and_preserves_trailing_rows(tmp_path):
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
        trailing_rows=[["", BLOCK_MARKER]],
    )
    solution = type(
        "WeekendSolution",
        (),
        {"assignments_by_week": [{"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "C"}]},
    )()
    output_path = tmp_path / "weekend_call_with_weekends.csv"

    write_weekend_schedule_csv(parsed, solution, output_path)

    with output_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))

    assert rows[0] == ["A", "B", "C", *WEEKEND_ROLES]
    assert rows[1][-3:] == ["A", "B", "C"]
    assert rows[-1] == ["", BLOCK_MARKER, "", "", ""]


def _small_weekend_fixture() -> ParsedCallScheduleCsv:
    return ParsedCallScheduleCsv(
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
