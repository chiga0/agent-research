from __future__ import annotations

from http import HTTPStatus
from typing import Any

from .models import RunSpec


JSONRPC_VERSION = "2.0"


def acp_capabilities(manager: Any) -> dict[str, Any]:
    return {
        "protocol": "acp-poc",
        "transport": "json-rpc-over-http",
        "methods": [
            "initialize",
            "run.create",
            "run.input",
            "run.status",
            "run.cancel",
        ],
        "runtime_capabilities": manager.capabilities()["features"],
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
        elif method == "run.cancel":
            run_id = require_string(params, "run_id")
            manager.cancel(run_id, params.get("reason"))
            result = {"cancelled": True, "run_id": run_id}
        else:
            return jsonrpc_error(request_id, -32601, "method not found"), HTTPStatus.NOT_FOUND
    except ValueError as exc:
        return jsonrpc_error(request_id, -32602, str(exc)), HTTPStatus.BAD_REQUEST
    except KeyError:
        return jsonrpc_error(request_id, -32004, "run not found"), HTTPStatus.NOT_FOUND

    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}, HTTPStatus.OK


def a2a_agent_card(manager: Any, base_url: str) -> dict[str, Any]:
    return {
        "protocol": "a2a-poc",
        "name": "Cloud Agents Runtime",
        "description": "Mission/task gateway over durable SAEU runs.",
        "url": base_url.rstrip("/"),
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
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


def jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }
