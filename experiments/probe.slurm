#!/bin/bash
# Slurm wrapper for a single feasibility probe. Pass the probe.py args:
#   sbatch experiments/probe.slurm --variant SBW --relax-locks --timeout 3000
#
# RoundingSat is single-threaded -> 1 CPU/job.
#
#SBATCH -A prescient1
#SBATCH -p defq
#SBATCH -n 1
#SBATCH -J probe
#SBATCH -o /cv/scratch/u/watkina6/scheduler/results_slurm/probe_%j.out
#SBATCH -e /cv/scratch/u/watkina6/scheduler/results_slurm/probe_%j.err
#SBATCH --time=02:30:00

set -euo pipefail

REPO=/cv/scratch/u/watkina6/scheduler
VENV=$REPO/.venv/bin/python

cd "$REPO"
export PYTHONPATH="src:new_approach/src"
export PYTHONUNBUFFERED=1

echo "Host: $(hostname)"
echo "Args: $*"
echo "Start: $(date)"
echo "---"

$VENV -u experiments/probe.py "$@"

echo "---"
echo "End: $(date)"
