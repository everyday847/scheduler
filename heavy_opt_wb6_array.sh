#!/bin/bash
# Parallel 6-hour heavy optimizations of workbook6, one defq node per variant.
# Each array task runs heavy_opt_wb6.py with a different VARIANT, expressing a
# different violation prioritization. Outputs stream to
# output_v3_wb6_<variant>.* (anytime: latest schedule always on disk).
#
#   sbatch --array=0-3 heavy_opt_wb6_array.sh
#
#SBATCH -A prescient1
#SBATCH -p defq
#SBATCH -n 1
#SBATCH -J wb6-opt
#SBATCH -o /cv/scratch/u/watkina6/scheduler/results_slurm/wb6_opt_%A_%a.out
#SBATCH -e /cv/scratch/u/watkina6/scheduler/results_slurm/wb6_opt_%A_%a.err
#SBATCH --time=06:30:00

set -euo pipefail

REPO=/cv/scratch/u/watkina6/scheduler
VENV=$REPO/.venv/bin/python

cd "$REPO"
export PYTHONPATH="src:new_approach/src"
export PYTHONUNBUFFERED=1

# Only baseline is feasible: hard_stroke / hard_sunday / hard_consec were all
# proven UNSAT on wb6 (Slurm SAT bisect, 2026-06-05). Keep the array to a single
# baseline task; re-add variants here if future workbooks make a hardening
# feasible.
VARIANTS=(baseline)
VARIANT="${VARIANTS[$SLURM_ARRAY_TASK_ID]}"

echo "Host: $(hostname)"
echo "Task: $SLURM_ARRAY_TASK_ID  Variant: $VARIANT"
echo "Start: $(date)"
echo "---"

$VENV -u heavy_opt_wb6.py --variant "$VARIANT" --max-seconds 21600

echo "---"
echo "End: $(date)"
