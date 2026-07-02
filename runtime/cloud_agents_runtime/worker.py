from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters import FakeAdapter, QwenServeAdapter, RuntimeAdapter
from .models import RunSpec, RunState


TERMINAL_EVENTS = {"run.completed", "run.failed", "run.cancelled"}


@dataclass(frozen=True)
class RemoteWorkerConfig:
    control_url: str
    token: str | None = None
    worker_id: str = field(default_factory=socket.gethostname)
    capacity: int = 1
    lease_ttl_seconds: int = 60
    poll_interval_seconds: float = 2.0
    heartbeat_interval_seconds: float = 10.0
    request_timeout_seconds: float = 10.0
    run_wait_timeout_seconds: float = 300.0
    artifact_root: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActiveRunContext:
    run: RunState
    adapter: RuntimeAdapter
    store: RemoteWorkerRunStore
    applied_controls: set[str] = field(default_factory=set)


class ControlPlaneClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout_seconds: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def heartbeat(self, worker_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request_json(
            f"/workers/{quote_path(worker_id)}/heartbeat",
            method="POST",
            payload=payload,
        )

    def claim(self, worker_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request_json(
            f"/workers/{quote_path(worker_id)}/claim",
            method="POST",
            payload=payload,
        )

    def control(self, worker_id: str) -> dict[str, Any]:
        return self.request_json(f"/workers/{quote_path(worker_id)}/control")

    def append_event(
        self,
        worker_id: str,
        run_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        return self.request_json(
            f"/workers/{quote_path(worker_id)}/runs/{quote_path(run_id)}/events",
            method="POST",
            payload={"type": event_type, "data": data},
        )

    def upload_artifact(
        self,
        worker_id: str,
        run_id: str,
        name: str,
        *,
        content: str | None = None,
        json_value: Any | None = None,
        mode: str = "write",
        chunk_index: int | None = None,
        final: bool | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name}
        if mode != "write":
            payload["mode"] = mode
        if chunk_index is not None:
            payload["chunk_index"] = chunk_index
        if final is not None:
            payload["final"] = final
        if json_value is not None:
            payload["json"] = json_value
        elif content is not None:
            payload["content"] = content
        else:
            payload["content"] = ""
        return self.request_json(
            f"/workers/{quote_path(worker_id)}/runs/{quote_path(run_id)}/artifacts",
            method="POST",
            payload=payload,
        )

    def request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"accept": "application/json"}
        if payload is not None:
            headers["content-type"] = "application/json"
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} failed: {exc.code} {detail}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError(f"{method} {path} returned non-object JSON")
        return parsed


class RemoteWorkerRunStore:
    def __init__(
        self,
        client: ControlPlaneClient,
        *,
        worker_id: str,
        run: RunState,
        artifact_root: Path | None = None,
    ):
        self.client = client
        self.worker_id = worker_id
        self.run = run
        self.artifact_root = artifact_root
        self._lock = threading.RLock()
        self._terminal = threading.Event()
        self._prompt_count = run.prompt_count
        self._raw_events: list[dict[str, Any]] = []
        self._status = run.status

    def set_adapter_run_id(self, run_id: str, adapter_run_id: str) -> None:
        self._require_run(run_id)
        with self._lock:
            self.run.adapter_run_id = adapter_run_id
        self.append_event(
            run_id,
            "adapter.run_id",
            {"adapter_run_id": adapter_run_id},
        )

    def increment_prompt_count(self, run_id: str) -> int:
        self._require_run(run_id)
        with self._lock:
            self._prompt_count += 1
            self.run.prompt_count = self._prompt_count
            return self._prompt_count

    def write_json(self, run_id: str, name: str, payload: dict[str, Any]) -> Path:
        self._require_run(run_id)
        self.client.upload_artifact(self.worker_id, run_id, name, json_value=payload)
        if self.artifact_root:
            path = safe_child_file(self.artifact_root / run_id, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return path
        return Path(name)

    def append_raw_event(self, run_id: str, adapter: str, payload: dict[str, Any]) -> None:
        self._require_run(run_id)
        with self._lock:
            raw_event = {
                "adapter": adapter,
                "payload": payload,
                "index": len(self._raw_events) + 1,
            }
            self._raw_events.append(raw_event)
            content = json.dumps(raw_event, ensure_ascii=False) + "\n"
            chunk_index = raw_event["index"]
            if self.artifact_root:
                path = safe_child_file(self.artifact_root / run_id, "raw_events.jsonl")
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as file:
                    file.write(content)
        self.client.upload_artifact(
            self.worker_id,
            run_id,
            "raw_events.jsonl",
            content=content,
            mode="append",
            chunk_index=chunk_index,
        )

    def append_event(
        self,
        run_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_run(run_id)
        payload = dict(data or {})
        event = self.client.append_event(self.worker_id, run_id, event_type, payload)
        with self._lock:
            if event_type == "run.started":
                self._status = "running"
            elif event_type == "run.completed":
                self._status = "completed"
            elif event_type == "run.failed":
                self._status = "failed"
            elif event_type == "run.cancelled":
                self._status = "cancelled"
            self.run.status = self._status
            if event_type in TERMINAL_EVENTS:
                self._terminal.set()
        return event

    def is_terminal(self, run_id: str) -> bool:
        self._require_run(run_id)
        return self._terminal.is_set() or self._status in {"completed", "failed", "cancelled"}

    def wait_terminal(self, timeout_seconds: float | None = None) -> bool:
        return self._terminal.wait(timeout_seconds)

    def _require_run(self, run_id: str) -> None:
        if run_id != self.run.run_id:
            raise KeyError(run_id)


class RemoteWorkerDaemon:
    def __init__(
        self,
        config: RemoteWorkerConfig,
        *,
        client: ControlPlaneClient | None = None,
        adapters: dict[str, RuntimeAdapter] | None = None,
    ):
        self.config = config
        self.client = client or ControlPlaneClient(
            config.control_url,
            token=config.token,
            timeout_seconds=config.request_timeout_seconds,
        )
        self.adapters = adapters or default_adapters()
        self._stop = threading.Event()
        self._active: list[threading.Thread] = []
        self._active_lock = threading.Lock()

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        while not self._stop.is_set():
            self.claim_once()
            self._stop.wait(self.config.poll_interval_seconds)

    def run_once(self, *, wait: bool = False) -> bool:
        thread = self.claim_once()
        if thread and wait:
            thread.join(self.config.run_wait_timeout_seconds + 5)
        return thread is not None

    def claim_once(self) -> threading.Thread | None:
        self._reap_finished()
        if self._active_count() >= self.config.capacity:
            self.client.heartbeat(self.config.worker_id, self._worker_payload())
            return None
        claim = self.client.claim(self.config.worker_id, self._worker_payload())
        run_payload = claim.get("run")
        if not isinstance(run_payload, dict):
            return None
        run = run_state_from_payload(run_payload)
        thread = threading.Thread(
            target=self._execute_run,
            args=(run,),
            name=f"remote-worker-{run.run_id}",
            daemon=True,
        )
        with self._active_lock:
            self._active.append(thread)
        thread.start()
        return thread

    def _execute_run(self, run: RunState) -> None:
        store = RemoteWorkerRunStore(
            self.client,
            worker_id=self.config.worker_id,
            run=run,
            artifact_root=self.config.artifact_root,
        )
        heartbeat_stop = threading.Event()
        adapter = self.adapters.get(run.spec.adapter)
        if adapter is None:
            store.append_event(
                run.run_id,
                "run.failed",
                {"reason": f"unknown adapter: {run.spec.adapter}"},
            )
            return
        context = ActiveRunContext(run=run, adapter=adapter, store=store)
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(heartbeat_stop, context),
            name=f"remote-worker-heartbeat-{run.run_id}",
            daemon=True,
        )
        heartbeat.start()
        try:
            adapter.start(run, store)  # type: ignore[arg-type]
            if run.spec.prompt and not store.is_terminal(run.run_id):
                adapter.send_input(run, run.spec.prompt, store)  # type: ignore[arg-type]
            timeout = run.spec.timeout_seconds or self.config.run_wait_timeout_seconds
            deadline = time.monotonic() + timeout
            while not store.is_terminal(run.run_id):
                self._apply_control(self._fetch_control(), context)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                store.wait_terminal(min(1.0, remaining))
            if not store.is_terminal(run.run_id):
                adapter.cancel(run, "remote worker timeout", store)  # type: ignore[arg-type]
                store.wait_terminal(5)
        except Exception as exc:  # noqa: BLE001 - surface worker failures to control plane
            if not store.is_terminal(run.run_id):
                store.append_event(
                    run.run_id,
                    "run.failed",
                    {"reason": str(exc), "worker_id": self.config.worker_id},
                )
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=2)

    def _heartbeat_loop(
        self,
        stop: threading.Event,
        context: ActiveRunContext | None = None,
    ) -> None:
        while not stop.is_set() and not self._stop.is_set():
            response = self.client.heartbeat(self.config.worker_id, self._worker_payload())
            control = control_from_response(response)
            if isinstance(control, dict) and context is not None:
                self._apply_control(control, context)
            stop.wait(self.config.heartbeat_interval_seconds)

    def _fetch_control(self) -> dict[str, Any]:
        try:
            return self.client.control(self.config.worker_id)
        except Exception:
            return {}

    def _apply_control(
        self,
        control: dict[str, Any] | None,
        context: ActiveRunContext,
    ) -> None:
        if not control or context.store.is_terminal(context.run.run_id):
            return
        runs = control.get("runs")
        if not isinstance(runs, list):
            return
        for item in runs:
            if not isinstance(item, dict) or item.get("run_id") != context.run.run_id:
                continue
            cancel_event = item.get("cancel")
            if isinstance(cancel_event, dict):
                control_id = str(cancel_event.get("id") or cancel_event.get("sequence"))
                if control_id not in context.applied_controls:
                    context.applied_controls.add(control_id)
                    data = cancel_event.get("data")
                    reason = None
                    if isinstance(data, dict):
                        reason = data.get("reason")
                    context.adapter.cancel(
                        context.run,
                        str(reason or "cancelled by control plane"),
                        context.store,  # type: ignore[arg-type]
                    )
            resolutions = item.get("permission_resolutions")
            if not isinstance(resolutions, list):
                continue
            for event in resolutions:
                if not isinstance(event, dict):
                    continue
                control_id = str(event.get("id") or event.get("sequence"))
                if control_id in context.applied_controls:
                    continue
                data = event.get("data")
                if not isinstance(data, dict):
                    continue
                permission_id = data.get("permission_id")
                payload = data.get("payload")
                if not isinstance(permission_id, str) or not isinstance(payload, dict):
                    continue
                context.applied_controls.add(control_id)
                context.adapter.resolve_permission(
                    context.run,
                    permission_id,
                    payload,
                    context.store,  # type: ignore[arg-type]
                )

    def _worker_payload(self) -> dict[str, Any]:
        metadata = dict(self.config.metadata)
        metadata.setdefault("hostname", socket.gethostname())
        raw_capabilities = metadata.get("capabilities")
        capabilities = dict(raw_capabilities) if isinstance(raw_capabilities, dict) else {}
        raw_features = capabilities.get("features")
        extra_features = raw_features if isinstance(raw_features, list) else []
        capabilities["adapters"] = sorted(self.adapters)
        capabilities["features"] = sorted(
            {*extra_features, "artifacts", "claim", "control", "events", "heartbeat"}
        )
        metadata["capabilities"] = capabilities
        return {
            "kind": "remote",
            "capacity": self.config.capacity,
            "lease_ttl_seconds": self.config.lease_ttl_seconds,
            "metadata": metadata,
        }

    def _active_count(self) -> int:
        with self._active_lock:
            return sum(1 for thread in self._active if thread.is_alive())

    def _reap_finished(self) -> None:
        with self._active_lock:
            self._active = [thread for thread in self._active if thread.is_alive()]


def default_adapters() -> dict[str, RuntimeAdapter]:
    return {
        "fake": FakeAdapter(),
        "qwen": QwenServeAdapter(
            base_url=os.environ.get("QWEN_SERVE_URL"),
            token=os.environ.get("QWEN_SERVE_TOKEN"),
        ),
    }


def run_state_from_payload(payload: dict[str, Any]) -> RunState:
    spec = RunSpec.from_payload(dict(payload.get("spec") or {}))
    return RunState(
        run_id=str(payload["run_id"]),
        spec=spec,
        status=str(payload.get("status") or "created"),
        adapter_run_id=payload.get("adapter_run_id"),
        created_at=str(payload.get("created_at") or ""),
        updated_at=str(payload.get("updated_at") or ""),
        event_count=int(payload.get("event_count") or 0),
        prompt_count=int(payload.get("prompt_count") or 0),
    )


def control_from_response(response: dict[str, Any]) -> dict[str, Any] | None:
    control = response.get("control")
    if isinstance(control, dict):
        return control
    worker = response.get("worker")
    if isinstance(worker, dict) and isinstance(worker.get("control"), dict):
        return worker["control"]
    return None


def safe_child_file(parent: Path, name: str) -> Path:
    candidate = Path(name)
    if candidate.name != name or name in {"", ".", ".."}:
        raise ValueError("artifact name must be a file name")
    return parent / name


def quote_path(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def parse_json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("metadata must be a JSON object")
    return parsed


def positive_int(value: int | None, default: int) -> int:
    if value is None:
        return default
    return max(0, value)


def positive_float(value: float | None, default: float) -> float:
    if value is None:
        return default
    return max(0.0, value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cloud Agents remote worker")
    parser.add_argument(
        "--control-url",
        default=os.environ.get("RUN_WORKER_CONTROL_URL"),
        help="Cloud Agents Runtime control-plane base URL",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("RUN_WORKER_TOKEN") or os.environ.get("RUN_MANAGER_TOKEN"),
        help="bearer token for the control plane",
    )
    parser.add_argument(
        "--worker-id",
        default=os.environ.get("RUN_WORKER_ID") or socket.gethostname(),
        help="stable worker id",
    )
    parser.add_argument(
        "--capacity",
        type=int,
        default=int(os.environ.get("RUN_WORKER_CAPACITY") or "1"),
        help="maximum active runs on this worker",
    )
    parser.add_argument(
        "--lease-ttl-seconds",
        type=int,
        default=int(os.environ.get("RUN_WORKER_LEASE_TTL_SECONDS") or "60"),
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=float(os.environ.get("RUN_WORKER_POLL_INTERVAL_SECONDS") or "2"),
    )
    parser.add_argument(
        "--heartbeat-interval-seconds",
        type=float,
        default=float(os.environ.get("RUN_WORKER_HEARTBEAT_INTERVAL_SECONDS") or "10"),
    )
    parser.add_argument(
        "--run-wait-timeout-seconds",
        type=float,
        default=float(os.environ.get("RUN_WORKER_RUN_WAIT_TIMEOUT_SECONDS") or "300"),
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=(
            Path(os.environ["RUN_WORKER_ARTIFACT_ROOT"])
            if os.environ.get("RUN_WORKER_ARTIFACT_ROOT")
            else None
        ),
        help="optional local mirror directory for worker artifacts",
    )
    parser.add_argument(
        "--metadata-json",
        default=os.environ.get("RUN_WORKER_METADATA_JSON"),
        help="additional worker metadata JSON object",
    )
    parser.add_argument("--once", action="store_true", help="claim at most one run and exit")
    args = parser.parse_args(argv)
    if not args.control_url:
        parser.error("--control-url or RUN_WORKER_CONTROL_URL is required")
    config = RemoteWorkerConfig(
        control_url=args.control_url,
        token=args.token,
        worker_id=args.worker_id,
        capacity=positive_int(args.capacity, 1),
        lease_ttl_seconds=positive_int(args.lease_ttl_seconds, 60),
        poll_interval_seconds=positive_float(args.poll_interval_seconds, 2.0),
        heartbeat_interval_seconds=positive_float(args.heartbeat_interval_seconds, 10.0),
        run_wait_timeout_seconds=positive_float(args.run_wait_timeout_seconds, 300.0),
        artifact_root=args.artifact_root,
        metadata=parse_json_object(args.metadata_json),
    )
    worker = RemoteWorkerDaemon(config)
    print(f"remote worker {config.worker_id} -> {config.control_url}")
    print(f"capacity: {config.capacity}")
    if args.once:
        return 0 if worker.run_once(wait=True) else 0
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        print("\nremote worker shutting down")
        worker.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
