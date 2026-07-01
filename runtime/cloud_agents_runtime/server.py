from __future__ import annotations

import argparse
import json
import os
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import __version__
from .auth import AuthConfig, is_authorized
from .manager import RunManager
from .models import RunSpec
from .supervisor import qwen_supervisor_from_env


def make_handler(
    manager: RunManager,
    auth_config: AuthConfig | None = None,
) -> type[BaseHTTPRequestHandler]:
    auth_config = auth_config or AuthConfig()

    class RuntimeHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"cloud-agents-runtime/{__version__}"

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if not self.require_auth(path):
                return
            parts = split_path(path)
            if path in {"/", "/ui"}:
                self.write_html(load_index_html())
                return
            if path == "/health":
                self.write_json({"ok": True, "version": __version__})
                return
            if path == "/capabilities":
                self.write_json(manager.capabilities())
                return
            if len(parts) == 1 and parts[0] == "runs":
                self.write_json({"runs": [run.to_dict() for run in manager.store.list_runs()]})
                return
            if len(parts) == 2 and parts[0] == "runs":
                run = manager.get_run(parts[1])
                if run is None:
                    self.write_error(HTTPStatus.NOT_FOUND, "run not found")
                    return
                self.write_json(run.to_dict())
                return
            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "events":
                self.stream_events(parts[1])
                return
            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "events.json":
                try:
                    events = manager.store.events_since(parts[1])
                except KeyError:
                    self.write_error(HTTPStatus.NOT_FOUND, "run not found")
                    return
                self.write_json({"events": [event.to_dict() for event in events]})
                return
            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "artifacts":
                try:
                    artifacts = manager.store.list_artifacts(parts[1])
                except KeyError:
                    self.write_error(HTTPStatus.NOT_FOUND, "run not found")
                    return
                self.write_json({"artifacts": artifacts})
                return
            self.write_error(HTTPStatus.NOT_FOUND, "not found")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if not self.require_auth(path):
                return
            parts = split_path(path)
            try:
                payload = self.read_json()
                if len(parts) == 1 and parts[0] == "runs":
                    spec = RunSpec.from_payload(payload)
                    run = manager.create_run(spec)
                    self.write_json(run.to_dict(), status=HTTPStatus.CREATED)
                    return
                if len(parts) == 3 and parts[0] == "runs" and parts[2] == "input":
                    prompt = payload.get("prompt")
                    if not isinstance(prompt, str) or not prompt.strip():
                        self.write_error(HTTPStatus.BAD_REQUEST, "prompt is required")
                        return
                    manager.send_input(parts[1], prompt)
                    self.write_json(
                        {"accepted": True, "run_id": parts[1]},
                        status=HTTPStatus.ACCEPTED,
                    )
                    return
                if len(parts) == 3 and parts[0] == "runs" and parts[2] == "cancel":
                    manager.cancel(parts[1], payload.get("reason"))
                    self.write_json(
                        {"cancelled": True, "run_id": parts[1]},
                        status=HTTPStatus.ACCEPTED,
                    )
                    return
                if (
                    len(parts) == 4
                    and parts[0] == "runs"
                    and parts[2] == "permissions"
                    and parts[3]
                ):
                    manager.resolve_permission(parts[1], parts[3], payload)
                    self.write_json(
                        {"accepted": True, "run_id": parts[1], "permission_id": parts[3]},
                        status=HTTPStatus.ACCEPTED,
                    )
                    return
            except KeyError:
                self.write_error(HTTPStatus.NOT_FOUND, "run not found")
                return
            except ValueError as exc:
                self.write_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except json.JSONDecodeError:
                self.write_error(HTTPStatus.BAD_REQUEST, "invalid json")
                return
            self.write_error(HTTPStatus.NOT_FOUND, "not found")

        def stream_events(self, run_id: str) -> None:
            if manager.get_run(run_id) is None:
                self.write_error(HTTPStatus.NOT_FOUND, "run not found")
                return

            last_sequence = parse_last_event_id(self.headers.get("Last-Event-ID"))
            last_sequence = manager.store.record_gap_if_needed(run_id, last_sequence)
            self.send_response(HTTPStatus.OK)
            self.send_header("content-type", "text/event-stream; charset=utf-8")
            self.send_header("cache-control", "no-cache")
            self.send_header("connection", "close")
            self.end_headers()
            self.close_connection = True

            last_heartbeat = time.monotonic()
            try:
                while True:
                    events = manager.store.wait_for_events(run_id, last_sequence, timeout=1.0)
                    for event in events:
                        self.write_sse(event.sequence, event.type, event.to_dict())
                        last_sequence = event.sequence
                    if manager.store.is_terminal(run_id) and not events:
                        break
                    if time.monotonic() - last_heartbeat >= 10:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                        last_heartbeat = time.monotonic()
            except (BrokenPipeError, ConnectionResetError):
                return

        def require_auth(self, path: str) -> bool:
            if is_authorized(auth_config, path, self.headers.get("authorization")):
                return True
            self.write_json(
                {"error": "unauthorized"},
                status=HTTPStatus.UNAUTHORIZED,
                headers={"www-authenticate": "Bearer"},
            )
            return False

        def read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("content-length", "0") or "0")
            if length == 0:
                return {}
            body = self.rfile.read(length)
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("json object required")
            return payload

        def write_json(
            self,
            payload: dict[str, Any],
            status: HTTPStatus = HTTPStatus.OK,
            headers: dict[str, str] | None = None,
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.close_connection = True

        def write_html(self, html: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.send_header("cache-control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.close_connection = True

        def write_error(self, status: HTTPStatus, message: str) -> None:
            self.write_json({"error": message}, status=status)

        def write_sse(self, event_id: int, event_type: str, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            frame = f"id: {event_id}\nevent: {event_type}\ndata: {data}\n\n"
            self.wfile.write(frame.encode("utf-8"))
            self.wfile.flush()

        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write(
                "%s - - [%s] %s\n"
                % (self.address_string(), self.log_date_time_string(), fmt % args)
            )

    return RuntimeHandler


def split_path(path: str) -> list[str]:
    return [part for part in path.strip("/").split("/") if part]


def parse_last_event_id(value: str | None) -> int:
    if not value:
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        return 0


def load_index_html() -> str:
    path = Path(__file__).with_name("static") / "index.html"
    return path.read_text(encoding="utf-8")


def build_server(
    host: str,
    port: int,
    artifact_root: Path,
    auth_config: AuthConfig | None = None,
    qwen_base_url: str | None = None,
    qwen_token: str | None = None,
) -> ThreadingHTTPServer:
    manager = RunManager(
        artifact_root=artifact_root,
        qwen_base_url=qwen_base_url,
        qwen_token=qwen_token,
    )
    return ThreadingHTTPServer((host, port), make_handler(manager, auth_config=auth_config))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cloud Agents Runtime POC")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("runtime/artifacts"),
        help="directory for run artifacts",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("RUN_MANAGER_TOKEN"),
        help="bearer token for Run Manager API; defaults to RUN_MANAGER_TOKEN",
    )
    parser.add_argument(
        "--protect-health",
        action="store_true",
        default=os.environ.get("RUN_MANAGER_PROTECT_HEALTH") == "1",
        help="require bearer token for /health too",
    )
    parser.add_argument(
        "--qwen-url",
        default=os.environ.get("QWEN_SERVE_URL"),
        help="existing qwen serve base URL",
    )
    parser.add_argument(
        "--qwen-token",
        default=os.environ.get("QWEN_SERVE_TOKEN"),
        help="bearer token for qwen serve",
    )
    args = parser.parse_args(argv)
    supervisor = qwen_supervisor_from_env()
    if supervisor:
        supervisor.start()
    server = build_server(
        args.host,
        args.port,
        args.artifact_root,
        auth_config=AuthConfig(token=args.token, protect_health=args.protect_health),
        qwen_base_url=args.qwen_url,
        qwen_token=args.qwen_token,
    )
    print(f"cloud-agents-runtime listening on http://{args.host}:{args.port}")
    print(f"artifacts: {args.artifact_root}")
    if args.token:
        print("run manager auth: enabled")
    if args.qwen_url:
        print(f"qwen serve: {args.qwen_url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        server.server_close()
        if supervisor:
            supervisor.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
