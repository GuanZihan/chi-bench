"""Exercise a real task runtime without calling either an agent LLM or the judge."""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import tomllib
from pathlib import Path

import httpx
from harbor.models.task.config import TaskConfig
from harbor.models.trial.paths import TrialPaths
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from chi_bench.experiment.apptainer_env import (
    ChiBenchApptainerEnvironment,
    SERVICE_PORTS,
    TRIAL_LOCK_PORT,
    task_id_for_path,
)


async def smoke(args: argparse.Namespace) -> None:
    task = args.task.resolve()
    config = TaskConfig.model_validate(tomllib.loads((task / "task.toml").read_text()))
    task_id = task_id_for_path(task, args.data_dir)
    paths = TrialPaths(trial_dir=args.output.resolve())
    paths.trial_dir.mkdir(parents=True, exist_ok=False)
    environment = ChiBenchApptainerEnvironment(
        environment_dir=task / "environment",
        environment_name=task_id,
        session_id=f"smoke-{paths.trial_dir.name}",
        trial_paths=paths,
        task_env_config=config.environment,
        network_policy=config.environment.resolve_baseline(),
        persistent_env={"CHI_BENCH_TASK_ID": task_id},
        image=str(args.image),
        data_dir=str(args.data_dir),
        keep_runtime=args.keep_runtime,
    )
    results = {"task": task_id, "llm_calls": 0, "mcp_tools": {}}
    try:
        # Startup probes, exec, and MCP tool discovery require no model credentials.
        await environment.start(force_build=False)
        # The private hosts file must expose the verifier's Docker-compatible alias.
        probe = await environment.exec("getent hosts chi-bench-server", timeout_sec=15)
        assert probe.return_code == 0 and "127.0.0.1" in probe.stdout, probe
        probe = await environment.exec(
            "test ! -e /fixtures && test -f /workspace/skills/managed-care-operations-handbook/SKILL.md "
            "&& printf smoke > /workspace/smoke-marker && cat /workspace/smoke-marker",
            timeout_sec=15,
        )
        assert probe.return_code == 0 and probe.stdout == "smoke", probe
        # Check the exact PDF engine that failed on the host, without changing data/.
        probe = await environment.exec(
            "python - <<'PY'\n"
            "from pathlib import Path\n"
            "from chi_bench.core.pdf.engine import html_to_pdf_sync\n"
            "output = Path('/logs/artifacts/smoke.pdf')\n"
            "html_to_pdf_sync('<p>chi-bench infrastructure smoke</p>', output)\n"
            "assert output.read_bytes().startswith(b'%PDF')\n"
            "PY",
            timeout_sec=60,
        )
        assert probe.return_code == 0, probe
        results["pdf_render"] = "passed"
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get("http://127.0.0.1:8023/health")
            response.raise_for_status()
        for port in (8020, 8100, 8200):
            async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert tools.tools, f"No tools on port {port}"
                    results["mcp_tools"][str(port)] = len(tools.tools)
        # The original verifier is copied only after the agent phase would have ended.
        await environment.upload_dir(task / "tests", "/tests")
        probe = await environment.exec("test -f /tests/test.sh && test -d /fixtures")
        assert probe.return_code == 0, probe
        # Export the same state surface that the native verifier reads, without judging it.
        async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
            response = await client.get("http://127.0.0.1:8023/export")
            response.raise_for_status()
            state = response.json()
            (paths.trial_dir / "artifacts/state-export.json").write_text(
                json.dumps(state, indent=2)
            )
        results["runtime_checks"] = "passed"
    finally:
        await environment.stop(delete=True)
    # A released node slot and all service ports prove normal teardown completed.
    for port in (*SERVICE_PORTS, TRIAL_LOCK_PORT):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
    results["cleanup"] = "passed"
    (paths.trial_dir / "smoke.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=Path("containers/chi-bench.sif"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--task",
        type=Path,
        default=Path("data/prior_auth_um/tasks/pa_t008_t008_o002_p01_mdreview_payer"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--keep-runtime", action="store_true")
    asyncio.run(smoke(parser.parse_args()))


if __name__ == "__main__":
    main()
