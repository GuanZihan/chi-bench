# chi-Bench Architecture

## Components

```
┌──────────────────────────────────────────────────────────────┐
│           chi-bench:latest (single image)                    │
│                                                              │
│   ┌─────────────────┐    ┌────────────────────────────────┐  │
│   │  Agent harness  │    │  cb serve                      │  │
│   │  (codex,        │◄──►│   • FastAPI :8023              │  │
│   │   claude-code,  │    │   • provider MCP :8020         │  │
│   │   openclaw, ...)│    │   • payer MCP :8100            │  │
│   └─────────────────┘    │   • CM MCP :8200               │  │
│                          └────────────────────────────────┘  │
│                                                              │
│   ┌─────────────────┐                                        │
│   │  Verifier       │ ──► writes /logs/artifacts/...         │
│   │  (WorkspaceJudge│                                        │
│   │   on Claude     │                                        │
│   │   Opus 4.7)     │                                        │
│   └─────────────────┘                                        │
└──────────────────────────────────────────────────────────────┘
```

## Layers (`src/chi_bench/`)

- **core/** — domain models (`PriorAuthCase`, `CMOutreachTask`, ...), state machines, world store.
- **services/** — ~29 HTTP/MCP-backed domain services (chart, coverage, intake, p2p, ...).
- **server/** — FastAPI app exposing the services as REST endpoints under `/api/...`.
- **mcp/** — three MCP servers wrapping the services (provider, payer, CM).
- **conversation/** — patient simulator + P2P-session orchestration.
- **verifier/** — pluggable judge (WorkspaceJudge by default) + per-stage rubrics.
- **experiment/** — Harbor-driven trial runner + 7 agent harnesses + `dual-pa-e2e`.

## Trial lifecycle

> Flag-by-flag CLI reference: [`docs/cli.md`](cli.md).

1. `cb experiment run -f <config>` shells out to Harbor.
2. Harbor spawns one container per trial via `ChiBenchDockerEnvironment`, `ChiBenchModalEnvironment`, or `ChiBenchApptainerEnvironment`.
3. The container entrypoint:
   - reads `CHI_BENCH_TASK_ID` and exports the task-specific `CHI_BENCH_FIXTURES_DIR` (the shared entrypoint does not expose a `/fixtures` alias to the agent);
   - starts the unified server (HTTP + 3 MCP threads);
   - waits for all four endpoints to accept traffic;
   - exec's the agent harness's CLI.
4. Agent harness runs the agent against the MCP tools.
5. After the agent stops (success / timeout / abstain), Harbor invokes the verifier in the same container.
6. Verifier writes `verifier/scorecard.json` and `verifier/verdicts.json`; Harbor produces `result.json`.

The [Apptainer backend](../containers/README.md) uses a shared SIF and per-trial
workspace/state/raw copies. Only the current task fixtures are staged, with
read-only task/world mounts. It creates the legacy `/fixtures` alias when the
verifier's `/tests` directory is uploaded. Services bind to localhost and fixed
ports, so trials are serialized per node. On teardown it stops its own process
group and removes its private runtime, retaining logs and the SIF. Slurm supplies
the resource limit; this backend does not implement per-task cgroup limits.

When the payer confirms failed member eligibility, processing stops at intake.
`determination.finalize` can use the completed intake record to set the existing
case status `denied`, with reason `ELIG_TERMED`. The agent then generates and
delivers the denial letter. Clinical review is not required for this result.

## Why the LLM judge needs Anthropic credits

The verifier always uses `claude-opus-4-7` (configurable but paper-faithful default).
See `docs/judge.md`.
