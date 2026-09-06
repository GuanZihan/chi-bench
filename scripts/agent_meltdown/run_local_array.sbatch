#!/bin/bash
#SBATCH --job-name=meltdown-local
#SBATCH --partition=standard
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=02:00:00
#SBATCH --array=0-71%8
#SBATCH --export=NONE
#SBATCH --output=paper-repro/slurm-logs/%A_%a.out
#SBATCH --error=paper-repro/slurm-logs/%A_%a.err

set -euo pipefail

REPO_ROOT="${REPO_ROOT_OVERRIDE:-$SLURM_SUBMIT_DIR}"
USER_HOME="$(getent passwd "$(id -un)" | cut -d: -f6)"

INDEX="$SLURM_ARRAY_TASK_ID"
REPLICATE="$((INDEX % 6 + 1))"
PROMPT_INDEX="$(((INDEX / 6) % 3 + 1))"
CONDITION_INDEX="$(((INDEX / 18) % 2))"
SCENARIO_INDEX="$((INDEX / 36))"

if [[ "$CONDITION_INDEX" -eq 0 ]]; then
  CONDITION="error"
else
  CONDITION="control"
fi

if [[ "$SCENARIO_INDEX" -eq 0 ]]; then
  SCENARIO="file-not-found"
else
  SCENARIO="missing-dependency"
fi

export REPRO_ROOT="$REPO_ROOT/paper-repro"
export CODEX_AUTH_FILE="$USER_HOME/.codex-paper-auth/auth.json"
export MODEL="gpt-5"
export EFFORT="medium"
export TRIAL_TIMEOUT="30m"

cd "$REPO_ROOT"
bash scripts/agent_meltdown/run_local_trial.sh \
  "$SCENARIO" "$CONDITION" "$PROMPT_INDEX" "$REPLICATE"
