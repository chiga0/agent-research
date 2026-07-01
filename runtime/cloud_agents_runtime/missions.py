from __future__ import annotations

import copy
import threading
from typing import TYPE_CHECKING, Any

from .events import RuntimeEvent, utc_now
from .models import AgentProfile, MissionSpec, MissionTask, RunSpec, clean_identifier
from .review_gate import (
    ReviewGate,
    is_review_gate_task,
    load_review_gate,
    review_gate_artifact_name,
)

if TYPE_CHECKING:
    from .manager import RunManager


TERMINAL_MISSION_STATUSES = {"completed", "failed", "cancelled", "blocked"}
TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled", "blocked"}


DEFAULT_SEQUENTIAL_TASKS: list[dict[str, Any]] = [
    {
        "id": "plan",
        "title": "Plan mission",
        "profile": "planner",
        "prompt": "Read the goal, inspect available context, and produce an execution plan.",
    },
    {
        "id": "code",
        "title": "Implement solution",
        "profile": "coder",
        "depends_on": ["plan"],
        "prompt": "Implement the planned changes in the isolated workspace.",
    },
    {
        "id": "test",
        "title": "Validate solution",
        "profile": "tester",
        "depends_on": ["code"],
        "prompt": "Run focused validation and produce reproducible test evidence.",
    },
    {
        "id": "review",
        "title": "Review result",
        "profile": "reviewer",
        "depends_on": ["test"],
        "prompt": "Review the implementation and test evidence for blocking risks.",
    },
    {
        "id": "report",
        "title": "Write final report",
        "profile": "doc-writer",
        "depends_on": ["review"],
        "prompt": "Summarize the mission result, artifacts, risks, and follow-up work.",
    },
]


DEFAULT_FANOUT_TASKS: list[dict[str, Any]] = [
    DEFAULT_SEQUENTIAL_TASKS[0],
    {
        "id": "code",
        "title": "Implement solution",
        "profile": "coder",
        "depends_on": ["plan"],
        "prompt": "Implement a solution from the plan and publish implementation artifacts.",
    },
    {
        "id": "test",
        "title": "Plan validation",
        "profile": "tester",
        "depends_on": ["plan"],
        "prompt": "Design and run validation that can execute independently from coding.",
    },
    {
        "id": "review",
        "title": "Risk review",
        "profile": "reviewer",
        "depends_on": ["plan"],
        "prompt": "Review the plan and implementation constraints for likely risks.",
    },
    {
        "id": "report",
        "title": "Fan-in report",
        "profile": "doc-writer",
        "depends_on": ["code", "test", "review"],
        "prompt": "Merge the child artifacts into one final mission report.",
    },
]


class MissionManager:
    def __init__(self, run_manager: "RunManager"):
        self.run_manager = run_manager
        self.store = run_manager.store
        self._lock = threading.RLock()

    def reconcile(self) -> None:
        for mission in self.store.list_missions():
            if mission.status in TERMINAL_MISSION_STATUSES:
                continue
            for task in self.store.list_mission_tasks(mission.mission_id):
                if task.run_id:
                    self.sync_task_from_run(task.run_id, None)
                elif task.status == "queued":
                    task.status = "pending"
                    task.updated_at = utc_now()
                    self.store.update_mission_task(task)
                    self.store.append_mission_event(
                        task.mission_id,
                        "task.requeued",
                        {"task_id": task.task_id, "reason": "queued without run on restart"},
                    )
            self.drain_mission(mission.mission_id)

    def create_profile(self, payload: dict[str, Any]) -> AgentProfile:
        return self.store.create_profile(payload)

    def create_mission(self, payload: dict[str, Any]) -> dict[str, Any]:
        spec = MissionSpec.from_payload(payload)
        definitions = build_task_definitions(spec)
        resolved: list[tuple[dict[str, Any], AgentProfile]] = []
        for definition in definitions:
            profile_id = definition["profile"]
            version = definition.get("profile_version")
            profile = self.store.get_profile(profile_id, version)
            if profile is None:
                raise ValueError(f"unknown profile: {profile_id}")
            resolved.append((definition, profile))

        mission = self.store.create_mission(spec)
        for order, (definition, profile) in enumerate(resolved, start=1):
            task = MissionTask(
                mission_id=mission.mission_id,
                task_id=definition["id"],
                title=definition["title"],
                profile_id=profile.id,
                profile_version=profile.version,
                prompt=definition["prompt"],
                order=order,
                depends_on=list(definition.get("depends_on") or []),
                profile_snapshot=profile.to_dict(),
                metadata=dict(definition.get("metadata") or {}),
            )
            self.store.add_mission_task(task)
            self.store.append_mission_event(
                mission.mission_id,
                "task.created",
                {
                    "task_id": task.task_id,
                    "profile": profile.id,
                    "depends_on": task.depends_on,
                },
            )
        self.store.append_mission_event(
            mission.mission_id,
            "mission.started",
            {"task_count": len(resolved), "strategy": spec.strategy},
        )
        self._write_manifest(mission.mission_id)
        self.drain_mission(mission.mission_id)
        return self.store.mission_snapshot(mission.mission_id)

    def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        if self.store.get_mission(mission_id) is None:
            return None
        return self.store.mission_snapshot(mission_id)

    def cancel_mission(self, mission_id: str, reason: str | None = None) -> dict[str, Any]:
        mission = self.store.get_mission(mission_id)
        if mission is None:
            raise KeyError(mission_id)
        if mission.status in TERMINAL_MISSION_STATUSES:
            self.store.append_mission_event(
                mission_id,
                "mission.cancel_ignored",
                {"reason": "mission already terminal"},
            )
            return self.store.mission_snapshot(mission_id)

        reason_text = reason or "mission cancelled"
        tasks = self.store.list_mission_tasks(mission_id)
        for task in tasks:
            if not task.run_id and task.status not in TERMINAL_TASK_STATUSES:
                task.status = "cancelled"
                task.completed_at = utc_now()
                task.updated_at = task.completed_at
                task.result = {"reason": reason_text}
                self.store.update_mission_task(task)
                self.store.append_mission_event(
                    mission_id,
                    "task.cancelled",
                    {"task_id": task.task_id, "reason": reason_text},
                )
        for task in tasks:
            if task.run_id and task.status not in TERMINAL_TASK_STATUSES:
                self.run_manager.cancel(task.run_id, reason_text)
        current = self.store.get_mission(mission_id)
        if current and current.status not in TERMINAL_MISSION_STATUSES:
            self.store.append_mission_event(
                mission_id,
                "mission.cancelled",
                {"reason": reason_text},
            )
        self._write_manifest(mission_id)
        return self.store.mission_snapshot(mission_id)

    def handle_run_event(self, event: RuntimeEvent) -> None:
        if event.type in {
            "run.queued",
            "run.started",
            "run.completed",
            "run.failed",
            "run.cancelled",
        }:
            self.sync_task_from_run(event.run_id, event)

    def drain_mission(self, mission_id: str) -> None:
        ready = self._claim_ready_tasks(mission_id)
        for task in ready:
            self._start_task(task)
        self._complete_if_done(mission_id)

    def sync_task_from_run(self, run_id: str, event: RuntimeEvent | None) -> None:
        task = self.store.get_task_by_run_id(run_id)
        if task is None:
            return
        run = self.run_manager.get_run(run_id)
        if run is None:
            return
        next_status = run_status_to_task_status(run.status)
        if next_status is None:
            return

        previous_status = task.status
        if previous_status == next_status and next_status not in TERMINAL_TASK_STATUSES:
            return
        task.status = next_status
        task.updated_at = utc_now()
        if next_status == "running" and not task.started_at:
            task.started_at = task.updated_at
        if next_status in TERMINAL_TASK_STATUSES:
            previous_gate = task.result.get("review_gate")
            task.completed_at = task.updated_at
            task.result = {
                "run_id": run.run_id,
                "status": run.status,
                "artifacts": self.store.list_artifacts(run.run_id),
                "final_event": event.to_dict() if event else None,
            }
            if isinstance(previous_gate, dict):
                task.result["review_gate"] = previous_gate
        self.store.update_mission_task(task)
        if previous_status != next_status:
            self.store.append_mission_event(
                task.mission_id,
                f"task.{next_status}",
                {"task_id": task.task_id, "run_id": run.run_id},
            )
        self._write_task_artifact(task)

        if next_status == "completed":
            gate_blocked = self._evaluate_review_gate_if_needed(task, run.run_id)
            if not gate_blocked:
                self.drain_mission(task.mission_id)
        elif next_status in {"failed", "cancelled"}:
            self._finish_mission(
                task.mission_id,
                next_status,
                f"task {task.task_id} {next_status}",
            )

    def _claim_ready_tasks(self, mission_id: str) -> list[MissionTask]:
        with self._lock:
            mission = self.store.get_mission(mission_id)
            if mission is None or mission.status in TERMINAL_MISSION_STATUSES:
                return []
            tasks = self.store.list_mission_tasks(mission_id)
            completed = {task.task_id for task in tasks if task.status == "completed"}
            ready: list[MissionTask] = []
            for task in tasks:
                if task.status != "pending":
                    continue
                if not all(dependency in completed for dependency in task.depends_on):
                    continue
                task.status = "queued"
                task.updated_at = utc_now()
                self.store.update_mission_task(task)
                self.store.append_mission_event(
                    mission_id,
                    "task.queued",
                    {"task_id": task.task_id, "depends_on": task.depends_on},
                )
                ready.append(task)
            return ready

    def _start_task(self, task: MissionTask) -> None:
        current_mission = self.store.get_mission(task.mission_id)
        current_task = self.store.get_mission_task(task.mission_id, task.task_id)
        if (
            current_mission is None
            or current_mission.status in TERMINAL_MISSION_STATUSES
            or current_task is None
            or current_task.status != "queued"
        ):
            return
        task = current_task
        try:
            run_spec = self._run_spec_for_task(task)
            run = self.run_manager.create_run(run_spec)
        except Exception as exc:
            task.status = "failed"
            task.completed_at = utc_now()
            task.updated_at = task.completed_at
            task.result = {"error": str(exc)}
            self.store.update_mission_task(task)
            self.store.append_mission_event(
                task.mission_id,
                "task.failed",
                {"task_id": task.task_id, "error": str(exc)},
            )
            self._finish_mission(task.mission_id, "failed", str(exc))
            return

        task.run_id = run.run_id
        task.status = run_status_to_task_status(run.status) or "queued"
        task.updated_at = utc_now()
        if task.status == "running":
            task.started_at = task.updated_at
        self.store.update_mission_task(task)
        self.store.append_mission_event(
            task.mission_id,
            "task.run_created",
            {"task_id": task.task_id, "run_id": run.run_id, "status": run.status},
        )
        self._write_task_artifact(task)
        self._write_manifest(task.mission_id)
        self.sync_task_from_run(run.run_id, None)

    def _run_spec_for_task(self, task: MissionTask) -> RunSpec:
        mission = self.store.get_mission(task.mission_id)
        if mission is None:
            raise KeyError(task.mission_id)
        profile = task.profile_snapshot
        runtime = dict(profile.get("runtime") or {})
        limits = dict(profile.get("limits") or {})
        dependency_refs = self._dependency_refs(task)
        prompt = compose_task_prompt(mission.spec, task, dependency_refs)
        metadata = dict(mission.spec.metadata)
        metadata.update(
            {
                "mission_id": task.mission_id,
                "mission_goal": mission.spec.goal,
                "mission_strategy": mission.spec.strategy,
                "task_id": task.task_id,
                "task_title": task.title,
                "task_profile": task.profile_id,
                "profile_snapshot": profile,
                "dependency_artifacts": dependency_refs,
            }
        )
        return RunSpec(
            prompt=prompt,
            adapter=mission.spec.adapter or runtime.get("preferred_adapter") or "fake",
            repo=mission.spec.repo,
            workspace=mission.spec.workspace,
            model=mission.spec.model or runtime.get("model"),
            sandbox=dict(mission.spec.sandbox),
            timeout_seconds=mission.spec.timeout_seconds or limits.get("timeout_seconds"),
            metadata=metadata,
        )

    def _dependency_refs(self, task: MissionTask) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        for dependency_id in task.depends_on:
            dependency = self.store.get_mission_task(task.mission_id, dependency_id)
            if dependency is None:
                continue
            refs.append(
                {
                    "task_id": dependency.task_id,
                    "title": dependency.title,
                    "profile": dependency.profile_id,
                    "run_id": dependency.run_id,
                    "status": dependency.status,
                    "artifacts": dependency.result.get("artifacts", []),
                }
            )
        return refs

    def _evaluate_review_gate_if_needed(self, task: MissionTask, run_id: str) -> bool:
        if not is_review_gate_task(task.profile_snapshot):
            return False
        existing = task.result.get("review_gate")
        if isinstance(existing, dict):
            if existing.get("blocks"):
                self._block_mission(task.mission_id, existing)
                return True
            return False

        artifact_name = review_gate_artifact_name(task.profile_snapshot)
        gate = load_review_gate(self.store.run_dir(run_id), artifact_name)
        gate_data = gate.to_dict()
        task.result = {**task.result, "review_gate": gate_data}
        task.updated_at = utc_now()
        self.store.update_mission_task(task)
        self._write_task_artifact(task)
        self.store.write_mission_json(task.mission_id, "review_gate.json", gate_data)

        event_type = review_gate_event_type(gate)
        self.store.append_mission_event(
            task.mission_id,
            event_type,
            {
                "task_id": task.task_id,
                "run_id": run_id,
                "gate": gate_data,
            },
        )
        if gate.blocks:
            self._block_mission(task.mission_id, gate_data)
            return True
        self._write_manifest(task.mission_id)
        return False

    def _complete_if_done(self, mission_id: str) -> None:
        mission = self.store.get_mission(mission_id)
        if mission is None or mission.status in TERMINAL_MISSION_STATUSES:
            return
        tasks = self.store.list_mission_tasks(mission_id)
        if tasks and all(task.status == "completed" for task in tasks):
            self._write_final_report(mission_id)
            self.store.append_mission_event(
                mission_id,
                "mission.completed",
                {"task_count": len(tasks)},
            )
            self._write_manifest(mission_id)

    def _finish_mission(self, mission_id: str, status: str, reason: str) -> None:
        mission = self.store.get_mission(mission_id)
        if mission is None or mission.status in TERMINAL_MISSION_STATUSES:
            return
        terminal = "mission.cancelled" if status == "cancelled" else "mission.failed"
        for task in self.store.list_mission_tasks(mission_id):
            if task.status == "pending":
                task.status = "blocked"
                task.completed_at = utc_now()
                task.updated_at = task.completed_at
                task.result = {"reason": reason}
                self.store.update_mission_task(task)
                self.store.append_mission_event(
                    mission_id,
                    "task.blocked",
                    {"task_id": task.task_id, "reason": reason},
                )
        self.store.append_mission_event(mission_id, terminal, {"reason": reason})
        self._write_manifest(mission_id)

    def _block_mission(self, mission_id: str, gate: dict[str, Any]) -> None:
        mission = self.store.get_mission(mission_id)
        if mission is None or mission.status in TERMINAL_MISSION_STATUSES:
            return
        for task in self.store.list_mission_tasks(mission_id):
            if task.status in {"pending", "queued"} and not task.run_id:
                task.status = "blocked"
                task.completed_at = utc_now()
                task.updated_at = task.completed_at
                task.result = {"reason": gate.get("reason"), "review_gate": gate}
                self.store.update_mission_task(task)
                self.store.append_mission_event(
                    mission_id,
                    "task.blocked",
                    {"task_id": task.task_id, "reason": gate.get("reason")},
                )
        self._write_final_report(mission_id)
        self.store.append_mission_event(
            mission_id,
            "mission.blocked",
            {"reason": gate.get("reason"), "gate": gate},
        )
        self._write_manifest(mission_id)

    def _write_task_artifact(self, task: MissionTask) -> None:
        self.store.write_mission_json(
            task.mission_id,
            f"task_{task.task_id}.json",
            task.to_dict(),
        )

    def _write_manifest(self, mission_id: str) -> None:
        self.store.write_mission_json(
            mission_id,
            "mission_manifest.json",
            self.store.mission_snapshot(mission_id),
        )

    def _write_final_report(self, mission_id: str) -> None:
        mission = self.store.get_mission(mission_id)
        if mission is None:
            return
        tasks = self.store.list_mission_tasks(mission_id)
        gate_summaries = [
            task.result["review_gate"]
            for task in tasks
            if isinstance(task.result.get("review_gate"), dict)
        ]
        lines = [
            f"# Mission Report: {mission.mission_id}",
            "",
            f"Goal: {mission.spec.goal}",
            "",
            "| Task | Profile | Status | Run | Artifacts |",
            "| --- | --- | --- | --- | --- |",
        ]
        for task in tasks:
            artifact_names = [
                artifact["name"]
                for artifact in task.result.get("artifacts", [])
                if isinstance(artifact, dict)
            ]
            lines.append(
                "| "
                f"{task.task_id} | {task.profile_id} | {task.status} | "
                f"{task.run_id or '-'} | {', '.join(artifact_names) or '-'} |"
            )
        if gate_summaries:
            lines.extend(["", "## Review Gate", ""])
            for gate in gate_summaries:
                lines.append(
                    "- "
                    f"decision={gate.get('decision')}, "
                    f"effective={gate.get('effective_decision')}, "
                    f"severity={gate.get('severity')}, "
                    f"blocks={gate.get('blocks')}, "
                    f"reason={gate.get('reason')}"
                )
        lines.extend(
            [
                "",
                "All child work was coordinated through SAEU run artifacts and events.",
                "Runtime SubAgents, if any, remain internal to their owning SAEU.",
                "",
            ]
        )
        self.store.write_mission_json(mission_id, "final_report.md", "\n".join(lines))


def build_task_definitions(spec: MissionSpec) -> list[dict[str, Any]]:
    if spec.strategy == "custom":
        payloads = copy.deepcopy(spec.tasks)
    elif spec.strategy == "fanout":
        payloads = copy.deepcopy(DEFAULT_FANOUT_TASKS)
    else:
        payloads = copy.deepcopy(DEFAULT_SEQUENTIAL_TASKS)

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, payload in enumerate(payloads, start=1):
        if not isinstance(payload, dict):
            raise ValueError("each task must be an object")
        profile = payload.get("profile") or payload.get("profile_id") or "coder"
        profile_id = clean_identifier(profile, "task profile")
        raw_id = payload.get("id") or f"task_{index}_{profile_id}"
        task_id = clean_identifier(raw_id, "task id")
        if task_id in seen:
            raise ValueError(f"duplicate task id: {task_id}")
        seen.add(task_id)
        depends_on = payload.get("depends_on") or []
        if not isinstance(depends_on, list):
            raise ValueError("depends_on must be a list")
        normalized.append(
            {
                "id": task_id,
                "title": str(payload.get("title") or task_id.replace("_", " ").title()),
                "profile": profile_id,
                "profile_version": payload.get("profile_version"),
                "prompt": str(payload.get("prompt") or ""),
                "depends_on": [
                    clean_identifier(dependency, "dependency id")
                    for dependency in depends_on
                ],
                "metadata": dict(payload.get("metadata") or {}),
            }
        )
    validate_task_graph(normalized)
    return normalized


def validate_task_graph(tasks: list[dict[str, Any]]) -> None:
    task_ids = {task["id"] for task in tasks}
    for task in tasks:
        for dependency in task["depends_on"]:
            if dependency not in task_ids:
                raise ValueError(f"task {task['id']} depends on unknown task {dependency}")
            if dependency == task["id"]:
                raise ValueError(f"task {task['id']} cannot depend on itself")

    visiting: set[str] = set()
    visited: set[str] = set()
    by_id = {task["id"]: task for task in tasks}

    def visit(task_id: str) -> None:
        if task_id in visited:
            return
        if task_id in visiting:
            raise ValueError("task dependency graph contains a cycle")
        visiting.add(task_id)
        for dependency in by_id[task_id]["depends_on"]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in task_ids:
        visit(task_id)


def compose_task_prompt(
    spec: MissionSpec,
    task: MissionTask,
    dependency_refs: list[dict[str, Any]],
) -> str:
    lines = [
        f"Mission goal: {spec.goal}",
        f"Mission strategy: {spec.strategy}",
        f"Task: {task.title} ({task.task_id})",
        f"Profile: {task.profile_id}@v{task.profile_version}",
        "",
        "Task instruction:",
        task.prompt or "Complete this task and publish the requested artifacts.",
        "",
        "Coordination rules:",
        "- Treat this run as one SAEU task in a larger mission.",
        "- Communicate with other tasks only through artifacts and event references.",
        "- Preserve auditability: summarize assumptions, changes, commands, and risks.",
    ]
    required = task.profile_snapshot.get("artifacts", {}).get("required", [])
    if required:
        lines.extend(["", f"Expected artifacts: {', '.join(required)}"])
    if dependency_refs:
        lines.extend(["", "Dependency artifacts:"])
        for ref in dependency_refs:
            artifacts = [
                artifact["name"]
                for artifact in ref.get("artifacts", [])
                if isinstance(artifact, dict)
            ]
            artifact_text = ", ".join(artifacts) or "no artifacts listed"
            lines.append(
                f"- {ref['task_id']} ({ref['profile']}, {ref['status']}): "
                f"run={ref.get('run_id') or '-'}, artifacts={artifact_text}"
            )
    return "\n".join(lines)


def run_status_to_task_status(status: str) -> str | None:
    if status in {"queued", "running", "completed", "failed", "cancelled"}:
        return status
    return None


def review_gate_event_type(gate: ReviewGate) -> str:
    if gate.effective_decision == "pass":
        return "review.gate_passed"
    if gate.effective_decision == "warn":
        return "review.gate_warned"
    if gate.effective_decision == "block":
        return "review.gate_blocked"
    return "review.gate_needs_human"
