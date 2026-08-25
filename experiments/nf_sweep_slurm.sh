#!/bin/bash
# Fan out the NF optimization parameter matrix across Slurm defq in parallel.
# Each combo is one single-CPU job; RoundingSat is single-threaded. Results land in
# results_slurm/nfopt_<jobid>.out — grep them once the queue drains.
#
# Usage:  bash experiments/nf_sweep_slurm.sh [TIME_LIMIT_SECONDS]
#   TIME_LIMIT defaults to 3600 (1h optimize per job).
#
# Matrix (edit inline): Elec floor x NCC1 continuity mode x CCM weight.
set -euo pipefail
REPO=/cv/scratch/u/watkina6/scheduler
cd "$REPO"
mkdir -p results_slurm

TL="${1:-3600}"
SBATCH_TIME="04:00:00"

# Parameter grids
ELEC_FLOORS=(2 4 6)
NCC1_MODES=("" "--ncc1-weekday" "--ncc1-fullweek")
CCM_WEIGHTS=(50 200)

submit() {
  local elec="$1" ncc1="$2" ccmw="$3"
  local tag="e${elec}_$(echo "${ncc1:-none}" | tr -d '-')_ccm${ccmw}"
  sbatch -A prescient1 -p defq -n 1 -J "nf_${tag}" \
    --time="$SBATCH_TIME" \
    -o "$REPO/results_slurm/sweep_${tag}_%j.out" \
    -e "$REPO/results_slurm/sweep_${tag}_%j.err" \
    --wrap "cd $REPO && PYTHONPATH=src PYTHONUNBUFFERED=1 .venv/bin/python -u \
      experiments/nf_optimize.py --time-limit $TL --elec-floor $elec $ncc1 \
      --ncc-weight 10 --ccm-weight $ccmw --prefix results_slurm/out_${tag}" \
    | sed "s/^/[$tag] /"
}

n=0
for elec in "${ELEC_FLOORS[@]}"; do
  for ncc1 in "${NCC1_MODES[@]}"; do
    for ccmw in "${CCM_WEIGHTS[@]}"; do
      submit "$elec" "$ncc1" "$ccmw"
      n=$((n+1))
    done
  done
done
echo "submitted $n jobs to defq (time-limit ${TL}s each). watch: squeue -u \$USER"
