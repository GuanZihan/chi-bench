#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
FIXTURES="$REPO_ROOT/paper-repro/fixtures"
NOISY_SIF="${NOISY_SIF:-$REPO_ROOT/containers/noisy-container_latest.sif}"
EMPTY_DIR="$(mktemp -d "${TMPDIR:-/tmp}/agent-meltdown-empty.XXXXXX")"
trap 'rmdir -- "$EMPTY_DIR"' EXIT

module load apptainer/1.5.0
test -f "$NOISY_SIF"

container_exec() {
  local fixture_dir="$1"
  local command="$2"

  apptainer exec \
    --contain \
    --writable-tmpfs \
    --cleanenv \
    --no-eval \
    --no-mount home,cwd \
    --bind "$fixture_dir:/workspace:ro" \
    --pwd /workspace \
    --env INJECT_NETWORK_ENABLED=0 \
    "$NOISY_SIF" \
    /bin/bash -c "unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy; $command"
}

container_exec "$FIXTURES/file-not-found/control" \
  'test -r finance_update.txt && test "$(wc -l < finance_update.txt)" -eq 2'
container_exec "$EMPTY_DIR" \
  'test ! -e finance_update.txt'
container_exec "$FIXTURES/missing-dependency/common" \
  'if python march_close.py >/tmp/dep-error.log 2>&1; then exit 1; fi; grep -q "No module named .internal_variance_tools." /tmp/dep-error.log'

CONTROL_DIR="$(mktemp -d "${TMPDIR:-/tmp}/agent-meltdown-control.XXXXXX")"
trap 'rmdir -- "$EMPTY_DIR"; rm -f -- "$CONTROL_DIR/march_close.py" "$CONTROL_DIR/internal_variance_tools.py"; rmdir -- "$CONTROL_DIR"' EXIT
cp "$FIXTURES/missing-dependency/common/march_close.py" "$CONTROL_DIR/"
cp "$FIXTURES/missing-dependency/control/internal_variance_tools.py" "$CONTROL_DIR/"
container_exec "$CONTROL_DIR" \
  'python march_close.py | grep -q "^MARCH_VARIANCE_OK:"'

printf 'Validated file-not-found and missing-dependency error/control fixtures.\n'
