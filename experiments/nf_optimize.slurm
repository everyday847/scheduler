#!/bin/bash
# Slurm wrapper for the NF-model optimization (experiments/nf_optimize.py).
# RoundingSat is single-threaded -> 1 CPU/job. Pass nf_optimize.py args through:
#   sbatch experiments/nf_optimize.slurm --time-limit 3600 --elec-floor 6 \
#          --ncc1-fullweek --prefix output_nf_opt
#
#SBATCH -A prescient1
#SBATCH -p defq
#SBATCH -n 1
#SBATCH -J nfopt
#SBATCH -o /cv/scratch/u/watkina6/scheduler/results_slurm/nfopt_%j.out
#SBATCH -e /cv/scratch/u/watkina6/scheduler/results_slurm/nfopt_%j.err
#SBATCH --time=04:00:00

set -euo pipefail

REPO=/cv/scratch/u/watkina6/scheduler
VENV=$REPO/.venv/bin/python

cd "$REPO"
export PYTHONPATH="src"
export PYTHONUNBUFFERED=1

echo "Host: $(hostname)"
echo "Args: $*"
echo "Start: $(date)"
echo "---"

$VENV -u experiments/nf_optimize.py "$@"

echo "---"
echo "End: $(date)"
