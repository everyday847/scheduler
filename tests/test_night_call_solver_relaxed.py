from datetime import date
from types import SimpleNamespace

from scheduler.call_schedule_common import NIGHT_ROLES, ParsedCallScheduleCsv, WEEKEND_ROLES, WeekRow
from scheduler.night_call_solver import NightSolverConfig, NightScheduleSolution
from scheduler.night_call_solver_relaxed import (
    _count_solution_soft_violations,
    solve_night_schedule_relaxed,
)


def test_relaxed_night_solver_uses_strict_tier_when_zero_soft_violations_are_feasible(monkeypatch):
    parsed = _small_night_fixture()
    strict_solution = SimpleNamespace(
        solution=NightScheduleSolution(
            assignments_by_week=[
                {
                    "Night Mon": "A",
                    "Night Tue": "B",
                    "Night Wed": "C",
                    "Night Thu": "D",
                    "Night Fri": "E",
                    "Night Sat": "F",
                    "Night Sun": "G",
                }
            ]
        ),
        soft_violations=0,
    )

    def fake_solve(parsed_arg, *, config, violation_limit):
        return strict_solution if violation_limit == 0 else None

    monkeypatch.setattr("scheduler.night_call_solver_relaxed._solve_with_violation_limit", fake_solve)

    result = solve_night_schedule_relaxed(
        parsed,
        config=_small_night_config(),
        relaxed_violation_limit=10,
        emit_summary=False,
    )

    assert result.tier == "strict-zero-soft-violations"


def test_relaxed_night_solver_reports_tier_and_soft_violation_count(capsys, monkeypatch):
    parsed = _small_night_fixture()
    solution = NightScheduleSolution(
        assignments_by_week=[
            {
                "Night Mon": "A",
                "Night Tue": "B",
                "Night Wed": "C",
                "Night Thu": "D",
                "Night Fri": "E",
                "Night Sat": "F",
                "Night Sun": "G",
            }
        ]
    )
    monkeypatch.setattr(
        "scheduler.night_call_solver_relaxed._solve_with_violation_limit",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "scheduler.night_call_solver_relaxed._solve_optimized_fallback",
        lambda *args, **kwargs: solution,
    )

    result = solve_night_schedule_relaxed(parsed, config=_small_night_config())

    assert result.tier == "optimized-fallback"
    stdout = capsys.readouterr().out
    assert "Tier: optimized-fallback" in stdout


def test_relaxed_night_soft_violation_count_is_zero_for_aligned_solution():
    parsed = ParsedCallScheduleCsv(
        fellow_names=["A", "B", "C", "D", "E", "F", "G"],
        existing_schedule_columns=WEEKEND_ROLES,
        week_rows=[
            WeekRow(
                weekday_assignments={
                    "A": "Elective",
                    "B": "Elective",
                    "C": "Elective",
                    "D": "Elective",
                    "E": "Elective",
                    "F": "Elective",
                    "G": "Elective",
                },
                schedule_assignments={"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "C"},
                raw_row=["Elective", "Elective", "Elective", "Elective", "Elective", "Elective", "Elective", "A", "B", "C"],
            )
        ],
        trailing_rows=[],
    )
    solution = NightScheduleSolution(
        assignments_by_week=[
            {
                "Night Mon": "A",
                "Night Tue": "B",
                "Night Wed": "C",
                "Night Thu": "D",
                "Night Fri": "E",
                "Night Sat": "F",
                "Night Sun": "G",
            }
        ]
    )

    assert _count_solution_soft_violations(parsed, solution) == 0


def test_relaxed_night_soft_violation_count_ignores_sunday_following_service_but_counts_stroke():
    parsed = ParsedCallScheduleCsv(
        fellow_names=["A", "B", "C", "D", "E", "F", "G"],
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
                    "G": "MSICU",
                },
                schedule_assignments={"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "C"},
                raw_row=["NCC1", "NCC2", "Stroke", "Elective", "Elective", "Elective", "MSICU", "A", "B", "C"],
            ),
            WeekRow(
                weekday_assignments={
                    "A": "Elective",
                    "B": "Elective",
                    "C": "Elective",
                    "D": "Elective",
                    "E": "Elective",
                    "F": "Elective",
                    "G": "Elective",
                },
                schedule_assignments={"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "C"},
                raw_row=["Elective", "Elective", "Elective", "Elective", "Elective", "Elective", "Elective", "A", "B", "C"],
            ),
        ],
        trailing_rows=[],
    )
    solution = NightScheduleSolution(
        assignments_by_week=[
            {
                "Night Mon": "A",
                "Night Tue": "B",
                "Night Wed": "C",
                "Night Thu": "D",
                "Night Fri": "E",
                "Night Sat": "F",
                "Night Sun": "G",
            },
            {
                "Night Mon": "A",
                "Night Tue": "B",
                "Night Wed": "C",
                "Night Thu": "D",
                "Night Fri": "E",
                "Night Sat": "F",
                "Night Sun": "G",
            },
        ]
    )

    assert _count_solution_soft_violations(parsed, solution) == 1


def _small_night_fixture() -> ParsedCallScheduleCsv:
    return ParsedCallScheduleCsv(
        fellow_names=["A", "B", "C", "D", "E", "F", "G"],
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
                },
                schedule_assignments={"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "C"},
                raw_row=["NCC1", "NCC2", "Stroke", "Elective", "Elective", "Elective", "Elective", "A", "B", "C"],
            )
        ],
        trailing_rows=[],
    )


def _small_night_config() -> NightSolverConfig:
    return NightSolverConfig(
        total_nights={"A": 1, "B": 1, "C": 1, "D": 1, "E": 1, "F": 1, "G": 1},
        friday_nights={"A": 1, "B": 0, "C": 0, "D": 0, "E": 0, "F": 0, "G": 0},
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(date(2026, 7, 3),),
    )
