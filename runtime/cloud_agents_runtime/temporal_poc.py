from __future__ import annotations

from typing import Any


def agent_run_workflow_plan(run: Any) -> dict[str, Any]:
    run_id = run.run_id
    return {
        "workflow": "AgentRunWorkflow",
        "workflow_id": f"agent-run-{run_id}",
        "status": run.status,
        "activities": [
            {"name": "prepare_workspace", "idempotency_key": run_id},
            {"name": "resolve_resources", "idempotency_key": run_id},
            {"name": "enqueue_or_claim_worker", "idempotency_key": run_id},
            {"name": "start_agent_runtime", "idempotency_key": run_id},
            {"name": "wait_for_terminal_event", "event_store_ref": f"runs/{run_id}/events.jsonl"},
            {"name": "collect_artifacts", "artifact_ref": f"runs/{run_id}/"},
            {"name": "cleanup_workspace", "idempotency_key": run_id},
        ],
        "signals": ["cancel", "permission_decision"],
        "queries": ["status", "artifact_refs", "last_event"],
        "notes": [
            "Token deltas and tool logs stay in the runtime event store, not Temporal history.",
            "Activities use run_id as the idempotency key.",
        ],
    }


def mission_workflow_plan(snapshot: dict[str, Any]) -> dict[str, Any]:
    mission_id = snapshot["mission_id"]
    tasks = snapshot.get("tasks", [])
    return {
        "workflow": "MissionWorkflow",
        "workflow_id": f"mission-{mission_id}",
        "status": snapshot.get("status"),
        "task_queue": "cloud-agents-runtime",
        "activities": [
            {"name": "create_task_run", "task_id": task["task_id"], "run_id": task.get("run_id")}
            for task in tasks
        ],
        "awaitables": [
            {
                "name": "wait_for_dependencies",
                "task_id": task["task_id"],
                "depends_on": task.get("depends_on", []),
            }
            for task in tasks
            if task.get("depends_on")
        ],
        "signals": ["cancel_mission", "review_gate_override", "permission_decision"],
        "queries": ["mission_status", "task_statuses", "artifact_refs"],
        "event_store_ref": f"missions/{mission_id}/events.jsonl",
        "artifact_ref": f"missions/{mission_id}/",
        "notes": [
            "MissionWorkflow owns coarse DAG progress and human waits.",
            "SAEU runs remain external activities with runtime event-store audit.",
        ],
    }
