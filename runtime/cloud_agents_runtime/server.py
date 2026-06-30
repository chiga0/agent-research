from __future__ import annotations

import argparse
import json
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import __version__
from .manager import RunManager
from .models import RunSpec


def make_handler(manager: RunManager) -> type[BaseHTTPRequestHandler]:
    class RuntimeHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"cloud-agents-runtime/{__version__}"

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            parts = split_path(path)
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
            self.write_error(HTTPStatus.NOT_FOUND, "not found")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
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
                    self.write_json({"accepted": True, "run_id": parts[1]}, status=HTTPStatus.ACCEPTED)
                    return
                if len(parts) == 3 and parts[0] == "runs" and parts[2] == "cancel":
                    manager.cancel(parts[1], payload.get("reason"))
                    self.write_json({"cancelled": True, "run_id": parts[1]}, status=HTTPStatus.ACCEPTED)
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
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def write_error(self, status: HTTPStatus, message: str) -> None:
            self.write_json({"error": message}, status=status)

        def write_sse(self, event_id: int, event_type: str, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            frame = f"id: {event_id}\nevent: {event_type}\ndata: {data}\n\n"
            self.wfile.write(frame.encode("utf-8"))
            self.wfile.flush()

        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

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


def build_server(host: str, port: int, artifact_root: Path) -> ThreadingHTTPServer:
    manager = RunManager(artifact_root=artifact_root)
    return ThreadingHTTPServer((host, port), make_handler(manager))


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
    args = parser.parse_args(argv)
    server = build_server(args.host, args.port, args.artifact_root)
    print(f"cloud-agents-runtime listening on http://{args.host}:{args.port}")
    print(f"artifacts: {args.artifact_root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
