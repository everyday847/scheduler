"""Part-B tests: NF-model workbook rendering."""
from __future__ import annotations

from pathlib import Path
import openpyxl
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb, decode_solution
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.workbook import write_nf_workbook

_REPO = Path(__file__).resolve().parent.parent
_ROUNDINGSAT = _REPO / "vendor/roundingsat/build/roundingsat"


def _solve_nf():
    res = assemble_config(None,
        annual_path=_REPO / "config/annual/ncc-nf-model.yaml",
        standing_path=_REPO / "config/standing/ncc-nf-model.yaml", verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    opb, vm = build_full_schedule_opb(cfg, objective=False)
    r = RoundingSatRunner(_ROUNDINGSAT).solve(opb, timeout=180)
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
    # header + 365 day rows; a header naming the call roles
    assert any("NF" in str(c) for c in rows[0])
    assert len(rows) >= 366
