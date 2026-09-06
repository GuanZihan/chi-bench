#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
REPRO_ROOT="${REPRO_ROOT:-$REPO_ROOT/paper-repro}"
NOISY_SIF="${NOISY_SIF:-$REPO_ROOT/containers/noisy-container_latest.sif}"

module load apptainer/1.5.0

test -x "$REPRO_ROOT/bin/codex-0.125.0"
test -f "$NOISY_SIF"

apptainer exec \
  --contain \
  --writable-tmpfs \
  --cleanenv \
  --no-eval \
  --bind "$REPRO_ROOT/bin/codex-0.125.0:/usr/local/bin/codex:ro" \
  "$NOISY_SIF" \
  /usr/local/bin/codex --version
