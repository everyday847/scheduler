# Regression Baseline — constraint-encoding refactor (clean branch)

Captured before any refactor, to assert post-refactor identical behavior.

- Repo: `/cv/scratch/u/watkina6/scheduler`
- Branch: `partial-import`
- HEAD: `d16068114e7629f1aaf15597f3632f75cdb3abf9`
- Run env: `PYTHONPATH=src`, solver `vendor/roundingsat/build/roundingsat`
- Canonical workbook: `workbook_partial_input6.xlsx` (repo root; 13 fellows,
  7 locked NCC+CCM). NOTE: docs say `new_approach/workbook_partial_input6.xlsx`
  from an older layout — the file actually lives at the repo root now.

---

## 1. Test-suite baseline (GREEN — must not regress)

Command:
```
cd /cv/scratch/u/watkina6/scheduler && PYTHONPATH=src python -m pytest tests/ -q
```
Result: **397 passed in 20.13s**. Zero failures, zero errors. No tests skipped
for a missing solver. This is the green baseline.

---

## 2. Night-criterion count baseline

### Where the counts come from
- Constants: `src/scheduler/night_policy_types.py`
  `CRITERION_ANAESTHESIA="anaesthesia"`, `CRITERION_CLINIC="clinic"`,
  `CRITERION_STROKE="stroke"`, `CRITERION_FRIDAY_WEEKEND_NCC1="friday_weekend_ncc1"`,
  `CRITERION_SUNDAY_FOLLOWING="sunday_following"`.
- `criteria_counts_for_solution(parsed, night_solution, config=...)` -> per-criterion
  counts + `weighted_total`. `print_policy_summary(...)` formats them (night-solver path).
- The full-schedule solve (`schedule.py optimize`) carries `sol.night_solution` /
  `sol.weekend_solution`; pass them through `solution_to_parsed` then
  `criteria_counts_for_solution`. The CLI itself prints only weekly/call penalty,
  NOT the per-criterion breakdown, so a small harness is needed to extract counts.

### How to run a solve that prints criterion counts
`schedule.py optimize` is the entry point. The reproducer harness
`docs/superpowers/plans/regression_criterion_fingerprint.py` (a doc artifact, no
source edits) builds the default-config OPB, runs ONE bounded RoundingSat
optimize, decodes the first/best incumbent, and prints criterion counts:
```
cd /cv/scratch/u/watkina6/scheduler && PYTHONPATH=src \
  python docs/superpowers/plans/regression_criterion_fingerprint.py \
  --workbook workbook_partial_input6.xlsx --seconds 120
```
Default config: `weekend_consecutive_hard=False`,
`night_hard_criteria=['anaesthesia','friday_weekend_ncc1']`.

### CRITICAL FINDING — time-budgeted solves are NOT reproducible
RoundingSat exposes no deterministic non-time stop (no conflict/decision/budget
limit; only `--time-limit`, which `roundingsat_runner.py` notes is unreliable on
this build — it SIGTERMs at the deadline). So a budgeted solve returns whatever
incumbent it happened to reach, which varies run-to-run. Observed:

| command (identical) | soft_total | clinic | stroke | sunday_following | weighted |
|---|---|---|---|---|---|
| `--seconds 120` run A | 20858 | 23 | 44 | 16 | 259 |
| `--seconds 120` run B | 16092 | 41 | 43 | 20 | 276 |
| stream 200s run C     | 9354  | 22 | 40 | 12 | 234 |

(anaesthesia=0 and friday_weekend_ncc1=0 in all — they are HARD in default config.)

After 200s the solve is still far from optimal (lower bound ~920 vs incumbent
~9354, `optimal=False`). Proven optimum is NOT reachable in minutes on the full
problem. => A time-budgeted criterion count is NOT a valid regression fingerprint.

### The DETERMINISTIC anchor for an encoding refactor: OPB structure
`build_full_schedule_opb(config, objective=True)` is deterministic in COUNTS
(stable across rebuilds), even though the raw OPB text ordering is not:
```
OPB constraints = 152209
OPB num_vars    = 49594
soft_violations = 20293
```
(The serialized-text sha256 differs between builds — set/dict iteration order —
so hash the COUNTS, not the text.) For an encoding refactor whose goal is
"identical behavior", assert these three counts are unchanged. This is the fast,
reproducible fingerprint to gate the refactor on, alongside the 397-test suite.

### One concrete (command, output) pair to reproduce later
```
PYTHONPATH=src python docs/superpowers/plans/regression_criterion_fingerprint.py \
  --workbook workbook_partial_input6.xlsx --seconds 120
# -> FEASIBLE incumbent: soft_total=20858 weekly=2500 call=18358 optimal=False
#    anaesthesia: 0  clinic: 23  friday_weekend_ncc1: 0  stroke: 44  sunday_following: 16
#    Weighted soft violations (night-policy): 259
```
NOTE: these counts are NON-deterministic (see table). Reproduce the
*deterministic* parts: 397-test suite + OPB counts (152209 / 49594 / 20293).

### To get a deterministic criterion fingerprint (if needed)
Run to PROVEN optimality (deterministic but slow — run on Slurm):
```
PYTHONPATH=src python schedule.py optimize \
  --workbook workbook_partial_input6.xlsx \
  --max-seconds 999999 --out-prefix regression_optimal
# wait for a step with [OPTIMAL]; that incumbent's criterion counts are stable.
```
Then feed `regression_optimal.csv`'s night columns through
`criteria_counts_for_solution`. Only the proven-optimal solution is a stable
criterion fingerprint; any early-stop incumbent is not.
