from __future__ import annotations

import json
import os
import subprocess
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
from runtime.cloud_agents_runtime.events import RuntimeEvent
from runtime.cloud_agents_runtime.executors import (
    ExecutorConfig,
    ExecutorRegistry,
    ManagedProcess,
    container_metadata,
    default_container_command,
    executor_env,
    normalize_strategy,
    parse_float as executor_parse_float,
    parse_int as executor_parse_int,
    port_available,
    render_command,
    reserve_ephemeral_port,
    safe_container_name,
)
from runtime.cloud_agents_runtime.interop import (
    a2a_task_from_mission,
    create_a2a_task,
    handle_acp_jsonrpc,
    jsonrpc_error,
    map_a2a_status,
    optional_int,
    require_string,
)
from runtime.cloud_agents_runtime.manager import RunManager
from runtime.cloud_agents_runtime.missions import build_task_definitions, run_status_to_task_status
from runtime.cloud_agents_runtime.models import (
    ExecutorLease,
    MissionSpec,
    RunSpec,
    RunState,
    clean_identifier,
)
from runtime.cloud_agents_runtime.ops import (
    env_nonnegative_int,
    latency_summary,
    pending_permission_count,
    permission_id_from_data,
    terminal_latency_seconds,
)
from runtime.cloud_agents_runtime.review_gate import (
    default_reason,
    extract_json_object,
    gate_artifact_name,
    gate_type,
    is_review_gate_task,
    is_structured_gate_task,
    load_review_gate,
    parse_review_gate,
    parse_review_gate_from_text,
    review_gate_artifact_name,
)
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
        auth = AuthConfig(
            token="x",
            login_user="operator",
            login_password="secret",
            session_secret="session-secret",
        )
        self.assertTrue(auth.login_matches("operator", "secret"))
        self.assertFalse(auth.login_matches("operator", "wrong"))
        cookie = auth.issue_session_cookie("operator")
        self.assertEqual(auth.session_identity(cookie)["principal_id"], "operator")
        self.assertTrue(auth.session_status(cookie)["authenticated"])
        self.assertFalse(auth.session_status(None)["authenticated"])
        self.assertEqual(parse_last_event_id(None), 0)
        self.assertEqual(parse_last_event_id("bad"), 0)
        self.assertEqual(parse_last_event_id("-1"), 0)
        self.assertEqual(parse_last_event_id("7"), 7)
        self.assertIsNone(parse_optional_int(None))
        self.assertIsNone(parse_optional_int(""))
        self.assertIsNone(parse_optional_int("bad"))
        self.assertEqual(parse_optional_int("4"), 4)

    def test_ops_helper_edges(self) -> None:
        self.assertEqual(permission_id_from_data({"permission_id": "direct"}), "direct")
        self.assertEqual(
            permission_id_from_data({"raw": {"data": {"requestId": "nested"}}}),
            "nested",
        )
        self.assertIsNone(permission_id_from_data({"raw": "bad"}))
        self.assertIsNone(permission_id_from_data({"raw": {"data": "bad"}}))
        events = [
            RuntimeEvent("permission.requested", "run_1", 1, {"permission_id": "a"}),
            RuntimeEvent(
                "permission.requested",
                "run_1",
                2,
                {"raw": {"data": {"requestId": "b"}}},
            ),
            RuntimeEvent("permission.resolved", "run_1", 3, {"permission_id": "a"}),
        ]
        self.assertEqual(pending_permission_count(events), 1)
        self.assertIsNone(terminal_latency_seconds([]))
        self.assertEqual(
            terminal_latency_seconds(
                [
                    RuntimeEvent(
                        "run.created",
                        "run_1",
                        1,
                        {},
                        created_at="2026-07-02T00:00:00+00:00",
                    ),
                    RuntimeEvent(
                        "run.completed",
                        "run_1",
                        2,
                        {},
                        created_at="2026-07-02T00:00:03+00:00",
                    ),
                ]
            ),
            3.0,
        )
        self.assertIsNone(
            terminal_latency_seconds(
                [
                    RuntimeEvent("run.created", "run_1", 1, {}, created_at="bad"),
                    RuntimeEvent("run.completed", "run_1", 2, {}, created_at="also-bad"),
                ]
            )
        )
        self.assertEqual(latency_summary([]), {"count": 0, "avg": None, "p95": None})
        self.assertEqual(latency_summary([1.0, 3.0])["avg"], 2.0)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(env_nonnegative_int("MISSING_INT", 7), 7)
        with patch.dict(os.environ, {"BAD_INT": "nope"}):
            self.assertEqual(env_nonnegative_int("BAD_INT", 7), 7)
        with patch.dict(os.environ, {"NEG_INT": "-5"}):
            self.assertEqual(env_nonnegative_int("NEG_INT", 7), 0)

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

        extracted = parse_review_gate_from_text(
            """
            reviewer summary
            ```json
            {"decision":"warn","severity":"medium","reason":"watch this","findings":[]}
            ```
            """
        )
        self.assertFalse(extracted.blocks)
        self.assertEqual(extracted.effective_decision, "warn")

    def test_review_gate_metadata_and_load_edges(self) -> None:
        reviewer_profile = {"artifacts": {"gate": {"type": "reviewer"}}}
        release_profile = {
            "artifacts": {"gate": {"type": "merge_deploy", "artifact": "release_gate.json"}}
        }
        self.assertTrue(is_review_gate_task(reviewer_profile))
        self.assertTrue(is_structured_gate_task(release_profile))
        self.assertFalse(is_structured_gate_task({"artifacts": "bad"}))
        self.assertEqual(gate_type(release_profile), "merge_deploy")
        self.assertIsNone(gate_type({}))
        self.assertEqual(gate_artifact_name(release_profile), "release_gate.json")
        self.assertEqual(review_gate_artifact_name({}), "review_gate.json")
        self.assertEqual(gate_artifact_name({"artifacts": "bad"}), "review_gate.json")

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            missing = load_review_gate(run_dir, "release_gate.json", "merge_deploy")
            self.assertFalse(missing.valid)
            self.assertEqual(missing.source_artifact, "release_gate.json")
            self.assertEqual(missing.gate_type, "merge_deploy")

            (run_dir / "bad.json").write_text("{", encoding="utf-8")
            bad_json = load_review_gate(run_dir, "bad.json")
            self.assertFalse(bad_json.valid)
            self.assertIn("invalid review gate json", bad_json.error or "")

            (run_dir / "array.json").write_text("[]", encoding="utf-8")
            not_object = load_review_gate(run_dir, "array.json")
            self.assertFalse(not_object.valid)
            self.assertIn("must be a JSON object", not_object.error or "")

        no_json = parse_review_gate_from_text("no json here")
        self.assertFalse(no_json.valid)
        self.assertIsNone(extract_json_object("```json\n[\n]\n```"))
        self.assertEqual(extract_json_object("prefix {\"decision\":\"pass\"}")["decision"], "pass")

        warn_high = parse_review_gate({"decision": "warn", "severity": "high"})
        self.assertTrue(warn_high.blocks)
        self.assertEqual(warn_high.effective_decision, "block")

        weird_severity = parse_review_gate({"decision": "pass", "severity": "surprise"})
        self.assertTrue(weird_severity.blocks)
        self.assertEqual(weird_severity.severity, "critical")

        self.assertEqual(
            default_reason("block", "high"),
            "blocking review finding severity: high",
        )
        self.assertEqual(
            default_reason("needs_human", "critical"),
            "review requires human decision",
        )
        self.assertEqual(
            default_reason("warn", "medium"),
            "review completed with warning severity: medium",
        )
        self.assertEqual(default_reason("pass", "none"), "review gate passed")

    def test_acp_and_a2a_interop_edges(self) -> None:
        manager = MiniInteropManager()

        response, status = handle_acp_jsonrpc(manager, {"id": 1})
        self.assertEqual(status.value, HTTPErrorCode.BAD_REQUEST.value)
        self.assertEqual(response["error"]["code"], -32600)

        response, status = handle_acp_jsonrpc(
            manager,
            {"jsonrpc": "2.0", "id": 2, "params": {}},
        )
        self.assertEqual(status.value, HTTPErrorCode.BAD_REQUEST.value)
        self.assertEqual(response["error"]["code"], -32600)

        response, status = handle_acp_jsonrpc(
            manager,
            {"jsonrpc": "2.0", "id": 3, "method": "unknown", "params": {}},
        )
        self.assertEqual(status.value, HTTPErrorCode.NOT_FOUND.value)
        self.assertEqual(response["error"]["code"], -32601)

        response, status = handle_acp_jsonrpc(
            manager,
            {"jsonrpc": "2.0", "id": 4, "method": "initialize", "params": {}},
        )
        self.assertEqual(status.value, 200)
        self.assertIn("run.create", response["result"]["methods"])
        self.assertIn("executor.list", response["result"]["methods"])

        response, status = handle_acp_jsonrpc(
            manager,
            {"jsonrpc": "2.0", "id": 41, "method": "capabilities.get", "params": {}},
        )
        self.assertEqual(status.value, 200)
        self.assertEqual(response["result"]["features"], ["interop-test"])

        response, status = handle_acp_jsonrpc(
            manager,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "run.create",
                "params": {"prompt": "hello", "adapter": "fake"},
            },
        )
        self.assertEqual(status.value, 200)
        self.assertEqual(response["result"]["run_id"], "run_acp")

        response, status = handle_acp_jsonrpc(
            manager,
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "run.status",
                "params": {"run_id": "run_acp"},
            },
        )
        self.assertEqual(status.value, 200)
        self.assertEqual(response["result"]["run_id"], "run_acp")

        response, status = handle_acp_jsonrpc(
            manager,
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "run.status",
                "params": {"run_id": "missing"},
            },
        )
        self.assertEqual(status.value, HTTPErrorCode.NOT_FOUND.value)
        self.assertEqual(response["error"]["code"], -32004)

        response, status = handle_acp_jsonrpc(
            manager,
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "run.input",
                "params": {"run_id": "run_acp", "prompt": "continue"},
            },
        )
        self.assertEqual(status.value, 200)
        self.assertEqual(manager.inputs, [("run_acp", "continue")])

        response, status = handle_acp_jsonrpc(
            manager,
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "run.input",
                "params": {"run_id": "run_acp", "prompt": ""},
            },
        )
        self.assertEqual(status.value, HTTPErrorCode.BAD_REQUEST.value)
        self.assertEqual(response["error"]["code"], -32602)

        response, status = handle_acp_jsonrpc(
            manager,
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "run.cancel",
                "params": {"run_id": "run_acp", "reason": "done"},
            },
        )
        self.assertEqual(status.value, 200)
        self.assertEqual(manager.cancelled, [("run_acp", "done")])

        response, status = handle_acp_jsonrpc(
            manager,
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "run.cancel",
                "params": {"run_id": "missing"},
            },
        )
        self.assertEqual(status.value, HTTPErrorCode.NOT_FOUND.value)
        self.assertEqual(response["error"]["message"], "run not found")

        response, status = handle_acp_jsonrpc(
            manager,
            {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "run.events",
                "params": {"run_id": "run_acp", "last_sequence": "0"},
            },
        )
        self.assertEqual(status.value, 200)
        self.assertEqual(response["result"]["events"], [])

        response, status = handle_acp_jsonrpc(
            manager,
            {
                "jsonrpc": "2.0",
                "id": 13,
                "method": "run.artifacts",
                "params": {"run_id": "run_acp"},
            },
        )
        self.assertEqual(status.value, 200)
        self.assertEqual(response["result"]["artifacts"], [])

        response, status = handle_acp_jsonrpc(
            manager,
            {
                "jsonrpc": "2.0",
                "id": 42,
                "method": "run.permissions",
                "params": {"run_id": "run_acp"},
            },
        )
        self.assertEqual(status.value, 200)
        self.assertEqual(response["result"]["permissions"], [])

        response, status = handle_acp_jsonrpc(
            manager,
            {"jsonrpc": "2.0", "id": 43, "method": "executor.list", "params": {}},
        )
        self.assertEqual(status.value, 200)
        self.assertEqual(response["result"]["executors"], [])

        response, status = handle_acp_jsonrpc(
            manager,
            {
                "jsonrpc": "2.0",
                "id": 14,
                "method": "mission.create",
                "params": {"goal": "mission via acp", "adapter": "fake"},
            },
        )
        self.assertEqual(status.value, 200)
        self.assertEqual(response["result"]["mission_id"], "mission_acp")

        response, status = handle_acp_jsonrpc(
            manager,
            {
                "jsonrpc": "2.0",
                "id": 15,
                "method": "mission.status",
                "params": {"mission_id": "mission_acp"},
            },
        )
        self.assertEqual(status.value, 200)
        self.assertEqual(response["result"]["mission_id"], "mission_acp")

        response, status = handle_acp_jsonrpc(
            manager,
            {
                "jsonrpc": "2.0",
                "id": 16,
                "method": "mission.events",
                "params": {"mission_id": "mission_acp"},
            },
        )
        self.assertEqual(status.value, 200)
        self.assertEqual(response["result"]["events"], [])

        response, status = handle_acp_jsonrpc(
            manager,
            {
                "jsonrpc": "2.0",
                "id": 17,
                "method": "mission.artifacts",
                "params": {"mission_id": "mission_acp"},
            },
        )
        self.assertEqual(status.value, 200)
        self.assertEqual(response["result"]["artifacts"], [{"name": "final_report.md"}])

        response, status = handle_acp_jsonrpc(
            manager,
            {
                "jsonrpc": "2.0",
                "id": 18,
                "method": "mission.cancel",
                "params": {"mission_id": "mission_acp", "reason": "operator"},
            },
        )
        self.assertEqual(status.value, 200)
        self.assertEqual(response["result"]["status"], "cancelled")

        response, status = handle_acp_jsonrpc(
            manager,
            {"jsonrpc": "2.0", "id": 44, "method": "access.policy", "params": {}},
        )
        self.assertEqual(status.value, 200)
        self.assertIn("roles", response["result"])

        response, status = handle_acp_jsonrpc(
            manager,
            {"jsonrpc": "2.0", "id": 45, "method": "cost.status", "params": {}},
        )
        self.assertEqual(status.value, 200)
        self.assertEqual(response["result"]["status"], "ok")

        self.assertEqual(require_string({"name": " alice "}, "name"), "alice")
        with self.assertRaisesRegex(ValueError, "name is required"):
            require_string({"name": " "}, "name")
        self.assertEqual(optional_int("7"), 7)
        self.assertEqual(optional_int("bad"), 0)
        self.assertEqual(jsonrpc_error("x", -1, "boom")["error"]["message"], "boom")

        with self.assertRaisesRegex(ValueError, "goal or message is required"):
            create_a2a_task(manager, {})
        task = create_a2a_task(manager, {"message": "ship it"})
        self.assertEqual(task["task_id"], "mission_acp")
        self.assertEqual(task["status"], "submitted")
        self.assertEqual(task["artifacts"], [{"name": "final_report.md"}])

        with self.assertRaises(KeyError):
            a2a_task_from_mission(manager, "missing")
        self.assertEqual(map_a2a_status("blocked"), "input-required")
        self.assertEqual(map_a2a_status("cancelled"), "canceled")
        self.assertEqual(map_a2a_status("weird"), "unknown")

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
        request = adapter._build_request("run_test", "GET", "/x")
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

    def test_executor_registry_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(Path(tmp))
            try:
                run = store.create_run(RunSpec(adapter="qwen"))
                disabled = ExecutorRegistry(store, ExecutorConfig(strategy="shared"))
                with self.assertRaisesRegex(RuntimeError, "not enabled"):
                    disabled.acquire_qwen(run)
                disabled.release_run(run.run_id, "no lease")

                container = ExecutorRegistry(store, ExecutorConfig(strategy="container"))
                with self.assertRaisesRegex(RuntimeError, "QWEN_CONTAINER_COMMAND"):
                    container.acquire_qwen(run)

                failing = ExecutorRegistry(
                    store,
                    ExecutorConfig(
                        strategy="per_run_process",
                        port_start=0,
                        command_template=(
                            f"{sys.executable} -c 'import sys; sys.exit(3)'"
                        ),
                        startup_timeout_seconds=1,
                    ),
                )
                with self.assertRaisesRegex(RuntimeError, "exited early"):
                    failing.acquire_qwen(run)
                failed_lease = store.get_executor_lease_for_run(run.run_id)
                self.assertIsNotNone(failed_lease)
                self.assertEqual(failed_lease.status, "failed")
                self.assertIn(
                    "executor.failed",
                    [event.type for event in store.events_since(run.run_id)],
                )

                registry = ExecutorRegistry(store, ExecutorConfig(strategy="per_run_process"))
                live_run = store.create_run(RunSpec(adapter="qwen"))
                process = subprocess.Popen([sys.executable, "-c", ""])
                process.wait(timeout=2)
                lease = ExecutorLease(
                    executor_id="exec_done",
                    run_id=live_run.run_id,
                    adapter="qwen",
                    strategy="per_run_process",
                    status="running",
                    base_url="http://127.0.0.1:1",
                    port=1,
                    pid=process.pid,
                )
                store.upsert_executor_lease(lease)
                registry._processes[lease.executor_id] = ManagedProcess(
                    process=process,
                    stdout=open(os.devnull, "w", encoding="utf-8"),
                    stderr=open(os.devnull, "w", encoding="utf-8"),
                )
                reaped = registry.reap_exited()
                self.assertEqual(reaped[0]["status"], "failed")
                self.assertIn(
                    "executor.exited",
                    [event.type for event in store.events_since(live_run.run_id)],
                )

                orphan_run = store.create_run(RunSpec(adapter="qwen"))
                orphan = ExecutorLease(
                    executor_id="exec_orphan",
                    run_id=orphan_run.run_id,
                    adapter="qwen",
                    strategy="per_run_process",
                    status="running",
                )
                store.upsert_executor_lease(orphan)
                ExecutorRegistry(store, ExecutorConfig(strategy="per_run_process"))
                self.assertEqual(store.get_executor_lease("exec_orphan").status, "orphaned")
            finally:
                store.close()

    def test_executor_readiness_retries_reset_with_auth(self) -> None:
        class ReadyResponse:
            status = 200

            def __enter__(self) -> "ReadyResponse":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

        class LiveProcess:
            def poll(self) -> None:
                return None

        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(Path(tmp))
            try:
                registry = ExecutorRegistry(
                    store,
                    ExecutorConfig(
                        strategy="per_run_process",
                        startup_timeout_seconds=1,
                    ),
                )
                lease = ExecutorLease(
                    executor_id="exec_health",
                    run_id="run_health",
                    adapter="qwen",
                    strategy="per_run_process",
                    status="starting",
                    base_url="http://127.0.0.1:4211",
                    token="health-token",
                )
                with (
                    patch(
                        "runtime.cloud_agents_runtime.executors.urllib.request.urlopen",
                        side_effect=[
                            ConnectionResetError(104, "Connection reset by peer"),
                            ReadyResponse(),
                        ],
                    ) as urlopen_mock,
                    patch("runtime.cloud_agents_runtime.executors.time.sleep"),
                ):
                    registry._wait_until_ready(lease, LiveProcess())

                self.assertEqual(urlopen_mock.call_count, 2)
                for call in urlopen_mock.call_args_list:
                    request = call.args[0]
                    self.assertEqual(
                        request.get_header("Authorization"),
                        "Bearer health-token",
                    )
            finally:
                store.close()

    def test_executor_config_and_helpers(self) -> None:
        self.assertEqual(normalize_strategy("per-run"), "per_run_process")
        self.assertEqual(normalize_strategy("docker"), "container")
        self.assertEqual(normalize_strategy("surprise"), "shared")
        self.assertEqual(executor_parse_int("7", 1), 7)
        self.assertEqual(executor_parse_int("bad", 2), 2)
        self.assertEqual(executor_parse_float("1.5", 1.0), 1.5)
        self.assertEqual(executor_parse_float("bad", 2.0), 2.0)
        self.assertEqual(render_command("echo {run_id}", {"run_id": "run_1"}), ["echo", "run_1"])
        with self.assertRaisesRegex(RuntimeError, "empty"):
            render_command("", {})
        port = reserve_ephemeral_port("127.0.0.1")
        self.assertTrue(port_available("127.0.0.1", port))
        lease = ExecutorLease(
            executor_id="exec_env",
            run_id="run_env",
            adapter="qwen",
            strategy="per_run_process",
            base_url="http://127.0.0.1:1234",
            token="secret",
            workspace="/tmp/workspace",
        )
        env = executor_env(lease)
        self.assertEqual(env["QWEN_SERVER_TOKEN"], "secret")
        self.assertEqual(env["QWEN_SERVE_TOKEN"], "secret")
        self.assertEqual(env["QWEN_SERVE_CWD"], "/tmp/workspace")
        container_run = RunState.create(
            RunSpec(
                adapter="qwen",
                metadata={"resource_policy": {"cpus": 0.75, "memory_mb": 384, "pids": 64}},
            ),
            run_id="run_container",
        )
        container_config = ExecutorConfig(
            strategy="container",
            container_image="qwen-code:test",
            container_extra_args="--read-only",
            token="tok",
        )
        command = default_container_command(
            container_config,
            workspace=Path("/tmp/workspace"),
            run=container_run,
            host="127.0.0.1",
            port=4321,
            executor_id="exec/container",
        )
        self.assertEqual(command[:4], ["docker", "run", "--rm", "--name"])
        self.assertIn("qwen-code:test", command)
        self.assertIn("--read-only", command)
        self.assertIn("127.0.0.1:4321:4321", command)
        self.assertIn("QWEN_SERVER_TOKEN", command)
        self.assertIn("QWEN_SERVE_TOKEN", command)
        self.assertFalse(any("tok" in part for part in command))
        self.assertIn("384m", command)
        self.assertEqual(command[command.index("--cpus") + 1], "0.75")
        self.assertEqual(container_metadata(container_config, container_run)["pids"], 64)
        self.assertEqual(safe_container_name("exec/container"), "cloud-agent-exec-container")

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


class MiniInteropStore:
    def list_mission_artifacts(self, mission_id: str) -> list[dict[str, str]]:
        return [{"name": "final_report.md"}]

    def list_artifacts(self, run_id: str) -> list[dict[str, str]]:
        return []

    def events_since(self, run_id: str, last_sequence: int = 0) -> list[object]:
        return []

    def mission_events_since(self, mission_id: str, last_sequence: int = 0) -> list[object]:
        return []


class MiniInteropManager:
    def __init__(self) -> None:
        self.runs: dict[str, RunState] = {}
        self.inputs: list[tuple[str, str]] = []
        self.cancelled: list[tuple[str, str | None]] = []
        self.mission: dict[str, object] | None = None
        self.store = MiniInteropStore()

    def capabilities(self) -> dict[str, object]:
        return {"features": ["interop-test"]}

    def executors(self) -> dict[str, object]:
        return {"executor_registry": {"config": {"strategy": "shared"}}, "executors": []}

    def access_policy(self, headers: object | None = None) -> dict[str, object]:
        return {"roles": [], "headers": bool(headers)}

    def cost_status(self) -> dict[str, object]:
        return {"status": "ok"}

    def create_run(self, spec: RunSpec) -> RunState:
        run = RunState.create(spec, run_id="run_acp")
        self.runs[run.run_id] = run
        return run

    def send_input(self, run_id: str, prompt: str) -> None:
        self.inputs.append((run_id, prompt))

    def get_run(self, run_id: str) -> RunState | None:
        return self.runs.get(run_id)

    def cancel(self, run_id: str, reason: str | None = None) -> None:
        if run_id not in self.runs:
            raise KeyError(run_id)
        self.cancelled.append((run_id, reason))

    def create_mission(self, payload: dict[str, object]) -> dict[str, object]:
        self.mission = {
            "mission_id": "mission_acp",
            "status": "created",
            "goal": payload.get("goal"),
            "tasks": [],
        }
        return self.mission

    def get_mission(self, mission_id: str) -> dict[str, object] | None:
        if mission_id != "mission_acp":
            return None
        return self.mission

    def cancel_mission(self, mission_id: str, reason: str | None = None) -> dict[str, object]:
        if mission_id != "mission_acp" or self.mission is None:
            raise KeyError(mission_id)
        self.mission["status"] = "cancelled"
        self.mission["cancel_reason"] = reason
        return self.mission


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
