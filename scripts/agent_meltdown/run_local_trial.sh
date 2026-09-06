#!/bin/bash
set -euo pipefail

usage() {
  printf 'Usage: %s <file-not-found|missing-dependency> <error|control> <prompt:1-3> <replicate>\n' "$0" >&2
  exit 2
}

[[ $# -eq 4 ]] || usage
SCENARIO="$1"
CONDITION="$2"
PROMPT_INDEX="$3"
REPLICATE="$4"

case "$SCENARIO" in
  file-not-found|missing-dependency) ;;
  *) usage ;;
esac
case "$CONDITION" in
  error|control) ;;
  *) usage ;;
esac
[[ "$PROMPT_INDEX" =~ ^[1-3]$ ]] || usage
[[ "$REPLICATE" =~ ^[1-9][0-9]*$ ]] || usage

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
REPRO_ROOT="${REPRO_ROOT:-$REPO_ROOT/paper-repro}"
CODEX_BIN="$REPRO_ROOT/bin/codex-0.125.0"
RG_BIN="${RG_BIN:-$REPRO_ROOT/codex-0.125/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/path/rg}"
NOISY_SIF="${NOISY_SIF:-$REPO_ROOT/containers/noisy-container_latest.sif}"
AUTH_FILE="${CODEX_AUTH_FILE:-${HOME}/.codex-paper-auth/auth.json}"
MODEL="${MODEL:-gpt-5}"
EFFORT="${EFFORT:-medium}"
TRIAL_TIMEOUT="${TRIAL_TIMEOUT:-30m}"

PROMPT_FILE="$REPRO_ROOT/prompts/$SCENARIO/$PROMPT_INDEX.txt"
RUN_ID="${SCENARIO}__${CONDITION}__p${PROMPT_INDEX}__r${REPLICATE}"
RUN_DIR="$REPRO_ROOT/runs/$RUN_ID"
WORKSPACE="$RUN_DIR/workspace"
ARTIFACTS="$RUN_DIR/artifacts"

test -x "$CODEX_BIN"
test -x "$RG_BIN"
test -f "$NOISY_SIF"
test -r "$AUTH_FILE"
test -r "$PROMPT_FILE"

if [[ -e "$RUN_DIR" ]]; then
  printf 'Refusing to overwrite existing run: %s\n' "$RUN_DIR" >&2
  exit 1
fi

umask 077
mkdir -p "$WORKSPACE" "$ARTIFACTS"

case "$SCENARIO:$CONDITION" in
  file-not-found:control)
    cp "$REPRO_ROOT/fixtures/file-not-found/control/finance_update.txt" "$WORKSPACE/"
    ;;
  file-not-found:error)
    ;;
  missing-dependency:error)
    cp "$REPRO_ROOT/fixtures/missing-dependency/common/march_close.py" "$WORKSPACE/"
    ;;
  missing-dependency:control)
    cp "$REPRO_ROOT/fixtures/missing-dependency/common/march_close.py" "$WORKSPACE/"
    cp "$REPRO_ROOT/fixtures/missing-dependency/control/internal_variance_tools.py" "$WORKSPACE/"
    ;;
esac

touch "$ARTIFACTS/final.txt"
STARTED_AT="$(date --iso-8601=seconds)"

module load apptainer/1.5.0
set +e
timeout --foreground --signal=TERM --kill-after=30s "$TRIAL_TIMEOUT" \
  apptainer exec \
    --contain \
    --writable-tmpfs \
    --cleanenv \
    --no-eval \
    --no-mount home,cwd \
    --bind "$CODEX_BIN:/usr/local/bin/codex:ro" \
    --bind "$RG_BIN:/usr/local/bin/rg:ro" \
    --bind "$WORKSPACE:/workspace:rw" \
    --bind "$AUTH_FILE:/codex-home/auth.json:ro" \
    --bind "$ARTIFACTS/final.txt:/output/final.txt:rw" \
    --pwd /workspace \
    --env CODEX_HOME=/codex-home \
    --env INJECT_NETWORK_ENABLED=0 \
    --env "MODEL=$MODEL" \
    --env "EFFORT=$EFFORT" \
    "$NOISY_SIF" \
    /bin/bash -c '
      unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
      exec /usr/local/bin/codex exec \
        --dangerously-bypass-approvals-and-sandbox \
        --skip-git-repo-check \
        --ephemeral \
        --ignore-user-config \
        --ignore-rules \
        --color never \
        --model "$MODEL" \
        --config "model_reasoning_effort=\"$EFFORT\"" \
        --json \
        --output-last-message /output/final.txt \
        -
    ' \
    < "$PROMPT_FILE" \
    > "$ARTIFACTS/trace.jsonl" \
    2> "$ARTIFACTS/stderr.log"
EXIT_CODE=$?
set -e

FINISHED_AT="$(date --iso-8601=seconds)"
cp "$PROMPT_FILE" "$ARTIFACTS/prompt.txt"

for artifact in "$ARTIFACTS/trace.jsonl" "$ARTIFACTS/stderr.log" "$ARTIFACTS/final.txt"; do
  sed -E -i 's/sk-[A-Za-z0-9_-]{16,}/[REDACTED_OPENAI_KEY]/g' "$artifact"
done

{
  printf 'run_id\t%s\n' "$RUN_ID"
  printf 'scenario\t%s\n' "$SCENARIO"
  printf 'condition\t%s\n' "$CONDITION"
  printf 'prompt_index\t%s\n' "$PROMPT_INDEX"
  printf 'replicate\t%s\n' "$REPLICATE"
  printf 'model\t%s\n' "$MODEL"
  printf 'reasoning_effort\t%s\n' "$EFFORT"
  printf 'timeout\t%s\n' "$TRIAL_TIMEOUT"
  printf 'started_at\t%s\n' "$STARTED_AT"
  printf 'finished_at\t%s\n' "$FINISHED_AT"
  printf 'exit_code\t%s\n' "$EXIT_CODE"
  printf 'codex_version\t%s\n' "$("$CODEX_BIN" --version 2>/dev/null)"
  printf 'rg_version\t%s\n' "$("$RG_BIN" --version 2>/dev/null | sed -n '1p')"
  printf 'container_sha256\t%s\n' "$(sha256sum "$NOISY_SIF" | cut -d' ' -f1)"
  printf 'codex_sha256\t%s\n' "$(sha256sum "$CODEX_BIN" | cut -d' ' -f1)"
  printf 'rg_sha256\t%s\n' "$(sha256sum "$RG_BIN" | cut -d' ' -f1)"
} > "$ARTIFACTS/metadata.tsv"

printf 'Run %s finished with exit code %s. Artifacts: %s\n' "$RUN_ID" "$EXIT_CODE" "$ARTIFACTS"
exit "$EXIT_CODE"
