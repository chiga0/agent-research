from __future__ import annotations

import argparse
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts.validate_qwen_mission import main, validate_single_run


class ValidateQwenMissionTest(unittest.TestCase):
    def test_main_single_run_acceptance_does_not_create_mission_by_default(self) -> None:
        client = CompletedRunClient()
        with patch("scripts.validate_qwen_mission.Client", return_value=client):
            self.assertEqual(
                main(
                    [
                        "--base-url",
                        "http://runtime.test",
                        "--token",
                        "token",
                        "--timeout",
                        "60",
                        "--validate-single-run",
                        "--expect-executor-strategy",
                        "per_run_process",
                    ]
                ),
                0,
            )

        self.assertIn(("POST", "/runs", client.run_payload), client.calls)
        self.assertNotIn("POST /missions", client.method_paths())

    def test_main_can_create_lightweight_custom_mission(self) -> None:
        client = CompletedMissionClient()
        with patch("scripts.validate_qwen_mission.Client", return_value=client):
            self.assertEqual(
                main(
                    [
                        "--base-url",
                        "http://runtime.test",
                        "--token",
                        "token",
                        "--timeout",
                        "60",
                        "--validate-mission",
                        "--mission-task-count",
                        "2",
                    ]
                ),
                0,
            )

        mission_payload = client.mission_payload
        self.assertIsNotNone(mission_payload)
        assert mission_payload is not None
        self.assertEqual(mission_payload["strategy"], "custom")
        self.assertEqual(mission_payload["adapter"], "qwen")
        tasks = mission_payload["tasks"]
        self.assertEqual([task["id"] for task in tasks], ["inspect", "report"])
        self.assertEqual(tasks[0]["depends_on"], [])
        self.assertEqual(tasks[1]["depends_on"], ["inspect"])
        self.assertEqual(
            mission_payload["metadata"],
            {
                "acceptance": "qwen",
                "mission_profile": "lightweight",
                "mission_task_count": 2,
            },
        )

    def test_main_rejects_invalid_mission_task_count(self) -> None:
        with patch("scripts.validate_qwen_mission.Client") as client_class:
            self.assertEqual(
                main(
                    [
                        "--base-url",
                        "http://runtime.test",
                        "--token",
                        "token",
                        "--validate-mission",
                        "--mission-task-count",
                        "0",
                    ]
                ),
                1,
            )

        client_class.assert_not_called()

    def test_single_run_timeout_cancels_run_and_prints_diagnostics(self) -> None:
        client = RecordingClient()
        args = argparse.Namespace(
            timeout=10.0,
            expect_executor_strategy=None,
            auto_approve_permissions=False,
        )

        with (
            patch(
                "scripts.validate_qwen_mission.now",
                side_effect=[0.0, 0.0, 11.0],
            ),
            patch("scripts.validate_qwen_mission.sleep_for"),
        ):
            self.assertFalse(validate_single_run(client, args, deadline=10.0))

        self.assertIn(
            (
                "POST",
                "/runs/run-timeout/cancel",
                {"reason": "qwen acceptance timeout"},
            ),
            client.calls,
        )
        self.assertIn(("GET", "/queue", None), client.calls)
        self.assertIn(("GET", "/executors", None), client.calls)
        self.assertIn(("GET", "/runs/run-timeout/events.json", None), client.calls)
        self.assertIn(("GET", "/runs/run-timeout/executor", None), client.calls)
        self.assertIn(("GET", "/runs/run-timeout/artifacts", None), client.calls)

    def test_single_run_failure_prints_redacted_artifact_diagnostics(self) -> None:
        client = FailedRunWithArtifactsClient()
        args = argparse.Namespace(
            timeout=10.0,
            expect_executor_strategy="container",
            auto_approve_permissions=False,
        )
        output = io.StringIO()

        with (
            patch("scripts.validate_qwen_mission.now", side_effect=[0.0, 0.0]),
            redirect_stdout(output),
        ):
            self.assertFalse(validate_single_run(client, args, deadline=10.0))

        self.assertIn(("GET", "/runs/run-failed/artifacts", None), client.calls)
        self.assertIn("/runs/run-failed/artifacts/executor.stderr.log", client.text_paths)
        self.assertIn("/runs/run-failed/artifacts/executor.stdout.log", client.text_paths)
        text = output.getvalue()
        self.assertIn("--- executor.stderr.log tail ---", text)
        self.assertIn("boot failed", text)
        self.assertIn("QWEN_SERVER_TOKEN=<redacted>", text)
        self.assertNotIn("super-secret-token", text)

    def test_single_run_can_auto_approve_pending_permissions(self) -> None:
        client = PermissionRunClient()
        args = argparse.Namespace(
            timeout=30.0,
            expect_executor_strategy=None,
            auto_approve_permissions=True,
        )

        with (
            patch("scripts.validate_qwen_mission.now", side_effect=[0.0, 0.0, 1.0]),
            patch("scripts.validate_qwen_mission.sleep_for"),
        ):
            self.assertTrue(validate_single_run(client, args, deadline=30.0))

        self.assertEqual(
            [
                call
                for call in client.calls
                if call[0] == "POST" and "/permissions/" in call[1]
            ],
            [
                (
                    "POST",
                    "/runs/run-permission/permissions/perm-1",
                    {
                        "decision": "approve",
                        "reason": "auto-approved by qwen smoke validation",
                    },
                )
            ],
        )


class CompletedRunClient:
    def __init__(self) -> None:
        self.run_payload: dict[str, object] | None = None
        self.calls: list[tuple[str, str, dict[str, object] | None]] = []

    def method_paths(self) -> list[str]:
        return [f"{method} {path}" for method, path, _payload in self.calls]

    def post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(("POST", path, payload))
        if path == "/runs":
            self.run_payload = payload
            return {"run_id": "run-ok", "status": "queued"}
        raise AssertionError(f"unexpected post: {path}")

    def get(self, path: str) -> dict[str, object]:
        self.calls.append(("GET", path, None))
        if path == "/health":
            return {"ok": True}
        if path == "/capabilities":
            return {
                "adapters": {"qwen": {}, "fake": {}},
                "executor_registry": {"config": {"strategy": "per_run_process"}},
            }
        if path == "/queue":
            return {"counts": {"completed": 1}}
        if path == "/executors":
            return {"executor_registry": {"config": {"strategy": "per_run_process"}}}
        if path == "/access/policy":
            return {"mode": "single-tenant-rbac-foundation"}
        if path == "/cost/status":
            return {"status": "unconfigured"}
        if path == "/runs/run-ok":
            return {"run_id": "run-ok", "status": "completed"}
        if path == "/runs/run-ok/events.json":
            return {"events": [{"type": "run.completed"}]}
        if path == "/runs/run-ok/artifacts":
            return {
                "artifacts": [
                    {"name": "events.jsonl"},
                    {"name": "raw_events.jsonl"},
                    {"name": "diagnostics.json"},
                    {"name": "cost.json"},
                ]
            }
        if path == "/runs/run-ok/executor":
            return {"executor": {"strategy": "per_run_process"}}
        raise AssertionError(f"unexpected get: {path}")


class CompletedMissionClient:
    def __init__(self) -> None:
        self.mission_payload: dict[str, object] | None = None
        self.calls: list[tuple[str, str, dict[str, object] | None]] = []
        self._mission_poll_count = 0

    def post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(("POST", path, payload))
        if path == "/missions":
            self.mission_payload = payload
            return {
                "mission_id": "mission-ok",
                "status": "running",
                "task_count": 2,
                "completed_task_count": 0,
            }
        raise AssertionError(f"unexpected post: {path}")

    def get(self, path: str) -> dict[str, object]:
        self.calls.append(("GET", path, None))
        if path == "/health":
            return {"ok": True}
        if path == "/capabilities":
            return {
                "adapters": {"qwen": {}, "fake": {}},
                "executor_registry": {"config": {"strategy": "per_run_process"}},
            }
        if path == "/queue":
            return {"counts": {"completed": 1}}
        if path == "/executors":
            return {"executor_registry": {"config": {"strategy": "per_run_process"}}}
        if path == "/access/policy":
            return {"mode": "single-tenant-rbac-foundation"}
        if path == "/cost/status":
            return {"status": "unconfigured"}
        if path == "/missions/mission-ok":
            self._mission_poll_count += 1
            return {
                "mission_id": "mission-ok",
                "status": "completed",
                "task_count": 2,
                "completed_task_count": 2,
            }
        if path == "/missions/mission-ok/events.json":
            return {"events": [{"type": "mission.completed"}]}
        if path == "/missions/mission-ok/artifacts":
            return {"artifacts": [{"name": "final_report.md"}]}
        raise AssertionError(f"unexpected get: {path}")


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object] | None]] = []

    def post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(("POST", path, payload))
        if path == "/runs":
            return {"run_id": "run-timeout", "status": "queued"}
        if path == "/runs/run-timeout/cancel":
            return {"cancelled": True}
        raise AssertionError(f"unexpected post: {path}")

    def get(self, path: str) -> dict[str, object]:
        self.calls.append(("GET", path, None))
        if path == "/runs/run-timeout":
            return {"run_id": "run-timeout", "status": "running"}
        if path == "/queue":
            return {"counts": {"running": 1}}
        if path == "/executors":
            return {"executors": []}
        if path == "/runs/run-timeout/events.json":
            return {"events": [{"type": "run.started"}]}
        if path == "/runs/run-timeout/executor":
            return {"executor": {"strategy": "per_run_process"}}
        if path == "/runs/run-timeout/artifacts":
            return {"artifacts": []}
        raise AssertionError(f"unexpected get: {path}")


class FailedRunWithArtifactsClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object] | None]] = []
        self.text_paths: list[str] = []

    def post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(("POST", path, payload))
        if path == "/runs":
            return {"run_id": "run-failed", "status": "queued"}
        raise AssertionError(f"unexpected post: {path}")

    def get(self, path: str) -> dict[str, object]:
        self.calls.append(("GET", path, None))
        if path == "/runs/run-failed":
            return {"run_id": "run-failed", "status": "failed"}
        if path == "/queue":
            return {"counts": {"failed": 1}}
        if path == "/executors":
            return {
                "executors": [
                    {
                        "command": ["docker", "run", "-e", "QWEN_SERVER_TOKEN=super-secret-token"],
                        "token": "configured",
                    }
                ]
            }
        if path == "/runs/run-failed/events.json":
            return {"events": [{"type": "executor.failed"}, {"type": "run.failed"}]}
        if path == "/runs/run-failed/executor":
            return {
                "executor": {
                    "strategy": "container",
                    "last_error": "executor exited early with code 1",
                    "token": "configured",
                }
            }
        if path == "/runs/run-failed/artifacts":
            return {
                "artifacts": [
                    {"name": "executor.stderr.log"},
                    {"name": "executor.stdout.log"},
                    {"name": "executor.json"},
                    {"name": "diagnostics.json"},
                ]
            }
        raise AssertionError(f"unexpected get: {path}")

    def get_text(self, path: str) -> str:
        self.text_paths.append(path)
        if path.endswith("/executor.stderr.log"):
            return "boot failed QWEN_SERVER_TOKEN=super-secret-token"
        if path.endswith("/executor.stdout.log"):
            return ""
        if path.endswith("/executor.json"):
            return '{"command":["docker","run","-e","QWEN_SERVER_TOKEN=super-secret-token"]}'
        if path.endswith("/diagnostics.json"):
            return '{"token":"super-secret-token"}'
        raise AssertionError(f"unexpected get_text: {path}")


class PermissionRunClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object] | None]] = []
        self._status_count = 0

    def post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(("POST", path, payload))
        if path == "/runs":
            return {"run_id": "run-permission", "status": "queued"}
        if path == "/runs/run-permission/permissions/perm-1":
            return {"accepted": True}
        raise AssertionError(f"unexpected post: {path}")

    def get(self, path: str) -> dict[str, object]:
        self.calls.append(("GET", path, None))
        if path == "/runs/run-permission":
            self._status_count += 1
            status = "completed" if self._status_count > 1 else "running"
            return {"run_id": "run-permission", "status": status}
        if path == "/runs/run-permission/events.json":
            return {
                "events": [
                    {
                        "type": "permission.requested",
                        "data": {"permission_id": "perm-1"},
                    },
                    {"type": "run.completed"},
                ]
            }
        if path == "/runs/run-permission/artifacts":
            return {
                "artifacts": [
                    {"name": "events.jsonl"},
                    {"name": "raw_events.jsonl"},
                    {"name": "diagnostics.json"},
                    {"name": "cost.json"},
                ]
            }
        raise AssertionError(f"unexpected get: {path}")
