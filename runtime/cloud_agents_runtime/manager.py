from __future__ import annotations

import json
import os
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .access import AccessManager
from .adapters import FakeAdapter, QwenServeAdapter, RuntimeAdapter
from .budget import BudgetConfig, CostManager
from .cleanup import CleanupManager, CleanupPolicy
from .events import RuntimeEvent, TERMINAL_RUN_EVENTS
from .executors import ExecutorConfig, ExecutorRegistry
from .missions import MissionManager
from .models import RunSpec, RunState
from .ops import BetaOpsConfig, OperationsManager
from .resources import ResourceLimitConfig, ResourcePolicyResolver
from .store import RunStore
from .workspace import WorkspaceAllocator


class RunManager:
    def __init__(
        self,
        artifact_root: Path,
        adapters: dict[str, RuntimeAdapter] | None = None,
        qwen_base_url: str | None = None,
        qwen_token: str | None = None,
        worker_id: str | None = None,
        worker_capacity: int | None = None,
        lease_ttl_seconds: int | None = None,
        permission_stall_seconds: int | None = None,
        permission_stall_action: str | None = None,
        ops_config: BetaOpsConfig | None = None,
        budget_config: BudgetConfig | None = None,
        resource_config: ResourceLimitConfig | None = None,
        cleanup_policy: CleanupPolicy | None = None,
        heartbeat_enabled: bool = False,
    ):
        self.store = RunStore(artifact_root)
        self.executor_registry = ExecutorRegistry(self.store, ExecutorConfig.from_env())
        self.workspace_allocator = WorkspaceAllocator(artifact_root)
        self.resource_resolver = ResourcePolicyResolver(resource_config)
        self.cleanup_manager = CleanupManager(self.store, cleanup_policy)
        self.ops = OperationsManager(self.store, ops_config)
        self.cost = CostManager(self.store, budget_config)
        self.access = AccessManager(
            self.store,
            os.environ.get("RUN_MANAGER_DEFAULT_PRINCIPAL") or "single-tenant-operator",
        )
        self.missions = MissionManager(self)
        self.adapters = adapters or {
            "fake": FakeAdapter(),
            "qwen": QwenServeAdapter(
                base_url=qwen_base_url,
                token=qwen_token,
                executor_registry=self.executor_registry,
            ),
        }
        self.worker_id = worker_id or os.environ.get("RUN_MANAGER_WORKER_ID")
        if not self.worker_id:
            self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.worker_capacity = positive_int(
            worker_capacity,
            os.environ.get("RUN_MANAGER_WORKER_CAPACITY"),
            default=1,
        )
        self.lease_ttl_seconds = positive_int(
            lease_ttl_seconds,
            os.environ.get("RUN_MANAGER_LEASE_TTL_SECONDS"),
            default=60,
        )
        self.permission_stall_seconds = positive_int(
            permission_stall_seconds,
            os.environ.get("RUN_MANAGER_PERMISSION_STALL_SECONDS"),
            default=300,
        )
        self.permission_stall_action = normalize_permission_stall_action(
            permission_stall_action or os.environ.get("RUN_MANAGER_PERMISSION_STALL_ACTION")
        )
        self._scheduler_lock = threading.Lock()
        self._run_threads: list[threading.Thread] = []
        self._run_threads_lock = threading.Lock()
        self._stop = threading.Event()
        self._closed = False
        self.store.add_event_listener(self._on_event)
        self.store.fail_orphaned_jobs_for_worker(
            self.worker_id,
            "runtime restarted without active run thread",
        )
        self.store.register_worker(
            self.worker_id,
            self.worker_capacity,
            self.lease_ttl_seconds,
        )
        self.store.recover_expired_leases()
        self.store.prune_stale_workers(self.ops.config.stale_worker_seconds)
        self._heartbeat_thread: threading.Thread | None = None
        self._cleanup_thread: threading.Thread | None = None
        if heartbeat_enabled:
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name=f"runtime-worker-heartbeat-{self.worker_id}",
                daemon=True,
            )
            self._heartbeat_thread.start()
        if self.cleanup_manager.policy.enabled:
            self._cleanup_thread = threading.Thread(
                target=self._cleanup_loop,
                name=f"runtime-cleanup-{self.worker_id}",
                daemon=True,
            )
            self._cleanup_thread.start()
        self._drain_queue()
        self.missions.reconcile()

    def capabilities(self) -> dict[str, Any]:
        return {
            "v": 1,
            "mode": "saeu-run-manager-poc",
            "features": [
                "run_create",
                "run_input",
                "run_events_sse",
                "run_cancel",
                "artifact_files",
                "permission_resolution",
                "durable_event_store",
                "run_replay",
                "event_gap_detection",
                "runtime_adapter_capabilities",
                "run_queue",
                "run_leases",
                "worker_heartbeat",
                "worker_capacity",
                "per_run_workspace",
                "resource_policy",
                "run_timeout_watchdog",
                "cleanup_policy",
                "profile_registry",
                "mission_task_dag",
                "mission_supervisor",
                "artifact_handoff",
                "reviewer_gate",
                "reviewer_gate_override",
                "merge_deploy_gate",
                "mission_final_report",
                "acp_jsonrpc_poc",
                "a2a_gateway_poc",
                "temporal_workflow_plan_poc",
                "metrics",
                "backup",
                "failure_drills",
                "p5_evaluation_registry",
                "stale_worker_detection",
                "executor_registry",
                "per_run_qwen_executor",
                "container_qwen_executor",
                "remote_worker_registry",
                "remote_worker_claim_api",
                "access_project_registry",
                "api_token_registry",
                "cost_budget",
            ],
            "resource_limits": self.resource_resolver.config.to_dict(),
            "cleanup_policy": self.cleanup_manager.policy.to_dict(),
            "ops_policy": self.ops.config.to_dict(),
            "cost_policy": self.cost.config.to_dict(),
            "executor_registry": self.executor_registry.capabilities(),
            "permission_stall_policy": {
                "seconds": self.permission_stall_seconds,
                "action": self.permission_stall_action,
            },
            "queue": self.queue_status(),
            "profiles": [profile.to_dict() for profile in self.store.list_profiles()],
            "adapters": {
                name: adapter.capabilities() for name, adapter in sorted(self.adapters.items())
            },
        }

    def create_run(self, spec: RunSpec) -> RunState:
        self._adapter(spec.adapter)
        run_id = f"run_{uuid4().hex}"
        resource_policy = self.resource_resolver.resolve(spec)
        cost_quote = self.cost.require_allowed(spec)
        spec.metadata["cost_quote"] = cost_quote
        allocation = self.workspace_allocator.prepare(run_id, spec)
        run = self.store.create_run(spec, run_id=run_id)
        self.store.write_json(run.run_id, "workspace.json", allocation.to_dict())
        self.store.append_event(run.run_id, "workspace.prepared", allocation.to_dict())
        self.store.write_json(run.run_id, "resources.json", resource_policy.to_dict())
        self.store.append_event(run.run_id, "resources.resolved", resource_policy.to_dict())
        self.store.write_json(run.run_id, "cost.json", cost_quote)
        self.store.append_event(run.run_id, "cost.quoted", cost_quote)
        self.store.enqueue_run(run.run_id)
        self._drain_queue()
        return self.store.get_run(run.run_id) or run

    def send_input(self, run_id: str, prompt: str) -> None:
        run = self._require_run(run_id)
        if run.status != "running":
            self.store.append_event(
                run_id,
                "input.rejected",
                {"reason": f"run is {run.status}; input requires running"},
            )
            return
        self._adapter(run.spec.adapter).send_input(run, prompt, self.store)

    def cancel(self, run_id: str, reason: str | None = None) -> None:
        run = self._require_run(run_id)
        if self.store.is_terminal(run_id):
            self.store.append_event(run_id, "cancel.ignored", {"reason": "run already terminal"})
            return
        if self.store.cancel_job(run_id):
            self.store.append_event(
                run_id,
                "run.cancelled",
                {"reason": reason or "cancelled before worker claim"},
            )
            self._drain_queue()
            return
        self._adapter(run.spec.adapter).cancel(run, reason, self.store)

    def resolve_permission(self, run_id: str, permission_id: str, payload: dict[str, Any]) -> None:
        run = self._require_run(run_id)
        decision = payload.get("decision")
        if decision not in {"approve", "deny", "cancel"}:
            raise ValueError("decision must be approve, deny, or cancel")
        self._adapter(run.spec.adapter).resolve_permission(run, permission_id, payload, self.store)

    def get_run(self, run_id: str) -> RunState | None:
        return self.store.get_run(run_id)

    def queue_status(self) -> dict[str, Any]:
        return self.store.queue_snapshot()

    def remote_worker_heartbeat(self, worker_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        worker_id = normalize_worker_id(worker_id)
        capacity = payload_positive_int(payload, "capacity", default=1)
        lease_ttl_seconds = payload_positive_int(
            payload,
            "lease_ttl_seconds",
            default=self.lease_ttl_seconds,
        )
        worker = self.store.heartbeat_worker(
            worker_id,
            capacity,
            lease_ttl_seconds,
            metadata=worker_metadata(payload, default_kind="remote"),
        )
        self.store.recover_expired_leases()
        return worker.to_dict()

    def claim_remote_run(self, worker_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        worker_id = normalize_worker_id(worker_id)
        capacity = payload_positive_int(payload, "capacity", default=1)
        lease_ttl_seconds = payload_positive_int(
            payload,
            "lease_ttl_seconds",
            default=self.lease_ttl_seconds,
        )
        worker = self.store.heartbeat_worker(
            worker_id,
            capacity,
            lease_ttl_seconds,
            metadata=worker_metadata(payload, default_kind="remote"),
        )
        self.store.recover_expired_leases()
        if self.store.active_job_count(worker_id) >= capacity:
            return {"worker": worker.to_dict(), "job": None, "run": None}
        job = self.store.claim_next_job(worker_id, lease_ttl_seconds)
        run = self.store.get_run(job.run_id) if job else None
        return {
            "worker": self.store.heartbeat_worker(
                worker_id,
                capacity,
                lease_ttl_seconds,
                metadata=worker.metadata,
            ).to_dict(),
            "job": job.to_dict() if job else None,
            "run": run.to_dict() if run else None,
        }

    def append_remote_worker_event(
        self,
        worker_id: str,
        run_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        worker_id = normalize_worker_id(worker_id)
        self._require_worker_job(worker_id, run_id)
        event_type = payload.get("type") or payload.get("event_type")
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event type is required")
        raw_data = payload.get("data", {})
        if raw_data is None:
            raw_data = {}
        if not isinstance(raw_data, dict):
            raise ValueError("event data must be an object")
        data = dict(raw_data)
        data.setdefault("worker_id", worker_id)
        event = self.store.append_event(run_id, event_type.strip(), data)
        return event.to_dict()

    def write_remote_worker_artifact(
        self,
        worker_id: str,
        run_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        worker_id = normalize_worker_id(worker_id)
        self._require_worker_job(worker_id, run_id)
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("artifact name is required")
        if "json" in payload:
            content = json.dumps(payload["json"], ensure_ascii=False, indent=2)
        else:
            content = payload.get("content")
            if not isinstance(content, str):
                raise ValueError("artifact content or json is required")
        path = self.store.write_text(run_id, name.strip(), content)
        event = self.store.append_event(
            run_id,
            "artifact.uploaded",
            {
                "worker_id": worker_id,
                "name": path.name,
                "size_bytes": path.stat().st_size,
            },
        )
        return {"artifact": {"name": path.name}, "event": event.to_dict()}

    def executors(self) -> dict[str, Any]:
        return self.executor_registry.snapshot()

    def cleanup_once(self) -> dict[str, Any]:
        return self.cleanup_manager.run_once().to_dict()

    def metrics(self) -> dict[str, Any]:
        metrics = self.ops.metrics()
        metrics["cost"] = self.cost.summary()
        return metrics

    def operations_status(self) -> dict[str, Any]:
        status = self.ops.status()
        status["cost"] = self.cost.summary()
        return status

    def cost_status(self) -> dict[str, Any]:
        return self.cost.status()

    def p5_evaluations(self) -> dict[str, Any]:
        return self.ops.p5_evaluations()

    def run_drills(self) -> dict[str, Any]:
        return self.ops.run_drills()

    def create_backup(self) -> dict[str, Any]:
        return self.ops.create_backup()

    def list_backups(self) -> list[dict[str, Any]]:
        return self.ops.list_backups()

    def backup_path(self, name: str) -> Path:
        return self.ops.backup_path(name)

    def access_policy(self, headers: Any | None = None) -> dict[str, Any]:
        return self.access.policy(headers)

    def create_access_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.access.create_project(payload)

    def list_access_projects(self) -> dict[str, Any]:
        return self.access.list_projects()

    def create_api_token(
        self,
        payload: dict[str, Any],
        headers: Any | None = None,
    ) -> dict[str, Any]:
        return self.access.create_token(payload, headers=headers)

    def list_api_tokens(self) -> dict[str, Any]:
        return self.access.list_tokens()

    def revoke_api_token(self, token_id: str) -> dict[str, Any]:
        return self.access.revoke_token(token_id)

    def run_audit_bundle(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        executor = self.store.get_executor_lease_for_run(run_id)
        return {
            "run": run.to_dict(),
            "events": [event.to_dict() for event in self.store.events_since(run_id)],
            "raw_events": self.store.raw_events(run_id),
            "artifacts": self.store.list_artifacts(run_id),
            "queue": self.queue_status(),
            "executor": executor.to_dict() if executor else None,
            "cost": self.cost.run_cost_entry(run),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        }

    def list_profiles(self) -> list[dict[str, Any]]:
        return [profile.to_dict() for profile in self.store.list_profiles()]

    def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        profile = self.store.get_profile(profile_id)
        return profile.to_dict() if profile else None

    def create_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.missions.create_profile(payload).to_dict()

    def create_mission(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.missions.create_mission(payload)

    def list_missions(self) -> list[dict[str, Any]]:
        missions = sorted(
            self.store.list_missions(),
            key=lambda mission: mission.created_at,
            reverse=True,
        )
        return [self.store.mission_snapshot(mission.mission_id) for mission in missions]

    def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        return self.missions.get_mission(mission_id)

    def cancel_mission(self, mission_id: str, reason: str | None = None) -> dict[str, Any]:
        return self.missions.cancel_mission(mission_id, reason)

    def override_review_gate(
        self,
        mission_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.missions.override_review_gate(mission_id, payload)

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=2)
        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=2)
        with self._run_threads_lock:
            run_threads = list(self._run_threads)
        for thread in run_threads:
            thread.join(timeout=2)
        self.executor_registry.shutdown()
        self.store.close()

    def _require_run(self, run_id: str) -> RunState:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    def _require_worker_job(self, worker_id: str, run_id: str) -> None:
        job = self.store.get_job(run_id)
        if job is None:
            raise KeyError(run_id)
        if job.worker_id != worker_id:
            raise ValueError("run is not leased to this worker")
        if job.status != "running":
            raise ValueError(f"run lease is {job.status}")

    def _adapter(self, name: str) -> RuntimeAdapter:
        adapter = self.adapters.get(name)
        if adapter is None:
            raise ValueError(f"unknown adapter: {name}")
        return adapter

    def _drain_queue(self) -> None:
        if self.worker_capacity <= 0:
            return
        with self._scheduler_lock:
            self.store.heartbeat_worker(
                self.worker_id,
                self.worker_capacity,
                self.lease_ttl_seconds,
            )
            self.store.recover_expired_leases()
            while self.store.active_job_count(self.worker_id) < self.worker_capacity:
                job = self.store.claim_next_job(self.worker_id, self.lease_ttl_seconds)
                if job is None:
                    return
                thread = threading.Thread(
                    target=self._start_claimed_run,
                    args=(job.run_id,),
                    name=f"runtime-run-{job.run_id}",
                    daemon=True,
                )
                with self._run_threads_lock:
                    self._run_threads.append(thread)
                thread.start()

    def _start_claimed_run(self, run_id: str) -> None:
        run = self._require_run(run_id)
        if self.store.is_terminal(run_id):
            return
        self._start_timeout_watchdog(run_id, run.spec.timeout_seconds)
        adapter = self._adapter(run.spec.adapter)
        adapter.start(run, self.store)
        current = self._require_run(run_id)
        if current.spec.prompt and not self.store.is_terminal(run_id):
            adapter.send_input(current, current.spec.prompt, self.store)

    def _start_timeout_watchdog(self, run_id: str, timeout_seconds: int | None) -> None:
        if not timeout_seconds or timeout_seconds <= 0:
            return
        thread = threading.Thread(
            target=self._timeout_watchdog,
            args=(run_id, timeout_seconds),
            name=f"runtime-timeout-{run_id}",
            daemon=True,
        )
        thread.start()

    def _timeout_watchdog(self, run_id: str, timeout_seconds: int) -> None:
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if self._stop.wait(min(1.0, remaining)):
                return
            if self.store.is_terminal(run_id):
                return
        run = self.store.get_run(run_id)
        if run is None:
            return
        self.store.append_event(
            run_id,
            "resources.timeout",
            {"timeout_seconds": timeout_seconds},
        )
        self._adapter(run.spec.adapter).cancel(
            run,
            f"resource timeout after {timeout_seconds}s",
            self.store,
        )

    def _heartbeat_loop(self) -> None:
        interval = max(1.0, min(5.0, self.lease_ttl_seconds / 3))
        while not self._stop.wait(interval):
            try:
                self.store.heartbeat_worker(
                    self.worker_id,
                    self.worker_capacity,
                    self.lease_ttl_seconds,
                )
                self.store.recover_expired_leases()
                self._drain_queue()
            except Exception:
                return

    def _cleanup_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.cleanup_once()
            except Exception:
                pass
            if self._stop.wait(self.cleanup_manager.policy.interval_seconds):
                return

    def _on_event(self, event: RuntimeEvent) -> None:
        self.missions.handle_run_event(event)
        if event.type == "permission.requested":
            self._start_permission_watchdog(event)
        if event.type in TERMINAL_RUN_EVENTS:
            self._drain_queue()
            run = self.store.get_run(event.run_id)
            metadata = run.spec.metadata if run else {}
            mission_id = metadata.get("mission_id")
            if isinstance(mission_id, str):
                self.missions.drain_mission(mission_id)

    def _start_permission_watchdog(self, event: RuntimeEvent) -> None:
        permission_id = permission_id_from_event(event)
        if not permission_id or self.permission_stall_seconds <= 0:
            return
        thread = threading.Thread(
            target=self._permission_watchdog,
            args=(event.run_id, event.sequence, permission_id),
            name=f"runtime-permission-{event.run_id}-{event.sequence}",
            daemon=True,
        )
        thread.start()

    def _permission_watchdog(
        self,
        run_id: str,
        requested_sequence: int,
        permission_id: str,
    ) -> None:
        if self._stop.wait(self.permission_stall_seconds):
            return
        if self.store.is_terminal(run_id) or self._permission_is_resolved(
            run_id,
            permission_id,
            requested_sequence,
        ):
            return
        self.store.append_event(
            run_id,
            "permission.stalled",
            {
                "permission_id": permission_id,
                "requested_sequence": requested_sequence,
                "stall_seconds": self.permission_stall_seconds,
                "action": self.permission_stall_action,
            },
        )
        if self.permission_stall_action == "cancel":
            self.cancel(run_id, f"permission stalled after {self.permission_stall_seconds}s")
        elif self.permission_stall_action == "deny":
            try:
                self.resolve_permission(
                    run_id,
                    permission_id,
                    {
                        "decision": "deny",
                        "option_id": "cancel",
                        "decided_by": "permission-watchdog",
                        "reason": (
                            "permission stalled after "
                            f"{self.permission_stall_seconds}s"
                        ),
                    },
                )
            except Exception as exc:  # noqa: BLE001 - audit failed recovery action
                self.store.append_event(
                    run_id,
                    "permission.stall_recovery_failed",
                    {"permission_id": permission_id, "reason": str(exc)},
                )

    def _permission_is_resolved(
        self,
        run_id: str,
        permission_id: str,
        requested_sequence: int,
    ) -> bool:
        for event in self.store.events_since(run_id, requested_sequence):
            if event.type != "permission.resolved":
                continue
            if permission_id_from_event(event) == permission_id:
                return True
        return False


def positive_int(value: int | None, env_value: str | None, default: int) -> int:
    candidate: int | None = value
    if candidate is None and env_value:
        try:
            candidate = int(env_value)
        except ValueError:
            candidate = None
    if candidate is None:
        candidate = default
    return max(0, candidate)


def payload_positive_int(payload: dict[str, Any], key: str, default: int) -> int:
    value = payload.get(key)
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str):
        try:
            return max(0, int(value))
        except ValueError:
            return default
    return default


def normalize_worker_id(value: str) -> str:
    worker_id = value.strip()
    if not worker_id:
        raise ValueError("worker id is required")
    return worker_id


def worker_metadata(payload: dict[str, Any], *, default_kind: str) -> dict[str, Any]:
    raw_metadata = payload.get("metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    kind = payload.get("kind")
    metadata["kind"] = kind if isinstance(kind, str) and kind else default_kind
    for key in ("endpoint", "hostname", "version", "region", "zone"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            metadata[key] = value
    for key in ("labels", "capabilities", "resources", "executor", "sandbox"):
        value = payload.get(key)
        if isinstance(value, (dict, list)):
            metadata[key] = value
    return metadata


def normalize_permission_stall_action(value: str | None) -> str:
    action = (value or "audit").strip().lower()
    if action not in {"audit", "deny", "cancel"}:
        return "audit"
    return action


def permission_id_from_event(event: RuntimeEvent) -> str | None:
    data = event.data or {}
    permission_id = data.get("permission_id")
    if isinstance(permission_id, str) and permission_id:
        return permission_id
    raw = data.get("raw")
    if isinstance(raw, dict):
        raw_data = raw.get("data")
        if isinstance(raw_data, dict):
            request_id = raw_data.get("requestId") or raw_data.get("permission_id")
            if isinstance(request_id, str) and request_id:
                return request_id
    return None
