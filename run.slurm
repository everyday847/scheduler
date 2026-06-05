#!/bin/bash
# Single Slurm wrapper for the unified scheduler CLI. Replaces the per-task
# sbatch scripts (heavy_opt_*_slurm.sh, sat_check_wb6_slurm.sh, etc.).
#
# Pass the schedule.py subcommand + args after the script name:
#   sbatch run.slurm optimize --workbook workbook_partial_input6.xlsx --max-seconds 21600
#   sbatch run.slurm sat      --workbook workbook_partial_input6.xlsx
#   sbatch run.slurm mus      --workbook workbook_partial_input6.xlsx
#
# RoundingSat is single-threaded -> 1 CPU/job.
#
#SBATCH -A prescient1
#SBATCH -p defq
#SBATCH -n 1
#SBATCH -J sched
#SBATCH -o /cv/scratch/u/watkina6/scheduler/results_slurm/sched_%j.out
#SBATCH -e /cv/scratch/u/watkina6/scheduler/results_slurm/sched_%j.err
#SBATCH --time=06:30:00

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

# shellcheck disable=SC2086
$VENV -u schedule.py "$@"

echo "---"
echo "End: $(date)"
