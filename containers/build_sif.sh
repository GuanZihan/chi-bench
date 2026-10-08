#!/bin/bash
set -euo pipefail

# Always use the repo as build context, including when launched from Slurm spool.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "$script_dir/.." && pwd -P)"
cd "$repo_root"
if [[ "${1:-}" == "--help" ]]; then
  printf 'Usage: bash containers/build_sif.sh [--force]\nOverrides: CHI_BENCH_SIF, BUILD_CPUS (2), BUILD_MEMORY (512M), BUILD_TMPDIR\n'
  exit 0
fi
force=0
if [[ "${1:-}" == "--force" ]]; then force=1; shift; fi
if [[ $# -ne 0 ]]; then printf 'Unexpected argument: %s\n' "$1" >&2; exit 2; fi

# Use Rivanna's module without requiring Docker or sudo.
if ! command -v apptainer >/dev/null 2>&1; then
  if ! command -v module >/dev/null 2>&1; then source /etc/profile.d/modules.sh; fi
  module load apptainer/1.5.0
fi
output="${CHI_BENCH_SIF:-$repo_root/containers/chi-bench.sif}"
mkdir -p -- "$(dirname -- "$output")"
output="$(realpath -m -- "$output")"
if [[ -e "$output" && "$force" -eq 0 ]]; then
  printf 'Using existing SIF: %s (pass --force to rebuild)\n' "$output"
  exit 0
fi

# Bound squashfs resources to avoid sizing its cache from the whole HPC node.
# Rivanna /tmp may be memory-backed and charged to an already busy allocation.
build_root="${BUILD_TMPDIR:-$repo_root/.cache/apptainer-build}"
mkdir -p -- "$build_root"
build_tmp="$(mktemp -d "$build_root/chi-sif-build.XXXXXX")"
image_tmp="$(mktemp -d "$(dirname -- "$output")/.chi-sif-build.XXXXXX")"
cleanup() {
  chmod -R u+rwX -- "$build_tmp" "$image_tmp" 2>/dev/null || true
  rm -rf -- "$build_tmp" "$image_tmp"
}
trap cleanup EXIT
export APPTAINER_IGNORE_PROOT=1
export APPTAINER_TMPDIR="$build_tmp"
export APPTAINER_CACHEDIR="${APPTAINER_CACHEDIR:-$repo_root/.apptainer-cache}"
mkdir -p -- "$APPTAINER_CACHEDIR"
apptainer build --fakeroot \
  --mksquashfs-args "-processors ${BUILD_CPUS:-2} -mem ${BUILD_MEMORY:-512M}" \
  "$image_tmp/chi-bench.sif" containers/chi-bench.def

# Preserve the previous image and install only a successfully built replacement.
if [[ -e "$output" ]]; then
  mv -- "$output" "${output%.sif}.previous-$(date +%Y%m%d-%H%M%S).sif"
fi
mv -- "$image_tmp/chi-bench.sif" "$output"
sha256sum "$output" > "$output.sha256"
printf 'Built %s\nNext: bash containers/smoke_test.sh\n' "$output"
