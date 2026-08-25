"""Emit Slurm array manifests and aggregate the per-cell JSON results.

Two roles:

1. Manifest generation — turn a high-level experiment into one slurm_sat_job.py
   argument line per array task:
     python slurm_sat_aggregate.py --emit workbook   --workbook workbook_partial_input5.xlsx
     python slurm_sat_aggregate.py --emit harden     --workbook workbook_partial_input5.xlsx
     python slurm_sat_aggregate.py --emit kbound      --k 0,2,4,6,8,10,15,20
   Writes results_slurm/manifest.txt; then submit:
     N=$(wc -l < results_slurm/manifest.txt)
     sbatch --array=0-$((N-1)) slurm_sat_array.sh results_slurm/manifest.txt

2. Aggregation — roll results_slurm/*.json into a table:
     python slurm_sat_aggregate.py --report
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

RESULTS = Path("results_slurm")

# The active soft rules worth a hardening sweep (mirrors harden_experiment.DEFAULT_ORDER).
from harden_experiment import DEFAULT_ORDER


def _emit(args):
    RESULTS.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    wb_arg = f"--workbook {args.workbook}" if args.workbook else ""
    common = (wb_arg + (f" --activate {args.activate}" if args.activate else "")).strip()

    if args.emit == "workbook":
        lines.append(f"--mode workbook {common} --label wb_check".strip())
    elif args.emit == "harden":
        for rule in DEFAULT_ORDER:
            safe = rule.replace("/", "_").replace(" ", "_").replace(":", "")
            lines.append(f'--mode harden --rule "{rule}" {common} --label harden_{safe}'.strip())
    elif args.emit == "kbound":
        ks = [int(x) for x in args.k.split(",")]
        for k in ks:
            lines.append(f"--mode kbound --k {k} {common} --label kbound_{k}".strip())

    manifest = RESULTS / "manifest.txt"
    manifest.write_text("\n".join(lines) + "\n")
    print(f"Wrote {manifest} ({len(lines)} tasks). Submit with:")
    print(f"  N=$(wc -l < {manifest}); sbatch --array=0-$((N-1)) slurm_sat_array.sh {manifest}")


def _report(args):
    rows = []
    for path in sorted(glob.glob(str(RESULTS / "*.json"))):
        rows.append(json.loads(Path(path).read_text()))
    if not rows:
        print("No results in results_slurm/*.json yet.")
        return
    print(f"{'label':32s} {'mode':9s} {'state':8s} {'secs':>6s} {'penalty(tot/wk/call)':22s} extra")
    for r in rows:
        pen = "" if r.get("total") is None else f"{r['total']}({r.get('weekly')}/{r.get('call')})"
        extra = ""
        if r.get("mode") == "kbound":
            extra = f"k={r.get('k')} mismatches={r.get('mismatch_count')}"
        opt = " opt" if r.get("optimal") else ""
        print(f"{r.get('label',''):32s} {r.get('mode',''):9s} {r.get('state',''):8s} "
              f"{r.get('seconds',0):6.1f} {pen:22s} {extra}{opt}")
    # kbound summary: smallest feasible k
    kb = [r for r in rows if r.get("mode") == "kbound" and r.get("state") == "SAT"]
    if kb:
        best = min(kb, key=lambda r: r["k"])
        print(f"\nSmallest feasible mismatch bound k = {best['k']} "
              f"(actual mismatches={best.get('mismatch_count')})")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emit", choices=["workbook", "harden", "kbound"])
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--workbook", type=str, default=None)
    ap.add_argument("--activate", type=str, default=None)
    ap.add_argument("--k", type=str, default="0,2,4,6,8,10,15,20")
    args = ap.parse_args()
    if args.emit:
        _emit(args)
    if args.report:
        _report(args)
    if not args.emit and not args.report:
        ap.error("pass --emit <kind> and/or --report")


if __name__ == "__main__":
    main()
