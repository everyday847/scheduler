#!/bin/bash
# Launch the NF tractability sweep as a Slurm job array (144 cells).
# Each cell: optimize() with a 15-min limit, reports time-to-first-incumbent + verdict + eval.
#   bash experiments/nf_tractability_sweep.sh [TIME_LIMIT_SECONDS]
set -euo pipefail
REPO=/cv/scratch/u/watkina6/scheduler
cd "$REPO"
mkdir -p results_slurm
TL="${1:-900}"
N=$(PYTHONPATH=src .venv/bin/python experiments/nf_tractability_sweep.py --grid-index -1 | grep -oE '[0-9]+')
LAST=$((N-1))
echo "submitting array 0-$LAST ($N cells), time-limit ${TL}s each"
sbatch --array=0-${LAST}%64 -A prescient1 -p defq -n1 -J nfsweep --time=00:20:00 \
  -o "$REPO/results_slurm/sweep_cell_%a.out" -e "$REPO/results_slurm/sweep_cell_%a.err" \
  --wrap "cd $REPO && PYTHONPATH=src PYTHONUNBUFFERED=1 .venv/bin/python -u \
          experiments/nf_tractability_sweep.py --grid-index \$SLURM_ARRAY_TASK_ID --time-limit $TL"
