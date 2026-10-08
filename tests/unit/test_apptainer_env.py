"""Protect trial isolation, stable task staging, and teardown without a SIF."""

import asyncio
import json
import socket
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from harbor.models.task.config import TaskConfig
from harbor.models.trial.paths import TrialPaths

import chi_bench.experiment.apptainer_env as runtime
from chi_bench.experiment.config import ExperimentConfig
from chi_bench.experiment.runner import _build_harbor_command


@pytest.fixture
def environment(tmp_path):
    # Minimal fixtures exercise filesystem behavior without depending on downloaded data.
    data = tmp_path / "data"
    task = data / "prior_auth_um/tasks/example"
    (task / "fixtures").mkdir(parents=True)
    (task / "fixtures/manifest.json").write_text('{"world_id": "world"}')
    (task / "task.toml").write_text('version = "1.0"\n')
    (task / "instruction.md").write_text("Use the task tools.")
    (task / "tool_reference.md").write_text("Reference")
    (task / "tests").mkdir()
    (task / "tests/test.sh").write_text("true\n")
    handbook = data / "skills/managed-care-operations-handbook"
    handbook.mkdir(parents=True)
    (handbook / "SKILL.md").write_text("Handbook")
    world = data / "prior_auth_um/shared/worlds"
    world.mkdir(parents=True)
    (world / "world.json").write_text("{}")
    image = tmp_path / "runtime.sif"
    image.write_bytes(b"test image")
    config = TaskConfig()
    return runtime.ChiBenchApptainerEnvironment(
        environment_dir=task / "environment",
        environment_name="actava-ai/example",
        session_id="example__test",
        trial_paths=TrialPaths(trial_dir=tmp_path / "trial"),
        task_env_config=config.environment,
        image=str(image),
        data_dir=str(data),
        runtime_root=str(tmp_path / "scratch"),
    )


def test_staging_preserves_source_and_marathon_identity(tmp_path):
    data = tmp_path / "data"
    task = data / "marathon/prior_auth_um"
    task.mkdir(parents=True)
    original = 'version = "1.0"\n'
    (task / "task.toml").write_text(original)
    staged = Path(runtime.prepare_dataset(str(task), str(data)))
    assert runtime.prepare_dataset(str(task), str(data)) == str(staged)
    assert (staged / "environment").is_dir()
    assert (
        tomllib.loads((staged / "task.toml").read_text())["environment"]["env"]["CHI_BENCH_TASK_ID"]
        == "marathon/prior_auth_um"
    )
    assert (task / "task.toml").read_text() == original
    with pytest.raises(ValueError):
        runtime.task_id_for_path(tmp_path / "outside", data)


def test_concurrent_staging_publishes_complete_descriptors(environment):
    # Separate compute nodes can prepare the same shared checkout concurrently.
    data = environment._data_dir
    task = data / "prior_auth_um/tasks/example"
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda _: runtime.prepare_dataset(str(task), str(data)), range(8)))
    assert len(set(paths)) == 1
    assert tomllib.loads((Path(paths[0]) / "task.toml").read_text())["environment"]["env"]


@pytest.mark.asyncio
async def test_runtime_copies_data_and_cleans_only_private_files(environment):
    environment._prepare_runtime()
    directory = environment._runtime
    assert directory is not None
    source = environment._data_dir / "prior_auth_um/tasks/example/fixtures/manifest.json"
    copy = directory / "tasks/example/fixtures/manifest.json"
    copy.write_text("modified private copy")
    assert "world_id" in source.read_text()
    assert not (directory / "tasks/example/tests").exists()
    assert not (directory / "tasks/example/solution").exists()
    # User-level downloads and temporary PDFs belong to the private disk runtime.
    for target in ("/root", "/tmp", "/var/tmp", "/workspace"):
        mount = next(m for m in environment._mounts if m["target"] == target)
        assert Path(mount["source"]).is_relative_to(directory)
    # The private hosts copy retains node entries and restores the local service alias.
    hosts_mount = next(m for m in environment._mounts if m["target"] == "/etc/hosts")
    resolver_mount = next(m for m in environment._mounts if m["target"] == "/etc/resolv.conf")
    assert Path(hosts_mount["source"]).is_relative_to(directory)
    assert Path(resolver_mount["source"]).is_relative_to(directory)
    assert "127.0.0.1 chi-bench-server" in Path(hosts_mount["source"]).read_text()
    assert Path(resolver_mount["source"]).read_text() == Path("/etc/resolv.conf").read_text()
    manifest = json.loads(
        (environment.trial_paths.trial_dir / "apptainer-runtime.json").read_text()
    )
    assert manifest["task_id"] == "example"
    assert manifest["image_sha256"]
    await environment.stop(delete=True)
    await environment.stop(delete=True)
    assert not directory.exists()
    assert source.exists()
    assert environment._image.exists()
    assert (environment.trial_paths.trial_dir / "apptainer-runtime.json").exists()


def test_conflicting_worlds_fail_without_mutating_sources(tmp_path):
    for domain, content in zip(runtime.DOMAINS[:2], ("one", "two")):
        path = tmp_path / domain / "shared/worlds/common.json"
        path.parent.mkdir(parents=True)
        path.write_text(content)
    with pytest.raises(ValueError, match="Conflicting world copies"):
        runtime.copy_worlds(tmp_path, tmp_path / "out")


def test_existing_listener_is_not_killed(monkeypatch):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        monkeypatch.setattr(runtime, "TRIAL_LOCK_PORT", busy.getsockname()[1])
        with pytest.raises(RuntimeError, match="occupied"):
            runtime.acquire_node_slot()
        assert busy.fileno() >= 0


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("startup failure"), asyncio.CancelledError()])
async def test_failed_launch_releases_runtime_and_slot(environment, monkeypatch, error):
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    monkeypatch.setattr(runtime, "TRIAL_LOCK_PORT", port)
    monkeypatch.setattr(runtime, "SERVICE_PORTS", ())
    monkeypatch.setattr(environment, "preflight", lambda: None)
    monkeypatch.setattr(runtime.shutil, "which", lambda _: "/usr/bin/apptainer")
    launch = AsyncMock(side_effect=error)
    monkeypatch.setattr(runtime.asyncio, "create_subprocess_exec", launch)
    with pytest.raises(type(error)):
        await environment.start(force_build=False)
    # Rivanna --containall creates empty resolver files unless both are rebound explicitly.
    command = launch.call_args.args
    bind_specs = [command[index + 1] for index, value in enumerate(command) if value == "--bind"]
    assert any(spec.endswith(":/etc/hosts:ro") for spec in bind_specs)
    assert any(spec.endswith(":/etc/resolv.conf:ro") for spec in bind_specs)
    assert environment._runtime is None
    assert environment._node_slot is None
    assert list(environment._runtime_root.iterdir()) == []
    assert (environment.trial_paths.trial_dir / "artifacts/apptainer.log").exists()


def test_launch_env_filters_host_secrets(environment, monkeypatch):
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-leak")
    monkeypatch.setenv("APPTAINERENV_UNRELATED_SECRET", "must-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "judge-test-key")
    environment._persistent_env["CHI_BENCH_TASK_ID"] = "example"
    env = environment._launch_environment()
    assert "must-not-leak" not in env.values()
    assert env["APPTAINERENV_ANTHROPIC_API_KEY"] == "judge-test-key"
    assert env["APPTAINERENV_CHI_BENCH_SKIP_PORT_CLEANUP"] == "1"


def test_runner_selects_backend_and_rejects_parallel_trials(tmp_path):
    config = ExperimentConfig(dataset=str(tmp_path), environment="apptainer")
    command = _build_harbor_command(config)
    assert "chi_bench.experiment.apptainer_env:ChiBenchApptainerEnvironment" in command
    assert any(item.startswith("image=/") for item in command)
    config.concurrency = 2
    with pytest.raises(ValueError, match="concurrency: 1"):
        _build_harbor_command(config)


def test_submission_does_not_silently_accept_unadapted_backend():
    # Submission packaging has different config/provenance and is not extended here.
    from pydantic import ValidationError

    from chi_bench.experiment.submission import SubmissionRun

    with pytest.raises(ValidationError):
        SubmissionRun(environment="apptainer")


def test_entrypoint_uses_ipv4_readiness_and_longer_timeout():
    # Rivanna can resolve localhost to IPv6 while the services bind to IPv4.
    entrypoint = Path(__file__).parents[2] / "docker/entrypoint.sh"
    text = entrypoint.read_text()
    assert "http://127.0.0.1:8023/health" in text
    assert "CHI_BENCH_STARTUP_ATTEMPTS:-120" in text
    assert "http://localhost" not in text
