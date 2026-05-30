from datetime import date

from scheduler.call_schedule_common import ParsedCallScheduleCsv, WEEKEND_ROLES, WeekRow
from scheduler.night_call_solver import NightSolverConfig, summarize_night_solution
from scheduler.night_call_solver_bool_matrix import solve_night_schedule_bool_matrix
from scheduler.night_call_solver_bool_matrix_incremental import solve_night_schedule_bool_matrix_incremental
from scheduler.night_call_solver_incremental_relaxed import solve_night_schedule_incremental_relaxed
from scheduler.night_call_solver_optimize_relaxed import solve_night_schedule_optimize_relaxed


def test_optimize_relaxed_variant_solves_small_schedule():
    result = solve_night_schedule_optimize_relaxed(_small_night_fixture(), config=_small_night_config(), emit_summary=False)

    assert result.tier == "optimize-soft-lex"
    assert result.soft_violations == 1
    assert _summary_totals(result.solution) == (7, 0)


def test_incremental_relaxed_variant_solves_small_schedule():
    result = solve_night_schedule_incremental_relaxed(_small_night_fixture(), config=_small_night_config(), emit_summary=False)

    assert result.tier == "incremental-soft<=1"
    assert result.soft_violations == 1
    assert _summary_totals(result.solution) == (7, 0)


def test_bool_matrix_variant_solves_small_schedule():
    result = solve_night_schedule_bool_matrix(_small_night_fixture(), config=_small_night_config(), emit_summary=False)

    assert result.tier == "bool-matrix-optimize"
    assert result.soft_violations == 5
    assert _summary_totals(result.solution) == (7, 0)


def test_bool_matrix_incremental_variant_solves_small_schedule():
    result = solve_night_schedule_bool_matrix_incremental(_small_night_fixture(), config=_small_night_config(), emit_summary=False)

    assert result.tier == "bool-matrix-incremental-soft<=5"
    assert result.soft_violations == 5
    assert _summary_totals(result.solution) == (7, 0)


def _summary_totals(solution):
    summary = summarize_night_solution(_small_night_fixture(), solution)
    return sum(summary.total_nights_by_fellow.values()), summary.friday_weekend_ncc1_violations


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
        friday_nights={"A": 0, "B": 1, "C": 0, "D": 0, "E": 0, "F": 0, "G": 0},
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(date(2026, 7, 3),),
    )
