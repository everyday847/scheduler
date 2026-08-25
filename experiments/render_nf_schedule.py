"""Render a solved NF-model schedule into a single inspectable .xlsx workbook.

Calls write_nf_workbook(sol, config, output_path) which produces:
  - Tab 1 "Fellow Schedule": weekly per-fellow rotation grid
  - Tab 2 "Call Detail": day-granular NCC1/NCC2/NF call assignments

Usage:
  PYTHONPATH=src .venv/bin/python experiments/render_nf_schedule.py \
      [--annual config/annual/ncc-nf-model.yaml] \
      [--standing config/standing/ncc-nf-model.yaml] \
      [--timeout 120] [--prefix output_nf_model]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb, decode_solution
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.workbook import write_nf_workbook

REPO = Path(__file__).resolve().parent.parent
ROUNDINGSAT = REPO / "vendor/roundingsat/build/roundingsat"


def _solve(annual: Path, standing: Path, timeout: float):
    res = assemble_config(None, annual_path=annual, standing_path=standing, verbose=False)
    config = res[0] if isinstance(res, tuple) else res
    opb, vm = build_full_schedule_opb(config, objective=False)
    runner = RoundingSatRunner(ROUNDINGSAT)
    r = runner.solve(opb, timeout=timeout)
    if not r.satisfiable:
        raise SystemExit("NF model is UNSAT/UNKNOWN — cannot render a schedule.")
    return config, decode_solution(r.assignment, vm)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--annual", default=str(REPO / "config/annual/ncc-nf-model.yaml"))
    ap.add_argument("--standing", default=str(REPO / "config/standing/ncc-nf-model.yaml"))
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--prefix", default="output_nf_model")
    args = ap.parse_args()

    config, sol = _solve(Path(args.annual), Path(args.standing), args.timeout)
    out_path = Path(args.prefix + ".xlsx")
    write_nf_workbook(sol, config, out_path)
    print(f"penalty={sol.soft_penalty}")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
