from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from typing import Any

from .base import RuntimeAdapter
from ..models import RunState
from ..review_gate import (
    gate_artifact_name,
    gate_type,
    is_structured_gate_task,
    parse_review_gate_from_text,
)
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
        self._active_prompts: dict[str, int] = {}
        self._prompt_ids: dict[str, dict[str, int]] = {}
        self._message_text: dict[str, list[str]] = {}
        self._run_metadata: dict[str, dict[str, Any]] = {}
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
                self._message_text[run.run_id] = []
                self._run_metadata[run.run_id] = dict(run.spec.metadata)
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
                self._request(
                    "POST",
                    f"/session/{session_id}/cancel",
                    {"reason": reason or "cancelled"},
                )
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
        self._forget_run(run.run_id)

    def resolve_permission(
        self,
        run: RunState,
        permission_id: str,
        payload: dict[str, Any],
        store: RunStore,
    ) -> None:
        decision_payload = self._permission_payload(payload)
        try:
            response = self._request("POST", f"/permission/{permission_id}", decision_payload)
        except Exception as exc:  # noqa: BLE001 - surface qwen mediation failure
            store.append_event(
                run.run_id,
                "permission.resolve_failed",
                {
                    "adapter": self.name,
                    "permission_id": permission_id,
                    "decision": payload["decision"],
                    "reason": str(exc),
                },
            )
            raise
        store.append_raw_event(
            run.run_id,
            self.name,
            {
                "kind": "permission_response",
                "permission_id": permission_id,
                "response": response,
            },
        )
        store.append_event(
            run.run_id,
            "permission.resolved",
            {
                "permission_id": permission_id,
                "decision": payload["decision"],
                "decided_by": payload.get("decided_by"),
                "reason": payload.get("reason"),
                "qwen_response": response,
            },
        )

    def _post_prompt(
        self,
        run_id: str,
        session_id: str,
        prompt_number: int,
        prompt: str,
        store: RunStore,
    ) -> None:
        if self._is_cancelled(run_id) or store.is_terminal(run_id):
            return
        store.append_event(
            run_id,
            "step.started",
            {"adapter": self.name, "prompt_number": prompt_number, "qwen_session_id": session_id},
        )
        with self._lock:
            self._active_prompts[run_id] = prompt_number
        try:
            response = self._request(
                "POST",
                f"/session/{session_id}/prompt",
                {"prompt": [{"type": "text", "text": prompt}]},
            )
            prompt_id = response.get("promptId") or response.get("prompt_id")
            with self._lock:
                if isinstance(prompt_id, str):
                    self._prompt_ids.setdefault(run_id, {})[prompt_id] = prompt_number
            store.append_raw_event(
                run_id,
                self.name,
                {
                    "kind": "prompt_response",
                    "prompt_number": prompt_number,
                    "response": response,
                },
            )
            if not self._is_cancelled(run_id) and not store.is_terminal(run_id):
                store.append_event(
                    run_id,
                    "step.submitted",
                    {
                        "prompt_number": prompt_number,
                        "prompt_id": prompt_id,
                        "qwen_response": response,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            if not self._is_cancelled(run_id) and not store.is_terminal(run_id):
                store.append_event(
                    run_id,
                    "run.failed",
                    {
                        "adapter": self.name,
                        "prompt_number": prompt_number,
                        "reason": str(exc),
                    },
                )
                self._forget_run(run_id)

    def _pump_events(self, run_id: str, session_id: str, store: RunStore) -> None:
        last_sse_id: str | None = None
        reconnects = 0
        while not self._is_cancelled(run_id) and not store.is_terminal(run_id):
            try:
                last_sse_id, events_seen = self._read_event_stream(
                    run_id, session_id, last_sse_id, store
                )
                reconnects = 0 if events_seen else reconnects + 1
                if store.is_terminal(run_id) or self._is_cancelled(run_id):
                    return
                store.append_event(
                    run_id,
                    "stream.warning",
                    {
                        "adapter": self.name,
                        "reason": "qwen event stream closed before terminal event",
                        "last_sse_id": last_sse_id,
                        "reconnect": reconnects,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                if store.is_terminal(run_id) or self._is_cancelled(run_id):
                    return
                reconnects += 1
                store.append_event(
                    run_id,
                    "stream.warning",
                    {
                        "adapter": self.name,
                        "reason": str(exc),
                        "last_sse_id": last_sse_id,
                        "reconnect": reconnects,
                    },
                )
            if reconnects >= 3:
                if not store.is_terminal(run_id):
                    store.append_event(
                        run_id,
                        "run.failed",
                        {
                            "adapter": self.name,
                            "reason": "qwen event stream disconnected",
                            "last_sse_id": last_sse_id,
                        },
                    )
                    self._forget_run(run_id)
                return

    def _read_event_stream(
        self,
        run_id: str,
        session_id: str,
        last_sse_id: str | None,
        store: RunStore,
    ) -> tuple[str | None, int]:
        headers = {"accept": "text/event-stream"}
        if last_sse_id:
            headers["Last-Event-ID"] = last_sse_id
        request = self._build_request("GET", f"/session/{session_id}/events", headers=headers)
        events_seen = 0
        event_name: str | None = None
        event_id: str | None = None
        data_lines: list[str] = []
        with urllib.request.urlopen(request, timeout=60) as response:
            for raw_line in response:
                if self._is_cancelled(run_id) or store.is_terminal(run_id):
                    return last_sse_id, events_seen
                line = raw_line.decode("utf-8").rstrip("\n")
                if line.startswith("id:"):
                    event_id = line[3:].strip()
                elif line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif line == "" and data_lines:
                    payload = parse_json_or_text("\n".join(data_lines))
                    self._record_qwen_gap(run_id, last_sse_id, event_id, store)
                    store.append_raw_event(
                        run_id,
                        self.name,
                        {"sse_id": event_id, "event": event_name, "data": payload},
                    )
                    self._map_qwen_event(run_id, event_name, payload, store)
                    last_sse_id = event_id or last_sse_id
                    events_seen += 1
                    event_name = None
                    event_id = None
                    data_lines = []
        return last_sse_id, events_seen

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
                text = str(content.get("text") or "")
                with self._lock:
                    self._message_text.setdefault(run_id, []).append(text)
                store.append_event(
                    run_id,
                    "message.delta",
                    {"text": text, "raw_type": qwen_type},
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
            self._forget_run(run_id)
            return
        if qwen_type == "turn_complete":
            self._complete_turn(run_id, payload, store)
            return
        if qwen_type == "turn_error":
            store.append_event(run_id, "run.failed", {"raw": payload})
            self._forget_run(run_id)
            return
        store.append_event(run_id, "adapter.event", {"adapter": self.name, "raw": payload})

    def _complete_turn(self, run_id: str, payload: dict[str, Any], store: RunStore) -> None:
        if store.is_terminal(run_id):
            return
        prompt_number = self._prompt_number_for_event(run_id, payload)
        self._write_gate_from_text_if_needed(run_id, store)
        final_artifact = f"final_{prompt_number}.json" if prompt_number else "final_qwen.json"
        store.write_json(
            run_id,
            final_artifact,
            {"prompt_number": prompt_number, "qwen_event": payload},
        )
        store.append_event(run_id, "step.completed", {"prompt_number": prompt_number})
        store.append_event(
            run_id,
            "run.completed",
            {
                "prompt_number": prompt_number,
                "final_artifact": final_artifact,
                "raw": payload,
            },
        )
        self._forget_run(run_id)

    def _write_gate_from_text_if_needed(self, run_id: str, store: RunStore) -> None:
        with self._lock:
            metadata = dict(self._run_metadata.get(run_id) or {})
            text = "".join(self._message_text.get(run_id) or [])
        profile = metadata.get("profile_snapshot")
        if not isinstance(profile, dict) or not is_structured_gate_task(profile):
            return
        artifact_name = gate_artifact_name(profile)
        gate_type_name = gate_type(profile) or "reviewer"
        gate = parse_review_gate_from_text(
            text,
            source_artifact=artifact_name,
            gate_type_name=gate_type_name,
        )
        if not gate.valid:
            store.append_event(
                run_id,
                "gate.extract_failed",
                {
                    "artifact": artifact_name,
                    "gate_type": gate_type_name,
                    "error": gate.error,
                },
            )
            return
        store.write_json(run_id, artifact_name, gate.to_dict())
        store.append_event(
            run_id,
            "gate.artifact_extracted",
            {"artifact": artifact_name, "gate_type": gate_type_name},
        )

    def _prompt_number_for_event(self, run_id: str, payload: dict[str, Any]) -> int | None:
        data = payload.get("data")
        prompt_id = data.get("promptId") if isinstance(data, dict) else None
        with self._lock:
            if isinstance(prompt_id, str):
                prompt_number = self._prompt_ids.get(run_id, {}).get(prompt_id)
                if prompt_number:
                    return prompt_number
            return self._active_prompts.get(run_id)

    def _forget_run(self, run_id: str) -> None:
        with self._lock:
            self._sessions.pop(run_id, None)
            self._active_prompts.pop(run_id, None)
            self._prompt_ids.pop(run_id, None)
            self._message_text.pop(run_id, None)
            self._run_metadata.pop(run_id, None)
            self._cancelled.discard(run_id)

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
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
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> urllib.request.Request:
        body = None
        request_headers = {"accept": "application/json"}
        request_headers.update(headers or {})
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            request_headers["content-type"] = "application/json"
        if self.token:
            request_headers["authorization"] = f"Bearer {self.token}"
        return urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=request_headers,
            method=method,
        )

    def _is_cancelled(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._cancelled

    def _permission_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        decision = payload["decision"]
        if decision == "cancel":
            return {"outcome": {"outcome": "cancelled", "reason": payload.get("reason")}}
        option_id = payload.get("option_id") or payload.get("optionId")
        if not isinstance(option_id, str) or not option_id:
            option_id = "proceed_once" if decision == "approve" else "deny"
        return {"outcome": {"outcome": "selected", "optionId": option_id}}

    def _record_qwen_gap(
        self,
        run_id: str,
        previous_sse_id: str | None,
        current_sse_id: str | None,
        store: RunStore,
    ) -> None:
        previous = parse_int(previous_sse_id)
        current = parse_int(current_sse_id)
        if previous is None or current is None or current <= previous + 1:
            return
        store.append_event(
            run_id,
            "event.gap_detected",
            {
                "source": "qwen_sse",
                "previous_sse_id": previous,
                "current_sse_id": current,
                "missing": current - previous - 1,
            },
        )


def parse_json_or_text(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
