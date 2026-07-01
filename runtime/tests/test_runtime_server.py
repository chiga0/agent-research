from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from runtime.cloud_agents_runtime.auth import AuthConfig
from runtime.cloud_agents_runtime.server import build_server


class RuntimeServerTest(unittest.TestCase):
    def test_auth_protects_run_routes_and_allows_health(self) -> None:
        with running_runtime(token="secret") as base_url:
            health = request_json(f"{base_url}/health")
            self.assertTrue(health["ok"])

            with self.assertRaises(urllib.error.HTTPError) as ctx:
                request_json(f"{base_url}/capabilities")
            self.assertEqual(ctx.exception.code, HTTPStatus.UNAUTHORIZED)

            capabilities = request_json(
                f"{base_url}/capabilities",
                headers={"authorization": "Bearer secret"},
            )
            self.assertIn("fake", capabilities["adapters"])

    def test_fake_run_streams_sse_and_writes_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with running_runtime(artifact_root=Path(tmp)) as base_url:
                html = request_text(f"{base_url}/")
                self.assertIn("Cloud Agents Runtime", html)
                run = request_json(
                    f"{base_url}/runs",
                    method="POST",
                    payload={"prompt": "hello integration runtime", "adapter": "fake"},
                )
                events = read_sse(f"{base_url}/runs/{run['run_id']}/events")
                event_names = [event["event"] for event in events]
                self.assertIn("run.created", event_names)
                self.assertIn("run.completed", event_names)
                run_dir = Path(tmp) / run["run_id"]
                self.assertTrue((run_dir / "events.jsonl").exists())
                self.assertTrue((run_dir / "final_1.json").exists())
                events_json = request_json(f"{base_url}/runs/{run['run_id']}/events.json")
                self.assertIn("events", events_json)
                artifacts = request_json(f"{base_url}/runs/{run['run_id']}/artifacts")
                artifact_names = {artifact["name"] for artifact in artifacts["artifacts"]}
                self.assertIn("events.jsonl", artifact_names)
                self.assertIn("diagnostics.json", artifact_names)

    def test_sse_reconnect_and_gap_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with running_runtime(artifact_root=Path(tmp)) as base_url:
                run = request_json(
                    f"{base_url}/runs",
                    method="POST",
                    payload={"prompt": "hello reconnect runtime", "adapter": "fake"},
                )
                initial_events = read_sse(f"{base_url}/runs/{run['run_id']}/events")
                self.assertGreater(len(initial_events), 2)

                replayed = read_sse(
                    f"{base_url}/runs/{run['run_id']}/events",
                    headers={"Last-Event-ID": "2"},
                )
                self.assertTrue(replayed)
                self.assertGreater(replayed[0]["data"]["sequence"], 2)

                gap = read_sse(
                    f"{base_url}/runs/{run['run_id']}/events",
                    headers={"Last-Event-ID": "999"},
                )
                self.assertEqual(gap[0]["event"], "event.gap_detected")
                self.assertEqual(gap[0]["data"]["data"]["requested_last_sequence"], 999)

    def test_permission_resolution_endpoint_writes_audit_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with running_runtime(artifact_root=Path(tmp)) as base_url:
                run = request_json(
                    f"{base_url}/runs",
                    method="POST",
                    payload={"prompt": "permission audit", "adapter": "fake"},
                )
                accepted = request_json(
                    f"{base_url}/runs/{run['run_id']}/permissions/perm-1",
                    method="POST",
                    payload={
                        "decision": "approve",
                        "decided_by": "tester",
                        "reason": "unit test",
                    },
                )
                self.assertTrue(accepted["accepted"])
                events = read_sse(f"{base_url}/runs/{run['run_id']}/events")
                self.assertIn("permission.resolved", [event["event"] for event in events])
                run_dir = Path(tmp) / run["run_id"]
                permission_artifacts = sorted(run_dir.glob("permission.resolved_*.json"))
                self.assertEqual(len(permission_artifacts), 1)

    def test_qwen_adapter_maps_fake_daemon_events(self) -> None:
        with running_fake_qwen() as qwen_url:
            with tempfile.TemporaryDirectory() as tmp:
                with running_runtime(artifact_root=Path(tmp), qwen_url=qwen_url) as base_url:
                    run = request_json(
                        f"{base_url}/runs",
                        method="POST",
                        payload={"prompt": "hello qwen", "adapter": "qwen"},
                    )
                    deadline = time.time() + 3
                    current: dict[str, Any] = {}
                    while time.time() < deadline:
                        current = request_json(f"{base_url}/runs/{run['run_id']}")
                        if current["status"] == "completed":
                            break
                        time.sleep(0.05)
                    self.assertEqual(current["status"], "completed")
                    events = read_sse(f"{base_url}/runs/{run['run_id']}/events")
                    event_names = [event["event"] for event in events]
                    self.assertIn("message.delta", event_names)
                    raw = (Path(tmp) / run["run_id"] / "raw_events.jsonl").read_text(
                        encoding="utf-8"
                    )
                    self.assertIn("agent_message_chunk", raw)


class running_runtime:
    def __init__(
        self,
        artifact_root: Path | None = None,
        token: str | None = None,
        qwen_url: str | None = None,
    ):
        self.tmp = tempfile.TemporaryDirectory() if artifact_root is None else None
        self.artifact_root = artifact_root or Path(self.tmp.name)
        self.server = build_server(
            "127.0.0.1",
            0,
            self.artifact_root,
            auth_config=AuthConfig(token=token),
            qwen_base_url=qwen_url,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        if self.tmp:
            self.tmp.cleanup()


class running_fake_qwen:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeQwenHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        FakeQwenHandler.cancelled = False
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


class FakeQwenHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    cancelled = False

    def do_GET(self) -> None:
        if self.path == "/health":
            self.write_json({"ok": True})
            return
        if self.path == "/session/session-1/events":
            self.send_response(HTTPStatus.OK)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            frame = {
                "id": 1,
                "v": 1,
                "type": "session_update",
                "data": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "hello from qwen"},
                },
            }
            self.wfile.write(
                (
                    "id: 1\n"
                    "event: session_update\n"
                    f"data: {json.dumps(frame)}\n\n"
                ).encode("utf-8")
            )
            self.wfile.flush()
            return
        self.write_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path == "/session":
            self.read_body()
            self.write_json(
                {"sessionId": "session-1", "workspaceCwd": "/tmp/workspace", "attached": False}
            )
            return
        if self.path == "/session/session-1/prompt":
            self.read_body()
            self.write_json({"stopReason": "end_turn"})
            return
        if self.path == "/session/session-1/cancel":
            FakeQwenHandler.cancelled = True
            self.read_body()
            self.write_json({"cancelled": True})
            return
        self.write_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def read_body(self) -> bytes:
        length = int(self.headers.get("content-length", "0") or "0")
        return self.rfile.read(length) if length else b""

    def write_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def request_json(
    url: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    if payload is not None:
        request.add_header("content-type", "application/json")
    with urllib.request.urlopen(request, timeout=5) as response:
        parsed = json.loads(response.read().decode("utf-8"))
        assert isinstance(parsed, dict)
        return parsed


def request_text(url: str, headers: dict[str, str] | None = None) -> str:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.read().decode("utf-8")


def read_sse(url: str, headers: dict[str, str] | None = None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=5) as response:
        event_name: str | None = None
        data_lines: list[str] = []
        for raw_line in response:
            line = raw_line.decode("utf-8").rstrip("\n")
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif line == "" and data_lines:
                events.append({"event": event_name, "data": json.loads("\n".join(data_lines))})
                data_lines = []
                event_name = None
    return events


if __name__ == "__main__":
    unittest.main()
