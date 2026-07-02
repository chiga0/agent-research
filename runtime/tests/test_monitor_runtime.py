from __future__ import annotations

import base64
import json
import threading
import unittest
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from scripts.monitor_runtime import PublicRuntimeMonitor, normalize_base_url


class MonitorRuntimeTest(unittest.TestCase):
    def test_normalize_base_url_defaults_cloud_agents_path(self) -> None:
        self.assertEqual(
            normalize_base_url("example.com"),
            "https://example.com/cloud-agents",
        )
        self.assertEqual(
            normalize_base_url("https://example.com/cloud-agents/"),
            "https://example.com/cloud-agents",
        )

    def test_public_monitor_checks_console_api_and_deep_run(self) -> None:
        with running_monitor_server() as base_url:
            monitor = PublicRuntimeMonitor(base_url, "cloudagents", "secret", 5.0)
            results = monitor.run(deep_run=True)
        self.assertTrue(all(result.ok for result in results), results)
        self.assertEqual(
            [result.name for result in results],
            [
                "edge-auth",
                "console-html",
                "health",
                "capabilities",
                "queue",
                "executors",
                "access-policy",
                "fake-run",
            ],
        )

    def test_public_monitor_fails_when_edge_auth_is_missing(self) -> None:
        with running_monitor_server(require_auth=False) as base_url:
            monitor = PublicRuntimeMonitor(base_url, "cloudagents", "secret", 5.0)
            results = monitor.run()
        edge_auth = results[0]
        self.assertFalse(edge_auth.ok)
        self.assertIn("expected 401", edge_auth.detail)


class MonitorHandler(BaseHTTPRequestHandler):
    require_auth = True
    basic_token = base64.b64encode(b"cloudagents:secret").decode("ascii")

    def do_GET(self) -> None:
        if self.require_auth and not self.is_authorized():
            self.send_basic_challenge()
            return
        if self.path == "/cloud-agents/":
            self.send_text(
                '<html><div id="root"></div>'
                '<script type="module" src="./assets/index.js"></script></html>'
            )
        elif self.path == "/cloud-agents/assets/index.js":
            self.send_text("console.log('Cloud Agents Runtime')")
        elif self.path == "/cloud-agents/health":
            self.send_json({"ok": True, "version": "test"})
        elif self.path == "/cloud-agents/capabilities":
            self.send_json(
                {
                    "adapters": ["fake", "qwen"],
                    "features": ["reviewer_gate_override"],
                }
            )
        elif self.path == "/cloud-agents/queue":
            self.send_json({"workers": [{"id": "worker-1"}], "counts": {"completed": 1}})
        elif self.path == "/cloud-agents/executors":
            self.send_json(
                {
                    "executor_registry": {
                        "config": {"strategy": "shared"},
                        "counts": {},
                    },
                    "executors": [],
                }
            )
        elif self.path == "/cloud-agents/access/policy":
            self.send_json(
                {
                    "current_principal": {"id": "test-user"},
                    "roles": [{"id": "owner"}],
                }
            )
        elif self.path == "/cloud-agents/runs/run_1/events":
            self.send_sse(
                [
                    ("run.created", {"run_id": "run_1"}),
                    ("run.completed", {"run_id": "run_1"}),
                ]
            )
        elif self.path == "/cloud-agents/runs/run_1":
            self.send_json({"run_id": "run_1", "status": "completed"})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.require_auth and not self.is_authorized():
            self.send_basic_challenge()
            return
        if self.path == "/cloud-agents/runs":
            self.send_json({"run_id": "run_1"}, HTTPStatus.CREATED)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def is_authorized(self) -> bool:
        return self.headers.get("authorization") == f"Basic {self.basic_token}"

    def send_basic_challenge(self) -> None:
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Cloud Agents Runtime"')
        self.end_headers()

    def send_text(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
        self.close_connection = True

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
        self.close_connection = True

    def send_sse(self, events: list[tuple[str, dict[str, Any]]]) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for event, payload in events:
            self.wfile.write(f"event: {event}\n".encode("utf-8"))
            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, format: str, *args: object) -> None:
        return


class running_monitor_server:
    def __init__(self, require_auth: bool = True) -> None:
        self.require_auth = require_auth
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def __enter__(self) -> str:
        handler = type(
            "ConfiguredMonitorHandler",
            (MonitorHandler,),
            {"require_auth": self.require_auth},
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}/cloud-agents"

    def __exit__(self, *exc: object) -> None:
        assert self.server is not None
        self.server.shutdown()
        self.server.server_close()


if __name__ == "__main__":
    unittest.main()
