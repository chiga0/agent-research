#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from urllib.parse import quote
import urllib.request
from typing import Any

TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
TERMINAL_MISSION_STATUSES = {"completed", "failed", "cancelled", "blocked"}
DIAGNOSTIC_ARTIFACTS = [
    "executor.stderr.log",
    "executor.stdout.log",
    "executor.json",
    "diagnostics.json",
]
SECRET_PATTERNS = [
    re.compile(r"(QWEN_(?:SERVER|SERVE|EXECUTOR)_TOKEN=)[^\s\"',]+"),
    re.compile(r"((?:\"|')?token(?:\"|')?\s*[:=]\s*(?:\"|')?)[^\s\"',}]+", re.IGNORECASE),
    re.compile(r"(authorization[\"']?\s*[:=]\s*[\"']?Bearer\s+)[^\s\"',]+", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/\-=]{12,}"),
]
LIGHTWEIGHT_MISSION_TASKS: list[dict[str, Any]] = [
    {
        "id": "inspect",
        "title": "Inspect runtime acceptance state",
        "profile": "planner",
        "prompt": (
            "Inspect the qwen-backed runtime acceptance context and produce a concise "
            "plan-quality summary. Do not modify files."
        ),
    },
    {
        "id": "report",
        "title": "Write acceptance report",
        "profile": "doc-writer",
        "depends_on": ["inspect"],
        "prompt": (
            "Summarize the acceptance result, child run evidence, artifacts, risks, "
            "and the next validation step."
        ),
    },
    {
        "id": "verify",
        "title": "Verify acceptance evidence",
        "profile": "tester",
        "depends_on": ["report"],
        "prompt": (
            "Review the mission evidence from dependency artifacts and produce a "
            "concise verification note without running long commands."
        ),
    },
    {
        "id": "audit",
        "title": "Audit acceptance risks",
        "profile": "planner",
        "depends_on": ["verify"],
        "prompt": (
            "Audit the lightweight qwen mission for reliability, timeout, artifact, "
            "and recovery risks. Keep the output concise."
        ),
    },
    {
        "id": "final",
        "title": "Finalize acceptance record",
        "profile": "doc-writer",
        "depends_on": ["audit"],
        "prompt": (
            "Write the final acceptance record and include any follow-up actions for "
            "executor isolation or container validation."
        ),
    },
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a qwen-backed Cloud Agents mission")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--token", default=os.environ.get("RUN_MANAGER_TOKEN"))
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--expect-executor-strategy",
        help="fail unless /capabilities reports this qwen executor strategy",
    )
    parser.add_argument(
        "--validate-single-run",
        action="store_true",
        help="create a qwen run before the mission and validate events/artifacts/executor",
    )
    parser.add_argument(
        "--validate-mission",
        action="store_true",
        help="create a qwen-backed mission after the single-run checks",
    )
    parser.add_argument(
        "--auto-approve-permissions",
        action="store_true",
        help=(
            "approve pending qwen permission requests during smoke validation; "
            "use only for trusted acceptance prompts"
        ),
    )
    parser.add_argument(
        "--mission-task-count",
        type=int,
        default=1,
        help=(
            "number of lightweight qwen mission tasks to run when "
            "--validate-mission is set; valid range is 1-5"
        ),
    )
    parser.add_argument(
        "--goal",
        default=(
            "Run a concise cloud-agents product smoke review. Inspect runtime health, "
            "summarize risks, and produce a final mission report."
        ),
    )
    args = parser.parse_args(argv)
    if args.mission_task_count < 1 or args.mission_task_count > len(LIGHTWEIGHT_MISSION_TASKS):
        print(
            f"mission-task-count must be between 1 and {len(LIGHTWEIGHT_MISSION_TASKS)}",
            file=sys.stderr,
        )
        return 1

    client = Client(args.base_url, args.token)
    deadline = now() + args.timeout
    health = client.get("/health")
    print(f"health: {health}")
    capabilities = client.get("/capabilities")
    adapters = capabilities.get("adapters", {})
    print(f"adapters: {sorted(adapters)}")
    if "qwen" not in adapters:
        print("qwen adapter is not available", file=sys.stderr)
        return 1
    executor_config = (
        capabilities.get("executor_registry", {})
        .get("config", {})
    )
    print(f"executor config: {executor_config}")
    if args.expect_executor_strategy:
        strategy = executor_config.get("strategy")
        if strategy != args.expect_executor_strategy:
            message = (
                "executor strategy mismatch: expected "
                f"{args.expect_executor_strategy}, got {strategy}"
            )
            print(
                message,
                file=sys.stderr,
            )
            return 1
    for path in ["/queue", "/executors", "/access/policy", "/cost/status"]:
        payload = client.get(path)
        print(f"{path}: {summary(payload)}")

    if args.validate_single_run and not validate_single_run(client, args, deadline):
        return 1
    if not args.validate_mission:
        return 0

    mission_timeout = remaining_timeout_seconds(deadline)
    if mission_timeout <= 0:
        print("acceptance timeout expired before mission validation", file=sys.stderr)
        return 1
    mission = client.post(
        "/missions",
        qwen_mission_payload(args, mission_timeout),
    )
    mission_id = mission["mission_id"]
    print(f"mission: {mission_id}")

    state: dict[str, Any] = mission
    approved_permissions: set[str] = set()
    while now() < deadline:
        state = client.get(f"/missions/{mission_id}")
        print(
            "state:",
            state.get("status"),
            f"{state.get('completed_task_count')}/{state.get('task_count')}",
        )
        if args.auto_approve_permissions:
            auto_approve_mission_permissions(
                client,
                state,
                approved_permissions,
            )
        if state.get("status") in TERMINAL_MISSION_STATUSES:
            break
        sleep_for(5)

    events = client.get(f"/missions/{mission_id}/events.json").get("events", [])
    names = [event.get("type") for event in events]
    print(f"mission events: {names}")
    artifacts = client.get(f"/missions/{mission_id}/artifacts").get("artifacts", [])
    artifact_names = {artifact.get("name") for artifact in artifacts}
    print(f"mission artifacts: {sorted(artifact_names)}")

    if state.get("status") != "completed":
        if state.get("status") not in TERMINAL_MISSION_STATUSES:
            cancel_mission(client, mission_id, "qwen acceptance timeout")
        print_debug_snapshot(client, mission_id=mission_id)
        print(f"mission did not complete: {state.get('status')}", file=sys.stderr)
        return 1
    if "mission.completed" not in names:
        print("missing mission.completed event", file=sys.stderr)
        return 1
    if "final_report.md" not in artifact_names and "final-report.md" not in artifact_names:
        print("missing final report artifact", file=sys.stderr)
        return 1
    return 0


def validate_single_run(
    client: "Client",
    args: argparse.Namespace,
    deadline: float,
) -> bool:
    run_timeout = remaining_timeout_seconds(deadline)
    if run_timeout <= 0:
        print("acceptance timeout expired before single-run validation", file=sys.stderr)
        return False
    run = client.post(
        "/runs",
        {
            "prompt": "Run a concise qwen single-run acceptance check.",
            "adapter": "qwen",
            "timeout_seconds": run_timeout,
        },
    )
    run_id = run["run_id"]
    print(f"single run: {run_id}")
    state: dict[str, Any] = run
    approved_permissions: set[str] = set()
    while now() < deadline:
        state = client.get(f"/runs/{run_id}")
        print("single run state:", state.get("status"))
        if args.auto_approve_permissions:
            auto_approve_run_permissions(client, run_id, approved_permissions)
        if state.get("status") in TERMINAL_RUN_STATUSES:
            break
        sleep_for(3)
    if state.get("status") != "completed":
        if state.get("status") not in TERMINAL_RUN_STATUSES:
            cancel_run(client, run_id, "qwen acceptance timeout")
        print_debug_snapshot(client, run_id=run_id)
        print(f"single run did not complete: {state.get('status')}", file=sys.stderr)
        return False
    events = client.get(f"/runs/{run_id}/events.json").get("events", [])
    names = [event.get("type") for event in events]
    print(f"single run events: {names}")
    artifacts = client.get(f"/runs/{run_id}/artifacts").get("artifacts", [])
    artifact_names = {artifact.get("name") for artifact in artifacts}
    print(f"single run artifacts: {sorted(artifact_names)}")
    required = {"events.jsonl", "raw_events.jsonl", "diagnostics.json", "cost.json"}
    missing = required - artifact_names
    if missing:
        print(f"single run missing artifacts: {sorted(missing)}", file=sys.stderr)
        return False
    if args.expect_executor_strategy and args.expect_executor_strategy != "shared":
        executor = client.get(f"/runs/{run_id}/executor").get("executor")
        print(f"single run executor: {executor}")
        if not executor or executor.get("strategy") != args.expect_executor_strategy:
            print("single run executor strategy mismatch", file=sys.stderr)
            return False
    return True


def auto_approve_mission_permissions(
    client: "Client",
    mission_state: dict[str, Any],
    approved_permissions: set[str],
) -> None:
    tasks = mission_state.get("tasks")
    if not isinstance(tasks, list):
        return
    for task in tasks:
        if not isinstance(task, dict):
            continue
        run_id = task.get("run_id")
        if isinstance(run_id, str) and run_id:
            auto_approve_run_permissions(client, run_id, approved_permissions)


def auto_approve_run_permissions(
    client: "Client",
    run_id: str,
    approved_permissions: set[str],
) -> None:
    events = client.get(f"/runs/{run_id}/events.json").get("events", [])
    if not isinstance(events, list):
        return
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "permission.requested":
            continue
        permission_id = permission_id_from_event(event)
        if not permission_id or permission_id in approved_permissions:
            continue
        client.post(
            f"/runs/{run_id}/permissions/{quote(permission_id, safe='')}",
            {
                "decision": "approve",
                "reason": "auto-approved by qwen smoke validation",
            },
        )
        approved_permissions.add(permission_id)
        print(f"approved permission: {run_id}/{permission_id}")


def permission_id_from_event(event: dict[str, Any]) -> str | None:
    data = event.get("data")
    if not isinstance(data, dict):
        return None
    candidates = [
        data.get("permission_id"),
        data.get("requestId"),
        nested_value(data, "raw", "data", "requestId"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def nested_value(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def qwen_mission_payload(args: argparse.Namespace, timeout_seconds: int) -> dict[str, Any]:
    tasks = limited_mission_tasks(args.mission_task_count)
    return {
        "goal": args.goal,
        "adapter": "qwen",
        "strategy": "custom",
        "timeout_seconds": timeout_seconds,
        "tasks": tasks,
        "metadata": {
            "acceptance": "qwen",
            "mission_profile": "lightweight",
            "mission_task_count": len(tasks),
        },
    }


def limited_mission_tasks(task_count: int) -> list[dict[str, Any]]:
    tasks = [dict(task) for task in LIGHTWEIGHT_MISSION_TASKS[:task_count]]
    included = {task["id"] for task in tasks}
    for task in tasks:
        dependencies = task.get("depends_on") or []
        task["depends_on"] = [
            dependency
            for dependency in dependencies
            if dependency in included
        ]
    return tasks


def remaining_timeout_seconds(deadline: float) -> int:
    remaining = deadline - now()
    if remaining <= 0:
        return 0
    return max(1, int(remaining))


def now() -> float:
    return time.monotonic()


def sleep_for(seconds: float) -> None:
    time.sleep(seconds)


def cancel_run(client: "Client", run_id: str, reason: str) -> None:
    try:
        client.post(f"/runs/{run_id}/cancel", {"reason": reason})
        print(f"cancelled timed-out run: {run_id}")
    except Exception as exc:  # pragma: no cover - best-effort diagnostics path
        print(f"failed to cancel timed-out run {run_id}: {exc}", file=sys.stderr)


def cancel_mission(client: "Client", mission_id: str, reason: str) -> None:
    try:
        client.post(f"/missions/{mission_id}/cancel", {"reason": reason})
        print(f"cancelled timed-out mission: {mission_id}")
    except Exception as exc:  # pragma: no cover - best-effort diagnostics path
        print(f"failed to cancel timed-out mission {mission_id}: {exc}", file=sys.stderr)


def print_debug_snapshot(
    client: "Client",
    *,
    run_id: str | None = None,
    mission_id: str | None = None,
) -> None:
    print_payload_summary(client, "queue", "/queue")
    print_payload_summary(client, "executors", "/executors")
    if run_id:
        print_event_tail(client, "single run", f"/runs/{run_id}/events.json")
        print_payload_summary(client, "single run executor", f"/runs/{run_id}/executor")
        print_run_artifact_diagnostics(client, run_id)
    if mission_id:
        print_event_tail(client, "mission", f"/missions/{mission_id}/events.json")


def print_payload_summary(client: "Client", label: str, path: str) -> None:
    try:
        payload = client.get(path)
        print(f"{label}: {json.dumps(redact(payload), sort_keys=True)[:2000]}")
    except Exception as exc:  # pragma: no cover - best-effort diagnostics path
        print(f"{label} unavailable: {exc}", file=sys.stderr)


def print_run_artifact_diagnostics(client: "Client", run_id: str) -> None:
    try:
        artifacts = client.get(f"/runs/{run_id}/artifacts").get("artifacts", [])
        artifact_names = {artifact.get("name") for artifact in artifacts}
        print(f"single run diagnostic artifacts: {sorted(artifact_names)}")
    except Exception as exc:  # pragma: no cover - best-effort diagnostics path
        print(f"single run diagnostic artifacts unavailable: {exc}", file=sys.stderr)
        return
    for name in DIAGNOSTIC_ARTIFACTS:
        if name not in artifact_names:
            continue
        print_artifact_tail(client, run_id, name)


def print_artifact_tail(
    client: "Client",
    run_id: str,
    name: str,
    *,
    max_chars: int = 4000,
) -> None:
    try:
        path = f"/runs/{run_id}/artifacts/{quote(name, safe='')}"
        content = client.get_text(path)
        content = redact_text(content).strip()
        if len(content) > max_chars:
            content = f"... truncated to last {max_chars} chars ...\n{content[-max_chars:]}"
        if not content:
            content = "<empty>"
        print(f"--- {name} tail ---")
        print(content)
        print(f"--- end {name} tail ---")
    except Exception as exc:  # pragma: no cover - best-effort diagnostics path
        print(f"{name} unavailable: {exc}", file=sys.stderr)


def print_event_tail(client: "Client", label: str, path: str) -> None:
    try:
        events = client.get(path).get("events", [])
        tail = events[-12:]
        names = [event.get("type") for event in tail]
        print(f"{label} event tail: {names}")
    except Exception as exc:  # pragma: no cover - best-effort diagnostics path
        print(f"{label} event tail unavailable: {exc}", file=sys.stderr)


def summary(payload: dict[str, Any]) -> str:
    if "status" in payload:
        return str(payload["status"])
    if "counts" in payload:
        return f"counts={payload['counts']}"
    if "executor_registry" in payload:
        registry = payload.get("executor_registry") or {}
        config = registry.get("config") if isinstance(registry, dict) else {}
        return f"strategy={config.get('strategy') if isinstance(config, dict) else None}"
    if "mode" in payload:
        return str(payload["mode"])
    return ",".join(sorted(payload)[:5])


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if "token" in key.lower() or key.lower() == "authorization":
                redacted[key] = "<redacted>" if item else item
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(r"\1<redacted>", redacted)
    return redacted


class Client:
    def __init__(self, base_url: str, token: str | None):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def get(self, path: str) -> dict[str, Any]:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", path, payload)

    def get_text(self, path: str) -> str:
        request = self.build_request("GET", path)
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="replace")

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = self.build_request(method, path, payload)
        with urllib.request.urlopen(request, timeout=20) as response:
            parsed = json.loads(response.read().decode("utf-8"))
            assert isinstance(parsed, dict)
            return parsed

    def build_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> urllib.request.Request:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"content-type": "application/json"} if payload is not None else {}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        return urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )


if __name__ == "__main__":
    raise SystemExit(main())
