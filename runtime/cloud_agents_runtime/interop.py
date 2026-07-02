from __future__ import annotations

from http import HTTPStatus
from typing import Any

from .models import RunSpec


JSONRPC_VERSION = "2.0"
ACP_PROTOCOL_VERSION = "cloud-agents-acp-compat-2026-07"
A2A_PROTOCOL_VERSION = "cloud-agents-a2a-compat-2026-07"


def acp_capabilities(manager: Any) -> dict[str, Any]:
    return {
        "protocol": "acp-poc",
        "protocol_version": ACP_PROTOCOL_VERSION,
        "transport": "json-rpc-over-http",
        "methods": [
            "initialize",
            "capabilities.get",
            "run.create",
            "run.input",
            "run.status",
            "run.events",
            "run.artifacts",
            "run.permissions",
            "run.cancel",
            "executor.list",
            "mission.create",
            "mission.status",
            "mission.cancel",
            "mission.events",
            "mission.artifacts",
            "access.policy",
            "cost.status",
        ],
        "runtime_capabilities": manager.capabilities()["features"],
        "event_stream": {
            "transport": "server-sent-events",
            "run_url": "/runs/{run_id}/events",
            "resume": "Last-Event-ID",
        },
    }


def handle_acp_jsonrpc(manager: Any, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
    request_id = payload.get("id")
    if payload.get("jsonrpc") != JSONRPC_VERSION:
        return jsonrpc_error(request_id, -32600, "jsonrpc must be 2.0"), HTTPStatus.BAD_REQUEST
    method = payload.get("method")
    params = payload.get("params") or {}
    if not isinstance(method, str) or not isinstance(params, dict):
        return (
            jsonrpc_error(request_id, -32600, "method and params are required"),
            HTTPStatus.BAD_REQUEST,
        )

    try:
        if method == "initialize":
            result = acp_capabilities(manager)
        elif method == "capabilities.get":
            result = manager.capabilities()
        elif method == "run.create":
            run = manager.create_run(RunSpec.from_payload(params))
            result = run.to_dict()
        elif method == "run.input":
            run_id = require_string(params, "run_id")
            prompt = require_string(params, "prompt")
            manager.send_input(run_id, prompt)
            result = {"accepted": True, "run_id": run_id}
        elif method == "run.status":
            run_id = require_string(params, "run_id")
            run = manager.get_run(run_id)
            if run is None:
                return jsonrpc_error(request_id, -32004, "run not found"), HTTPStatus.NOT_FOUND
            result = run.to_dict()
        elif method == "run.events":
            run_id = require_string(params, "run_id")
            last_sequence = optional_int(params.get("last_sequence"))
            result = {
                "run_id": run_id,
                "events": [
                    event.to_dict()
                    for event in manager.store.events_since(run_id, last_sequence)
                ],
            }
        elif method == "run.artifacts":
            run_id = require_string(params, "run_id")
            result = {"run_id": run_id, "artifacts": manager.store.list_artifacts(run_id)}
        elif method == "run.permissions":
            run_id = require_string(params, "run_id")
            last_sequence = optional_int(params.get("last_sequence"))
            events = manager.store.events_since(run_id, last_sequence)
            result = {
                "run_id": run_id,
                "permissions": [
                    event.to_dict()
                    for event in events
                    if event.type.startswith("permission.")
                ],
            }
        elif method == "run.cancel":
            run_id = require_string(params, "run_id")
            manager.cancel(run_id, params.get("reason"))
            result = {"cancelled": True, "run_id": run_id}
        elif method == "executor.list":
            result = manager.executors()
        elif method == "mission.create":
            mission = manager.create_mission(params)
            result = mission
        elif method == "mission.status":
            mission_id = require_string(params, "mission_id")
            mission = manager.get_mission(mission_id)
            if mission is None:
                return jsonrpc_error(request_id, -32004, "mission not found"), HTTPStatus.NOT_FOUND
            result = mission
        elif method == "mission.cancel":
            mission_id = require_string(params, "mission_id")
            result = manager.cancel_mission(mission_id, params.get("reason"))
        elif method == "mission.events":
            mission_id = require_string(params, "mission_id")
            last_sequence = optional_int(params.get("last_sequence"))
            result = {
                "mission_id": mission_id,
                "events": [
                    event.to_dict()
                    for event in manager.store.mission_events_since(mission_id, last_sequence)
                ],
            }
        elif method == "mission.artifacts":
            mission_id = require_string(params, "mission_id")
            result = {
                "mission_id": mission_id,
                "artifacts": manager.store.list_mission_artifacts(mission_id),
            }
        elif method == "access.policy":
            result = manager.access_policy(None)
        elif method == "cost.status":
            result = manager.cost_status()
        else:
            return jsonrpc_error(request_id, -32601, "method not found"), HTTPStatus.NOT_FOUND
    except ValueError as exc:
        return jsonrpc_error(request_id, -32602, str(exc)), HTTPStatus.BAD_REQUEST
    except KeyError:
        return jsonrpc_error(request_id, -32004, "run not found"), HTTPStatus.NOT_FOUND

    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}, HTTPStatus.OK


def a2a_agent_card(manager: Any, base_url: str) -> dict[str, Any]:
    base = base_url.rstrip("/")
    return {
        "protocol": "a2a-poc",
        "protocolVersion": A2A_PROTOCOL_VERSION,
        "name": "Cloud Agents Runtime",
        "description": "Mission/task gateway over durable SAEU runs.",
        "url": base,
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": True,
            "permissionRequests": True,
            "executorIntrospection": True,
        },
        "securitySchemes": {
            "bearer": {
                "type": "http",
                "scheme": "bearer",
                "description": "Runtime bearer token or hashed API token.",
            }
        },
        "endpoints": {
            "tasks": f"{base}/a2a/tasks",
            "task": f"{base}/a2a/tasks/{{task_id}}",
            "task_events": f"{base}/a2a/tasks/{{task_id}}/events.json",
            "task_artifacts": f"{base}/a2a/tasks/{{task_id}}/artifacts",
            "acp": f"{base}/acp",
            "executors": f"{base}/executors",
            "cost": f"{base}/cost/status",
        },
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["application/json", "text/markdown"],
        "skills": [
            {
                "id": "mission-orchestration",
                "name": "Mission orchestration",
                "description": "Create and monitor multi-task coding missions.",
            },
            {
                "id": "single-run",
                "name": "Single SAEU run",
                "description": "Create and monitor a single stable Agent execution unit.",
            },
            {
                "id": "executor-introspection",
                "name": "Executor introspection",
                "description": "Inspect per-run qwen executors and isolation state.",
            },
        ],
        "runtime_capabilities": manager.capabilities()["features"],
    }


def create_a2a_task(manager: Any, payload: dict[str, Any]) -> dict[str, Any]:
    mission_payload = payload.get("mission")
    if not isinstance(mission_payload, dict):
        goal = payload.get("goal") or payload.get("message")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal or message is required")
        mission_payload = {
            "goal": goal.strip(),
            "strategy": "custom",
            "adapter": payload.get("adapter") or "fake",
            "tasks": [
                {"id": "plan", "profile": "planner", "prompt": "plan the external task"},
                {
                    "id": "report",
                    "profile": "doc-writer",
                    "depends_on": ["plan"],
                    "prompt": "summarize the external task result",
                },
            ],
        }
    mission = manager.create_mission(mission_payload)
    return a2a_task_from_mission(manager, mission["mission_id"])


def a2a_task_from_mission(manager: Any, mission_id: str) -> dict[str, Any]:
    mission = manager.get_mission(mission_id)
    if mission is None:
        raise KeyError(mission_id)
    return {
        "task_id": mission_id,
        "kind": "mission",
        "status": map_a2a_status(mission["status"]),
        "mission": mission,
        "events_url": f"/a2a/tasks/{mission_id}/events.json",
        "artifacts": manager.store.list_mission_artifacts(mission_id),
    }


def map_a2a_status(status: str) -> str:
    return {
        "created": "submitted",
        "running": "working",
        "blocked": "input-required",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "canceled",
    }.get(status, "unknown")


def require_string(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def optional_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }
