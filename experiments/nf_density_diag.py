"""Diagnostic: measure the shipped NF schedule's week-labeling vs call-day density.

Tests the user's hypothesis: are low-density "NCC weeks" the result of the
weekend-NCC1-on-Elec exemption NOT being enforced (i.e. thin weekend-only weeks
getting counted as NCC weeks, dragging the average below 5)?

For each fellow, per week, reports:
  - weekly label (NCC / Elec / MICU / ...)
  - # call-days that week, split weekday vs weekend, by role
  - whether the week's ONLY call is weekend NCC1 (the exemption case)

Then aggregates per fellow: NCC weeks, mean call-days/NCC-week, Elec weeks,
blank weeks, total service days, and how many NCC weeks are "thin" (<5 days)
or weekend-only.

Usage:
  PYTHONPATH=src .venv/bin/python experiments/nf_density_diag.py [--timeout 120]
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb,
    decode_solution,
    CALL_ROLES,
)
from parafrost_scheduler.schedule_types import day_of_week, day_to_week
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

REPO = Path(__file__).resolve().parent.parent
ROUNDINGSAT = REPO / "vendor/roundingsat/build/roundingsat"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--annual", default=str(REPO / "config/annual/ncc-nf-model.yaml"))
    ap.add_argument("--standing", default=str(REPO / "config/standing/ncc-nf-model.yaml"))
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()

    res = assemble_config(None, annual_path=Path(args.annual),
                          standing_path=Path(args.standing), verbose=False)
    config = res[0] if isinstance(res, tuple) else res
    opb, vm = build_full_schedule_opb(config, objective=False)
    runner = RoundingSatRunner(ROUNDINGSAT)
    r = runner.solve(opb, timeout=args.timeout)
    if not r.satisfiable:
        raise SystemExit("NF model UNSAT/UNKNOWN — cannot diagnose.")
    sol = decode_solution(r.assignment, vm)

    start_dow = config.start_dow
    num_days = config.num_days
    fellows = list(sol.weekly_assignments.keys())
    jr = set(config.fellow_groups.get("NCC_JR", []))
    sr = set(config.fellow_groups.get("NCC_SR", []))

    # Build per-fellow per-week call-day tallies from call_assignments_by_day.
    # week -> fellow -> list of (dow, role)
    perweek = defaultdict(lambda: defaultdict(list))
    for d, holders in enumerate(sol.call_assignments_by_day):
        w = day_to_week(d, start_dow)
        dow = day_of_week(d, start_dow)
        for role in CALL_ROLES:
            who = holders.get(role, "")
            if who:
                perweek[w][who].append((dow, role))

    num_weeks = len(sol.weekly_assignments[fellows[0]])

    for name in fellows:
        grp = "JR" if name in jr else "SR" if name in sr else "CCM"
        labels = sol.weekly_assignments[name]
        ncc_weeks = 0
        ncc_calldays = 0
        thin_ncc = 0          # NCC-labeled weeks with <5 call-days
        weekendonly_ncc = 0   # NCC-labeled weeks whose only call is weekend
        elec_weeks = 0
        elec_with_call = 0    # Elec weeks that carry weekend NCC1 (exemption in action)
        blank_weeks = 0
        total_service = 0
        for w in range(num_weeks):
            lbl = labels[w]
            calls = perweek[w].get(name, [])
            n = len(calls)
            total_service += n
            wkend = [c for c in calls if c[0] in (5, 6)]
            wkday = [c for c in calls if c[0] not in (5, 6)]
            if lbl == "NCC":
                ncc_weeks += 1
                ncc_calldays += n
                if n < 5:
                    thin_ncc += 1
                if n > 0 and not wkday:
                    weekendonly_ncc += 1
            elif lbl == "Elec":
                elec_weeks += 1
                if n > 0:
                    elec_with_call += 1
            elif lbl == "":
                blank_weeks += 1
        dens = ncc_calldays / ncc_weeks if ncc_weeks else 0.0
        print(f"{name:18s} [{grp}]  service-days={total_service:3d}  "
              f"NCC-wks={ncc_weeks:2d}  mean-calldays/NCC-wk={dens:4.2f}  "
              f"thin(<5)={thin_ncc:2d}  wkend-only-NCC={weekendonly_ncc:2d}  "
              f"Elec-wks={elec_weeks:2d}  Elec-w/call={elec_with_call:2d}  "
              f"blank={blank_weeks:2d}")

    # ---- Mechanism probe: what makes thin NCC weeks thin? ----
    # For thin NCC weeks (<5 call-days), classify each as:
    #   - NF-only (calls are all NF -> an NF run straddling the week boundary)
    #   - has-NF (mix of NF + day call)
    #   - day-only (NCC1/NCC2 only, no NF)
    print("\n--- thin-NCC-week composition (mechanism probe) ---")
    for name in fellows:
        if name in jr or name in sr:
            labels = sol.weekly_assignments[name]
            nf_only = has_nf = day_only = 0
            for w in range(num_weeks):
                if labels[w] != "NCC":
                    continue
                calls = perweek[w].get(name, [])
                if len(calls) >= 5 or not calls:
                    continue
                roles = [role for _, role in calls]
                has = "NF" in roles
                allnf = all(r == "NF" for r in roles)
                if allnf:
                    nf_only += 1
                elif has:
                    has_nf += 1
                else:
                    day_only += 1
            print(f"{name:18s}  thin NCC weeks: NF-only={nf_only:2d}  "
                  f"mixed-w/NF={has_nf:2d}  day-call-only={day_only:2d}")

    # ---- NCC1 / NCC2 / NF run-length distribution per fellow ----
    # A "run" = maximal stretch of consecutive days the SAME fellow holds the SAME
    # role. Shows directly whether NCC1 clumps into multi-day blocks or scatters.
    print("\n--- per-role consecutive-day run lengths (JR/SR) ---")
    holder_by_day = {role: [h.get(role, "") for h in sol.call_assignments_by_day]
                     for role in CALL_ROLES}
    for name in fellows:
        if name not in jr and name not in sr:
            continue
        line = []
        for role in CALL_ROLES:
            seq = holder_by_day[role]
            runs = []
            cur = 0
            for d in range(num_days):
                if seq[d] == name:
                    cur += 1
                else:
                    if cur:
                        runs.append(cur)
                    cur = 0
            if cur:
                runs.append(cur)
            hist = defaultdict(int)
            for r in runs:
                hist[r] += 1
            histstr = ",".join(f"{ln}d×{hist[ln]}" for ln in sorted(hist))
            line.append(f"{role}: {len(runs)} runs [{histstr or '-'}]")
        print(f"{name:18s}  " + "  |  ".join(line))

    print(f"\npenalty={sol.soft_penalty}")
    print(f"(solved objective={'OPT' if False else 'FALSE -> FIRST-FEASIBLE, no density/continuity objective'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
