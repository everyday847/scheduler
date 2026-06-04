#!/bin/bash
# Slurm array harness for parallel SAT experiments on defq.
#
# Each array task runs ONE slurm_sat_job.py cell (RoundingSat is single-threaded,
# so 1 CPU/task) by reading its argument line from a manifest file. Generate the
# manifest with slurm_sat_aggregate.py --emit-manifest, then:
#
#   N=$(wc -l < results_slurm/manifest.txt)
#   sbatch --array=0-$((N-1)) slurm_sat_array.sh results_slurm/manifest.txt
#
# Each manifest line is the argument string passed to slurm_sat_job.py, e.g.:
#   --mode workbook --workbook workbook_partial_input5.xlsx --label wb5
#   --mode harden --rule "NCC1 Coverage" --label harden_ncc1
#   --mode kbound --k 4 --label kbound_4
#
#SBATCH -A prescient1
#SBATCH -p defq
#SBATCH -n 1
#SBATCH -J sat-array
#SBATCH -o /cv/scratch/u/watkina6/scheduler/results_slurm/slurm_%A_%a.out
#SBATCH -e /cv/scratch/u/watkina6/scheduler/results_slurm/slurm_%A_%a.err
#SBATCH --time=00:30:00

set -euo pipefail

REPO=/cv/scratch/u/watkina6/scheduler
VENV=$REPO/.venv/bin/python
MANIFEST="${1:?usage: sbatch --array=0-N slurm_sat_array.sh <manifest.txt>}"

cd "$REPO"
export PYTHONPATH="src:new_approach/src"

# Pick this task's line (0-indexed array id -> 1-indexed sed line).
LINE=$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" "$MANIFEST")

echo "Host: $(hostname)"
echo "Task: $SLURM_ARRAY_TASK_ID"
echo "Args: $LINE"
echo "Start: $(date)"
echo "---"

# shellcheck disable=SC2086
eval "$VENV slurm_sat_job.py $LINE"

echo "---"
echo "End: $(date)"
