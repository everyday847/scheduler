"""Diagnose why buffered-hard no-consecutive-weekends is infeasible on wb6.

Solves wb6 with SOFT consecutive-weekends (feasible), then reports, per fellow:
the consecutive-weekend pairs they work and whether each is BUFFERED (no weekend
in w-1 AND a light weekday rotation in w+2). If most pairs are unbufferable, the
hard rule is infeasible because coverage demand exceeds the light-week budget.
"""
from __future__ import annotations
import os
os.environ["SCHED_DIAG_CONSECUTIVE"] = "soft"

from collections import Counter

import run_v3_optimize_wb6 as r
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.schedule_solver import (
    build_full_schedule_opb, decode_solution, CONSECUTIVE_WEEKEND_BUFFER_SHIFTS,
)
from scheduler.call_schedule_common import WEEKEND_ROLES

config, annual = r.load_config()
runner = RoundingSatRunner(r.ROUNDINGSAT)
opb, vm = build_full_schedule_opb(config, objective=True)
print("Solving wb6 (soft consecutive) for a feasible schedule...", flush=True)
res = runner.optimize(opb, time_limit=400.0)
if not (res.satisfiable and res.assignment):
    print("NO FEASIBLE SOLUTION FOUND in budget"); raise SystemExit(1)
sol = decode_solution(res.assignment, vm)

weekly = sol.weekly_assignments
wknd = sol.weekend_solution.assignments_by_week
num_weeks = config.num_weeks
fellows = list(weekly.keys())

def works_weekend(w, f):
    if w < 0 or w >= num_weeks:
        return False
    return any(wknd[w].get(role) == f for role in WEEKEND_ROLES)

total_pairs = 0
buffered = 0
unbuf_no_light = 0
unbuf_prev_weekend = 0
pair_detail = []
for f in fellows:
    for w in range(num_weeks - 1):
        if works_weekend(w, f) and works_weekend(w + 1, f):
            total_pairs += 1
            prev_off = not works_weekend(w - 1, f)
            w2 = w + 2
            light_after = (w2 < num_weeks and
                           weekly[f][w2] in CONSECUTIVE_WEEKEND_BUFFER_SHIFTS)
            if prev_off and light_after:
                buffered += 1
            else:
                if not light_after:
                    unbuf_no_light += 1
                if not prev_off:
                    unbuf_prev_weekend += 1
                after = weekly[f][w2] if w2 < num_weeks else "(past horizon)"
                pair_detail.append((f, w, after, prev_off, light_after))

print(f"\nFeasible soft solution penalty: {sol.soft_penalty}")
print(f"Total consecutive-weekend pairs: {total_pairs}")
print(f"  BUFFERED (off before + light after): {buffered}")
print(f"  unbuffered (no light week after):    {unbuf_no_light}")
print(f"  unbuffered (worked weekend before):  {unbuf_prev_weekend}")
print(f"\nUnbufferable pairs (fellow, week w, week w+2 service, prev_off, light_after):")
for d in pair_detail:
    print(f"  {d[0]:28s} w{d[1]:<2d} after={d[2]:16s} prev_off={d[3]} light_after={d[4]}")

# Light-week budget per weekend-eligible fellow
print("\nLight-week (buffer-shift) totals per fellow in the solution:")
for f in fellows:
    c = Counter(weekly[f])
    light = sum(c.get(s, 0) for s in CONSECUTIVE_WEEKEND_BUFFER_SHIFTS)
    wknds = sum(1 for w in range(num_weeks) if works_weekend(w, f))
    if wknds:
        print(f"  {f:28s} weekends={wknds:2d}  light_weeks={light:2d}  "
              f"({ {s: c.get(s,0) for s in CONSECUTIVE_WEEKEND_BUFFER_SHIFTS if c.get(s,0)} })")
