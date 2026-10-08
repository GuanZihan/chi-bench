#!/bin/bash
set -euo pipefail

# Run the upstream PDF repair inside the SIF, using its Chromium/system libraries.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "$script_dir/.." && pwd -P)"
cd "$repo_root"
if ! command -v apptainer >/dev/null 2>&1; then
  if ! command -v module >/dev/null 2>&1; then source /etc/profile.d/modules.sh; fi
  module load apptainer/1.5.0
fi
image="${CHI_BENCH_SIF:-$repo_root/containers/chi-bench.sif}"
data_dir="$(realpath -e -- "${CHI_BENCH_DATA_DIR:-$repo_root/data}")"
export APPTAINER_IGNORE_PROOT=1

# Chromium uses a disk-backed private /tmp instead of Apptainer's small overlay.
mkdir -p -- "$repo_root/.cache"
repair_tmp="$(mktemp -d "$repo_root/.cache/chi-data-repair.XXXXXX")"
cleanup() {
  chmod -R u+rwX -- "$repair_tmp" 2>/dev/null || true
  rm -rf -- "$repair_tmp"
}
trap cleanup EXIT

# These are the benchmark's existing data corrections, not fault injection.
for script in repair_pa_t035.py regenerate_payer_request_forms.py; do
  apptainer exec --cleanenv --containall --pid --fakeroot --writable-tmpfs \
    --no-mount home,tmp,bind-paths,cwd --pwd /task-data \
    --bind "$repo_root/scripts:/repair-scripts:ro" --bind "$data_dir:/task-data" \
    --bind "$repair_tmp:/tmp" \
    "$image" /opt/chi-bench-venv/bin/python "/repair-scripts/$script" --data-dir /task-data
done
uv run --extra dev cb data verify
