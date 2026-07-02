from __future__ import annotations

import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path
from typing import Any

from runtime.cloud_agents_runtime.adapters import FakeAdapter, RuntimeAdapter
from runtime.cloud_agents_runtime.models import RunState
from runtime.cloud_agents_runtime.store import RunStore
from runtime.cloud_agents_runtime.worker import (
    RemoteWorkerConfig,
    RemoteWorkerDaemon,
    main as worker_main,
)
from runtime.tests.test_runtime_server import request_json, running_runtime


class RemoteWorkerDaemonTest(unittest.TestCase):
    def test_remote_worker_daemon_once_executes_fake_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            control_root = Path(tmp) / "control"
            worker_root = Path(tmp) / "worker"
            with running_runtime(
                artifact_root=control_root,
                token="secret",
                worker_capacity=0,
            ) as base_url:
                headers = {"authorization": "Bearer secret"}
                run = request_json(
                    f"{base_url}/runs",
                    method="POST",
                    payload={"prompt": "hello from remote daemon", "adapter": "fake"},
                    headers=headers,
                )
                worker = RemoteWorkerDaemon(
                    RemoteWorkerConfig(
                        control_url=base_url,
                        token="secret",
                        worker_id="vps/a",
                        capacity=1,
                        lease_ttl_seconds=30,
                        heartbeat_interval_seconds=0.05,
                        run_wait_timeout_seconds=2,
                        artifact_root=worker_root,
                        metadata={
                            "region": "test-region",
                            "capabilities": {"features": ["custom-feature"]},
                        },
                    )
                )

                self.assertTrue(worker.run_once(wait=True))

                deadline = time.time() + 2
                current: dict[str, object] = {}
                while time.time() < deadline:
                    current = request_json(
                        f"{base_url}/runs/{run['run_id']}",
                        headers=headers,
                    )
                    if current["status"] == "completed":
                        break
                    time.sleep(0.02)

                self.assertEqual(current["status"], "completed")
                events = request_json(
                    f"{base_url}/runs/{run['run_id']}/events.json",
                    headers=headers,
                )
                event_types = [event["type"] for event in events["events"]]
                self.assertIn("adapter.run_id", event_types)
                self.assertIn("run.started", event_types)
                self.assertIn("run.completed", event_types)
                artifacts = request_json(
                    f"{base_url}/runs/{run['run_id']}/artifacts",
                    headers=headers,
                )
                artifact_names = {artifact["name"] for artifact in artifacts["artifacts"]}
                self.assertIn("final_1.json", artifact_names)
                self.assertIn("raw_events.jsonl", artifact_names)
                self.assertTrue((worker_root / run["run_id"] / "final_1.json").exists())
                raw_events = worker_root / run["run_id"] / "raw_events.jsonl"
                self.assertIn('"index": 1', raw_events.read_text(encoding="utf-8"))
                worker_path = urllib.parse.quote("vps/a", safe="")
                worker_state = request_json(f"{base_url}/workers/{worker_path}", headers=headers)
                self.assertEqual(worker_state["worker"]["metadata"]["region"], "test-region")
                self.assertIn(
                    "fake",
                    worker_state["worker"]["metadata"]["capabilities"]["adapters"],
                )
                self.assertIn(
                    "custom-feature",
                    worker_state["worker"]["metadata"]["capabilities"]["features"],
                )
                self.assertIn(
                    "claim",
                    worker_state["worker"]["metadata"]["capabilities"]["features"],
                )

    def test_remote_worker_cli_help(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            worker_main(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_remote_worker_applies_control_plane_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            control_root = Path(tmp) / "control"
            with running_runtime(
                artifact_root=control_root,
                token="secret",
                worker_capacity=0,
            ) as base_url:
                headers = {"authorization": "Bearer secret"}
                prompt = " ".join(f"word-{index}" for index in range(220))
                run = request_json(
                    f"{base_url}/runs",
                    method="POST",
                    payload={"prompt": prompt, "adapter": "fake", "timeout_seconds": 5},
                    headers=headers,
                )
                worker = RemoteWorkerDaemon(
                    RemoteWorkerConfig(
                        control_url=base_url,
                        token="secret",
                        worker_id="vps-cancel",
                        capacity=1,
                        heartbeat_interval_seconds=0.05,
                        run_wait_timeout_seconds=5,
                    ),
                    adapters={"fake": FakeAdapter(delay_seconds=0.05)},
                )
                self.assertTrue(worker.run_once(wait=False))
                wait_for_status(base_url, run["run_id"], "running", headers)
                request_json(
                    f"{base_url}/runs/{run['run_id']}/cancel",
                    method="POST",
                    payload={"reason": "operator cancel"},
                    headers=headers,
                )
                cancelled = wait_for_status(base_url, run["run_id"], "cancelled", headers)
                self.assertEqual(cancelled["status"], "cancelled")
                events = request_json(
                    f"{base_url}/runs/{run['run_id']}/events.json",
                    headers=headers,
                )
                event_types = [event["type"] for event in events["events"]]
                self.assertIn("run.cancel_requested", event_types)
                self.assertIn("run.cancelled", event_types)

    def test_remote_worker_applies_permission_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            control_root = Path(tmp) / "control"
            with running_runtime(
                artifact_root=control_root,
                token="secret",
                worker_capacity=0,
            ) as base_url:
                headers = {"authorization": "Bearer secret"}
                run = request_json(
                    f"{base_url}/runs",
                    method="POST",
                    payload={"prompt": "needs approval", "adapter": "fake"},
                    headers=headers,
                )
                worker = RemoteWorkerDaemon(
                    RemoteWorkerConfig(
                        control_url=base_url,
                        token="secret",
                        worker_id="vps-permission",
                        capacity=1,
                        heartbeat_interval_seconds=0.05,
                        run_wait_timeout_seconds=5,
                    ),
                    adapters={"fake": PermissionAdapter()},
                )
                self.assertTrue(worker.run_once(wait=False))
                wait_for_event(base_url, run["run_id"], "permission.requested", headers)
                request_json(
                    f"{base_url}/runs/{run['run_id']}/permissions/perm-remote",
                    method="POST",
                    payload={
                        "decision": "approve",
                        "decided_by": "test",
                        "reason": "remote permission test",
                    },
                    headers=headers,
                )
                completed = wait_for_status(base_url, run["run_id"], "completed", headers)
                self.assertEqual(completed["status"], "completed")
                events = request_json(
                    f"{base_url}/runs/{run['run_id']}/events.json",
                    headers=headers,
                )
                event_types = [event["type"] for event in events["events"]]
                self.assertIn("permission.resolve_requested", event_types)
                self.assertIn("permission.resolved", event_types)


class PermissionAdapter(RuntimeAdapter):
    name = "fake"

    def capabilities(self) -> dict[str, Any]:
        return {"name": self.name, "features": ["permission"]}

    def start(self, run: RunState, store: RunStore) -> None:
        store.append_event(run.run_id, "run.started", {"adapter": self.name})

    def send_input(self, run: RunState, prompt: str, store: RunStore) -> None:
        store.increment_prompt_count(run.run_id)
        store.append_event(
            run.run_id,
            "permission.requested",
            {
                "permission_id": "perm-remote",
                "prompt": "Approve remote worker action?",
                "options": [{"id": "approve"}, {"id": "deny"}],
            },
        )

    def cancel(self, run: RunState, reason: str | None, store: RunStore) -> None:
        store.append_event(run.run_id, "run.cancelled", {"reason": reason or "cancelled"})

    def resolve_permission(
        self,
        run: RunState,
        permission_id: str,
        payload: dict[str, Any],
        store: RunStore,
    ) -> None:
        super().resolve_permission(run, permission_id, payload, store)
        store.append_event(run.run_id, "run.completed", {"permission_id": permission_id})


def wait_for_status(
    base_url: str,
    run_id: str,
    status: str,
    headers: dict[str, str],
) -> dict[str, object]:
    deadline = time.time() + 5
    current: dict[str, object] = {}
    while time.time() < deadline:
        current = request_json(f"{base_url}/runs/{run_id}", headers=headers)
        if current.get("status") == status:
            return current
        time.sleep(0.03)
    return current


def wait_for_event(
    base_url: str,
    run_id: str,
    event_type: str,
    headers: dict[str, str],
) -> None:
    deadline = time.time() + 5
    while time.time() < deadline:
        events = request_json(f"{base_url}/runs/{run_id}/events.json", headers=headers)
        if event_type in {event["type"] for event in events["events"]}:
            return
        time.sleep(0.03)
    raise AssertionError(f"event not observed: {event_type}")


if __name__ == "__main__":
    unittest.main()
