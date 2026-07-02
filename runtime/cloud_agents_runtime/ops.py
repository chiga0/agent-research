from __future__ import annotations

import os
import tarfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .events import TERMINAL_RUN_EVENTS
from .store import RunStore


@dataclass(frozen=True)
class BetaOpsConfig:
    stale_worker_seconds: int = 300
    backup_retention_count: int = 10

    @classmethod
    def from_env(cls) -> "BetaOpsConfig":
        return cls(
            stale_worker_seconds=env_nonnegative_int("RUN_MANAGER_STALE_WORKER_SECONDS", 300),
            backup_retention_count=max(
                1,
                env_nonnegative_int("RUN_MANAGER_BACKUP_RETENTION_COUNT", 10),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DrillCheck:
    id: str
    status: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OperationsManager:
    def __init__(self, store: RunStore, config: BetaOpsConfig | None = None):
        self.store = store
        self.config = config or BetaOpsConfig.from_env()
        self.backup_dir = store.artifact_root / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def metrics(self) -> dict[str, Any]:
        runs = self.store.list_runs()
        missions = self.store.list_missions()
        queue = self.store.queue_snapshot(stale_after_seconds=self.config.stale_worker_seconds)
        run_statuses = count_by(run.status for run in runs)
        mission_statuses = count_by(mission.status for mission in missions)
        pending_permissions = 0
        stalled_permissions = 0
        failure_kinds: dict[str, int] = {}
        terminal_latencies: list[float] = []
        for run in runs:
            events = self.store.events_since(run.run_id)
            pending_permissions += pending_permission_count(events)
            stalled_permissions += sum(1 for event in events if event.type == "permission.stalled")
            for event in events:
                if event.type == "run.failed":
                    reason = event.data.get("reason") or event.data.get("adapter") or "unknown"
                    failure_kinds[str(reason)] = failure_kinds.get(str(reason), 0) + 1
            latency = terminal_latency_seconds(events)
            if latency is not None:
                terminal_latencies.append(latency)
        return {
            "generated_at": utc_now(),
            "runs": {"total": len(runs), "by_status": run_statuses},
            "missions": {"total": len(missions), "by_status": mission_statuses},
            "queue": {
                "counts": queue["counts"],
                "worker_count": len(queue["workers"]),
                "active_workers": sum(
                    1 for worker in queue["workers"] if worker["status"] == "active"
                ),
                "stale_workers": sum(
                    1 for worker in queue["workers"] if worker["status"] == "stale"
                ),
            },
            "permissions": {
                "pending": pending_permissions,
                "stalled": stalled_permissions,
            },
            "failures": {"by_reason": failure_kinds},
            "latency_seconds": latency_summary(terminal_latencies),
        }

    def status(self) -> dict[str, Any]:
        queue = self.store.queue_snapshot(stale_after_seconds=self.config.stale_worker_seconds)
        return {
            "generated_at": utc_now(),
            "config": self.config.to_dict(),
            "artifact_root": str(self.store.artifact_root),
            "database": {
                "path": str(self.store.db_path),
                "exists": self.store.db_path.exists(),
                "size_bytes": (
                    self.store.db_path.stat().st_size if self.store.db_path.exists() else 0
                ),
            },
            "queue": queue,
            "metrics": self.metrics(),
            "security": self.security_posture(),
        }

    def security_posture(self) -> dict[str, Any]:
        return {
            "run_manager_bind": "127.0.0.1 expected in production",
            "public_runtime_api": "blocked by signed runtime session cookies",
            "docker_socket": Path("/var/run/docker.sock").exists(),
            "secrets_policy": "runtime tokens stay in /etc/cloud-agents-runtime.env",
            "qwen_public": False,
        }

    def p5_evaluations(self) -> dict[str, Any]:
        return {
            "generated_at": utc_now(),
            "components": [
                {
                    "id": "acp-streamable-http",
                    "status": "implemented",
                    "mode": "json-rpc-http-plus-sse-events",
                    "entrypoints": ["/acp", "/runs/{run_id}/events"],
                    "decision": "keep as protocol facade over SAEU contract",
                },
                {
                    "id": "a2a-gateway",
                    "status": "implemented",
                    "mode": "mission-task-gateway",
                    "entrypoints": [
                        "/.well-known/agent-card.json",
                        "/a2a/tasks",
                        "/a2a/tasks/{task_id}",
                    ],
                    "decision": "safe to expose as mission gateway",
                },
                external_component("e2b-sandbox", "E2B_API_KEY"),
                external_component("daytona-sandbox", "DAYTONA_API_KEY"),
                external_component("langgraph-supervisor", "LANGGRAPH_ENABLED"),
                {
                    "id": "temporal-workflow",
                    "status": "plan_export",
                    "mode": "workflow-plan-poc",
                    "entrypoints": [
                        "/temporal/workflows/runs/{run_id}/plan",
                        "/temporal/workflows/missions/{mission_id}/plan",
                    ],
                    "decision": "defer service dependency until multi-worker recovery requires it",
                },
                {
                    "id": "airflow-outer-scheduler",
                    "status": "deferred",
                    "mode": "outer-batch-orchestration",
                    "decision": (
                        "use only for external batch schedules, "
                        "not live Agent session control"
                    ),
                },
            ],
        }

    def run_drills(self) -> dict[str, Any]:
        checks = [
            self._check_db(),
            self._check_artifact_root(),
            self._check_queue_leases(),
            self._check_backup_writable(),
            self._check_security_posture(),
        ]
        failed = [check for check in checks if check.status != "pass"]
        return {
            "generated_at": utc_now(),
            "status": "pass" if not failed else "warn",
            "checks": [check.to_dict() for check in checks],
        }

    def create_backup(self) -> dict[str, Any]:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = f"cloud-agents-backup-{timestamp}.tar.gz"
        path = self.backup_dir / name
        manifest = {
            "created_at": utc_now(),
            "artifact_root": str(self.store.artifact_root),
            "database": self.store.db_path.name,
            "included_paths": ["runtime.db", "artifacts/"],
            "metrics": self.metrics(),
        }
        manifest_path = self.backup_dir / f"{name}.manifest.json"
        manifest_path.write_text(json_dumps(manifest), encoding="utf-8")
        with tarfile.open(path, "w:gz") as archive:
            if self.store.db_path.exists():
                archive.add(self.store.db_path, arcname="runtime.db")
            self._add_artifacts_to_backup(archive)
            archive.add(manifest_path, arcname="manifest.json")
        manifest_path.unlink(missing_ok=True)
        self._prune_backups()
        return self._backup_info(path)

    def list_backups(self) -> list[dict[str, Any]]:
        return [self._backup_info(path) for path in sorted(self.backup_dir.glob("*.tar.gz"))]

    def backup_path(self, name: str) -> Path:
        if Path(name).name != name or not name.endswith(".tar.gz"):
            raise ValueError("backup name must be a tar.gz file name")
        path = self.backup_dir / name
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(name)
        return path

    def _backup_info(self, path: Path) -> dict[str, Any]:
        stat = path.stat()
        return {
            "name": path.name,
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(
                timespec="milliseconds"
            ),
        }

    def _prune_backups(self) -> None:
        backups = sorted(
            self.backup_dir.glob("*.tar.gz"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in backups[self.config.backup_retention_count :]:
            path.unlink(missing_ok=True)

    def _add_artifacts_to_backup(self, archive: tarfile.TarFile) -> None:
        excluded_roots = {
            self.backup_dir.resolve(),
            (self.store.artifact_root / "workspaces").resolve(),
        }
        for path in sorted(self.store.artifact_root.rglob("*")):
            if not path.is_file() or path == self.store.db_path:
                continue
            resolved = path.resolve()
            if any(resolved == root or root in resolved.parents for root in excluded_roots):
                continue
            arcname = Path("artifacts") / path.relative_to(self.store.artifact_root)
            archive.add(path, arcname=str(arcname))

    def _check_db(self) -> DrillCheck:
        exists = self.store.db_path.exists()
        return DrillCheck(
            id="runtime-db",
            status="pass" if exists else "fail",
            summary="runtime.db is present" if exists else "runtime.db is missing",
            details={"path": str(self.store.db_path)},
        )

    def _check_artifact_root(self) -> DrillCheck:
        path = self.store.artifact_root
        ok = path.exists() and os.access(path, os.R_OK | os.W_OK)
        return DrillCheck(
            id="artifact-root",
            status="pass" if ok else "fail",
            summary=(
                "artifact root is readable and writable"
                if ok
                else "artifact root is not writable"
            ),
            details={"path": str(path)},
        )

    def _check_queue_leases(self) -> DrillCheck:
        queue = self.store.queue_snapshot(stale_after_seconds=self.config.stale_worker_seconds)
        running_without_worker = [
            job["run_id"]
            for job in queue["jobs"]
            if job["status"] == "running" and not job.get("worker_id")
        ]
        return DrillCheck(
            id="queue-leases",
            status="pass" if not running_without_worker else "warn",
            summary="queue leases are attributable"
            if not running_without_worker
            else "running jobs without workers detected",
            details={"running_without_worker": running_without_worker},
        )

    def _check_backup_writable(self) -> DrillCheck:
        ok = self.backup_dir.exists() and os.access(self.backup_dir, os.W_OK)
        return DrillCheck(
            id="backup-writable",
            status="pass" if ok else "fail",
            summary="backup directory is writable" if ok else "backup directory is not writable",
            details={"path": str(self.backup_dir)},
        )

    def _check_security_posture(self) -> DrillCheck:
        posture = self.security_posture()
        status = "warn" if posture["docker_socket"] else "pass"
        return DrillCheck(
            id="security-posture",
            status=status,
            summary="security posture checked",
            details=posture,
        )


def count_by(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


def pending_permission_count(events: list[Any]) -> int:
    requested: set[str] = set()
    resolved: set[str] = set()
    for event in events:
        if event.type == "permission.requested":
            permission_id = permission_id_from_data(event.data)
            if permission_id:
                requested.add(permission_id)
        elif event.type == "permission.resolved":
            permission_id = permission_id_from_data(event.data)
            if permission_id:
                resolved.add(permission_id)
    return len(requested - resolved)


def permission_id_from_data(data: dict[str, Any]) -> str | None:
    permission_id = data.get("permission_id")
    if isinstance(permission_id, str):
        return permission_id
    raw = data.get("raw")
    if not isinstance(raw, dict):
        return None
    raw_data = raw.get("data")
    if not isinstance(raw_data, dict):
        return None
    request_id = raw_data.get("requestId") or raw_data.get("permission_id")
    return request_id if isinstance(request_id, str) else None


def terminal_latency_seconds(events: list[Any]) -> float | None:
    first = events[0].created_at if events else None
    terminal = next(
        (event.created_at for event in events if event.type in TERMINAL_RUN_EVENTS),
        None,
    )
    if not first or not terminal:
        return None
    try:
        return max(
            0.0,
            (datetime.fromisoformat(terminal) - datetime.fromisoformat(first)).total_seconds(),
        )
    except ValueError:
        return None


def latency_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "avg": None, "p95": None}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))
    return {
        "count": len(values),
        "avg": round(sum(values) / len(values), 3),
        "p95": round(ordered[p95_index], 3),
    }


def external_component(component_id: str, env_name: str) -> dict[str, Any]:
    configured = bool(os.environ.get(env_name))
    return {
        "id": component_id,
        "status": "configured" if configured else "not_configured",
        "mode": "external-adapter",
        "required_env": env_name,
        "decision": "available for adapter implementation when credentials and workload justify it",
    }


def env_nonnegative_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(0, parsed)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def json_dumps(payload: Any) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
