from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from enum import IntEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from runtime.cloud_agents_runtime.adapters.base import RuntimeAdapter
from runtime.cloud_agents_runtime.adapters.fake import FakeAdapter
from runtime.cloud_agents_runtime.adapters.qwen import (
    QwenServeAdapter,
    parse_int,
    parse_json_or_text,
)
from runtime.cloud_agents_runtime.auth import AuthConfig, is_authorized
from runtime.cloud_agents_runtime.manager import RunManager
from runtime.cloud_agents_runtime.missions import build_task_definitions, run_status_to_task_status
from runtime.cloud_agents_runtime.models import MissionSpec, RunSpec, clean_identifier
from runtime.cloud_agents_runtime.review_gate import parse_review_gate
from runtime.cloud_agents_runtime.server import parse_last_event_id, parse_optional_int
from runtime.cloud_agents_runtime.store import RunStore
from runtime.cloud_agents_runtime.supervisor import QwenServeProcess, qwen_supervisor_from_env

from test_runtime_server import request_json, running_fake_qwen, running_runtime


class RuntimeEdgeTest(unittest.TestCase):
    def test_server_error_paths(self) -> None:
        with running_runtime() as base_url:
            self.assertEqual(request_json(f"{base_url}/runs")["runs"], [])
            self.assert_http_error(f"{base_url}/missing", HTTPErrorCode.NOT_FOUND)
            self.assert_http_error(f"{base_url}/runs/missing", HTTPErrorCode.NOT_FOUND)
            self.assert_http_error(f"{base_url}/runs/missing/events", HTTPErrorCode.NOT_FOUND)
            self.assert_http_error(f"{base_url}/profiles/missing", HTTPErrorCode.NOT_FOUND)
            self.assert_http_error(f"{base_url}/missions/missing", HTTPErrorCode.NOT_FOUND)
            self.assert_http_error(
                f"{base_url}/missions/missing/events.json",
                HTTPErrorCode.NOT_FOUND,
            )
            self.assert_http_error(
                f"{base_url}/missions/missing/artifacts",
                HTTPErrorCode.NOT_FOUND,
            )
            self.assert_http_error(
                f"{base_url}/missions/missing/cancel",
                HTTPErrorCode.NOT_FOUND,
                method="POST",
                body={},
            )
            self.assert_http_error(
                f"{base_url}/profiles",
                HTTPErrorCode.BAD_REQUEST,
                method="POST",
                body={"id": "bad/profile"},
            )
            self.assert_http_error(
                f"{base_url}/missions",
                HTTPErrorCode.BAD_REQUEST,
                method="POST",
                body={},
            )
            self.assert_http_error(
                f"{base_url}/runs/missing/input",
                HTTPErrorCode.BAD_REQUEST,
                method="POST",
                body={"prompt": ""},
            )
            self.assert_http_error(
                f"{base_url}/runs/missing/input",
                HTTPErrorCode.NOT_FOUND,
                method="POST",
                body={"prompt": "x"},
            )
            self.assert_http_error(
                f"{base_url}/runs",
                HTTPErrorCode.BAD_REQUEST,
                method="POST",
                raw_body=b"[]",
            )
            self.assert_http_error(
                f"{base_url}/runs",
                HTTPErrorCode.BAD_REQUEST,
                method="POST",
                raw_body=b"{",
            )
            self.assert_http_error(
                f"{base_url}/not-found",
                HTTPErrorCode.NOT_FOUND,
                method="POST",
                body={},
            )

    def test_cancel_active_fake_run(self) -> None:
        with running_runtime() as base_url:
            run = request_json(
                f"{base_url}/runs",
                method="POST",
                payload={"prompt": "one two three four five six", "adapter": "fake"},
            )
            cancel = request_json(
                f"{base_url}/runs/{run['run_id']}/cancel",
                method="POST",
                payload={"reason": "test"},
            )
            self.assertTrue(cancel["cancelled"])

    def test_auth_helpers_and_last_event_id(self) -> None:
        self.assertTrue(is_authorized(AuthConfig(), "/runs", None))
        self.assertTrue(is_authorized(AuthConfig(token="x"), "/health", None))
        self.assertFalse(is_authorized(AuthConfig(token="x", protect_health=True), "/health", None))
        self.assertTrue(is_authorized(AuthConfig(token="x"), "/runs", "Bearer x"))
        self.assertEqual(parse_last_event_id(None), 0)
        self.assertEqual(parse_last_event_id("bad"), 0)
        self.assertEqual(parse_last_event_id("-1"), 0)
        self.assertEqual(parse_last_event_id("7"), 7)
        self.assertIsNone(parse_optional_int(None))
        self.assertIsNone(parse_optional_int(""))
        self.assertIsNone(parse_optional_int("bad"))
        self.assertEqual(parse_optional_int("4"), 4)

    def test_mission_model_and_dag_validation_edges(self) -> None:
        with self.assertRaisesRegex(ValueError, "goal is required"):
            MissionSpec.from_payload({})
        with self.assertRaisesRegex(ValueError, "strategy must"):
            MissionSpec.from_payload({"goal": "x", "strategy": "weird"})
        with self.assertRaisesRegex(ValueError, "tasks must be a list"):
            MissionSpec.from_payload({"goal": "x", "tasks": "bad"})
        with self.assertRaisesRegex(ValueError, "custom strategy requires tasks"):
            MissionSpec.from_payload({"goal": "x", "strategy": "custom"})
        with self.assertRaisesRegex(ValueError, "profile is required"):
            clean_identifier("", "profile")
        with self.assertRaisesRegex(ValueError, "may only contain"):
            clean_identifier("bad/profile", "profile")

        spec = MissionSpec.from_payload(
            {
                "goal": "x",
                "strategy": "custom",
                "tasks": [{"title": "Generated", "prompt": "p"}],
            }
        )
        self.assertEqual(build_task_definitions(spec)[0]["id"], "task_1_coder")
        with self.assertRaisesRegex(ValueError, "each task"):
            build_task_definitions(
                MissionSpec.from_payload(
                    {"goal": "x", "strategy": "custom", "tasks": ["bad"]}
                )
            )
        with self.assertRaisesRegex(ValueError, "duplicate task"):
            build_task_definitions(
                MissionSpec.from_payload(
                    {
                        "goal": "x",
                        "strategy": "custom",
                        "tasks": [{"id": "a"}, {"id": "a"}],
                    }
                )
            )
        with self.assertRaisesRegex(ValueError, "depends_on must"):
            build_task_definitions(
                MissionSpec.from_payload(
                    {
                        "goal": "x",
                        "strategy": "custom",
                        "tasks": [{"id": "a", "depends_on": "b"}],
                    }
                )
            )
        with self.assertRaisesRegex(ValueError, "cycle"):
            build_task_definitions(
                MissionSpec.from_payload(
                    {
                        "goal": "x",
                        "strategy": "custom",
                        "tasks": [
                            {"id": "a", "depends_on": ["b"]},
                            {"id": "b", "depends_on": ["a"]},
                        ],
                    }
                )
            )
        self.assertIsNone(run_status_to_task_status("created"))

    def test_review_gate_parser_is_conservative(self) -> None:
        gate = parse_review_gate(
            {
                "decision": "pass",
                "severity": "low",
                "findings": [
                    {
                        "id": "sec",
                        "severity": "critical",
                        "message": "critical finding",
                    }
                ],
            }
        )
        self.assertTrue(gate.blocks)
        self.assertEqual(gate.effective_decision, "block")
        self.assertEqual(gate.severity, "critical")

        invalid_decision = parse_review_gate({"decision": "maybe"})
        self.assertTrue(invalid_decision.blocks)
        self.assertFalse(invalid_decision.valid)
        self.assertEqual(invalid_decision.effective_decision, "needs_human")

        invalid_finding = parse_review_gate(
            {"decision": "warn", "findings": [{"severity": "low"}]}
        )
        self.assertTrue(invalid_finding.blocks)
        self.assertIn("finding 1", invalid_finding.error)

        invalid_evidence = parse_review_gate(
            {
                "decision": "warn",
                "findings": [
                    {
                        "id": "audit-001",
                        "severity": "low",
                        "message": "evidence must be structured",
                        "evidence": ["not", "an", "object"],
                    }
                ],
            }
        )
        self.assertTrue(invalid_evidence.blocks)
        self.assertFalse(invalid_evidence.valid)

    def test_qwen_not_configured_and_inactive_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RunManager(Path(tmp), adapters={"qwen": QwenServeAdapter()})
            try:
                run = manager.create_run(RunSpec(prompt=None, adapter="qwen"))
                self.wait_for_status(manager, run.run_id, "failed")
                manager.send_input(run.run_id, "late prompt")
                events = [event.type for event in manager.store.events_since(run.run_id)]
                self.assertIn("adapter.not_configured", events)
                self.assertIn("input.rejected", events)
            finally:
                manager.shutdown()

    def test_qwen_event_mapping_and_request_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(Path(tmp))
            try:
                run = store.create_run(RunSpec(adapter="qwen"))
                adapter = QwenServeAdapter(base_url="http://127.0.0.1:1")
                adapter._map_qwen_event(run.run_id, "x", "text", store)
                adapter._map_qwen_event(
                    run.run_id,
                    "permission_request",
                    {"type": "permission_request"},
                    store,
                )
                adapter._map_qwen_event(
                    run.run_id,
                    "permission_resolved",
                    {"type": "permission_resolved"},
                    store,
                )
                adapter._map_qwen_event(run.run_id, "session_died", {"type": "session_died"}, store)
                adapter._map_qwen_event(run.run_id, "other", {"type": "other"}, store)
                names = [event.type for event in store.events_since(run.run_id)]
                self.assertIn("permission.requested", names)
                self.assertIn("permission.resolved", names)
                self.assertIn("run.failed", names)

                complete_run = store.create_run(RunSpec(adapter="qwen"))
                adapter._active_prompts[complete_run.run_id] = 1
                adapter._map_qwen_event(
                    complete_run.run_id,
                    "turn_complete",
                    {"type": "turn_complete", "data": {"promptId": "missing"}},
                    store,
                )
                self.assertEqual(store.get_run(complete_run.run_id).status, "completed")

                error_run = store.create_run(RunSpec(adapter="qwen"))
                adapter._map_qwen_event(
                    error_run.run_id,
                    "turn_error",
                    {"type": "turn_error", "data": {"message": "boom"}},
                    store,
                )
                self.assertEqual(store.get_run(error_run.run_id).status, "failed")

                gap_run = store.create_run(RunSpec(adapter="qwen"))
                adapter._record_qwen_gap(gap_run.run_id, "1", "4", store)
                self.assertIn(
                    "event.gap_detected",
                    [event.type for event in store.events_since(gap_run.run_id)],
                )
                self.assertEqual(parse_json_or_text("{"), "{")
                self.assertEqual(parse_int("7"), 7)
                self.assertIsNone(parse_int("x"))
            finally:
                store.close()

    def test_small_adapter_and_store_edges(self) -> None:
        self.assertEqual(FakeAdapter._chunks(""), ["empty prompt"])
        self.assertEqual(FakeAdapter._chunks("   "), ["   "])

        adapter = QwenServeAdapter(base_url="http://example.test", token="tok")
        request = adapter._build_request("GET", "/x")
        self.assertEqual(request.headers["Authorization"], "Bearer tok")
        self.assertEqual(
            adapter._permission_payload({"decision": "approve"}),
            {"outcome": {"outcome": "selected", "optionId": "proceed_once"}},
        )
        self.assertEqual(
            adapter._permission_payload({"decision": "deny"}),
            {"outcome": {"outcome": "selected", "optionId": "deny"}},
        )
        self.assertEqual(
            adapter._permission_payload({"decision": "cancel", "reason": "timeout"}),
            {"outcome": {"outcome": "cancelled", "reason": "timeout"}},
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(Path(tmp))
            try:
                run = store.create_run(RunSpec(adapter="fake"))
                store.append_event(run.run_id, "input.accepted", {})
                self.assertEqual(store.get_run(run.run_id).status, "queued")
                store.update_status(run.run_id, "manual")
                self.assertEqual(store.get_run(run.run_id).status, "manual")
                self.assertEqual(store.wait_for_events(run.run_id, 999, timeout=0.01), [])
            finally:
                store.close()

    def test_qwen_cancel_and_http_error_paths(self) -> None:
        with running_fake_qwen() as qwen_url:
            with tempfile.TemporaryDirectory() as tmp:
                manager = RunManager(
                    Path(tmp),
                    adapters={"qwen": QwenServeAdapter(base_url=qwen_url)},
                )
                try:
                    run = manager.create_run(RunSpec(adapter="qwen"))
                    manager.cancel(run.run_id, "stop")
                    self.assertEqual(manager.get_run(run.run_id).status, "cancelled")
                finally:
                    manager.shutdown()

        with running_error_qwen() as qwen_url:
            with tempfile.TemporaryDirectory() as tmp:
                manager = RunManager(
                    Path(tmp),
                    adapters={"qwen": QwenServeAdapter(base_url=qwen_url)},
                )
                try:
                    run = manager.create_run(RunSpec(adapter="qwen"))
                    self.wait_for_status(manager, run.run_id, "failed")
                finally:
                    manager.shutdown()

    def test_supervisor_env_and_process_lifecycle(self) -> None:
        with patched_env(
            QWEN_SERVE_COMMAND="python3 -m http.server 9999",
            QWEN_SERVE_URL="http://127.0.0.1:9999",
            QWEN_SERVE_CWD="/tmp",
            QWEN_SERVE_STARTUP_TIMEOUT="0.1",
        ):
            supervisor = qwen_supervisor_from_env()
            self.assertIsNotNone(supervisor)
            self.assertEqual(supervisor.config.cwd, Path("/tmp"))

        with patched_env(QWEN_SERVE_COMMAND=None):
            self.assertIsNone(qwen_supervisor_from_env())

        port = free_port()
        command = [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"]
        process = QwenServeProcess.from_command(
            command,
            base_url=f"http://127.0.0.1:{port}",
            startup_timeout_seconds=3,
        )
        process.start()
        process.start()
        process.stop()
        process.stop()

    def assert_http_error(
        self,
        url: str,
        code: "HTTPErrorCode",
        method: str = "GET",
        body: dict[str, object] | None = None,
        raw_body: bytes | None = None,
    ) -> None:
        data = (
            raw_body
            if raw_body is not None
            else json.dumps(body).encode()
            if body is not None
            else None
        )
        request = urllib.request.Request(url, data=data, method=method)
        if body is not None or raw_body is not None:
            request.add_header("content-type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(ctx.exception.code, code.value)

    def wait_for_status(self, manager: RunManager, run_id: str, status: str) -> None:
        deadline = time.time() + 2
        while time.time() < deadline:
            current = manager.get_run(run_id)
            if current and current.status == status:
                return
            time.sleep(0.02)
        self.fail(f"run {run_id} did not reach {status}")


class HTTPErrorCode(IntEnum):
    BAD_REQUEST = 400
    NOT_FOUND = 404


class running_error_qwen:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ErrorQwenHandler)
        import threading

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


class ErrorQwenHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        body = b'{"error":"boom"}'
        self.send_response(500)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


@contextmanager
def patched_env(**values: str | None):
    old = {name: os.environ.get(name) for name in values}
    try:
        for name, value in values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _BaseSmoke(RuntimeAdapter):
    name = "smoke"

    def capabilities(self) -> dict[str, object]:
        return {}

    def start(self, run, store) -> None:
        return None

    def send_input(self, run, prompt: str, store) -> None:
        return None

    def cancel(self, run, reason: str | None, store) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
