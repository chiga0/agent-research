from __future__ import annotations

import os
import shlex
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .events import utc_now
from .models import ExecutorLease, RunState
from .store import RunStore


MANAGED_QWEN_STRATEGIES = {"per_run_process", "container"}


@dataclass(frozen=True)
class ExecutorConfig:
    strategy: str = "shared"
    host: str = "127.0.0.1"
    port_start: int = 4210
    port_end: int = 4310
    command_template: str | None = None
    container_command_template: str | None = None
    container_image: str | None = None
    container_network: str = "bridge"
    container_cpus: float = 1.0
    container_memory_mb: int = 1024
    container_pids: int = 256
    container_extra_args: str | None = None
    startup_timeout_seconds: float = 20.0
    stop_timeout_seconds: float = 5.0
    token: str | None = None

    @classmethod
    def from_env(cls) -> "ExecutorConfig":
        return cls(
            strategy=normalize_strategy(os.environ.get("QWEN_EXECUTOR_STRATEGY")),
            host=os.environ.get("QWEN_EXECUTOR_HOST") or "127.0.0.1",
            port_start=parse_int(os.environ.get("QWEN_EXECUTOR_PORT_START"), 4210),
            port_end=parse_int(os.environ.get("QWEN_EXECUTOR_PORT_END"), 4310),
            command_template=os.environ.get("QWEN_EXECUTOR_COMMAND"),
            container_command_template=os.environ.get("QWEN_CONTAINER_COMMAND"),
            container_image=os.environ.get("QWEN_CONTAINER_IMAGE"),
            container_network=os.environ.get("QWEN_CONTAINER_NETWORK") or "bridge",
            container_cpus=parse_float(os.environ.get("QWEN_CONTAINER_CPUS"), 1.0),
            container_memory_mb=parse_int(os.environ.get("QWEN_CONTAINER_MEMORY_MB"), 1024),
            container_pids=parse_int(os.environ.get("QWEN_CONTAINER_PIDS"), 256),
            container_extra_args=os.environ.get("QWEN_CONTAINER_EXTRA_ARGS"),
            startup_timeout_seconds=parse_float(
                os.environ.get("QWEN_EXECUTOR_STARTUP_TIMEOUT"),
                20.0,
            ),
            stop_timeout_seconds=parse_float(
                os.environ.get("QWEN_EXECUTOR_STOP_TIMEOUT"),
                5.0,
            ),
            token=(
                os.environ.get("QWEN_EXECUTOR_TOKEN")
                or os.environ.get("QWEN_SERVE_TOKEN")
                or os.environ.get("QWEN_SERVER_TOKEN")
            ),
        )

    @property
    def enabled(self) -> bool:
        return self.strategy in MANAGED_QWEN_STRATEGIES

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "enabled": self.enabled,
            "host": self.host,
            "port_start": self.port_start,
            "port_end": self.port_end,
            "command_template": bool(self.command_template),
            "container_command_template": bool(self.container_command_template),
            "container_image": self.container_image,
            "container_network": self.container_network,
            "container_cpus": self.container_cpus,
            "container_memory_mb": self.container_memory_mb,
            "container_pids": self.container_pids,
            "container_extra_args": bool(self.container_extra_args),
            "startup_timeout_seconds": self.startup_timeout_seconds,
            "stop_timeout_seconds": self.stop_timeout_seconds,
            "token": "configured" if self.token else None,
        }


@dataclass
class ManagedProcess:
    process: subprocess.Popen[str]
    stdout: Any
    stderr: Any

    def close_logs(self) -> None:
        for handle in (self.stdout, self.stderr):
            try:
                handle.close()
            except Exception:
                pass


class ExecutorRegistry:
    def __init__(
        self,
        store: RunStore,
        config: ExecutorConfig | None = None,
    ):
        self.store = store
        self.config = config or ExecutorConfig.from_env()
        self._processes: dict[str, ManagedProcess] = {}
        self._reserved_ports: set[int] = set()
        self._lock = threading.RLock()
        self._mark_unmanaged_active_leases()

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def capabilities(self) -> dict[str, Any]:
        leases = self.store.list_executor_leases()
        counts: dict[str, int] = {}
        for lease in leases:
            counts[lease.status] = counts.get(lease.status, 0) + 1
        return {
            "config": self.config.to_dict(),
            "counts": counts,
            "active": [
                lease.to_dict()
                for lease in leases
                if lease.status in {"starting", "running"}
            ],
        }

    def snapshot(self) -> dict[str, Any]:
        self.reap_exited()
        return {
            "executor_registry": self.capabilities(),
            "executors": [lease.to_dict() for lease in self.store.list_executor_leases()],
        }

    def acquire_qwen(self, run: RunState) -> ExecutorLease:
        if not self.enabled:
            raise RuntimeError("executor registry is not enabled")
        with self._lock:
            port = self._allocate_port()
            executor_id = f"exec_{uuid4().hex}"
            base_url = f"http://{self.config.host}:{port}"
            workspace = Path(run.spec.workspace or ".").expanduser().resolve()
            variables = {
                "host": self.config.host,
                "port": str(port),
                "workspace": str(workspace),
                "run_id": run.run_id,
                "executor_id": executor_id,
                "token": self.config.token or "",
            }
            try:
                command = self._command_for_run(run, workspace, variables)
            except Exception:
                self._release_port(port)
                raise
            lease = ExecutorLease(
                executor_id=executor_id,
                run_id=run.run_id,
                adapter="qwen",
                strategy=self.config.strategy,
                status="starting",
                base_url=base_url,
                token=self.config.token,
                workspace=str(workspace),
                port=port,
                command=command,
                heartbeat_at=utc_now(),
                metadata={
                    "resource_policy": run.spec.metadata.get("resource_policy"),
                    "workspace_allocation": run.spec.metadata.get("workspace_allocation"),
                    "container": container_metadata(self.config, run),
                },
            )
            self.store.upsert_executor_lease(lease)
            self.store.write_json(run.run_id, "executor.json", lease.to_dict())
            self.store.append_event(run.run_id, "executor.starting", lease.to_dict())

            stdout = (self.store.run_dir(run.run_id) / "executor.stdout.log").open(
                "a",
                encoding="utf-8",
            )
            stderr = (self.store.run_dir(run.run_id) / "executor.stderr.log").open(
                "a",
                encoding="utf-8",
            )
            try:
                env = executor_env(lease)
                process = subprocess.Popen(
                    command,
                    cwd=str(workspace),
                    env=env,
                    text=True,
                    stdout=stdout,
                    stderr=stderr,
                )
                lease.pid = process.pid
                lease.updated_at = utc_now()
                self.store.upsert_executor_lease(lease)
                managed = ManagedProcess(process=process, stdout=stdout, stderr=stderr)
                self._processes[executor_id] = managed
                self._wait_until_ready(lease, process)
                lease.status = "running"
                lease.heartbeat_at = utc_now()
                lease.updated_at = utc_now()
                self.store.upsert_executor_lease(lease)
                self.store.write_json(run.run_id, "executor.json", lease.to_dict())
                self.store.append_event(run.run_id, "executor.acquired", lease.to_dict())
                return lease
            except Exception as exc:
                lease.status = "failed"
                lease.last_error = str(exc)
                lease.updated_at = utc_now()
                self.store.upsert_executor_lease(lease)
                self.store.write_json(run.run_id, "executor.json", lease.to_dict())
                self.store.append_event(run.run_id, "executor.failed", lease.to_dict())
                self._release_port(port)
                self._terminate_process(executor_id, self.config.stop_timeout_seconds)
                stdout.close()
                stderr.close()
                raise

    def release_run(self, run_id: str, reason: str) -> None:
        with self._lock:
            lease = self.store.get_executor_lease_for_run(run_id)
            if not lease or lease.status in {"released", "failed", "orphaned"}:
                return
            managed = self._processes.get(lease.executor_id)
            exit_code = None
            if managed:
                exit_code = self._terminate_process(
                    lease.executor_id,
                    self.config.stop_timeout_seconds,
                )
            lease.status = "released"
            lease.released_at = utc_now()
            lease.exit_code = exit_code
            lease.metadata = {**lease.metadata, "release_reason": reason}
            lease.updated_at = utc_now()
            self.store.upsert_executor_lease(lease)
            self.store.write_json(run_id, "executor.json", lease.to_dict())
            self.store.append_event(run_id, "executor.released", lease.to_dict())
            if lease.port is not None:
                self._release_port(lease.port)

    def reap_exited(self) -> list[dict[str, Any]]:
        reaped: list[dict[str, Any]] = []
        with self._lock:
            for executor_id, managed in list(self._processes.items()):
                exit_code = managed.process.poll()
                if exit_code is None:
                    continue
                managed.close_logs()
                self._processes.pop(executor_id, None)
                lease = self.store.get_executor_lease(executor_id)
                if lease and lease.status in {"starting", "running"}:
                    lease.status = "failed"
                    lease.exit_code = exit_code
                    lease.last_error = f"executor process exited with code {exit_code}"
                    lease.released_at = utc_now()
                    lease.updated_at = utc_now()
                    self.store.upsert_executor_lease(lease)
                    self.store.write_json(lease.run_id, "executor.json", lease.to_dict())
                    self.store.append_event(lease.run_id, "executor.exited", lease.to_dict())
                    if lease.port is not None:
                        self._release_port(lease.port)
                    reaped.append(lease.to_dict())
        return reaped

    def shutdown(self) -> None:
        with self._lock:
            executor_ids = list(self._processes)
        for executor_id in executor_ids:
            lease = self.store.get_executor_lease(executor_id)
            if lease:
                self.release_run(lease.run_id, "runtime shutdown")

    def _wait_until_ready(
        self,
        lease: ExecutorLease,
        process: subprocess.Popen[str],
    ) -> None:
        deadline = time.monotonic() + self.config.startup_timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            exit_code = process.poll()
            if exit_code is not None:
                raise RuntimeError(f"executor exited early with code {exit_code}")
            try:
                with urllib.request.urlopen(f"{lease.base_url}/health", timeout=1) as response:
                    if response.status < 500:
                        return
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    return
                last_error = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
            time.sleep(0.2)
        raise RuntimeError(f"executor did not become healthy: {last_error}")

    def _allocate_port(self) -> int:
        start = max(0, self.config.port_start)
        end = max(start, self.config.port_end)
        if start == 0:
            port = reserve_ephemeral_port(self.config.host)
            self._reserved_ports.add(port)
            return port
        for port in range(start, end + 1):
            if port in self._reserved_ports:
                continue
            if port_available(self.config.host, port):
                self._reserved_ports.add(port)
                return port
        raise RuntimeError(f"no executor port available in {start}-{end}")

    def _release_port(self, port: int) -> None:
        self._reserved_ports.discard(port)

    def _terminate_process(self, executor_id: str, timeout: float) -> int | None:
        managed = self._processes.pop(executor_id, None)
        if not managed:
            return None
        process = managed.process
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=timeout)
        managed.close_logs()
        return process.returncode

    def _mark_unmanaged_active_leases(self) -> None:
        for lease in self.store.list_executor_leases():
            if lease.status not in {"starting", "running"}:
                continue
            lease.status = "orphaned"
            lease.last_error = "runtime restarted without process handle"
            lease.released_at = utc_now()
            lease.updated_at = utc_now()
            self.store.upsert_executor_lease(lease)

    def _command_for_run(
        self,
        run: RunState,
        workspace: Path,
        variables: dict[str, str],
    ) -> list[str]:
        if self.config.strategy == "container":
            if self.config.container_command_template:
                return render_command(self.config.container_command_template, variables)
            return default_container_command(
                self.config,
                workspace=workspace,
                run=run,
                host=variables["host"],
                port=int(variables["port"]),
                executor_id=variables["executor_id"],
            )
        command_template = self.config.command_template or default_qwen_command()
        return render_command(command_template, variables)


def normalize_strategy(value: str | None) -> str:
    strategy = (value or "shared").strip().lower().replace("-", "_")
    if strategy in {"", "global", "shared_daemon"}:
        return "shared"
    if strategy in {"per_run", "per_run_daemon", "process"}:
        return "per_run_process"
    if strategy in {"docker", "container_worker"}:
        return "container"
    if strategy not in {"shared", "per_run_process", "container"}:
        return "shared"
    return strategy


def default_qwen_command() -> str:
    return "qwen serve --hostname {host} --port {port}"


def default_container_command(
    config: ExecutorConfig,
    *,
    workspace: Path,
    run: RunState,
    host: str,
    port: int,
    executor_id: str,
) -> list[str]:
    if not config.container_image:
        raise RuntimeError("QWEN_CONTAINER_IMAGE or QWEN_CONTAINER_COMMAND is required")
    resource_policy = run.spec.metadata.get("resource_policy")
    cpus = resource_float(resource_policy, "cpus", config.container_cpus)
    memory_mb = resource_int(resource_policy, "memory_mb", config.container_memory_mb)
    pids = resource_int(resource_policy, "pids", config.container_pids)
    container_name = safe_container_name(executor_id)
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--cpus",
        compact_number(cpus),
        "--memory",
        f"{memory_mb}m",
        "--pids-limit",
        str(pids),
        "--network",
        config.container_network,
    ]
    if config.container_network != "host":
        command.extend(["-p", f"{host}:{port}:{port}"])
    command.extend(["-v", f"{workspace}:{workspace}:rw", "-w", str(workspace)])
    if config.token:
        command.extend(["-e", f"QWEN_SERVER_TOKEN={config.token}"])
    if config.container_extra_args:
        command.extend(shlex.split(config.container_extra_args))
    command.extend(
        [
            config.container_image,
            "qwen",
            "serve",
            "--hostname",
            "0.0.0.0" if config.container_network != "host" else host,
            "--port",
            str(port),
        ]
    )
    return command


def container_metadata(config: ExecutorConfig, run: RunState) -> dict[str, Any] | None:
    if config.strategy != "container":
        return None
    resource_policy = run.spec.metadata.get("resource_policy")
    return {
        "image": config.container_image,
        "network": config.container_network,
        "cpus": resource_float(resource_policy, "cpus", config.container_cpus),
        "memory_mb": resource_int(resource_policy, "memory_mb", config.container_memory_mb),
        "pids": resource_int(resource_policy, "pids", config.container_pids),
        "custom_command": bool(config.container_command_template),
    }


def render_command(template: str, variables: dict[str, str]) -> list[str]:
    rendered = template.format_map(variables)
    command = shlex.split(rendered)
    if not command:
        raise RuntimeError("executor command is empty")
    return command


def executor_env(lease: ExecutorLease) -> dict[str, str]:
    env = os.environ.copy()
    env["QWEN_SERVE_URL"] = lease.base_url or ""
    env["QWEN_EXECUTOR_ID"] = lease.executor_id
    env["QWEN_EXECUTOR_RUN_ID"] = lease.run_id
    if lease.workspace:
        env["QWEN_SERVE_CWD"] = lease.workspace
    if lease.token:
        env["QWEN_SERVER_TOKEN"] = lease.token
        env["QWEN_SERVE_TOKEN"] = lease.token
    return env


def port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
        return True


def reserve_ephemeral_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def parse_int(value: str | None, default: int) -> int:
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def parse_float(value: str | None, default: float) -> float:
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def resource_float(resource_policy: Any, key: str, default: float) -> float:
    if isinstance(resource_policy, dict):
        value = resource_policy.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0.0, float(value))
    return max(0.0, float(default))


def resource_int(resource_policy: Any, key: str, default: int) -> int:
    if isinstance(resource_policy, dict):
        value = resource_policy.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return max(1, value)
        if isinstance(value, float):
            return max(1, int(value))
    return max(1, int(default))


def safe_container_name(executor_id: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in executor_id
    )
    return f"cloud-agent-{safe[:48]}"


def compact_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)
