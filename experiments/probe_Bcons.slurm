#!/bin/bash
#SBATCH -A prescient1
#SBATCH -p defq
#SBATCH -n 1
#SBATCH -J probeBc
#SBATCH -o /cv/scratch/u/watkina6/scheduler/results_slurm/probeBc_%j.out
#SBATCH -e /cv/scratch/u/watkina6/scheduler/results_slurm/probeBc_%j.err
#SBATCH --time=02:30:00
set -euo pipefail
REPO=/cv/scratch/u/watkina6/scheduler
cd "$REPO"
export PYTHONPATH="src:new_approach/src"
export PYTHONUNBUFFERED=1
echo "Host: $(hostname)"; echo "Start: $(date)"; echo "---"
$REPO/.venv/bin/python -u experiments/probe_Bcons.py "$@"
echo "---"; echo "End: $(date)"
