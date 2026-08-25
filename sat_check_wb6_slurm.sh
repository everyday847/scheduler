#!/bin/bash
# Gating SAT-feasibility checks for workbook6 with the new defaults
# (full NCC2 coverage, week-0-exempt Telestroke, hard no-consecutive-weekends,
# fixed clinic criterion). Runs TWO 1-CPU checks back-to-back on one defq node:
#   1. full config (hard consecutive-weekends — the shipped default)
#   2. consecutive-weekends forced SOFT (isolation: if #1 UNSAT but #2 SAT, the
#      hard consecutive-weekend constraint is the culprit)
#
#   sbatch sat_check_wb6_slurm.sh
#
#SBATCH -A prescient1
#SBATCH -p defq
#SBATCH -n 1
#SBATCH -J wb6-sat
#SBATCH -o /cv/scratch/u/watkina6/scheduler/results_slurm/wb6_sat_%j.out
#SBATCH -e /cv/scratch/u/watkina6/scheduler/results_slurm/wb6_sat_%j.err
#SBATCH --time=01:00:00

set -euo pipefail

REPO=/cv/scratch/u/watkina6/scheduler
VENV=$REPO/.venv/bin/python

cd "$REPO"
export PYTHONPATH="src:new_approach/src"
export PYTHONUNBUFFERED=1

echo "Host: $(hostname)"
echo "Start: $(date)"
echo "=============================================================="
echo "CHECK 1: wb6 full config (hard no-consecutive-weekends default)"
echo "=============================================================="
$VENV -u slurm_sat_job.py --mode workbook --workbook workbook_partial_input6.xlsx \
    --label wb6_full --sat-limit 300 --unsat-limit 1200 \
    --out results_slurm/wb6_full.json

echo ""
echo "=============================================================="
echo "CHECK 2: wb6 with consecutive-weekends forced SOFT (isolation)"
echo "=============================================================="
SCHED_DIAG_CONSECUTIVE=soft $VENV -u slurm_sat_job.py --mode workbook \
    --workbook workbook_partial_input6.xlsx \
    --label wb6_consec_soft --sat-limit 300 --unsat-limit 1200 \
    --out results_slurm/wb6_consec_soft.json

echo "---"
echo "End: $(date)"
