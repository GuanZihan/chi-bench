"""Apptainer trials: shared SIF, private data copies, fixed-port node isolation.

Harbor supplies command execution and file transfer. The chi-bench entrypoint
boots the simulation and probes all MCP endpoints before starting that server.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import shutil
import signal
import socket
import tempfile
from pathlib import Path

import httpx
from harbor.environments.singularity import SingularityEnvironment
from harbor.environments.singularity import singularity as harbor_singularity

from chi_bench.experiment.docker_env import FORWARDED_ENV_KEYS

DOMAINS = ("prior_auth_provider", "prior_auth_um", "care_management", "prior_auth_e2e")
SERVICE_PORTS = (8020, 8023, 8100, 8200)
TRIAL_LOCK_PORT = 18023
HOST_NETWORK_FILES = ("hosts", "resolv.conf")


def task_id_for_path(task: Path, data_dir: Path) -> str:
    """Keep marathon/<domain> intact and reject tasks outside this dataset."""
    relative = task.resolve().relative_to(data_dir.resolve())
    if len(relative.parts) == 3 and relative.parts[0] in DOMAINS:
        if relative.parts[1] == "tasks":
            return relative.parts[2]
    if len(relative.parts) == 2 and relative.parts[0] == "marathon":
        if relative.parts[1] in DOMAINS[:3]:
            return relative.as_posix()
    raise ValueError(f"Unsupported chi-bench task path: {task}")


def prepare_dataset(dataset: str, data_dir: str) -> str:
    """Stage stable Harbor descriptors without editing downloaded task files."""
    from chi_bench.experiment.runner import _inject_modal_task_id

    source = Path(dataset).expanduser().resolve(strict=True)
    data = Path(data_dir).expanduser().resolve(strict=True)
    single = (source / "task.toml").is_file()
    tasks = [source] if single else sorted(p.parent for p in source.glob("*/task.toml"))
    if not tasks:
        raise ValueError(f"No task.toml files under {source}")
    # Include descriptor contents so a changed dataset gets a fresh staged directory.
    fingerprint = hashlib.sha256(str(source).encode())
    for task in tasks:
        fingerprint.update(str(task).encode())
        fingerprint.update((task / "task.toml").read_bytes())
    root = data.parent / ".cache" / "apptainer-tasks" / fingerprint.hexdigest()[:16]
    for task in tasks:
        task_id = task_id_for_path(task, data)
        target = root / task.name
        (target / "environment").mkdir(parents=True, exist_ok=True)
        # Other nodes may stage the same task simultaneously; publish atomically.
        with tempfile.NamedTemporaryFile(mode="w", dir=target, delete=False) as handle:
            handle.write(_inject_modal_task_id((task / "task.toml").read_text(), task_id))
        Path(handle.name).replace(target / "task.toml")
        # Harbor uploads tests/solution only during their respective phases.
        for name in ("instruction.md", "tests", "solution", "tool_reference.md", "README.md"):
            original, link = task / name, target / name
            if original.exists() and not link.is_symlink() and not link.exists():
                try:
                    link.symlink_to(original, target_is_directory=original.is_dir())
                except FileExistsError:
                    if link.resolve() != original.resolve():
                        raise
    return str(root / source.name if single else root)


def acquire_node_slot() -> socket.socket:
    """Fail early on occupied ports; leave every existing listener untouched."""
    slot = socket.socket()
    busy_port = TRIAL_LOCK_PORT
    try:
        slot.bind(("127.0.0.1", TRIAL_LOCK_PORT))
        slot.listen(1)
        for port in SERVICE_PORTS:
            busy_port = port
            with socket.socket() as probe:
                # TIME_WAIT from a completed trial must not block the next one.
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", port))
    except OSError as exc:
        slot.close()
        raise RuntimeError(
            f"chi-bench port {busy_port} is occupied. Run one Apptainer trial per node, "
            "or choose another node; existing listeners were left running."
        ) from exc
    return slot


def copy_worlds(data: Path, destination: Path) -> None:
    """Flatten shared worlds and reject conflicting copies."""
    destination.mkdir(parents=True)
    for domain in DOMAINS:
        source = data / domain / ("worlds" if domain == "prior_auth_e2e" else "shared/worlds")
        for original in sorted(source.glob("*.json")):
            target = destination / original.name
            if target.exists() and target.read_bytes() != original.read_bytes():
                raise ValueError(f"Conflicting world copies: {original.name}")
            shutil.copy2(original, target)


class ChiBenchApptainerEnvironment(SingularityEnvironment):
    """One process tree, writable overlay, and data copy per Harbor trial."""

    def __init__(
        self,
        *args,
        image: str = "containers/chi-bench.sif",
        data_dir: str = "data",
        runtime_root: str | None = None,
        keep_runtime: bool = False,
        startup_timeout_sec: int = 600,
        **kwargs,
    ) -> None:
        self._image = Path(image).expanduser().resolve()
        self._data_dir = Path(data_dir).expanduser().resolve()
        self._runtime_root = (
            Path(runtime_root).expanduser().resolve()
            if runtime_root
            else self._data_dir.parent / ".cache/apptainer-runtime"
        )
        self._keep_runtime = keep_runtime
        self._startup_timeout = startup_timeout_sec
        self._runtime: Path | None = None
        self._node_slot: socket.socket | None = None
        self._process_log = None
        self._process_group: int | None = None
        # The image is built explicitly; Harbor should not create a second image cache.
        kwargs.setdefault("singularity_image_cache_dir", self._image.parent)
        super().__init__(*args, **kwargs)

    @property
    def _docker_image(self) -> str:
        return str(self._image)

    def _resolve_workdir(self) -> str:
        return "/workspace"

    @classmethod
    def preflight(cls) -> None:
        if not (shutil.which("apptainer") or shutil.which("singularity")):
            raise RuntimeError("Load Apptainer first: module load apptainer/1.5.0")

    def _source_task(self) -> tuple[str, Path]:
        task_id = self._persistent_env.get("CHI_BENCH_TASK_ID", self.environment_name)
        if task_id.startswith("marathon/"):
            candidates = [self._data_dir / task_id]
        else:
            task_id = task_id.rsplit("/", 1)[-1]
            candidates = [self._data_dir / domain / "tasks" / task_id for domain in DOMAINS]
        matches = [p for p in candidates if (p / "task.toml").is_file()]
        if len(matches) != 1:
            raise ValueError(f"Expected one source task for {task_id!r}, found {len(matches)}")
        task_id_for_path(matches[0], self._data_dir)
        return task_id, matches[0]

    def _prepare_runtime(self) -> None:
        task_id, source = self._source_task()
        handbook = self._data_dir / "skills/managed-care-operations-handbook"
        if not (handbook / "SKILL.md").is_file():
            raise FileNotFoundError(f"Download the approved handbook first: {handbook}")
        if self._runtime_root:
            self._runtime_root.mkdir(parents=True, exist_ok=True)
        self._runtime = Path(tempfile.mkdtemp(prefix="chi-trial-", dir=self._runtime_root))
        runtime = self._runtime
        task = runtime / "tasks" / task_id
        task.mkdir(parents=True)
        shutil.copytree(source / "fixtures", task / "fixtures")
        for name in ("instruction.md", "tool_reference.md"):
            if (source / name).is_file():
                shutil.copy2(source / name, task / name)
        copy_worlds(self._data_dir, runtime / "worlds")
        # Disk-backed workspaces keep large outputs out of the writable tmpfs layer.
        workspace = runtime / "workspace"
        workspace.mkdir()
        (workspace / ".venv").symlink_to("/opt/chi-bench-venv", target_is_directory=True)
        shutil.copytree(handbook, workspace / "skills/managed-care-operations-handbook")
        assets = runtime / "task-assets"
        assets.mkdir()
        reference = source / "tool_reference.md"
        if not reference.exists():
            reference = self._data_dir / "care_management/shared/tool_reference.md"
        shutil.copy2(reference, assets / "tool_reference.md")
        for name in ("raw", "state", "staging", "home", "tmp", "var-tmp"):
            (runtime / name).mkdir()
        (runtime / "state/trial-id").write_text(self.session_id)
        # Preserve node DNS while adding the Docker-compatible service alias privately.
        network = runtime / "network"
        network.mkdir()
        for name in HOST_NETWORK_FILES:
            source = Path("/etc") / name
            if not source.is_file():
                raise FileNotFoundError(f"Required host network file is missing: {source}")
            shutil.copy2(source, network / name)
        hosts = network / "hosts"
        hosts_text = hosts.read_text()
        if "chi-bench-server" not in hosts_text:
            separator = "" if hosts_text.endswith("\n") else "\n"
            hosts.write_text(f"{hosts_text}{separator}127.0.0.1 chi-bench-server\n")
        # Match the command server to the host's installed Harbor version.
        self._staging_dir = runtime / "staging"
        shutil.copy2(
            Path(harbor_singularity.__file__).with_name("server.py"), self._staging / "_hbexec.py"
        )
        mounts = [
            # Agent caches/PDFs must not fill Apptainer's small system overlay.
            ("home", "/root", False),
            ("tmp", "/tmp", False),
            ("var-tmp", "/var/tmp", False),
            ("workspace", "/workspace", False),
            ("tasks", "/opt/chi-bench/tasks", True),
            ("worlds", "/opt/chi-bench/worlds", True),
            ("state", "/tmp/chi-bench-runtime", False),
            ("raw", "/srv/chi-bench/raw", False),
            ("task-assets", "/opt/chi-bench-task-assets", False),
            ("staging", "/staging", False),
            ("network/hosts", "/etc/hosts", True),
            ("network/resolv.conf", "/etc/resolv.conf", True),
        ]
        existing = {m["target"] for m in self._mounts}
        for local, target, read_only in mounts:
            if target in existing:
                raise ValueError(f"Conflicting runtime mount: {target}")
            self._mounts.append(
                {
                    "type": "bind",
                    "source": str(runtime / local),
                    "target": target,
                    "read_only": read_only,
                }
            )
        # Direct infrastructure smoke tests also need Harbor's three output mounts.
        for name in ("agent", "verifier", "artifacts"):
            path = self.trial_paths.trial_dir / name
            path.mkdir(parents=True, exist_ok=True)
            if f"/logs/{name}" not in existing:
                self._mounts.append(
                    {"type": "bind", "source": str(path.resolve()), "target": f"/logs/{name}"}
                )
        self._persistent_env.update(
            {
                "CHI_BENCH_TASK_ID": task_id,
                "CHI_BENCH_FIXTURES_DIR": f"/opt/chi-bench/tasks/{task_id}/fixtures",
                # Harbor's exec server prepends system Python unless env overrides it.
                "PATH": "/workspace/.venv/bin:/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "VIRTUAL_ENV": "/opt/chi-bench-venv",
            }
        )
        # Save input/image provenance without any credential values.
        with self._image.open("rb") as image:
            image_hash = hashlib.file_digest(image, "sha256").hexdigest()
        inputs = {
            p.relative_to(runtime).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for root in (task, runtime / "worlds")
            for p in sorted(root.rglob("*"))
            if p.is_file()
        }
        manifest = {
            "backend": "apptainer",
            "task_id": task_id,
            "image": str(self._image),
            "image_sha256": image_hash,
            "runtime": str(runtime),
            "input_sha256": inputs,
            "hostname": socket.gethostname(),
            "slurm_job_id": os.getenv("SLURM_JOB_ID"),
            "keep_runtime": self._keep_runtime,
        }
        (self.trial_paths.trial_dir / "apptainer-runtime.json").write_text(
            json.dumps(manifest, indent=2) + "\n"
        )

    def _launch_environment(self) -> dict[str, str]:
        # Carry only host process essentials and explicitly forwarded benchmark settings.
        host = {
            k: v
            for k, v in os.environ.items()
            if k
            in {
                "PATH",
                "HOME",
                "USER",
                "LOGNAME",
                "TMPDIR",
                "APPTAINER_CACHEDIR",
                "APPTAINER_TMPDIR",
            }
        }
        host["APPTAINER_IGNORE_PROOT"] = "1"
        values = {k: os.environ[k] for k in FORWARDED_ENV_KEYS if os.environ.get(k)}
        values.update(self._persistent_env)
        values.update({"CHI_BENCH_SKIP_PORT_CLEANUP": "1", "CHI_BENCH_BIND_HOST": "127.0.0.1"})
        host.update({f"APPTAINERENV_{k}": v for k, v in values.items()})
        return host

    async def start(self, force_build: bool) -> None:
        if force_build:
            raise ValueError(
                "Build with bash containers/build_sif.sh; use --no-force-build in Harbor."
            )
        self.preflight()
        reserved = None
        try:
            self._node_slot = acquire_node_slot()
            self._prepare_runtime()
            self._sif_path = self._image
            reserved, self._server_port = self._reserve_port()
            executable = shutil.which("apptainer") or shutil.which("singularity")
            command = [
                executable,
                "exec",
                "--cleanenv",
                "--containall",
                "--pid",
                "--fakeroot",
                "--no-mount",
                "home,tmp,bind-paths,cwd",
                "--writable-tmpfs",
                "--pwd",
                "/workspace",
            ]
            for mount in self._mounts:
                if mount["type"] != "bind":
                    raise ValueError("Apptainer only supports bind mounts")
                source, target = str(mount["source"]), str(mount["target"])
                if any(c in source + target for c in (":", ",", "\n")):
                    raise ValueError("Bind paths cannot contain colons, commas or newlines")
                suffix = ":ro" if mount.get("read_only") else ""
                command += ["--bind", f"{source}:{target}{suffix}"]
            command += [
                str(self._image),
                "/usr/bin/tini",
                "-s",
                "--",
                "/usr/local/bin/entrypoint.sh",
                "/workspace/.venv/bin/python",
                "/staging/_hbexec.py",
                "--port",
                str(self._server_port),
                "--workdir",
                "/workspace",
            ]
            self._process_log = (self.trial_paths.trial_dir / "artifacts/apptainer.log").open("ab")
            reserved.close()
            reserved = None
            self._server_process = await asyncio.create_subprocess_exec(
                *command,
                stdout=self._process_log,
                stderr=asyncio.subprocess.STDOUT,
                env=self._launch_environment(),
                start_new_session=True,
            )
            self._process_group = self._server_process.pid
            self._http_client = httpx.AsyncClient(timeout=5, trust_env=False)
            # This endpoint appears only after entrypoint's real MCP initialize probes pass.
            deadline = asyncio.get_running_loop().time() + self._startup_timeout
            while asyncio.get_running_loop().time() < deadline:
                if self._server_process.returncode is not None:
                    raise RuntimeError(
                        "Apptainer startup failed; read artifacts/apptainer.log and chi-bench-server.log"
                    )
                try:
                    response = await self._http_client.get(
                        f"http://127.0.0.1:{self._server_port}/health"
                    )
                    if response.status_code == 200:
                        probe = await self.exec("cat /tmp/chi-bench-runtime/trial-id")
                        if probe.stdout == self.session_id:
                            return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
            raise TimeoutError(f"Apptainer did not become ready within {self._startup_timeout}s")
        except BaseException:
            # Cancellation and startup failure use the same cleanup as completed trials.
            await self.stop(delete=True)
            raise
        finally:
            if reserved:
                reserved.close()

    async def upload_dir(self, source_dir: Path | str, target_dir: str) -> None:
        await super().upload_dir(source_dir, target_dir)
        # Legacy marathon verifiers require /fixtures, created only at verifier setup.
        if target_dir.rstrip("/") == "/tests":
            fixture = shlex.quote(self._persistent_env["CHI_BENCH_FIXTURES_DIR"])
            result = await self.exec(f"ln -sfn {fixture} /fixtures")
            if result.return_code:
                raise RuntimeError("Could not prepare verifier fixtures")

    async def stop(self, delete: bool) -> None:
        try:
            # Signal only the process group that this environment created.
            if self._process_group is not None:
                try:
                    os.killpg(self._process_group, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                if self._server_process:
                    try:
                        await asyncio.wait_for(self._server_process.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        pass
                try:
                    os.killpg(self._process_group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                if self._server_process:
                    await self._server_process.wait()
                self._process_group = None
            if self._http_client:
                await self._http_client.aclose()
                self._http_client = None
        finally:
            if self._process_log:
                self._process_log.close()
                self._process_log = None
            try:
                if self._runtime and not self._keep_runtime:
                    shutil.rmtree(self._runtime)
                    self._runtime = None
                    self._staging_dir = None
            finally:
                if self._node_slot:
                    self._node_slot.close()
                    self._node_slot = None
