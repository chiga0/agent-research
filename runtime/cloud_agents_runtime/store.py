from __future__ import annotations

import hmac
import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .events import RuntimeEvent, TERMINAL_RUN_EVENTS, utc_now
from .models import (
    AgentProfile,
    AccessProject,
    ApiToken,
    MissionEvent,
    MissionSpec,
    MissionState,
    MissionTask,
    ExecutorLease,
    RunJob,
    RunSpec,
    RunState,
    WorkerState,
)
from .profiles import builtin_profiles, latest_profiles


class RunStore:
    def __init__(self, artifact_root: Path):
        self.artifact_root = artifact_root
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.artifact_root / "runtime.db"
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._runs: dict[str, RunState] = {}
        self._jobs: dict[str, RunJob] = {}
        self._workers: dict[str, WorkerState] = {}
        self._profiles: dict[tuple[str, int], AgentProfile] = builtin_profiles()
        self._access_projects: dict[str, AccessProject] = {}
        self._api_tokens: dict[str, ApiToken] = {}
        self._missions: dict[str, MissionState] = {}
        self._mission_tasks: dict[str, list[MissionTask]] = {}
        self._mission_events: dict[str, list[MissionEvent]] = {}
        self._task_runs: dict[str, tuple[str, str]] = {}
        self._executor_leases: dict[str, ExecutorLease] = {}
        self._events: dict[str, list[RuntimeEvent]] = {}
        self._conditions: dict[str, threading.Condition] = {}
        self._event_listeners: list[Callable[[RuntimeEvent], None]] = []
        self._lock = threading.RLock()
        self._init_db()
        self._load_from_db()

    def add_event_listener(self, listener: Callable[[RuntimeEvent], None]) -> None:
        with self._lock:
            self._event_listeners.append(listener)

    def create_run(self, spec: RunSpec, run_id: str | None = None) -> RunState:
        with self._lock:
            run = RunState.create(spec, run_id=run_id)
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

    def create_profile(self, payload: dict[str, Any]) -> AgentProfile:
        with self._lock:
            profile_id = str(payload.get("id") or "").strip()
            if (profile_id, 1) in self._profiles:
                existing = self._profiles[(profile_id, 1)]
                if existing.source == "system":
                    raise ValueError("copy a built-in profile to a new id before editing")
            version = self.next_profile_version(profile_id)
            profile = AgentProfile.from_payload(payload, version=version, source="user")
            self._profiles[(profile.id, profile.version)] = profile
            self._persist_profile(profile)
            return profile

    def list_profiles(self) -> list[AgentProfile]:
        with self._lock:
            return latest_profiles(list(self._profiles.values()))

    def get_profile(
        self,
        profile_id: str,
        version: int | None = None,
    ) -> AgentProfile | None:
        with self._lock:
            if version is not None:
                return self._profiles.get((profile_id, version))
            candidates = [
                profile
                for key, profile in self._profiles.items()
                if key[0] == profile_id
            ]
            if not candidates:
                return None
            return max(candidates, key=lambda profile: profile.version)

    def next_profile_version(self, profile_id: str) -> int:
        versions = [version for key, version in self._profiles if key == profile_id]
        return (max(versions) + 1) if versions else 1

    def create_mission(
        self,
        spec: MissionSpec,
        mission_id: str | None = None,
    ) -> MissionState:
        with self._lock:
            mission = MissionState.create(spec, mission_id=mission_id)
            self._missions[mission.mission_id] = mission
            self._mission_tasks[mission.mission_id] = []
            self._mission_events[mission.mission_id] = []
            self.mission_dir(mission.mission_id).mkdir(parents=True, exist_ok=True)
            self.write_mission_json(mission.mission_id, "mission_spec.json", spec.to_dict())
            self._persist_mission(mission)
            self.append_mission_event(
                mission.mission_id,
                "mission.created",
                {"spec": spec.to_dict()},
            )
            return mission

    def get_mission(self, mission_id: str) -> MissionState | None:
        with self._lock:
            return self._missions.get(mission_id)

    def list_missions(self) -> list[MissionState]:
        with self._lock:
            return list(self._missions.values())

    def add_mission_task(self, task: MissionTask) -> MissionTask:
        with self._lock:
            self._require_mission(task.mission_id)
            tasks = self._mission_tasks.setdefault(task.mission_id, [])
            if any(existing.task_id == task.task_id for existing in tasks):
                raise ValueError(f"duplicate task id: {task.task_id}")
            tasks.append(task)
            tasks.sort(key=lambda item: (item.order, item.task_id))
            if task.run_id:
                self._task_runs[task.run_id] = (task.mission_id, task.task_id)
            self._persist_task(task)
            self._refresh_mission_counts(task.mission_id)
            return task

    def list_mission_tasks(self, mission_id: str) -> list[MissionTask]:
        with self._lock:
            self._require_mission(mission_id)
            return list(self._mission_tasks.get(mission_id, []))

    def get_mission_task(self, mission_id: str, task_id: str) -> MissionTask | None:
        with self._lock:
            for task in self._mission_tasks.get(mission_id, []):
                if task.task_id == task_id:
                    return task
            return None

    def get_task_by_run_id(self, run_id: str) -> MissionTask | None:
        with self._lock:
            location = self._task_runs.get(run_id)
            if not location:
                return None
            return self.get_mission_task(location[0], location[1])

    def update_mission_task(self, task: MissionTask) -> None:
        with self._lock:
            tasks = self._mission_tasks.get(task.mission_id, [])
            for index, existing in enumerate(tasks):
                if existing.task_id == task.task_id:
                    if existing.run_id and existing.run_id != task.run_id:
                        self._task_runs.pop(existing.run_id, None)
                    tasks[index] = task
                    if task.run_id:
                        self._task_runs[task.run_id] = (task.mission_id, task.task_id)
                    self._persist_task(task)
                    self._refresh_mission_counts(task.mission_id)
                    return
            raise KeyError(task.task_id)

    def update_mission_status(self, mission_id: str, status: str) -> None:
        with self._lock:
            mission = self._require_mission(mission_id)
            mission.status = status
            mission.updated_at = utc_now()
            self._persist_mission(mission)

    def append_mission_event(
        self,
        mission_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> MissionEvent:
        with self._lock:
            mission = self._require_mission(mission_id)
            if event_type == "mission.started" and mission.status == "created":
                mission.status = "running"
            elif event_type == "mission.completed":
                mission.status = "completed"
            elif event_type == "mission.failed":
                mission.status = "failed"
            elif event_type == "mission.cancelled":
                mission.status = "cancelled"
            elif event_type == "mission.blocked":
                mission.status = "blocked"
            events = self._mission_events.setdefault(mission_id, [])
            event = MissionEvent(
                type=event_type,
                mission_id=mission_id,
                sequence=len(events) + 1,
                data=data or {},
            )
            events.append(event)
            mission.event_count = len(events)
            mission.updated_at = event.created_at
            self._append_mission_jsonl(mission_id, "events.jsonl", event.to_dict())
            self._insert_mission_event(event)
            self._persist_mission(mission)
            return event

    def mission_events_since(
        self,
        mission_id: str,
        last_sequence: int = 0,
    ) -> list[MissionEvent]:
        with self._lock:
            self._require_mission(mission_id)
            return [
                event
                for event in self._mission_events.get(mission_id, [])
                if event.sequence > last_sequence
            ]

    def mission_snapshot(self, mission_id: str) -> dict[str, Any]:
        with self._lock:
            mission = self._require_mission(mission_id)
            return {
                **mission.to_dict(),
                "tasks": [
                    task.to_dict()
                    for task in self._mission_tasks.get(mission_id, [])
                ],
            }

    def write_mission_json(self, mission_id: str, name: str, payload: Any) -> Path:
        path = self.mission_dir(mission_id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def list_mission_artifacts(self, mission_id: str) -> list[dict[str, Any]]:
        with self._lock:
            self._require_mission(mission_id)
            mission_dir = self.mission_dir(mission_id)
            artifacts: list[dict[str, Any]] = []
            if not mission_dir.exists():
                return artifacts
            for path in sorted(mission_dir.iterdir()):
                if not path.is_file():
                    continue
                stat = path.stat()
                artifacts.append(
                    {
                        "name": path.name,
                        "size_bytes": stat.st_size,
                        "updated_at": utc_now_from_timestamp(stat.st_mtime),
                    }
                )
            return artifacts

    def enqueue_run(self, run_id: str) -> RunJob:
        with self._lock:
            self._require_run(run_id)
            job = RunJob(run_id=run_id)
            self._jobs[run_id] = job
            self._persist_job(job)
        self.append_event(run_id, "run.queued", {"queued_at": job.queued_at})
        return job

    def register_worker(
        self,
        worker_id: str,
        capacity: int,
        lease_ttl_seconds: int,
        metadata: dict[str, Any] | None = None,
    ) -> WorkerState:
        with self._lock:
            now = utc_now()
            worker = self._workers.get(worker_id)
            if worker is None:
                worker = WorkerState(
                    worker_id=worker_id,
                    capacity=capacity,
                    lease_ttl_seconds=lease_ttl_seconds,
                    heartbeat_at=now,
                    created_at=now,
                    updated_at=now,
                )
            if metadata is not None:
                worker.metadata = {**worker.metadata, **metadata}
            worker.status = "active"
            worker.capacity = capacity
            worker.lease_ttl_seconds = lease_ttl_seconds
            worker.active_count = self.active_job_count(worker_id)
            worker.heartbeat_at = now
            worker.updated_at = now
            self._workers[worker_id] = worker
            self._persist_worker(worker)
            return worker

    def heartbeat_worker(
        self,
        worker_id: str,
        capacity: int,
        lease_ttl_seconds: int,
        metadata: dict[str, Any] | None = None,
    ) -> WorkerState:
        with self._lock:
            worker = self.register_worker(
                worker_id,
                capacity,
                lease_ttl_seconds,
                metadata=metadata,
            )
            now = utc_now()
            lease_expires_at = utc_now_plus(lease_ttl_seconds)
            for job in self._jobs.values():
                if job.status != "running" or job.worker_id != worker_id:
                    continue
                if self._runs[job.run_id].status in {"completed", "failed", "cancelled"}:
                    continue
                job.heartbeat_at = now
                job.lease_expires_at = lease_expires_at
                job.updated_at = now
                self._persist_job(job)
            return worker

    def active_job_count(self, worker_id: str) -> int:
        with self._lock:
            return sum(
                1
                for job in self._jobs.values()
                if job.status == "running" and job.worker_id == worker_id
            )

    def queued_job_count(self) -> int:
        with self._lock:
            return sum(1 for job in self._jobs.values() if job.status == "queued")

    def get_job(self, run_id: str) -> RunJob | None:
        with self._lock:
            return self._jobs.get(run_id)

    def claim_next_job(
        self,
        worker_id: str,
        lease_ttl_seconds: int,
    ) -> RunJob | None:
        with self._lock:
            queued = sorted(
                (job for job in self._jobs.values() if job.status == "queued"),
                key=lambda job: (job.queued_at, job.run_id),
            )
            if not queued:
                return None
            job = queued[0]
            now = utc_now()
            job.status = "running"
            job.worker_id = worker_id
            job.started_at = job.started_at or now
            job.heartbeat_at = now
            job.lease_expires_at = utc_now_plus(lease_ttl_seconds)
            job.attempts += 1
            job.updated_at = now
            self._persist_job(job)
            worker = self._workers.get(worker_id)
            if worker:
                worker.active_count = self.active_job_count(worker_id)
                worker.updated_at = now
                self._persist_worker(worker)
        self.append_event(
            job.run_id,
            "lease.claimed",
            {
                "worker_id": worker_id,
                "attempts": job.attempts,
                "lease_expires_at": job.lease_expires_at,
            },
        )
        return job

    def cancel_job(self, run_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(run_id)
            if job is None or job.status != "queued":
                return False
            now = utc_now()
            job.status = "cancelled"
            job.completed_at = now
            job.lease_expires_at = None
            job.updated_at = now
            self._persist_job(job)
            return True

    def recover_expired_leases(self) -> list[str]:
        recovered: list[tuple[str, str | None, int]] = []
        with self._lock:
            now = datetime.now(timezone.utc)
            for job in self._jobs.values():
                if job.status != "running" or not iso_before(job.lease_expires_at, now):
                    continue
                run = self._runs[job.run_id]
                if run.status in {"completed", "failed", "cancelled"}:
                    self._finish_job(run.run_id, run.status)
                    continue
                previous_worker = job.worker_id
                job.status = "queued"
                job.worker_id = None
                job.heartbeat_at = None
                job.lease_expires_at = None
                job.updated_at = utc_now()
                self._persist_job(job)
                recovered.append((job.run_id, previous_worker, job.attempts))
        for run_id, previous_worker, attempts in recovered:
            self.append_event(
                run_id,
                "lease.expired",
                {"previous_worker_id": previous_worker, "attempts": attempts},
            )
        return [run_id for run_id, _previous_worker, _attempts in recovered]

    def fail_orphaned_jobs_for_worker(self, worker_id: str, reason: str) -> list[str]:
        orphaned: list[tuple[str, int, str | None]] = []
        with self._lock:
            for job in self._jobs.values():
                if job.status != "running" or job.worker_id != worker_id:
                    continue
                run = self._runs[job.run_id]
                if run.status in {"completed", "failed", "cancelled"}:
                    self._finish_job(run.run_id, run.status)
                    continue
                orphaned.append((job.run_id, job.attempts, job.lease_expires_at))
        for run_id, attempts, lease_expires_at in orphaned:
            self.append_event(
                run_id,
                "lease.orphaned",
                {
                    "worker_id": worker_id,
                    "attempts": attempts,
                    "lease_expires_at": lease_expires_at,
                    "reason": reason,
                },
            )
            self.append_event(
                run_id,
                "run.failed",
                {
                    "reason": reason,
                    "recovery": "orphaned_worker_run",
                },
            )
        return [run_id for run_id, _attempts, _lease_expires_at in orphaned]

    def prune_stale_workers(self, stale_after_seconds: int | None) -> list[str]:
        if not stale_after_seconds or stale_after_seconds <= 0:
            return []
        stale_cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
        pruned: list[str] = []
        with self._lock:
            for worker_id, worker in list(self._workers.items()):
                if self.active_job_count(worker_id) > 0:
                    continue
                try:
                    heartbeat_at = datetime.fromisoformat(worker.heartbeat_at)
                except ValueError:
                    heartbeat_at = datetime.min.replace(tzinfo=timezone.utc)
                if heartbeat_at > stale_cutoff:
                    continue
                pruned.append(worker_id)
                del self._workers[worker_id]
                self._db.execute("delete from workers where worker_id = ?", (worker_id,))
            if pruned:
                self._db.commit()
        return pruned

    def queue_snapshot(self, stale_after_seconds: int | None = None) -> dict[str, Any]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda job: (job.queued_at, job.run_id))
            workers = sorted(
                [
                    worker_with_stale_status(worker, stale_after_seconds)
                    for worker in self._workers.values()
                ],
                key=lambda worker: worker.worker_id,
            )
            counts: dict[str, int] = {}
            for job in jobs:
                counts[job.status] = counts.get(job.status, 0) + 1
            return {
                "counts": counts,
                "jobs": [job.to_dict() for job in jobs],
                "workers": [worker.to_dict() for worker in workers],
            }

    def upsert_executor_lease(self, lease: ExecutorLease) -> ExecutorLease:
        with self._lock:
            self._executor_leases[lease.executor_id] = lease
            self._persist_executor_lease(lease)
            return lease

    def get_executor_lease(self, executor_id: str) -> ExecutorLease | None:
        with self._lock:
            return self._executor_leases.get(executor_id)

    def get_executor_lease_for_run(self, run_id: str) -> ExecutorLease | None:
        with self._lock:
            leases = [
                lease
                for lease in self._executor_leases.values()
                if lease.run_id == run_id
            ]
            if not leases:
                return None
            return max(leases, key=lambda lease: lease.started_at)

    def list_executor_leases(self) -> list[ExecutorLease]:
        with self._lock:
            return sorted(
                self._executor_leases.values(),
                key=lambda lease: (lease.started_at, lease.executor_id),
                reverse=True,
            )

    def create_access_project(self, payload: dict[str, Any]) -> AccessProject:
        with self._lock:
            project = AccessProject.from_payload(payload)
            if project.project_id in self._access_projects:
                raise ValueError(f"project already exists: {project.project_id}")
            self._access_projects[project.project_id] = project
            self._persist_access_project(project)
            return project

    def ensure_access_project(
        self,
        project_id: str,
        display_name: str | None = None,
    ) -> AccessProject:
        with self._lock:
            existing = self._access_projects.get(project_id)
            if existing:
                return existing
            project = AccessProject(
                project_id=project_id,
                display_name=display_name or project_id,
                description="Default single-tenant project",
            )
            self._access_projects[project.project_id] = project
            self._persist_access_project(project)
            return project

    def list_access_projects(self) -> list[AccessProject]:
        with self._lock:
            return sorted(
                self._access_projects.values(),
                key=lambda project: (project.status != "active", project.project_id),
            )

    def create_api_token(self, token: ApiToken) -> ApiToken:
        with self._lock:
            if token.token_id in self._api_tokens:
                raise ValueError(f"token already exists: {token.token_id}")
            if token.project_id and token.project_id not in self._access_projects:
                raise ValueError(f"project not found: {token.project_id}")
            self._api_tokens[token.token_id] = token
            self._persist_api_token(token)
            return token

    def list_api_tokens(self) -> list[ApiToken]:
        with self._lock:
            return sorted(
                self._api_tokens.values(),
                key=lambda token: (token.status != "active", token.created_at, token.token_id),
            )

    def revoke_api_token(self, token_id: str) -> ApiToken:
        with self._lock:
            token = self._api_tokens.get(token_id)
            if token is None:
                raise KeyError(token_id)
            token.status = "revoked"
            token.revoked_at = utc_now()
            token.updated_at = token.revoked_at
            self._persist_api_token(token)
            return token

    def find_api_token_by_hash(self, token_hash: str) -> ApiToken | None:
        with self._lock:
            for token in self._api_tokens.values():
                if token.status == "active" and hmac.compare_digest(
                    token.token_hash,
                    token_hash,
                ):
                    token.last_used_at = utc_now()
                    token.updated_at = token.last_used_at
                    self._persist_api_token(token)
                    return token
            return None

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
            already_terminal = run.status in {"completed", "failed", "cancelled"}
            if already_terminal:
                pass
            elif event_type == "run.queued":
                run.status = "queued"
            elif event_type == "run.started":
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
            if event_type in TERMINAL_RUN_EVENTS:
                self._finish_job(run_id, run.status)
            self._persist_run(run)
            self._write_diagnostics(run_id)
            if event_type.startswith("permission."):
                self.write_json(run_id, f"{event_type}_{event.sequence}.json", event.to_dict())
            self._conditions[run_id].notify_all()
            listeners = list(self._event_listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                pass
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

    def raw_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            self._require_run(run_id)
            rows = self._db.execute(
                """
                select source, payload_json, created_at
                from raw_events
                where run_id = ?
                order by id
                """,
                (run_id,),
            ).fetchall()
            return [
                {
                    "source": row["source"],
                    "created_at": row["created_at"],
                    "payload": json.loads(row["payload_json"]),
                }
                for row in rows
            ]

    def write_json(self, run_id: str, name: str, payload: Any) -> Path:
        path = self.run_dir(run_id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def write_text(self, run_id: str, name: str, content: str) -> Path:
        with self._lock:
            self._require_run(run_id)
            path = safe_child_file(self.run_dir(run_id), name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return path

    def events_since(self, run_id: str, last_sequence: int = 0) -> list[RuntimeEvent]:
        with self._lock:
            self._require_run(run_id)
            return [event for event in self._events[run_id] if event.sequence > last_sequence]

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            self._require_run(run_id)
            run_dir = self.run_dir(run_id)
            artifacts: list[dict[str, Any]] = []
            if not run_dir.exists():
                return artifacts
            for path in sorted(run_dir.iterdir()):
                if not path.is_file():
                    continue
                stat = path.stat()
                artifacts.append(
                    {
                        "name": path.name,
                        "size_bytes": stat.st_size,
                        "updated_at": utc_now_from_timestamp(stat.st_mtime),
                    }
                )
            return artifacts

    def artifact_path(self, run_id: str, name: str) -> Path:
        with self._lock:
            self._require_run(run_id)
            path = safe_child_file(self.run_dir(run_id), name)
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(name)
            return path

    def mission_artifact_path(self, mission_id: str, name: str) -> Path:
        with self._lock:
            self._require_mission(mission_id)
            path = safe_child_file(self.mission_dir(mission_id), name)
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(name)
            return path

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

    def mission_dir(self, mission_id: str) -> Path:
        return self.artifact_root / "missions" / mission_id

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _append_jsonl(self, run_id: str, name: str, payload: Any) -> None:
        path = self.run_dir(run_id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def _append_mission_jsonl(self, mission_id: str, name: str, payload: Any) -> None:
        path = self.mission_dir(mission_id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def _require_run(self, run_id: str) -> RunState:
        run = self._runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    def _require_mission(self, mission_id: str) -> MissionState:
        mission = self._missions.get(mission_id)
        if mission is None:
            raise KeyError(mission_id)
        return mission

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
            create table if not exists run_jobs (
              run_id text primary key,
              status text not null,
              worker_id text,
              queued_at text not null,
              started_at text,
              completed_at text,
              heartbeat_at text,
              lease_expires_at text,
              attempts integer not null,
              updated_at text not null
            );
            create table if not exists workers (
              worker_id text primary key,
              status text not null,
              capacity integer not null,
              active_count integer not null,
              lease_ttl_seconds integer not null,
              heartbeat_at text not null,
              created_at text not null,
              updated_at text not null,
              metadata_json text not null
            );
            create table if not exists executor_leases (
              executor_id text primary key,
              run_id text not null,
              adapter text not null,
              strategy text not null,
              status text not null,
              base_url text,
              workspace text,
              port integer,
              pid integer,
              command_json text not null,
              started_at text not null,
              heartbeat_at text,
              released_at text,
              exit_code integer,
              last_error text,
              metadata_json text not null,
              updated_at text not null
            );
            create table if not exists access_projects (
              project_id text primary key,
              display_name text not null,
              description text not null,
              status text not null,
              metadata_json text not null,
              created_at text not null,
              updated_at text not null
            );
            create table if not exists api_tokens (
              token_id text primary key,
              name text not null,
              principal_id text not null,
              project_id text,
              scopes_json text not null,
              status text not null,
              token_prefix text not null,
              token_hash text not null,
              created_at text not null,
              updated_at text not null,
              revoked_at text,
              last_used_at text,
              metadata_json text not null
            );
            create table if not exists agent_profiles (
              profile_id text not null,
              version integer not null,
              profile_json text not null,
              source text not null,
              created_at text not null,
              updated_at text not null,
              primary key (profile_id, version)
            );
            create table if not exists missions (
              mission_id text primary key,
              spec_json text not null,
              status text not null,
              created_at text not null,
              updated_at text not null,
              event_count integer not null,
              task_count integer not null,
              completed_task_count integer not null,
              failed_task_count integer not null
            );
            create table if not exists mission_tasks (
              mission_id text not null,
              task_id text not null,
              title text not null,
              profile_id text not null,
              profile_version integer not null,
              prompt text not null,
              task_order integer not null,
              depends_on_json text not null,
              status text not null,
              run_id text,
              profile_snapshot_json text not null,
              result_json text not null,
              metadata_json text not null,
              created_at text not null,
              updated_at text not null,
              started_at text,
              completed_at text,
              primary key (mission_id, task_id)
            );
            create table if not exists mission_events (
              mission_id text not null,
              sequence integer not null,
              event_id text not null,
              type text not null,
              data_json text not null,
              created_at text not null,
              primary key (mission_id, sequence)
            );
            """
        )
        self._ensure_column("workers", "metadata_json", "text not null default '{}'")
        self._db.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            row["name"]
            for row in self._db.execute(f"pragma table_info({table})").fetchall()
        }
        if column not in columns:
            self._db.execute(f"alter table {table} add column {column} {definition}")

    def _load_from_db(self) -> None:
        with self._lock:
            for row in self._db.execute("select * from agent_profiles order by profile_id"):
                payload = json.loads(row["profile_json"])
                profile = AgentProfile.from_payload(
                    payload,
                    version=row["version"],
                    source=row["source"],
                )
                profile.created_at = row["created_at"]
                profile.updated_at = row["updated_at"]
                self._profiles[(profile.id, profile.version)] = profile
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
            for row in self._db.execute("select * from run_jobs order by queued_at"):
                self._jobs[row["run_id"]] = RunJob(
                    run_id=row["run_id"],
                    status=row["status"],
                    worker_id=row["worker_id"],
                    queued_at=row["queued_at"],
                    started_at=row["started_at"],
                    completed_at=row["completed_at"],
                    heartbeat_at=row["heartbeat_at"],
                    lease_expires_at=row["lease_expires_at"],
                    attempts=row["attempts"],
                    updated_at=row["updated_at"],
                )
            for row in self._db.execute("select * from workers order by worker_id"):
                self._workers[row["worker_id"]] = WorkerState(
                    worker_id=row["worker_id"],
                    status=row["status"],
                    capacity=row["capacity"],
                    active_count=row["active_count"],
                    lease_ttl_seconds=row["lease_ttl_seconds"],
                    heartbeat_at=row["heartbeat_at"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    metadata=json.loads(row["metadata_json"]),
                )
            for row in self._db.execute("select * from executor_leases order by started_at"):
                self._executor_leases[row["executor_id"]] = ExecutorLease(
                    executor_id=row["executor_id"],
                    run_id=row["run_id"],
                    adapter=row["adapter"],
                    strategy=row["strategy"],
                    status=row["status"],
                    base_url=row["base_url"],
                    workspace=row["workspace"],
                    port=row["port"],
                    pid=row["pid"],
                    command=json.loads(row["command_json"]),
                    started_at=row["started_at"],
                    heartbeat_at=row["heartbeat_at"],
                    released_at=row["released_at"],
                    exit_code=row["exit_code"],
                    last_error=row["last_error"],
                    metadata=json.loads(row["metadata_json"]),
                    updated_at=row["updated_at"],
                )
            for row in self._db.execute("select * from access_projects order by project_id"):
                self._access_projects[row["project_id"]] = AccessProject(
                    project_id=row["project_id"],
                    display_name=row["display_name"],
                    description=row["description"],
                    status=row["status"],
                    metadata=json.loads(row["metadata_json"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            for row in self._db.execute("select * from api_tokens order by created_at"):
                self._api_tokens[row["token_id"]] = ApiToken(
                    token_id=row["token_id"],
                    name=row["name"],
                    principal_id=row["principal_id"],
                    project_id=row["project_id"],
                    scopes=json.loads(row["scopes_json"]),
                    status=row["status"],
                    token_prefix=row["token_prefix"],
                    token_hash=row["token_hash"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    revoked_at=row["revoked_at"],
                    last_used_at=row["last_used_at"],
                    metadata=json.loads(row["metadata_json"]),
                )
            for row in self._db.execute("select * from missions order by created_at"):
                spec = MissionSpec.from_payload(json.loads(row["spec_json"]))
                mission = MissionState(
                    mission_id=row["mission_id"],
                    spec=spec,
                    status=row["status"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    event_count=row["event_count"],
                    task_count=row["task_count"],
                    completed_task_count=row["completed_task_count"],
                    failed_task_count=row["failed_task_count"],
                )
                self._missions[mission.mission_id] = mission
                self._mission_tasks[mission.mission_id] = []
                self._mission_events[mission.mission_id] = []
            for row in self._db.execute(
                "select * from mission_tasks order by mission_id, task_order, task_id"
            ):
                task = MissionTask(
                    mission_id=row["mission_id"],
                    task_id=row["task_id"],
                    title=row["title"],
                    profile_id=row["profile_id"],
                    profile_version=row["profile_version"],
                    prompt=row["prompt"],
                    order=row["task_order"],
                    depends_on=json.loads(row["depends_on_json"]),
                    status=row["status"],
                    run_id=row["run_id"],
                    profile_snapshot=json.loads(row["profile_snapshot_json"]),
                    result=json.loads(row["result_json"]),
                    metadata=json.loads(row["metadata_json"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    started_at=row["started_at"],
                    completed_at=row["completed_at"],
                )
                self._mission_tasks.setdefault(task.mission_id, []).append(task)
                if task.run_id:
                    self._task_runs[task.run_id] = (task.mission_id, task.task_id)
            for row in self._db.execute(
                "select * from mission_events order by mission_id, sequence"
            ):
                event = MissionEvent(
                    type=row["type"],
                    mission_id=row["mission_id"],
                    sequence=row["sequence"],
                    data=json.loads(row["data_json"]),
                    id=row["event_id"],
                    created_at=row["created_at"],
                )
                self._mission_events.setdefault(row["mission_id"], []).append(event)

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

    def _persist_job(self, job: RunJob) -> None:
        self._db.execute(
            """
            insert into run_jobs(
              run_id, status, worker_id, queued_at, started_at, completed_at,
              heartbeat_at, lease_expires_at, attempts, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(run_id) do update set
              status=excluded.status,
              worker_id=excluded.worker_id,
              started_at=excluded.started_at,
              completed_at=excluded.completed_at,
              heartbeat_at=excluded.heartbeat_at,
              lease_expires_at=excluded.lease_expires_at,
              attempts=excluded.attempts,
              updated_at=excluded.updated_at
            """,
            (
                job.run_id,
                job.status,
                job.worker_id,
                job.queued_at,
                job.started_at,
                job.completed_at,
                job.heartbeat_at,
                job.lease_expires_at,
                job.attempts,
                job.updated_at,
            ),
        )
        self._db.commit()

    def _persist_worker(self, worker: WorkerState) -> None:
        self._db.execute(
            """
            insert into workers(
              worker_id, status, capacity, active_count, lease_ttl_seconds,
              heartbeat_at, created_at, updated_at, metadata_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(worker_id) do update set
              status=excluded.status,
              capacity=excluded.capacity,
              active_count=excluded.active_count,
              lease_ttl_seconds=excluded.lease_ttl_seconds,
              heartbeat_at=excluded.heartbeat_at,
              updated_at=excluded.updated_at,
              metadata_json=excluded.metadata_json
            """,
            (
                worker.worker_id,
                worker.status,
                worker.capacity,
                worker.active_count,
                worker.lease_ttl_seconds,
                worker.heartbeat_at,
                worker.created_at,
                worker.updated_at,
                json.dumps(worker.metadata, ensure_ascii=False, sort_keys=True),
            ),
        )
        self._db.commit()

    def _persist_executor_lease(self, lease: ExecutorLease) -> None:
        self._db.execute(
            """
            insert into executor_leases(
              executor_id, run_id, adapter, strategy, status, base_url, workspace,
              port, pid, command_json, started_at, heartbeat_at, released_at,
              exit_code, last_error, metadata_json, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(executor_id) do update set
              status=excluded.status,
              base_url=excluded.base_url,
              workspace=excluded.workspace,
              port=excluded.port,
              pid=excluded.pid,
              command_json=excluded.command_json,
              heartbeat_at=excluded.heartbeat_at,
              released_at=excluded.released_at,
              exit_code=excluded.exit_code,
              last_error=excluded.last_error,
              metadata_json=excluded.metadata_json,
              updated_at=excluded.updated_at
            """,
            (
                lease.executor_id,
                lease.run_id,
                lease.adapter,
                lease.strategy,
                lease.status,
                lease.base_url,
                lease.workspace,
                lease.port,
                lease.pid,
                json.dumps(lease.command, ensure_ascii=False, sort_keys=True),
                lease.started_at,
                lease.heartbeat_at,
                lease.released_at,
                lease.exit_code,
                lease.last_error,
                json.dumps(lease.metadata, ensure_ascii=False, sort_keys=True),
                lease.updated_at,
            ),
        )
        self._db.commit()

    def _persist_access_project(self, project: AccessProject) -> None:
        self._db.execute(
            """
            insert into access_projects(
              project_id, display_name, description, status, metadata_json,
              created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?)
            on conflict(project_id) do update set
              display_name=excluded.display_name,
              description=excluded.description,
              status=excluded.status,
              metadata_json=excluded.metadata_json,
              updated_at=excluded.updated_at
            """,
            (
                project.project_id,
                project.display_name,
                project.description,
                project.status,
                json.dumps(project.metadata, ensure_ascii=False, sort_keys=True),
                project.created_at,
                project.updated_at,
            ),
        )
        self._db.commit()

    def _persist_api_token(self, token: ApiToken) -> None:
        self._db.execute(
            """
            insert into api_tokens(
              token_id, name, principal_id, project_id, scopes_json, status,
              token_prefix, token_hash, created_at, updated_at, revoked_at,
              last_used_at, metadata_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(token_id) do update set
              name=excluded.name,
              principal_id=excluded.principal_id,
              project_id=excluded.project_id,
              scopes_json=excluded.scopes_json,
              status=excluded.status,
              token_prefix=excluded.token_prefix,
              token_hash=excluded.token_hash,
              updated_at=excluded.updated_at,
              revoked_at=excluded.revoked_at,
              last_used_at=excluded.last_used_at,
              metadata_json=excluded.metadata_json
            """,
            (
                token.token_id,
                token.name,
                token.principal_id,
                token.project_id,
                json.dumps(token.scopes, ensure_ascii=False, sort_keys=True),
                token.status,
                token.token_prefix,
                token.token_hash,
                token.created_at,
                token.updated_at,
                token.revoked_at,
                token.last_used_at,
                json.dumps(token.metadata, ensure_ascii=False, sort_keys=True),
            ),
        )
        self._db.commit()

    def _persist_profile(self, profile: AgentProfile) -> None:
        self._db.execute(
            """
            insert into agent_profiles(
              profile_id, version, profile_json, source, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?)
            on conflict(profile_id, version) do update set
              profile_json=excluded.profile_json,
              source=excluded.source,
              updated_at=excluded.updated_at
            """,
            (
                profile.id,
                profile.version,
                json.dumps(profile.to_dict(), ensure_ascii=False, sort_keys=True),
                profile.source,
                profile.created_at,
                profile.updated_at,
            ),
        )
        self._db.commit()

    def _persist_mission(self, mission: MissionState) -> None:
        self._db.execute(
            """
            insert into missions(
              mission_id, spec_json, status, created_at, updated_at, event_count,
              task_count, completed_task_count, failed_task_count
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(mission_id) do update set
              spec_json=excluded.spec_json,
              status=excluded.status,
              updated_at=excluded.updated_at,
              event_count=excluded.event_count,
              task_count=excluded.task_count,
              completed_task_count=excluded.completed_task_count,
              failed_task_count=excluded.failed_task_count
            """,
            (
                mission.mission_id,
                json.dumps(mission.spec.to_dict(), ensure_ascii=False, sort_keys=True),
                mission.status,
                mission.created_at,
                mission.updated_at,
                mission.event_count,
                mission.task_count,
                mission.completed_task_count,
                mission.failed_task_count,
            ),
        )
        self._db.commit()

    def _persist_task(self, task: MissionTask) -> None:
        self._db.execute(
            """
            insert into mission_tasks(
              mission_id, task_id, title, profile_id, profile_version, prompt,
              task_order, depends_on_json, status, run_id, profile_snapshot_json,
              result_json, metadata_json, created_at, updated_at, started_at,
              completed_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(mission_id, task_id) do update set
              title=excluded.title,
              profile_id=excluded.profile_id,
              profile_version=excluded.profile_version,
              prompt=excluded.prompt,
              task_order=excluded.task_order,
              depends_on_json=excluded.depends_on_json,
              status=excluded.status,
              run_id=excluded.run_id,
              profile_snapshot_json=excluded.profile_snapshot_json,
              result_json=excluded.result_json,
              metadata_json=excluded.metadata_json,
              updated_at=excluded.updated_at,
              started_at=excluded.started_at,
              completed_at=excluded.completed_at
            """,
            (
                task.mission_id,
                task.task_id,
                task.title,
                task.profile_id,
                task.profile_version,
                task.prompt,
                task.order,
                json.dumps(task.depends_on, ensure_ascii=False, sort_keys=True),
                task.status,
                task.run_id,
                json.dumps(task.profile_snapshot, ensure_ascii=False, sort_keys=True),
                json.dumps(task.result, ensure_ascii=False, sort_keys=True),
                json.dumps(task.metadata, ensure_ascii=False, sort_keys=True),
                task.created_at,
                task.updated_at,
                task.started_at,
                task.completed_at,
            ),
        )
        self._db.commit()

    def _insert_mission_event(self, event: MissionEvent) -> None:
        self._db.execute(
            """
            insert or ignore into mission_events(
              mission_id, sequence, event_id, type, data_json, created_at
            ) values (?, ?, ?, ?, ?, ?)
            """,
            (
                event.mission_id,
                event.sequence,
                event.id,
                event.type,
                json.dumps(event.data, ensure_ascii=False, sort_keys=True),
                event.created_at,
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

    def _finish_job(self, run_id: str, terminal_status: str) -> None:
        job = self._jobs.get(run_id)
        if job is None or job.status in {"completed", "failed", "cancelled"}:
            return
        now = utc_now()
        job.status = terminal_status
        job.completed_at = now
        job.heartbeat_at = now
        job.lease_expires_at = None
        job.updated_at = now
        self._persist_job(job)
        if job.worker_id and job.worker_id in self._workers:
            worker = self._workers[job.worker_id]
            worker.active_count = self.active_job_count(job.worker_id)
            worker.updated_at = now
            self._persist_worker(worker)

    def _refresh_mission_counts(self, mission_id: str) -> None:
        mission = self._missions.get(mission_id)
        if mission is None:
            return
        tasks = self._mission_tasks.get(mission_id, [])
        mission.task_count = len(tasks)
        mission.completed_task_count = sum(1 for task in tasks if task.status == "completed")
        mission.failed_task_count = sum(1 for task in tasks if task.status == "failed")
        mission.updated_at = utc_now()
        self._persist_mission(mission)

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
            "workspace": run.spec.workspace,
            "resource_policy": run.spec.metadata.get("resource_policy"),
            "artifact_dir": str(self.run_dir(run_id)),
        }
        job = self._jobs.get(run_id)
        if job:
            diagnostics["job"] = job.to_dict()
        executor = self.get_executor_lease_for_run(run_id)
        if executor:
            diagnostics["executor"] = executor.to_dict()
        self.write_json(run_id, "diagnostics.json", diagnostics)


def utc_now_from_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="milliseconds")


def utc_now_plus(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(
        timespec="milliseconds"
    )


def iso_before(value: str | None, moment: datetime) -> bool:
    if not value:
        return False
    try:
        return datetime.fromisoformat(value) <= moment
    except ValueError:
        return False


def worker_with_stale_status(
    worker: WorkerState,
    stale_after_seconds: int | None,
) -> WorkerState:
    if not stale_after_seconds or stale_after_seconds <= 0:
        return worker
    try:
        heartbeat_at = datetime.fromisoformat(worker.heartbeat_at)
    except ValueError:
        heartbeat_at = datetime.min.replace(tzinfo=timezone.utc)
    stale_cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
    if heartbeat_at > stale_cutoff:
        return worker
    copy = WorkerState(
        worker_id=worker.worker_id,
        status="stale",
        capacity=worker.capacity,
        active_count=worker.active_count,
        lease_ttl_seconds=worker.lease_ttl_seconds,
        heartbeat_at=worker.heartbeat_at,
        created_at=worker.created_at,
        updated_at=worker.updated_at,
        metadata=worker.metadata,
    )
    return copy


def safe_child_file(parent: Path, name: str) -> Path:
    candidate = Path(name)
    if candidate.name != name or name in {"", ".", ".."}:
        raise ValueError("artifact name must be a file name")
    return parent / name
