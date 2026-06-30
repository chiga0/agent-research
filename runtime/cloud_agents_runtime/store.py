from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .events import RuntimeEvent, TERMINAL_RUN_EVENTS, utc_now
from .models import RunSpec, RunState


class RunStore:
    def __init__(self, artifact_root: Path):
        self.artifact_root = artifact_root
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.artifact_root / "runtime.db"
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._runs: dict[str, RunState] = {}
        self._events: dict[str, list[RuntimeEvent]] = {}
        self._conditions: dict[str, threading.Condition] = {}
        self._lock = threading.RLock()
        self._init_db()
        self._load_from_db()

    def create_run(self, spec: RunSpec) -> RunState:
        with self._lock:
            run = RunState.create(spec)
            self._runs[run.run_id] = run
            self._events[run.run_id] = []
            self._conditions[run.run_id] = threading.Condition(self._lock)
            run_dir = self.run_dir(run.run_id)
            run_dir.mkdir(parents=True, exist_ok=True)
            self.write_json(run.run_id, "run_spec.json", spec.to_dict())
            self._persist_run(run)
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
            self._persist_run(run)

    def set_adapter_run_id(self, run_id: str, adapter_run_id: str) -> None:
        with self._lock:
            run = self._require_run(run_id)
            run.adapter_run_id = adapter_run_id
            run.updated_at = utc_now()
            self._persist_run(run)

    def increment_prompt_count(self, run_id: str) -> int:
        with self._lock:
            run = self._require_run(run_id)
            run.prompt_count += 1
            run.updated_at = utc_now()
            self._persist_run(run)
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
            self._insert_event(event)
            self._persist_run(run)
            self._write_diagnostics(run_id)
            if event_type.startswith("permission."):
                self.write_json(run_id, f"{event_type}_{event.sequence}.json", event.to_dict())
            self._conditions[run_id].notify_all()
            return event

    def append_raw_event(self, run_id: str, source: str, payload: Any) -> None:
        self._append_jsonl(
            run_id,
            "raw_events.jsonl",
            {"source": source, "created_at": utc_now(), "payload": payload},
        )
        with self._lock:
            self._db.execute(
                """
                insert into raw_events(
                  run_id, source, payload_json, created_at
                ) values (?, ?, ?, ?)
                """,
                (run_id, source, json.dumps(payload, ensure_ascii=False), utc_now()),
            )
            self._db.commit()

    def write_json(self, run_id: str, name: str, payload: Any) -> Path:
        path = self.run_dir(run_id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def events_since(self, run_id: str, last_sequence: int = 0) -> list[RuntimeEvent]:
        with self._lock:
            self._require_run(run_id)
            return [event for event in self._events[run_id] if event.sequence > last_sequence]

    def max_sequence(self, run_id: str) -> int:
        with self._lock:
            self._require_run(run_id)
            return len(self._events[run_id])

    def record_gap_if_needed(self, run_id: str, requested_last_sequence: int) -> int:
        with self._lock:
            available = self.max_sequence(run_id)
            if requested_last_sequence <= available:
                return requested_last_sequence
            self.append_event(
                run_id,
                "event.gap_detected",
                {
                    "requested_last_sequence": requested_last_sequence,
                    "available_last_sequence": available,
                },
            )
            return available

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

    def _init_db(self) -> None:
        self._db.executescript(
            """
            create table if not exists runs (
              run_id text primary key,
              spec_json text not null,
              status text not null,
              adapter_run_id text,
              created_at text not null,
              updated_at text not null,
              event_count integer not null,
              prompt_count integer not null
            );
            create table if not exists run_events (
              run_id text not null,
              sequence integer not null,
              event_id text not null,
              type text not null,
              data_json text not null,
              created_at text not null,
              primary key (run_id, sequence)
            );
            create table if not exists raw_events (
              id integer primary key autoincrement,
              run_id text not null,
              source text not null,
              payload_json text not null,
              created_at text not null
            );
            """
        )
        self._db.commit()

    def _load_from_db(self) -> None:
        with self._lock:
            for row in self._db.execute("select * from runs order by created_at"):
                spec = RunSpec.from_payload(json.loads(row["spec_json"]))
                run = RunState(
                    run_id=row["run_id"],
                    spec=spec,
                    status=row["status"],
                    adapter_run_id=row["adapter_run_id"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    event_count=row["event_count"],
                    prompt_count=row["prompt_count"],
                )
                self._runs[run.run_id] = run
                self._events[run.run_id] = []
                self._conditions[run.run_id] = threading.Condition(self._lock)
            for row in self._db.execute("select * from run_events order by run_id, sequence"):
                event = RuntimeEvent(
                    type=row["type"],
                    run_id=row["run_id"],
                    sequence=row["sequence"],
                    data=json.loads(row["data_json"]),
                    id=row["event_id"],
                    created_at=row["created_at"],
                )
                self._events.setdefault(row["run_id"], []).append(event)

    def _persist_run(self, run: RunState) -> None:
        self._db.execute(
            """
            insert into runs(
              run_id, spec_json, status, adapter_run_id, created_at, updated_at,
              event_count, prompt_count
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(run_id) do update set
              spec_json=excluded.spec_json,
              status=excluded.status,
              adapter_run_id=excluded.adapter_run_id,
              updated_at=excluded.updated_at,
              event_count=excluded.event_count,
              prompt_count=excluded.prompt_count
            """,
            (
                run.run_id,
                json.dumps(run.spec.to_dict(), ensure_ascii=False, sort_keys=True),
                run.status,
                run.adapter_run_id,
                run.created_at,
                run.updated_at,
                run.event_count,
                run.prompt_count,
            ),
        )
        self._db.commit()

    def _insert_event(self, event: RuntimeEvent) -> None:
        self._db.execute(
            """
            insert or ignore into run_events(
              run_id, sequence, event_id, type, data_json, created_at
            ) values (?, ?, ?, ?, ?, ?)
            """,
            (
                event.run_id,
                event.sequence,
                event.id,
                event.type,
                json.dumps(event.data, ensure_ascii=False, sort_keys=True),
                event.created_at,
            ),
        )
        self._db.commit()

    def _write_diagnostics(self, run_id: str) -> None:
        run = self._require_run(run_id)
        diagnostics = {
            "run_id": run.run_id,
            "status": run.status,
            "adapter": run.spec.adapter,
            "adapter_run_id": run.adapter_run_id,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "event_count": run.event_count,
            "prompt_count": run.prompt_count,
            "artifact_dir": str(self.run_dir(run_id)),
        }
        self.write_json(run_id, "diagnostics.json", diagnostics)
