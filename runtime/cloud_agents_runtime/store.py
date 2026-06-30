from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .events import RuntimeEvent, TERMINAL_RUN_EVENTS, utc_now
from .models import RunSpec, RunState


class RunStore:
    def __init__(self, artifact_root: Path):
        self.artifact_root = artifact_root
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self._runs: dict[str, RunState] = {}
        self._events: dict[str, list[RuntimeEvent]] = {}
        self._conditions: dict[str, threading.Condition] = {}
        self._lock = threading.RLock()

    def create_run(self, spec: RunSpec) -> RunState:
        with self._lock:
            run = RunState.create(spec)
            self._runs[run.run_id] = run
            self._events[run.run_id] = []
            self._conditions[run.run_id] = threading.Condition(self._lock)
            run_dir = self.run_dir(run.run_id)
            run_dir.mkdir(parents=True, exist_ok=True)
            self.write_json(run.run_id, "run_spec.json", spec.to_dict())
            self.append_event(run.run_id, "run.created", {"spec": spec.to_dict()})
            return run

    def get_run(self, run_id: str) -> RunState | None:
        with self._lock:
            return self._runs.get(run_id)

    def list_runs(self) -> list[RunState]:
        with self._lock:
            return list(self._runs.values())

    def update_status(self, run_id: str, status: str) -> None:
        with self._lock:
            run = self._require_run(run_id)
            run.status = status
            run.updated_at = utc_now()

    def set_adapter_run_id(self, run_id: str, adapter_run_id: str) -> None:
        with self._lock:
            run = self._require_run(run_id)
            run.adapter_run_id = adapter_run_id
            run.updated_at = utc_now()

    def increment_prompt_count(self, run_id: str) -> int:
        with self._lock:
            run = self._require_run(run_id)
            run.prompt_count += 1
            run.updated_at = utc_now()
            return run.prompt_count

    def append_event(
        self, run_id: str, event_type: str, data: dict[str, Any] | None = None
    ) -> RuntimeEvent:
        with self._lock:
            run = self._require_run(run_id)
            if event_type == "run.started":
                run.status = "running"
            elif event_type == "run.completed":
                run.status = "completed"
            elif event_type == "run.failed":
                run.status = "failed"
            elif event_type == "run.cancelled":
                run.status = "cancelled"
            elif run.status == "created" and event_type.startswith("input."):
                run.status = "queued"

            events = self._events[run_id]
            event = RuntimeEvent(
                type=event_type,
                run_id=run_id,
                sequence=len(events) + 1,
                data=data or {},
            )
            events.append(event)
            run.event_count = len(events)
            run.updated_at = event.created_at
            self._append_jsonl(run_id, "events.jsonl", event.to_dict())
            self._conditions[run_id].notify_all()
            return event

    def append_raw_event(self, run_id: str, source: str, payload: Any) -> None:
        self._append_jsonl(
            run_id,
            "raw_events.jsonl",
            {"source": source, "created_at": utc_now(), "payload": payload},
        )

    def write_json(self, run_id: str, name: str, payload: Any) -> Path:
        path = self.run_dir(run_id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def events_since(self, run_id: str, last_sequence: int = 0) -> list[RuntimeEvent]:
        with self._lock:
            self._require_run(run_id)
            return [event for event in self._events[run_id] if event.sequence > last_sequence]

    def wait_for_events(
        self, run_id: str, last_sequence: int, timeout: float
    ) -> list[RuntimeEvent]:
        with self._lock:
            self._require_run(run_id)
            condition = self._conditions[run_id]
            if not any(event.sequence > last_sequence for event in self._events[run_id]):
                condition.wait(timeout=timeout)
            return self.events_since(run_id, last_sequence)

    def is_terminal(self, run_id: str) -> bool:
        with self._lock:
            run = self._require_run(run_id)
            return run.status in {"completed", "failed", "cancelled"} or any(
                event.type in TERMINAL_RUN_EVENTS for event in self._events[run_id]
            )

    def run_dir(self, run_id: str) -> Path:
        return self.artifact_root / run_id

    def _append_jsonl(self, run_id: str, name: str, payload: Any) -> None:
        path = self.run_dir(run_id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def _require_run(self, run_id: str) -> RunState:
        run = self._runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

