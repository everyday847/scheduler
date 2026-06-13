# Experiment job tracking (constraint additions on wb7, --relax-locks)

Workbook: workbook_partial_input7.xlsx. All runs use --relax-locks (match v6).
Config variants: config/annual/exp/exp-{S,B,W,SB,SW,BW,SBW}.yaml.

Conditions:
- S = Sunday-night-before-Vac HARD (Vac-only, sunday_following_vac criterion).
- B = SCVMC Rehab contiguous 2-week block (block_rotation, STROKE).
- W = weekend-off forbids: Aditya wks {5,38,47}, Helena 43, Harneet 16.

## Feasibility probes (submitted 2026-06-13; probe.slurm, 1.5h solve cap)
- 16196634 variant=S
- 16196635 variant=B
- 16196636 variant=W
- 16196637 variant=SB
- 16196638 variant=SW
- 16196639 variant=BW
- 16196640 variant=SBW
Read results: `grep RESULT results_slurm/probe_<jobid>.out`

## Pre-existing jobs (DO NOT disturb — prior session)
- 16192802 wb7relax6 = DEFINITIVE v6 heavy opt -> output_wb7_relax_v6.*
- 16161220 wb7relax3 = STALE pre-alignment model (kept as data point)

## Heavy opts (TBD after probes — branch logic)
Per user: if both S and B SAT together -> 1 heavy opt on SB; if SAT individually
but UNSAT together -> 2 heavy opts (S, B). Then W on top of whatever is feasible
(SW/BW/SBW) -> additional heavy opts where SAT.
Heavy opt cmd (--annual is a TOP-LEVEL arg -> BEFORE the subcommand):
  sbatch --time=12:30:00 --mem=32G run.slurm \
    --annual config/annual/exp/exp-<V>.yaml optimize \
    --workbook workbook_partial_input7.xlsx \
    --out-prefix output_wb7_exp_<V> --max-seconds 43200 \
    --preview 8,25,90,600,1800 --relax-locks
(run.slurm passes "$@" to schedule.py, so the order is schedule.py <top-level> optimize <sub>.)
