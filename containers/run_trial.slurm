#!/bin/bash
#SBATCH --partition=bii
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=72:00:00
#SBATCH --exclusive
#SBATCH --job-name=chi-bench
#SBATCH --output=chi-bench-%j.out
set -euo pipefail

# sbatch copies this script to a spool directory; resolve the checkout explicitly.
repo_root="${CHI_BENCH_ROOT:-${SLURM_SUBMIT_DIR:?Submit from the chi-bench directory}}"
if [[ ! -f "$repo_root/containers/run_task.sh" ]]; then
  printf 'Set CHI_BENCH_ROOT or submit from the chi-bench checkout.\n' >&2
  exit 2
fi
cd "$repo_root"
exec bash containers/run_task.sh "$@"
