from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from runtime.cloud_agents_runtime.manager import RunManager
from runtime.cloud_agents_runtime.models import RunSpec


class RunManagerTest(unittest.TestCase):
    def test_fake_run_completes_and_writes_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RunManager(Path(tmp))
            run = manager.create_run(RunSpec(prompt="hello runtime", adapter="fake"))

            deadline = time.time() + 2
            while time.time() < deadline:
                current = manager.get_run(run.run_id)
                if current and current.status == "completed":
                    break
                time.sleep(0.02)

            current = manager.get_run(run.run_id)
            self.assertIsNotNone(current)
            self.assertEqual(current.status, "completed")
            events = manager.store.events_since(run.run_id)
            self.assertEqual(events[0].type, "run.created")
            self.assertTrue(any(event.type == "message.delta" for event in events))
            self.assertTrue((Path(tmp) / run.run_id / "events.jsonl").exists())
            self.assertTrue((Path(tmp) / run.run_id / "raw_events.jsonl").exists())
            self.assertTrue((Path(tmp) / run.run_id / "final_1.json").exists())

    def test_unknown_adapter_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RunManager(Path(tmp))
            with self.assertRaises(ValueError):
                manager.create_run(RunSpec(prompt="x", adapter="missing"))

    def test_cancel_terminal_run_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RunManager(Path(tmp))
            run = manager.create_run(RunSpec(prompt="short", adapter="fake"))

            deadline = time.time() + 2
            while time.time() < deadline and manager.get_run(run.run_id).status != "completed":
                time.sleep(0.02)

            manager.cancel(run.run_id, "too late")
            events = manager.store.events_since(run.run_id)
            self.assertEqual(events[-1].type, "cancel.ignored")

    def test_store_persists_runs_events_and_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = RunManager(root)
            run = manager.create_run(RunSpec(prompt="persist me", adapter="fake"))
            self.wait_for_status(manager, run.run_id, "completed")

            self.assertTrue((root / "runtime.db").exists())
            self.assertTrue((root / run.run_id / "diagnostics.json").exists())

            restored = RunManager(root)
            restored_run = restored.get_run(run.run_id)
            self.assertIsNotNone(restored_run)
            self.assertEqual(restored_run.status, "completed")
            events = restored.store.events_since(run.run_id)
            self.assertEqual(events[0].type, "run.created")
            self.assertEqual(events[-1].type, "run.completed")

    def test_replay_cli_rebuilds_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = RunManager(root)
            run = manager.create_run(RunSpec(prompt="replay me", adapter="fake"))
            self.wait_for_status(manager, run.run_id, "completed")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/replay_run.py",
                    "--artifact-root",
                    str(root),
                    "--run-id",
                    run.run_id,
                    "--format",
                    "state",
                ],
                check=True,
                cwd=Path(__file__).resolve().parents[2],
                capture_output=True,
                text=True,
            )
            state = json.loads(result.stdout)
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["last_event"], "run.completed")

    def test_permission_resolution_rejects_bad_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RunManager(Path(tmp))
            run = manager.create_run(RunSpec(adapter="fake"))
            with self.assertRaises(ValueError):
                manager.resolve_permission(run.run_id, "perm-1", {"decision": "maybe"})

    def wait_for_status(self, manager: RunManager, run_id: str, status: str) -> None:
        deadline = time.time() + 2
        while time.time() < deadline:
            current = manager.get_run(run_id)
            if current and current.status == status:
                return
            time.sleep(0.02)
        self.fail(f"run {run_id} did not reach {status}")


if __name__ == "__main__":
    unittest.main()
