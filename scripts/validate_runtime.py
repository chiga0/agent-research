#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.request
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Cloud Agents Runtime")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--token", default=None)
    parser.add_argument("--adapter", default="fake", choices=["fake", "qwen"])
    parser.add_argument("--prompt", default="hello cloud runtime")
    parser.add_argument("--artifact-root", type=pathlib.Path)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)

    client = Client(args.base_url, args.token)
    health = client.get("/health")
    print(f"health: {health}")
    capabilities = client.get("/capabilities")
    print(f"capabilities adapters: {sorted(capabilities['adapters'])}")
    run = client.post("/runs", {"prompt": args.prompt, "adapter": args.adapter})
    run_id = run["run_id"]
    print(f"run: {run_id}")
    events = client.sse(f"/runs/{run_id}/events", timeout=args.timeout)
    names = [event["event"] for event in events]
    print(f"events: {names}")
    if "run.completed" not in names:
        print("run did not complete", file=sys.stderr)
        return 1
    state = client.get(f"/runs/{run_id}")
    print(f"state: {state['status']}")
    if state["status"] != "completed":
        return 1
    if args.artifact_root:
        required = [
            "run_spec.json",
            "events.jsonl",
            "raw_events.jsonl",
            "input_1.json",
            "diagnostics.json",
        ]
        run_dir = args.artifact_root / run_id
        missing = [name for name in required if not (run_dir / name).exists()]
        if missing:
            print(f"missing artifacts: {missing}", file=sys.stderr)
            return 1
        if not (args.artifact_root / "runtime.db").exists():
            print("missing runtime.db", file=sys.stderr)
            return 1
    return 0


class Client:
    def __init__(self, base_url: str, token: str | None):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def get(self, path: str) -> dict[str, Any]:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", path, payload)

    def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers=self.headers(payload is not None),
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            parsed = json.loads(response.read().decode("utf-8"))
            assert isinstance(parsed, dict)
            return parsed

    def sse(self, path: str, timeout: float) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        request = urllib.request.Request(f"{self.base_url}{path}", headers=self.headers(False))
        events: list[dict[str, Any]] = []
        with urllib.request.urlopen(request, timeout=timeout) as response:
            event_name: str | None = None
            data_lines: list[str] = []
            for raw_line in response:
                if time.monotonic() > deadline:
                    raise TimeoutError("SSE validation timed out")
                line = raw_line.decode("utf-8").rstrip("\n")
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif line == "" and data_lines:
                    events.append({"event": event_name, "data": json.loads("\n".join(data_lines))})
                    if event_name in {"run.completed", "run.failed", "run.cancelled"}:
                        return events
                    event_name = None
                    data_lines = []
        return events

    def headers(self, has_json_body: bool) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        if has_json_body:
            headers["content-type"] = "application/json"
        return headers


if __name__ == "__main__":
    raise SystemExit(main())
