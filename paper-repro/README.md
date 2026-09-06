# Agent Meltdowns Reproduction

This scaffold reproduces the paper's Codex 0.125.0 local file-not-found and missing-dependency comparisons with synthetic data. The three prompts per scenario are transcribed from Appendix A with `<FILE>` and `<SCRIPT>` replaced by local fixture names. The paper does not publish its original fixture contents, runner, raw traces, or image digest, so this is a methodology reproduction rather than a bit-for-bit artifact reproduction.

## Verified environment

- Noisy image: `containers/noisy-container_latest.sif`
- Agent: `codex-cli 0.125.0`
- Model: `gpt-5`, medium reasoning
- Authentication: `${HOME}/.codex-paper-auth/auth.json`

Check the installation and fixtures without calling a model:

```bash
bash scripts/agent_meltdown/codex_install.sh
bash scripts/agent_meltdown/validate_local_fixtures.sh
```

## Run one paid pilot

Each invocation creates a fresh workspace and refuses to overwrite a prior run:

```bash
bash scripts/agent_meltdown/run_local_trial.sh \
  file-not-found error 1 1
```

Artifacts are written to `paper-repro/runs/<run-id>/artifacts/`: `trace.jsonl`, `final.txt`, `stderr.log`, the prompt, and reproducibility metadata. API-key-shaped strings are redacted after execution. Use a dedicated, low-limit API project because a fully enabled agent can inspect its own credential file.

## Run the paper-sized local matrix

Each scenario uses 3 prompts × 6 repetitions × 2 conditions = 36 runs. Both implemented scenarios total 72 billable runs. Submit from the repository root after replacing the allocation name:

```bash
sbatch --account=<allocation> scripts/agent_meltdown/run_local_array.sbatch
```

The array limits concurrency to eight tasks. Run a single pilot and inspect its trace before submitting the full matrix.
