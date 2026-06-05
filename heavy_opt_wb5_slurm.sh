#!/bin/bash
# Long-budget WB5 optimization on defq. Single 1-CPU job (RoundingSat is
# single-threaded). Incumbents stream to output_v3_wb5.* in the repo dir.
#
#   sbatch heavy_opt_wb5_slurm.sh
#
#SBATCH -A prescient1
#SBATCH -p defq
#SBATCH -n 1
#SBATCH -J wb5-opt
#SBATCH -o /cv/scratch/u/watkina6/scheduler/results_slurm/wb5_opt_%j.out
#SBATCH -e /cv/scratch/u/watkina6/scheduler/results_slurm/wb5_opt_%j.err
#SBATCH --time=04:00:00

set -euo pipefail

REPO=/cv/scratch/u/watkina6/scheduler
VENV=$REPO/.venv/bin/python

cd "$REPO"
export PYTHONPATH="src:new_approach/src"
export PYTHONUNBUFFERED=1   # stream prints to the Slurm log live (no block buffering)

echo "Host: $(hostname)"
echo "CPU: $(lscpu | grep 'Model name' | sed 's/.*: *//')"
echo "Start: $(date)"
echo "---"

$VENV -u heavy_opt_wb5.py

echo "---"
echo "End: $(date)"
