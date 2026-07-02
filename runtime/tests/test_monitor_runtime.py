from __future__ import annotations

import json
import threading
import unittest
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from scripts.monitor_runtime import PublicRuntimeMonitor, normalize_base_url


class MonitorRuntimeTest(unittest.TestCase):
    def test_normalize_base_url_defaults_agentflow_path(self) -> None:
        self.assertEqual(
            normalize_base_url("example.com"),
            "https://example.com/agentflow",
        )
        self.assertEqual(
            normalize_base_url("https://example.com/agentflow/"),
            "https://example.com/agentflow",
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

    def test_public_monitor_fails_when_api_is_public_without_session(self) -> None:
        with running_monitor_server(require_auth=False) as base_url:
            monitor = PublicRuntimeMonitor(base_url, "cloudagents", "secret", 5.0)
            results = monitor.run()
        edge_auth = results[0]
        self.assertFalse(edge_auth.ok)
        self.assertIn("expected unauthenticated API 401", edge_auth.detail)


class MonitorHandler(BaseHTTPRequestHandler):
    require_auth = True
    session_cookie = "cloud_agents_session=test-session"

    def do_GET(self) -> None:
        if self.path == "/agentflow/":
            self.send_text(
                '<html><div id="root"></div>'
                '<script type="module" src="./assets/index.js"></script></html>'
            )
        elif self.path == "/agentflow/assets/index.js":
            self.send_text("console.log('AgentFlow')")
        elif self.path == "/agentflow/auth/session":
            self.send_json(
                {
                    "authenticated": self.is_authorized(),
                    "principal": (
                        {"id": "cloudagents", "roles": ["owner"]}
                        if self.is_authorized()
                        else None
                    ),
                }
            )
        elif self.path == "/agentflow/health":
            if not self.require_session():
                return
            self.send_json({"ok": True, "version": "test"})
        elif self.path == "/agentflow/capabilities":
            if not self.require_session():
                return
            self.send_json(
                {
                    "adapters": ["fake", "qwen"],
                    "features": ["reviewer_gate_override"],
                }
            )
        elif self.path == "/agentflow/queue":
            if not self.require_session():
                return
            self.send_json({"workers": [{"id": "worker-1"}], "counts": {"completed": 1}})
        elif self.path == "/agentflow/executors":
            if not self.require_session():
                return
            self.send_json(
                {
                    "executor_registry": {
                        "config": {"strategy": "shared"},
                        "counts": {},
                    },
                    "executors": [],
                }
            )
        elif self.path == "/agentflow/access/policy":
            if not self.require_session():
                return
            self.send_json(
                {
                    "current_principal": {"id": "test-user"},
                    "roles": [{"id": "owner"}],
                }
            )
        elif self.path == "/agentflow/runs/run_1/events":
            if not self.require_session():
                return
            self.send_sse(
                [
                    ("run.created", {"run_id": "run_1"}),
                    ("run.completed", {"run_id": "run_1"}),
                ]
            )
        elif self.path == "/agentflow/runs/run_1":
            if not self.require_session():
                return
            self.send_json({"run_id": "run_1", "status": "completed"})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path == "/agentflow/auth/login":
            payload = json.loads(self.read_body().decode("utf-8"))
            if payload == {"username": "cloudagents", "password": "secret"}:
                self.send_json(
                    {
                        "authenticated": True,
                        "principal": {"id": "cloudagents", "roles": ["owner"]},
                    },
                    headers={"Set-Cookie": f"{self.session_cookie}; Path=/agentflow"},
                )
                return
            self.send_json({"error": "invalid credentials"}, HTTPStatus.UNAUTHORIZED)
        elif self.path == "/agentflow/runs":
            if not self.require_session():
                return
            self.send_json({"run_id": "run_1"}, HTTPStatus.CREATED)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def is_authorized(self) -> bool:
        return self.session_cookie in (self.headers.get("cookie") or "")

    def require_session(self) -> bool:
        if not self.require_auth or self.is_authorized():
            return True
        self.send_json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
        return False

    def read_body(self) -> bytes:
        length = int(self.headers.get("content-length", "0") or "0")
        return self.rfile.read(length) if length else b""

    def send_text(self, body: str, headers: dict[str, str] | None = None) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(encoded)
        self.close_connection = True

    def send_json(
        self,
        payload: dict[str, Any],
        status: HTTPStatus = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
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
        return f"http://{host}:{port}/agentflow"

    def __exit__(self, *exc: object) -> None:
        assert self.server is not None
        self.server.shutdown()
        self.server.server_close()


if __name__ == "__main__":
    unittest.main()
