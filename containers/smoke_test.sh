#!/bin/bash
set -euo pipefail

# Keep smoke artifacts on disk and choose a fresh directory for every invocation.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "$script_dir/.." && pwd -P)"
cd "$repo_root"
if ! command -v apptainer >/dev/null 2>&1; then
  if ! command -v module >/dev/null 2>&1; then source /etc/profile.d/modules.sh; fi
  module load apptainer/1.5.0
fi
export APPTAINER_IGNORE_PROOT=1
exec uv run --extra dev python scripts/smoke_apptainer.py \
  --image "${CHI_BENCH_SIF:-$repo_root/containers/chi-bench.sif}" \
  --output "logs/smoke/apptainer-$(date +%Y%m%d-%H%M%S)-$$" "$@"
