import csv
from datetime import date

from scheduler.call_schedule_common import NIGHT_ROLES, ParsedCallScheduleCsv, WEEKEND_ROLES, WeekRow
from scheduler.night_call_solver import (
    NightSolverConfig,
    absolute_day_index,
    date_to_week_and_day,
    holiday_indices_for_config,
    is_night_blocked,
    is_night_holiday_eligible,
    is_preferred_sunday_following_service,
    solve_night_schedule,
    summarize_night_solution,
    write_night_schedule_csv,
)


def test_absolute_day_mapping_uses_june_29_2026_monday_epoch():
    assert absolute_day_index(0, 0) == 0
    assert absolute_day_index(0, 4) == 4
    assert absolute_day_index(1, 0) == 7
    assert date_to_week_and_day(date(2026, 6, 29)) == (0, 0)
    assert date_to_week_and_day(date(2026, 7, 3)) == (0, 4)
    assert date_to_week_and_day(date(2027, 5, 31)) == (48, 0)


def test_holiday_indices_match_requested_dates():
    config = NightSolverConfig()

    assert holiday_indices_for_config(config) == (4, 70, 150, 151, 178, 179, 186, 203, 231, 336)


def test_is_night_blocked_matches_weekly_service_policy():
    for service in ["Resifellow MSICU", "SICU", "Vacation", "NS SCVMC", "AAN", "Elective/NCS 2026"]:
        assert is_night_blocked(service)

    for service in ["NCC1", "NCC2", "Stroke", "Elective", "Clinic/Elective", "Anesthesia"]:
        assert not is_night_blocked(service)


def test_night_soft_rule_helpers_detect_service_preferences():
    assert is_night_holiday_eligible("NCC1")
    assert is_night_holiday_eligible("Stroke")
    assert not is_night_holiday_eligible("Elective")
    assert is_preferred_sunday_following_service("Elective")
    assert not is_preferred_sunday_following_service("Clinic/Elective")
    assert not is_preferred_sunday_following_service("Anesthesia")
    assert not is_preferred_sunday_following_service("MSICU")


def test_solve_night_schedule_satisfies_small_hard_rules_and_emits_summary(capsys):
    parsed = ParsedCallScheduleCsv(
        fellow_names=["A", "B", "C", "D", "E", "F", "G", "CCM"],
        existing_schedule_columns=WEEKEND_ROLES,
        week_rows=[
            WeekRow(
                weekday_assignments={
                    "A": "NCC1",
                    "B": "NCC2",
                    "C": "Stroke",
                    "D": "Elective",
                    "E": "Elective",
                    "F": "Elective",
                    "G": "Elective",
                    "CCM": "",
                },
                schedule_assignments={
                    "Weekend NCC1": "A",
                    "Weekend NCC2": "B",
                    "Weekend Stroke": "C",
                },
                raw_row=["NCC1", "NCC2", "Stroke", "Elective", "Elective", "Elective", "Elective", "", "A", "B", "C"],
            )
        ],
        trailing_rows=[],
    )
    config = NightSolverConfig(
        total_nights={"A": 1, "B": 1, "C": 1, "D": 1, "E": 1, "F": 1, "G": 1},
        friday_nights={"A": 1, "B": 0, "C": 0, "D": 0, "E": 0, "F": 0, "G": 0},
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset({"CCM"}),
        holiday_dates=(date(2026, 7, 3),),
    )

    solution = solve_night_schedule(parsed, config=config)

    assert len(solution.assignments_by_week) == 1
    assignments = solution.assignments_by_week[0]
    assert tuple(assignments.keys()) == NIGHT_ROLES
    assert set(assignments.values()) == {"A", "B", "C", "D", "E", "F", "G"}
    assert assignments["Night Fri"] == "A"

    stdout = capsys.readouterr().out
    assert "Night summary:" in stdout
    assert "A: total=1 friday=1" in stdout
    assert "anaesthesia nights: 0" in stdout


def test_summarize_night_solution_counts_preference_violations():
    parsed = ParsedCallScheduleCsv(
        fellow_names=["A", "B"],
        existing_schedule_columns=WEEKEND_ROLES,
        week_rows=[
            WeekRow(
                weekday_assignments={"A": "Anesthesia", "B": "Clinic/Elective"},
                schedule_assignments={"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "A"},
                raw_row=["Anesthesia", "Clinic/Elective", "A", "B", "A"],
            ),
            WeekRow(
                weekday_assignments={"A": "MSICU", "B": "Elective"},
                schedule_assignments={"Weekend NCC1": "B", "Weekend NCC2": "A", "Weekend Stroke": "B"},
                raw_row=["MSICU", "Elective", "B", "A", "B"],
            ),
        ],
        trailing_rows=[],
    )
    solution = type(
        "NightSolution",
        (),
        {
            "assignments_by_week": [
                {
                    "Night Mon": "A",
                    "Night Tue": "B",
                    "Night Wed": "A",
                    "Night Thu": "B",
                    "Night Fri": "A",
                    "Night Sat": "B",
                    "Night Sun": "A",
                },
                {
                    "Night Mon": "B",
                    "Night Tue": "A",
                    "Night Wed": "B",
                    "Night Thu": "A",
                    "Night Fri": "B",
                    "Night Sat": "A",
                    "Night Sun": "B",
                },
            ]
        },
    )()

    summary = summarize_night_solution(parsed, solution)

    assert summary.anaesthesia_nights == 4
    assert summary.clinic_nights == 3
    assert summary.friday_weekend_ncc1_violations == 2
    assert summary.sunday_following_service_violations == 1


def test_write_night_schedule_csv_appends_all_night_columns(tmp_path):
    parsed = ParsedCallScheduleCsv(
        fellow_names=["A", "B"],
        existing_schedule_columns=WEEKEND_ROLES,
        week_rows=[
            WeekRow(
                weekday_assignments={"A": "NCC1", "B": "NCC2"},
                schedule_assignments={"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "A"},
                raw_row=["NCC1", "NCC2", "A", "B", "A"],
            )
        ],
        trailing_rows=[["", "Block 1", "", "", ""]],
    )
    solution = type(
        "NightSolution",
        (),
        {
            "assignments_by_week": [
                {
                    "Night Mon": "A",
                    "Night Tue": "B",
                    "Night Wed": "A",
                    "Night Thu": "B",
                    "Night Fri": "A",
                    "Night Sat": "B",
                    "Night Sun": "A",
                }
            ]
        },
    )()
    output_path = tmp_path / "weekend_call_with_nights.csv"

    write_night_schedule_csv(parsed, solution, output_path)

    with output_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))

    assert rows[0] == ["A", "B", *WEEKEND_ROLES, *NIGHT_ROLES]
    assert rows[1][-7:] == ["A", "B", "A", "B", "A", "B", "A"]
    assert rows[-1] == ["", "Block 1", "", "", "", "", "", "", "", "", "", ""]
