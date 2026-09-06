#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
REPRO_ROOT="${REPRO_ROOT:-$REPO_ROOT/paper-repro}"
CODEX_BIN="$REPRO_ROOT/bin/codex-0.125.0"
AUTH_DIR="${CODEX_AUTH_DIR:-${HOME}/.codex-paper-auth}"

if [[ ! -x "$CODEX_BIN" ]]; then
  printf 'Codex binary is missing or not executable: %s\n' "$CODEX_BIN" >&2
  exit 1
fi

umask 077
mkdir -p "$AUTH_DIR"

read -rsp "OpenAI API key: " OPENAI_API_KEY
printf '\n'
export OPENAI_API_KEY
trap 'unset OPENAI_API_KEY' EXIT

printenv OPENAI_API_KEY |
  CODEX_HOME="$AUTH_DIR" \
  "$CODEX_BIN" login --with-api-key

CODEX_HOME="$AUTH_DIR" \
  "$CODEX_BIN" login status
