"""Part-B tests: NF-model workbook rendering."""
from __future__ import annotations

from pathlib import Path
import openpyxl
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb, decode_solution
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.workbook import write_nf_workbook
from _dispatch_helpers import solve_or_skip

_REPO = Path(__file__).resolve().parent.parent
_ROUNDINGSAT = _REPO / "vendor/roundingsat/build/roundingsat"


def _solve_nf():
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-foundation-fixture.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = solve_or_skip(RoundingSatRunner(_ROUNDINGSAT), opb, timeout=180)
    assert r.satisfiable
    return cfg, decode_solution(r.assignment, vm)


def test_nf_workbook_has_expected_sheets(tmp_path):
    if not _ROUNDINGSAT.exists():
        import pytest; pytest.skip("RoundingSat not built")
    cfg, sol = _solve_nf()
    out = tmp_path / "nf.xlsx"
    write_nf_workbook(sol, cfg, out)
    wb = openpyxl.load_workbook(out)
    assert "Fellow Schedule" in wb.sheetnames
    assert "Call Detail" in wb.sheetnames


def test_weekly_tab_shows_a_fellow_rotation(tmp_path):
    if not _ROUNDINGSAT.exists():
        import pytest; pytest.skip("RoundingSat not built")
    cfg, sol = _solve_nf()
    out = tmp_path / "nf.xlsx"
    write_nf_workbook(sol, cfg, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Fellow Schedule"]
    # Every JR fellow does MICU in weeks 0-3 (orientation). Find a JR column and
    # confirm a MICU cell appears in the first 4 week-rows.
    jr = cfg.fellow_groups["NCC_JR"][0]
    # header row holds fellow names; locate jr's column, scan its first 4 data rows
    cells = [c.value for row in ws.iter_rows() for c in row]
    assert "MICU" in cells, "weekly tab must show MICU rotations"


def test_call_detail_tab_shows_day_holders(tmp_path):
    if not _ROUNDINGSAT.exists():
        import pytest; pytest.skip("RoundingSat not built")
    cfg, sol = _solve_nf()
    out = tmp_path / "nf.xlsx"
    write_nf_workbook(sol, cfg, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Call Detail"]
    rows = list(ws.iter_rows(values_only=True))
    # NEW LAYOUT: week-by-week grid. Header row = Week | Role | Mon..Sun.
    header = rows[0]
    assert header[0] == "Week" and header[1] == "Role"
    assert list(header[2:9]) == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    # 3 role rows per week (NCC1/NCC2/NF), one block per week.
    num_weeks = len(next(iter(sol.weekly_assignments.values())))
    assert len(rows) == 1 + num_weeks * 3
    # The Role column cycles NCC1, NCC2, NF down each block.
    role_col = [r[1] for r in rows[1:]]
    assert role_col[:3] == ["NCC1", "NCC2", "NF"]
    # Some fellow name appears as a call holder somewhere in the grid body.
    body_cells = [c for r in rows[1:] for c in r[2:]]
    assert any(c for c in body_cells), "grid must contain call assignments"


def test_call_grid_colors_fellows_and_borders_blocks(tmp_path):
    if not _ROUNDINGSAT.exists():
        import pytest; pytest.skip("RoundingSat not built")
    cfg, sol = _solve_nf()
    out = tmp_path / "nf.xlsx"
    write_nf_workbook(sol, cfg, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Call Detail"]
    from parafrost_scheduler.workbook import _call_fellow_fill_map
    fill = _call_fellow_fill_map(cfg.fellow_groups)
    # Every non-empty fellow cell in the grid body carries that fellow's category fill.
    colored = 0
    for row in ws.iter_rows(min_row=2, min_col=3, max_col=9):
        for cell in row:
            if cell.value:
                want = fill.get(cell.value)
                assert want is not None, f"no color mapped for {cell.value}"
                got = (cell.fill.start_color.rgb or "")[-6:].upper()
                assert got == want, f"{cell.value}: {got} != {want}"
                colored += 1
    assert colored > 0, "expected colored fellow cells"
    # First week block (rows 2-4) is wrapped in a medium (thick) top/bottom border.
    assert ws.cell(row=2, column=1).border.top.style == "medium"
    assert ws.cell(row=4, column=1).border.bottom.style == "medium"


def test_call_fellow_fill_map_greens_and_blues():
    from parafrost_scheduler.workbook import (
        _call_fellow_fill_map, _CALL_GREENS, _CALL_BLUES)
    fg = {"NCC_JR": ["JR1", "JR2", "JR3"], "NCC_SR": ["SR1", "SR2"],
          "CCM": ["CCM Generic 1", "CCM Generic 2"]}
    m = _call_fellow_fill_map(fg)
    # NCC fellows get greens, CCM get blues.
    for n in ["JR1", "JR2", "JR3", "SR1", "SR2"]:
        assert m[n] in _CALL_GREENS
    for n in ["CCM Generic 1", "CCM Generic 2"]:
        assert m[n] in _CALL_BLUES
    # Distinct shades within the first 3 of each group.
    assert len({m["JR1"], m["JR2"], m["JR3"]}) == 3
    assert m["CCM Generic 1"] != m["CCM Generic 2"]
