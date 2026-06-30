from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()

