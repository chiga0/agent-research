from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .adapters import FakeAdapter, QwenServeAdapter, RuntimeAdapter
from .events import RuntimeEvent, TERMINAL_RUN_EVENTS
from .models import RunSpec, RunState
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
        resource_config: ResourceLimitConfig | None = None,
        heartbeat_enabled: bool = False,
    ):
        self.store = RunStore(artifact_root)
        self.workspace_allocator = WorkspaceAllocator(artifact_root)
        self.resource_resolver = ResourcePolicyResolver(resource_config)
        self.adapters = adapters or {
            "fake": FakeAdapter(),
            "qwen": QwenServeAdapter(base_url=qwen_base_url, token=qwen_token),
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
        self._scheduler_lock = threading.Lock()
        self._run_threads: list[threading.Thread] = []
        self._run_threads_lock = threading.Lock()
        self._stop = threading.Event()
        self._closed = False
        self.store.add_event_listener(self._on_event)
        self.store.register_worker(
            self.worker_id,
            self.worker_capacity,
            self.lease_ttl_seconds,
        )
        self.store.recover_expired_leases()
        self._heartbeat_thread: threading.Thread | None = None
        if heartbeat_enabled:
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name=f"runtime-worker-heartbeat-{self.worker_id}",
                daemon=True,
            )
            self._heartbeat_thread.start()
        self._drain_queue()

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
            ],
            "resource_limits": self.resource_resolver.config.to_dict(),
            "queue": self.queue_status(),
            "adapters": {
                name: adapter.capabilities() for name, adapter in sorted(self.adapters.items())
            },
        }

    def create_run(self, spec: RunSpec) -> RunState:
        self._adapter(spec.adapter)
        run_id = f"run_{uuid4().hex}"
        resource_policy = self.resource_resolver.resolve(spec)
        allocation = self.workspace_allocator.prepare(run_id, spec)
        run = self.store.create_run(spec, run_id=run_id)
        self.store.write_json(run.run_id, "workspace.json", allocation.to_dict())
        self.store.append_event(run.run_id, "workspace.prepared", allocation.to_dict())
        self.store.write_json(run.run_id, "resources.json", resource_policy.to_dict())
        self.store.append_event(run.run_id, "resources.resolved", resource_policy.to_dict())
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

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=2)
        with self._run_threads_lock:
            run_threads = list(self._run_threads)
        for thread in run_threads:
            thread.join(timeout=2)
        self.store.close()

    def _require_run(self, run_id: str) -> RunState:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

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

    def _on_event(self, event: RuntimeEvent) -> None:
        if event.type in TERMINAL_RUN_EVENTS:
            self._drain_queue()


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
