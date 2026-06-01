from datetime import date

from scheduler.call_schedule_common import ParsedCallScheduleCsv, WEEKEND_ROLES, WeekRow
from scheduler.night_call_solver import NightScheduleSolution, NightSolverConfig
from scheduler.night_call_solver_policy import (
    CRITERION_ANAESTHESIA,
    CRITERION_FRIDAY_WEEKEND_NCC1,
    CRITERION_STROKE,
    CRITERION_SUNDAY_FOLLOWING,
    NightPolicyCounts,
    NightPolicySolveResult,
    NightPolicyWeights,
    criteria_counts_for_solution,
    main,
    solve_night_schedule_policy_incremental,
    staged_policy_specs,
)


def test_policy_counts_use_configurable_stroke_weight():
    parsed = _policy_fixture()
    solution = NightScheduleSolution(
        assignments_by_week=[
            {
                "Night Mon": "Clinic",
                "Night Tue": "Stroke",
                "Night Wed": "A",
                "Night Thu": "B",
                "Night Fri": "FridayWeekend",
                "Night Sat": "D",
                "Night Sun": "SundayBad",
            },
            {
                "Night Mon": "A",
                "Night Tue": "B",
                "Night Wed": "D",
                "Night Thu": "E",
                "Night Fri": "F",
                "Night Sat": "G",
                "Night Sun": "H",
            },
        ]
    )

    counts = criteria_counts_for_solution(parsed, solution, weights=NightPolicyWeights(stroke=5))

    assert counts.by_criterion["clinic"] == 1
    assert counts.by_criterion["stroke"] == 1
    assert counts.by_criterion["friday_weekend_ncc1"] == 1
    assert counts.by_criterion["sunday_following"] == 1
    assert counts.weighted_total == 8


def test_staged_policy_specs_progressively_harden_priority_criteria():
    specs = staged_policy_specs()

    assert specs[0].name == "all-soft"
    assert specs[0].hard_criteria == frozenset()
    assert specs[1].hard_criteria == frozenset({CRITERION_SUNDAY_FOLLOWING})
    assert specs[2].hard_criteria == frozenset({CRITERION_SUNDAY_FOLLOWING, CRITERION_ANAESTHESIA})
    assert specs[3].hard_criteria == frozenset({CRITERION_SUNDAY_FOLLOWING, CRITERION_ANAESTHESIA, CRITERION_FRIDAY_WEEKEND_NCC1})
    assert specs[4].hard_criteria == frozenset(
        {CRITERION_SUNDAY_FOLLOWING, CRITERION_ANAESTHESIA, CRITERION_FRIDAY_WEEKEND_NCC1, CRITERION_STROKE}
    )


def test_policy_incremental_solver_can_make_stroke_hard():
    parsed = _policy_fixture()
    config = NightSolverConfig(
        total_nights={
            "A": 2,
            "B": 2,
            "Clinic": 2,
            "Stroke": 0,
            "FridayWeekend": 2,
            "SundayBad": 2,
            "D": 1,
            "E": 1,
            "F": 1,
            "G": 1,
            "H": 0,
        },
        friday_nights={
            "A": 0,
            "B": 0,
            "Clinic": 0,
            "Stroke": 0,
            "FridayWeekend": 0,
            "SundayBad": 0,
            "D": 1,
            "E": 1,
            "F": 0,
            "G": 0,
            "H": 0,
        },
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(),
    )

    result = solve_night_schedule_policy_incremental(
        parsed,
        config=config,
        hard_criteria={CRITERION_STROKE},
        weights=NightPolicyWeights(stroke=5),
        emit_summary=False,
    )

    assigned = {fellow for week in result.solution.assignments_by_week for fellow in week.values()}
    assert "Stroke" not in assigned
    assert result.counts.by_criterion["stroke"] == 0


def test_policy_cli_reports_single_run_progress(tmp_path, capsys, monkeypatch):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("placeholder\n", encoding="utf-8")
    parsed = _policy_fixture()
    solution = NightScheduleSolution(
        assignments_by_week=[
            {
                "Night Mon": "A",
                "Night Tue": "B",
                "Night Wed": "Clinic",
                "Night Thu": "FridayWeekend",
                "Night Fri": "D",
                "Night Sat": "E",
                "Night Sun": "F",
            },
            {
                "Night Mon": "G",
                "Night Tue": "H",
                "Night Wed": "A",
                "Night Thu": "B",
                "Night Fri": "D",
                "Night Sat": "E",
                "Night Sun": "F",
            },
        ]
    )
    result = NightPolicySolveResult(
        tier="policy-unoptimized-soft<=100",
        solution=solution,
        counts=NightPolicyCounts(by_criterion={criterion: 0 for criterion in staged_policy_specs()[0].hard_criteria}, weighted_total=3),
        hard_criteria=frozenset(),
        optimized=False,
    )
    monkeypatch.setattr("scheduler.night_call_solver_policy.parse_night_call_csv", lambda path: parsed)
    monkeypatch.setattr("scheduler.night_call_solver_policy.solve_night_schedule_policy_at_limit", lambda *args, **kwargs: result)
    monkeypatch.setattr("scheduler.night_call_solver_policy.write_night_schedule_csv", lambda *args: None)

    exit_code = main([str(input_path), str(output_path), "--no-optimize", "--max-soft", "100"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Parsed 2 schedule weeks" in stdout
    assert "Launching single policy run" in stdout
    assert "Completed single policy run" in stdout
    assert f"Wrote {output_path}" in stdout


def _policy_fixture() -> ParsedCallScheduleCsv:
    fellow_names = ["A", "B", "Clinic", "Stroke", "FridayWeekend", "SundayBad", "D", "E", "F", "G", "H"]
    return ParsedCallScheduleCsv(
        fellow_names=fellow_names,
        existing_schedule_columns=WEEKEND_ROLES,
        week_rows=[
            WeekRow(
                weekday_assignments={
                    "A": "NCC1",
                    "B": "NCC2",
                    "Clinic": "Clinic/Elective",
                    "Stroke": "Stroke",
                    "FridayWeekend": "Elective",
                    "SundayBad": "Elective",
                    "D": "Elective",
                    "E": "Elective",
                    "F": "Elective",
                    "G": "Elective",
                    "H": "Elective",
                },
                schedule_assignments={"Weekend NCC1": "FridayWeekend", "Weekend NCC2": "A", "Weekend Stroke": "B"},
                raw_row=[
                    "NCC1",
                    "NCC2",
                    "Clinic/Elective",
                    "Stroke",
                    "Elective",
                    "Elective",
                    "Elective",
                    "Elective",
                    "Elective",
                    "Elective",
                    "Elective",
                    "FridayWeekend",
                    "A",
                    "B",
                ],
            ),
            WeekRow(
                weekday_assignments={
                    "A": "Elective",
                    "B": "Elective",
                    "Clinic": "Elective",
                    "Stroke": "Elective",
                    "FridayWeekend": "Elective",
                    "SundayBad": "MSICU",
                    "D": "Elective",
                    "E": "Elective",
                    "F": "Elective",
                    "G": "Elective",
                    "H": "Elective",
                },
                schedule_assignments={"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "D"},
                raw_row=[
                    "Elective",
                    "Elective",
                    "Elective",
                    "Elective",
                    "Elective",
                    "MSICU",
                    "Elective",
                    "Elective",
                    "Elective",
                    "Elective",
                    "Elective",
                    "A",
                    "B",
                    "D",
                ],
            ),
        ],
        trailing_rows=[],
    )
