from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .events import TERMINAL_RUN_EVENTS
from .models import RunState
from .store import RunStore


DEFAULT_WORKSPACE_RETENTION_SECONDS = 7 * 24 * 60 * 60
DEFAULT_ARTIFACT_RETENTION_SECONDS = 30 * 24 * 60 * 60
DEFAULT_CLEANUP_INTERVAL_SECONDS = 60 * 60


@dataclass(frozen=True)
class CleanupPolicy:
    enabled: bool = True
    workspace_retention_seconds: int = DEFAULT_WORKSPACE_RETENTION_SECONDS
    artifact_retention_seconds: int = DEFAULT_ARTIFACT_RETENTION_SECONDS
    interval_seconds: int = DEFAULT_CLEANUP_INTERVAL_SECONDS

    @classmethod
    def from_env(cls) -> "CleanupPolicy":
        return cls(
            enabled=env_bool("RUN_MANAGER_CLEANUP_ENABLED", True),
            workspace_retention_seconds=env_nonnegative_int(
                "RUN_MANAGER_WORKSPACE_RETENTION_SECONDS",
                DEFAULT_WORKSPACE_RETENTION_SECONDS,
            ),
            artifact_retention_seconds=env_nonnegative_int(
                "RUN_MANAGER_ARTIFACT_RETENTION_SECONDS",
                DEFAULT_ARTIFACT_RETENTION_SECONDS,
            ),
            interval_seconds=max(
                1,
                env_nonnegative_int(
                    "RUN_MANAGER_CLEANUP_INTERVAL_SECONDS",
                    DEFAULT_CLEANUP_INTERVAL_SECONDS,
                ),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CleanupResult:
    workspaces_deleted: list[dict[str, Any]] = field(default_factory=list)
    artifacts_deleted: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CleanupManager:
    def __init__(self, store: RunStore, policy: CleanupPolicy | None = None):
        self.store = store
        self.policy = policy or CleanupPolicy.from_env()
        self.workspace_root = (store.artifact_root / "workspaces").resolve()

    def run_once(self, now: datetime | None = None) -> CleanupResult:
        moment = now or datetime.now(timezone.utc)
        result = CleanupResult()
        for run in self.store.list_runs():
            if run.status not in {"completed", "failed", "cancelled"}:
                continue
            events = self.store.events_since(run.run_id)
            terminal_at = terminal_created_at(events)
            if terminal_at is None:
                continue
            age_seconds = (moment - terminal_at).total_seconds()
            event_types = {event.type for event in events}
            if age_seconds >= self.policy.workspace_retention_seconds:
                self._cleanup_workspace(run, event_types, result)
            if age_seconds >= self.policy.artifact_retention_seconds:
                self._cleanup_artifacts(run, event_types, result)
        return result

    def _cleanup_workspace(
        self,
        run: RunState,
        event_types: set[str],
        result: CleanupResult,
    ) -> None:
        if "cleanup.workspace_deleted" in event_types:
            return
        allocation = run.spec.metadata.get("workspace_allocation")
        if not isinstance(allocation, dict):
            return
        if allocation.get("isolated") is False:
            return
        path_value = allocation.get("path") or run.spec.workspace
        if not isinstance(path_value, str) or not path_value:
            return
        path = Path(path_value).expanduser().resolve()
        if path == self.workspace_root or not is_relative_to(path, self.workspace_root):
            result.warnings.append(
                {
                    "run_id": run.run_id,
                    "target": "workspace",
                    "reason": "workspace outside managed root",
                    "path": str(path),
                }
            )
            return
        if not path.exists():
            return
        shutil.rmtree(path)
        payload = {
            "path": str(path),
            "retention_seconds": self.policy.workspace_retention_seconds,
        }
        self.store.append_event(run.run_id, "cleanup.workspace_deleted", payload)
        result.workspaces_deleted.append({"run_id": run.run_id, **payload})

    def _cleanup_artifacts(
        self,
        run: RunState,
        event_types: set[str],
        result: CleanupResult,
    ) -> None:
        if "cleanup.artifacts_deleted" in event_types:
            return
        path = self.store.run_dir(run.run_id)
        if not path.exists():
            return
        size_bytes = directory_size(path)
        shutil.rmtree(path)
        payload = {
            "path": str(path),
            "retention_seconds": self.policy.artifact_retention_seconds,
            "size_bytes": size_bytes,
            "audit_store": "runtime.db",
        }
        self.store.append_event(run.run_id, "cleanup.artifacts_deleted", payload)
        shutil.rmtree(path, ignore_errors=True)
        result.artifacts_deleted.append({"run_id": run.run_id, **payload})


def terminal_created_at(events: list[Any]) -> datetime | None:
    for event in events:
        if event.type not in TERMINAL_RUN_EVENTS:
            continue
        try:
            return datetime.fromisoformat(event.created_at)
        except ValueError:
            return None
    return None


def directory_size(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_nonnegative_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except ValueError:
        raise ValueError(f"{name} must be a non-negative integer") from None
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative")
    return parsed
