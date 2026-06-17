#!/bin/bash
# Slurm wrapper for the NF model full-year feasibility probe. Pass args:
#   sbatch experiments/probe_nf_model.slurm --timeout 7200
#
# RoundingSat is single-threaded -> 1 CPU/job.
#
#SBATCH -A prescient1
#SBATCH -p defq
#SBATCH -n 1
#SBATCH -J nfprobe
#SBATCH -o /cv/scratch/u/watkina6/scheduler/results_slurm/nfprobe_%j.out
#SBATCH -e /cv/scratch/u/watkina6/scheduler/results_slurm/nfprobe_%j.err
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

$VENV -u experiments/probe_nf_model.py "$@"

echo "---"
echo "End: $(date)"
