#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from typing import Any


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
        "--goal",
        default=(
            "Run a concise cloud-agents product smoke review. Inspect runtime health, "
            "summarize risks, and produce a final mission report."
        ),
    )
    args = parser.parse_args(argv)

    client = Client(args.base_url, args.token)
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

    if args.validate_single_run and not validate_single_run(client, args):
        return 1

    mission = client.post(
        "/missions",
        {
            "goal": args.goal,
            "adapter": "qwen",
            "strategy": "sequential",
            "timeout_seconds": int(args.timeout),
        },
    )
    mission_id = mission["mission_id"]
    print(f"mission: {mission_id}")

    deadline = time.monotonic() + args.timeout
    state: dict[str, Any] = mission
    while time.monotonic() < deadline:
        state = client.get(f"/missions/{mission_id}")
        print(
            "state:",
            state.get("status"),
            f"{state.get('completed_task_count')}/{state.get('task_count')}",
        )
        if state.get("status") in {"completed", "failed", "cancelled", "blocked"}:
            break
        time.sleep(5)

    events = client.get(f"/missions/{mission_id}/events.json").get("events", [])
    names = [event.get("type") for event in events]
    print(f"mission events: {names}")
    artifacts = client.get(f"/missions/{mission_id}/artifacts").get("artifacts", [])
    artifact_names = {artifact.get("name") for artifact in artifacts}
    print(f"mission artifacts: {sorted(artifact_names)}")

    if state.get("status") != "completed":
        print(f"mission did not complete: {state.get('status')}", file=sys.stderr)
        return 1
    if "mission.completed" not in names:
        print("missing mission.completed event", file=sys.stderr)
        return 1
    if "final_report.md" not in artifact_names and "final-report.md" not in artifact_names:
        print("missing final report artifact", file=sys.stderr)
        return 1
    return 0


def validate_single_run(client: "Client", args: argparse.Namespace) -> bool:
    run = client.post(
        "/runs",
        {
            "prompt": "Run a concise qwen single-run acceptance check.",
            "adapter": "qwen",
            "timeout_seconds": int(args.timeout),
        },
    )
    run_id = run["run_id"]
    print(f"single run: {run_id}")
    deadline = time.monotonic() + args.timeout
    state: dict[str, Any] = run
    while time.monotonic() < deadline:
        state = client.get(f"/runs/{run_id}")
        print("single run state:", state.get("status"))
        if state.get("status") in {"completed", "failed", "cancelled"}:
            break
        time.sleep(3)
    if state.get("status") != "completed":
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


class Client:
    def __init__(self, base_url: str, token: str | None):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def get(self, path: str) -> dict[str, Any]:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", path, payload)

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"content-type": "application/json"} if payload is not None else {}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            parsed = json.loads(response.read().decode("utf-8"))
            assert isinstance(parsed, dict)
            return parsed


if __name__ == "__main__":
    raise SystemExit(main())
