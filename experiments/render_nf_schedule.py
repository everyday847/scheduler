"""Render a solved NF-model schedule into human-inspectable files.

The stock workbook/CSV writers are week-oriented and assume the legacy weekend +
per-night layers — both gated off for the NF model. The meaningful NF data is the
DAY-GRANULAR call tier (`call_assignments_by_day`), so this renderer emits:

  1. <prefix>_call_ledger.csv  — one row PER DAY: date, week, dow, NCC1, NCC2, NF,
     plus that week's Backup / Weekend Backup. The primary inspection view (lets you
     eyeball coverage every day and see NF runs as consecutive same-fellow stretches).
  2. <prefix>_week_grid.txt    — a per-week ASCII grid: for each fellow, their weekly
     background rotation + a 7-char call strip (which call role they hold each day of
     the week, '.' = none). The apples-to-apples weekly calendar view.
  3. <prefix>_nf_runs.txt      — the NF assignment collapsed into runs (fellow, start
     date, end date, length) so you can verify run structure at a glance.

Usage:
  PYTHONPATH=src .venv/bin/python experiments/render_nf_schedule.py \
      [--annual config/annual/ncc-nf-model.yaml] \
      [--standing config/standing/ncc-nf-model.yaml] \
      [--timeout 120] [--prefix output_nf_model]
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import timedelta
from pathlib import Path

from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb, decode_solution
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.schedule_types import CALL_ROLES, day_of_week, day_to_week

REPO = Path(__file__).resolve().parent.parent
ROUNDINGSAT = REPO / "vendor/roundingsat/build/roundingsat"
_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_CALL_GLYPH = {"NCC1": "1", "NCC2": "2", "NF": "F"}


def _solve(annual: Path, standing: Path, timeout: float):
    res = assemble_config(None, annual_path=annual, standing_path=standing, verbose=False)
    config = res[0] if isinstance(res, tuple) else res
    opb, vm = build_full_schedule_opb(config, objective=False)
    runner = RoundingSatRunner(ROUNDINGSAT)
    r = runner.solve(opb, timeout=timeout)
    if not r.satisfiable:
        raise SystemExit("NF model is UNSAT/UNKNOWN — cannot render a schedule.")
    return config, decode_solution(r.assignment, vm)


def _write_call_ledger(path: Path, config, sol) -> None:
    start = config.night_config.horizon_start_date
    start_dow = config.start_dow
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "week", "dow", "NCC1", "NCC2", "NF",
                    "Backup", "WeekendBackup"])
        for d, holders in enumerate(sol.call_assignments_by_day):
            wk = day_to_week(d, start_dow)
            dow = _DOW[day_of_week(d, start_dow)]
            bk = (sol.backup_solution.assignments_by_week[wk]
                  if sol.backup_solution and wk < len(sol.backup_solution.assignments_by_week)
                  else {})
            w.writerow([
                (start + timedelta(days=d)).isoformat(), wk, dow,
                holders.get("NCC1", ""), holders.get("NCC2", ""), holders.get("NF", ""),
                bk.get("Backup", ""), bk.get("Weekend Backup", ""),
            ])


def _write_week_grid(path: Path, config, sol) -> None:
    start_dow = config.start_dow
    fellows = list(sol.weekly_assignments.keys())
    num_weeks = len(next(iter(sol.weekly_assignments.values())))
    num_days = len(sol.call_assignments_by_day)
    # call role per (day, fellow)
    def role_on(d, fellow):
        h = sol.call_assignments_by_day[d]
        for role in CALL_ROLES:
            if h.get(role) == fellow:
                return _CALL_GLYPH[role]
        return "."
    lines = []
    lines.append("NF MODEL — per-week grid.  Call strip = Mon..Sun glyphs: "
                 "1=NCC1 2=NCC2 F=NF .=none.  bg=weekly background rotation.")
    lines.append("")
    for wk in range(num_weeks):
        # the 7 absolute days of this week that are in-horizon
        days = [d for d in range(num_days) if day_to_week(d, start_dow) == wk]
        if not days:
            continue
        lines.append(f"=== Week {wk} ===")
        for fellow in fellows:
            bg = sol.weekly_assignments[fellow][wk] or "-"
            # build a Mon..Sun strip; pad days not in horizon with space
            strip = {day_of_week(d, start_dow): role_on(d, fellow) for d in days}
            strip_str = "".join(strip.get(i, " ") for i in range(7))
            lines.append(f"  {fellow:<16} {strip_str}   bg={bg}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_nf_runs(path: Path, config, sol) -> None:
    start = config.night_config.horizon_start_date
    lines = ["NF runs (consecutive-day NF stretches per fellow):", ""]
    cur_fellow = None
    run_start = None
    prev_d = None

    def flush(end_d):
        if cur_fellow is None:
            return
        length = end_d - run_start + 1
        lines.append(f"  {cur_fellow:<16} {(start+timedelta(days=run_start)).isoformat()} "
                     f"-> {(start+timedelta(days=end_d)).isoformat()}  ({length}d)")

    for d, holders in enumerate(sol.call_assignments_by_day):
        nf = holders.get("NF", "")
        if nf == cur_fellow and cur_fellow != "":
            prev_d = d
            continue
        # boundary
        if cur_fellow not in (None, ""):
            flush(prev_d)
        cur_fellow = nf
        run_start = d
        prev_d = d
    if cur_fellow not in (None, ""):
        flush(prev_d)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--annual", default=str(REPO / "config/annual/ncc-nf-model.yaml"))
    ap.add_argument("--standing", default=str(REPO / "config/standing/ncc-nf-model.yaml"))
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--prefix", default="output_nf_model")
    args = ap.parse_args()

    config, sol = _solve(Path(args.annual), Path(args.standing), args.timeout)
    prefix = Path(args.prefix)
    _write_call_ledger(prefix.with_name(prefix.name + "_call_ledger.csv"), config, sol)
    _write_week_grid(prefix.with_name(prefix.name + "_week_grid.txt"), config, sol)
    _write_nf_runs(prefix.with_name(prefix.name + "_nf_runs.txt"), config, sol)
    print(f"penalty={sol.soft_penalty}")
    print(f"wrote {prefix.name}_call_ledger.csv, {prefix.name}_week_grid.txt, "
          f"{prefix.name}_nf_runs.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
