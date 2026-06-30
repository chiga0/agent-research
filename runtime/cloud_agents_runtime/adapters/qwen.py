from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from typing import Any

from .base import RuntimeAdapter
from ..models import RunState
from ..store import RunStore


class QwenServeAdapter(RuntimeAdapter):
    """Boundary for qwen serve REST/SSE integration.

    The adapter is intentionally present in P1 so the Run Manager contract does
    not leak fake-adapter assumptions. The live transport will map:

    - POST /session
    - POST /session/{id}/prompt
    - GET /session/{id}/events
    - POST /session/{id}/cancel

    into canonical SAEU events.
    """

    name = "qwen"

    def __init__(self, base_url: str | None = None, token: str | None = None):
        self.base_url = (base_url or os.environ.get("QWEN_SERVE_URL") or "").rstrip("/")
        self.token = token or os.environ.get("QWEN_SERVE_TOKEN")
        self._sessions: dict[str, str] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    def capabilities(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "mode": "qwen_serve_rest_sse",
            "configured": bool(self.base_url),
            "base_url": self.base_url or None,
            "features": ["start", "input", "events", "cancel"],
            "status": "ready" if self.base_url else "missing_QWEN_SERVE_URL",
        }

    def start(self, run: RunState, store: RunStore) -> None:
        if not self.base_url:
            store.append_event(
                run.run_id,
                "adapter.not_configured",
                {"adapter": self.name, "expected": "set QWEN_SERVE_URL"},
            )
            store.append_event(run.run_id, "run.failed", {"reason": "qwen adapter not configured"})
            return

        try:
            body: dict[str, Any] = {}
            if run.spec.workspace:
                body["cwd"] = run.spec.workspace
            response = self._request("POST", "/session", body)
            session_id = response["sessionId"]
            if not isinstance(session_id, str):
                raise ValueError("qwen /session response missing sessionId")
            with self._lock:
                self._sessions[run.run_id] = session_id
            store.set_adapter_run_id(run.run_id, session_id)
            store.append_event(
                run.run_id,
                "run.started",
                {
                    "adapter": self.name,
                    "qwen_session_id": session_id,
                    "attached": response.get("attached", False),
                    "workspace": response.get("workspaceCwd"),
                },
            )
            threading.Thread(
                target=self._pump_events,
                args=(run.run_id, session_id, store),
                daemon=True,
            ).start()
        except Exception as exc:  # noqa: BLE001 - convert adapter failure to canonical event
            store.append_event(
                run.run_id,
                "run.failed",
                {"adapter": self.name, "reason": str(exc)},
            )

    def send_input(self, run: RunState, prompt: str, store: RunStore) -> None:
        session_id = self._sessions.get(run.run_id)
        if not session_id:
            store.append_event(
                run.run_id,
                "input.rejected",
                {"adapter": self.name, "reason": "qwen session is not active"},
            )
            return

        prompt_number = store.increment_prompt_count(run.run_id)
        store.write_json(
            run.run_id,
            f"input_{prompt_number}.json",
            {"prompt": prompt, "prompt_number": prompt_number},
        )
        store.append_event(
            run.run_id,
            "input.accepted",
            {"prompt_number": prompt_number, "qwen_session_id": session_id},
        )
        threading.Thread(
            target=self._post_prompt,
            args=(run.run_id, session_id, prompt_number, prompt, store),
            daemon=True,
        ).start()

    def cancel(self, run: RunState, reason: str | None, store: RunStore) -> None:
        session_id = self._sessions.get(run.run_id)
        with self._lock:
            self._cancelled.add(run.run_id)
        if session_id:
            try:
                self._request("POST", f"/session/{session_id}/cancel", {"reason": reason or "cancelled"})
            except Exception as exc:  # noqa: BLE001
                store.append_event(
                    run.run_id,
                    "cancel.warning",
                    {"adapter": self.name, "reason": str(exc)},
                )
        store.append_event(
            run.run_id,
            "run.cancelled",
            {"adapter": self.name, "qwen_session_id": session_id, "reason": reason or "cancelled"},
        )

    def _post_prompt(
        self,
        run_id: str,
        session_id: str,
        prompt_number: int,
        prompt: str,
        store: RunStore,
    ) -> None:
        if self._is_cancelled(run_id):
            return
        store.append_event(
            run_id,
            "step.started",
            {"adapter": self.name, "prompt_number": prompt_number, "qwen_session_id": session_id},
        )
        try:
            response = self._request(
                "POST",
                f"/session/{session_id}/prompt",
                {"prompt": [{"type": "text", "text": prompt}]},
            )
            store.write_json(
                run_id,
                f"final_{prompt_number}.json",
                {"prompt_number": prompt_number, "qwen_response": response},
            )
            if not self._is_cancelled(run_id):
                store.append_event(run_id, "step.completed", {"prompt_number": prompt_number})
                store.append_event(
                    run_id,
                    "run.completed",
                    {
                        "prompt_number": prompt_number,
                        "stop_reason": response.get("stopReason"),
                        "final_artifact": f"final_{prompt_number}.json",
                    },
                )
        except Exception as exc:  # noqa: BLE001
            if not self._is_cancelled(run_id):
                store.append_event(
                    run_id,
                    "run.failed",
                    {
                        "adapter": self.name,
                        "prompt_number": prompt_number,
                        "reason": str(exc),
                    },
                )

    def _pump_events(self, run_id: str, session_id: str, store: RunStore) -> None:
        try:
            request = self._build_request("GET", f"/session/{session_id}/events")
            with urllib.request.urlopen(request, timeout=60) as response:
                event_name: str | None = None
                event_id: str | None = None
                data_lines: list[str] = []
                for raw_line in response:
                    if self._is_cancelled(run_id) or store.is_terminal(run_id):
                        return
                    line = raw_line.decode("utf-8").rstrip("\n")
                    if line.startswith("id:"):
                        event_id = line[3:].strip()
                    elif line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                    elif line == "" and data_lines:
                        payload = parse_json_or_text("\n".join(data_lines))
                        store.append_raw_event(
                            run_id,
                            self.name,
                            {"sse_id": event_id, "event": event_name, "data": payload},
                        )
                        self._map_qwen_event(run_id, event_name, payload, store)
                        event_name = None
                        event_id = None
                        data_lines = []
        except Exception as exc:  # noqa: BLE001
            if not store.is_terminal(run_id):
                store.append_event(
                    run_id,
                    "stream.warning",
                    {"adapter": self.name, "reason": str(exc)},
                )

    def _map_qwen_event(
        self, run_id: str, event_name: str | None, payload: Any, store: RunStore
    ) -> None:
        if not isinstance(payload, dict):
            store.append_event(run_id, "adapter.event", {"adapter": self.name, "event": event_name})
            return
        qwen_type = payload.get("type") or event_name
        data = payload.get("data")
        if qwen_type == "session_update" and isinstance(data, dict):
            session_update = data.get("sessionUpdate")
            content = data.get("content")
            if session_update == "agent_message_chunk" and isinstance(content, dict):
                store.append_event(
                    run_id,
                    "message.delta",
                    {"text": content.get("text", ""), "raw_type": qwen_type},
                )
                return
        if qwen_type == "permission_request":
            store.append_event(run_id, "permission.requested", {"raw": payload})
            return
        if qwen_type == "permission_resolved":
            store.append_event(run_id, "permission.resolved", {"raw": payload})
            return
        if qwen_type in {"session_died", "client_evicted"}:
            store.append_event(run_id, "run.failed", {"raw": payload})
            return
        store.append_event(run_id, "adapter.event", {"adapter": self.name, "raw": payload})

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = self._build_request(method, path, payload)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"qwen {method} {path} failed: {exc.code} {error_body}") from exc
        if not body:
            return {}
        parsed = json.loads(body.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise RuntimeError(f"qwen {method} {path} returned non-object JSON")
        return parsed

    def _build_request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> urllib.request.Request:
        body = None
        headers = {"accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["content-type"] = "application/json"
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        return urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )

    def _is_cancelled(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._cancelled


def parse_json_or_text(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value
