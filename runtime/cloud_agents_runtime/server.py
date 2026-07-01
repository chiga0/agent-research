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
from .interop import (
    a2a_agent_card,
    a2a_task_from_mission,
    create_a2a_task,
    handle_acp_jsonrpc,
)
from .manager import RunManager
from .models import RunSpec
from .supervisor import qwen_supervisor_from_env
from .temporal_poc import agent_run_workflow_plan, mission_workflow_plan


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
            if path == "/acp":
                self.write_json(
                    {
                        "protocol": "acp-poc",
                        "transport": "json-rpc-over-http",
                        "endpoint": "/acp",
                    }
                )
                return
            if path == "/.well-known/agent-card.json":
                self.write_json(a2a_agent_card(manager, self.base_url()))
                return
            if path == "/queue":
                self.write_json(manager.queue_status())
                return
            if path == "/workers":
                self.write_json({"workers": manager.queue_status()["workers"]})
                return
            if len(parts) == 1 and parts[0] == "profiles":
                self.write_json({"profiles": manager.list_profiles()})
                return
            if len(parts) == 2 and parts[0] == "profiles":
                profile = manager.get_profile(parts[1])
                if profile is None:
                    self.write_error(HTTPStatus.NOT_FOUND, "profile not found")
                    return
                self.write_json(profile)
                return
            if len(parts) == 1 and parts[0] == "missions":
                self.write_json({"missions": manager.list_missions()})
                return
            if len(parts) == 2 and parts[0] == "a2a" and parts[1] == "tasks":
                self.write_json({"tasks": manager.list_missions()})
                return
            if len(parts) == 3 and parts[0] == "a2a" and parts[1] == "tasks":
                try:
                    self.write_json(a2a_task_from_mission(manager, parts[2]))
                except KeyError:
                    self.write_error(HTTPStatus.NOT_FOUND, "task not found")
                return
            if len(parts) == 2 and parts[0] == "missions":
                mission = manager.get_mission(parts[1])
                if mission is None:
                    self.write_error(HTTPStatus.NOT_FOUND, "mission not found")
                    return
                self.write_json(mission)
                return
            if len(parts) == 3 and parts[0] == "missions" and parts[2] == "events.json":
                try:
                    events = manager.store.mission_events_since(parts[1])
                except KeyError:
                    self.write_error(HTTPStatus.NOT_FOUND, "mission not found")
                    return
                self.write_json({"events": [event.to_dict() for event in events]})
                return
            if len(parts) == 3 and parts[0] == "missions" and parts[2] == "artifacts":
                try:
                    artifacts = manager.store.list_mission_artifacts(parts[1])
                except KeyError:
                    self.write_error(HTTPStatus.NOT_FOUND, "mission not found")
                    return
                self.write_json({"artifacts": artifacts})
                return
            if (
                len(parts) == 5
                and parts[0] == "temporal"
                and parts[1] == "workflows"
                and parts[2] == "missions"
                and parts[4] == "plan"
            ):
                mission = manager.get_mission(parts[3])
                if mission is None:
                    self.write_error(HTTPStatus.NOT_FOUND, "mission not found")
                    return
                self.write_json(mission_workflow_plan(mission))
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
            if (
                len(parts) == 5
                and parts[0] == "temporal"
                and parts[1] == "workflows"
                and parts[2] == "runs"
                and parts[4] == "plan"
            ):
                run = manager.get_run(parts[3])
                if run is None:
                    self.write_error(HTTPStatus.NOT_FOUND, "run not found")
                    return
                self.write_json(agent_run_workflow_plan(run))
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
                if path == "/acp":
                    response, status = handle_acp_jsonrpc(manager, payload)
                    self.write_json(response, status=status)
                    return
                if len(parts) == 1 and parts[0] == "cleanup":
                    self.write_json({"cleanup": manager.cleanup_once()})
                    return
                if len(parts) == 1 and parts[0] == "profiles":
                    profile = manager.create_profile(payload)
                    self.write_json(profile, status=HTTPStatus.CREATED)
                    return
                if len(parts) == 1 and parts[0] == "missions":
                    mission = manager.create_mission(payload)
                    self.write_json(mission, status=HTTPStatus.CREATED)
                    return
                if len(parts) == 2 and parts[0] == "a2a" and parts[1] == "tasks":
                    task = create_a2a_task(manager, payload)
                    self.write_json(task, status=HTTPStatus.CREATED)
                    return
                if len(parts) == 3 and parts[0] == "missions" and parts[2] == "cancel":
                    try:
                        mission = manager.cancel_mission(parts[1], payload.get("reason"))
                    except KeyError:
                        self.write_error(HTTPStatus.NOT_FOUND, "mission not found")
                        return
                    self.write_json(mission, status=HTTPStatus.ACCEPTED)
                    return
                if (
                    len(parts) == 4
                    and parts[0] == "missions"
                    and parts[2] == "review-gate"
                    and parts[3] == "override"
                ):
                    try:
                        mission = manager.override_review_gate(parts[1], payload)
                    except KeyError:
                        self.write_error(HTTPStatus.NOT_FOUND, "mission not found")
                        return
                    self.write_json(mission, status=HTTPStatus.ACCEPTED)
                    return
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
            except RuntimeError as exc:
                self.write_error(HTTPStatus.BAD_GATEWAY, str(exc))
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

        def base_url(self) -> str:
            host = self.headers.get("host")
            if host:
                return f"http://{host}"
            server_host, server_port = self.server.server_address[:2]
            return f"http://{server_host}:{server_port}"

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
    worker_capacity: int | None = None,
    worker_id: str | None = None,
    lease_ttl_seconds: int | None = None,
) -> ThreadingHTTPServer:
    manager = RunManager(
        artifact_root=artifact_root,
        qwen_base_url=qwen_base_url,
        qwen_token=qwen_token,
        worker_capacity=worker_capacity,
        worker_id=worker_id,
        lease_ttl_seconds=lease_ttl_seconds,
        heartbeat_enabled=True,
    )
    return RuntimeHTTPServer((host, port), make_handler(manager, auth_config=auth_config), manager)


class RuntimeHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        manager: RunManager,
    ):
        super().__init__(server_address, handler_class)
        self.manager = manager

    def server_close(self) -> None:
        self.manager.shutdown()
        super().server_close()


def parse_optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


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
    parser.add_argument(
        "--worker-capacity",
        type=int,
        default=parse_optional_int(os.environ.get("RUN_MANAGER_WORKER_CAPACITY")),
        help="max concurrent SAEU runs for this local worker",
    )
    parser.add_argument(
        "--worker-id",
        default=os.environ.get("RUN_MANAGER_WORKER_ID"),
        help="stable id for this local worker heartbeat",
    )
    parser.add_argument(
        "--lease-ttl-seconds",
        type=int,
        default=parse_optional_int(os.environ.get("RUN_MANAGER_LEASE_TTL_SECONDS")),
        help="seconds before an unrefreshed run lease can be reclaimed",
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
        worker_capacity=args.worker_capacity,
        worker_id=args.worker_id,
        lease_ttl_seconds=args.lease_ttl_seconds,
    )
    print(f"cloud-agents-runtime listening on http://{args.host}:{args.port}")
    print(f"artifacts: {args.artifact_root}")
    if args.token:
        print("run manager auth: enabled")
    if args.qwen_url:
        print(f"qwen serve: {args.qwen_url}")
    print(f"worker capacity: {server.manager.worker_capacity}")
    print(f"resource limits: {server.manager.resource_resolver.config.to_dict()}")
    print(f"cleanup policy: {server.manager.cleanup_manager.policy.to_dict()}")
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
