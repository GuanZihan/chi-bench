#!/bin/bash
set -euo pipefail

# Accept any cb experiment arguments; keep model/agent choice explicit.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "$script_dir/.." && pwd -P)"
cd "$repo_root"
if ! command -v apptainer >/dev/null 2>&1; then
  if ! command -v module >/dev/null 2>&1; then source /etc/profile.d/modules.sh; fi
  module load apptainer/1.5.0
fi
export APPTAINER_IGNORE_PROOT=1
export APPTAINER_CACHEDIR="${APPTAINER_CACHEDIR:-$repo_root/.apptainer-cache}"
mkdir -p -- "$APPTAINER_CACHEDIR"
exec uv run --extra dev cb experiment run "$@" --environment apptainer
