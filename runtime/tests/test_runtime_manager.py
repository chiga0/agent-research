from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from runtime.cloud_agents_runtime.adapters.base import RuntimeAdapter
from runtime.cloud_agents_runtime.adapters.fake import FakeAdapter
from runtime.cloud_agents_runtime.cleanup import CleanupPolicy
from runtime.cloud_agents_runtime.manager import RunManager, positive_int
from runtime.cloud_agents_runtime.models import RunSpec
from runtime.cloud_agents_runtime.resources import ResourceLimitConfig
from runtime.cloud_agents_runtime.store import RunStore, utc_now_plus


class RunManagerTest(unittest.TestCase):
    def test_fake_run_completes_and_writes_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RunManager(Path(tmp))
            try:
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
                self.assertEqual(events[1].type, "workspace.prepared")
                self.assertEqual(events[2].type, "resources.resolved")
                self.assertTrue(any(event.type == "message.delta" for event in events))
                self.assertTrue((Path(tmp) / run.run_id / "events.jsonl").exists())
                self.assertTrue((Path(tmp) / run.run_id / "raw_events.jsonl").exists())
                self.assertTrue((Path(tmp) / run.run_id / "final_1.json").exists())
                self.assertTrue((Path(tmp) / run.run_id / "workspace.json").exists())
                self.assertTrue((Path(tmp) / run.run_id / "resources.json").exists())
                self.assertTrue(Path(current.spec.workspace).is_dir())
                self.assertEqual(
                    current.spec.metadata["workspace_allocation"]["strategy"],
                    "empty",
                )
                self.assertEqual(
                    current.spec.metadata["resource_policy"]["memory_mb"],
                    1024,
                )
            finally:
                manager.shutdown()

    def test_unknown_adapter_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RunManager(Path(tmp))
            try:
                with self.assertRaises(ValueError):
                    manager.create_run(RunSpec(prompt="x", adapter="missing"))
            finally:
                manager.shutdown()

    def test_cancel_terminal_run_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RunManager(Path(tmp))
            try:
                run = manager.create_run(RunSpec(prompt="short", adapter="fake"))

                deadline = time.time() + 2
                while time.time() < deadline and manager.get_run(run.run_id).status != "completed":
                    time.sleep(0.02)

                manager.cancel(run.run_id, "too late")
                events = manager.store.events_since(run.run_id)
                self.assertEqual(events[-1].type, "cancel.ignored")
            finally:
                manager.shutdown()

    def test_store_persists_runs_events_and_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = RunManager(root)
            try:
                run = manager.create_run(RunSpec(prompt="persist me", adapter="fake"))
                self.wait_for_status(manager, run.run_id, "completed")
            finally:
                manager.shutdown()

            self.assertTrue((root / "runtime.db").exists())
            self.assertTrue((root / run.run_id / "diagnostics.json").exists())

            restored = RunManager(root)
            try:
                restored_run = restored.get_run(run.run_id)
                self.assertIsNotNone(restored_run)
                self.assertEqual(restored_run.status, "completed")
                events = restored.store.events_since(run.run_id)
                self.assertEqual(events[0].type, "run.created")
                self.assertEqual(events[-1].type, "run.completed")
            finally:
                restored.shutdown()

    def test_replay_cli_rebuilds_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = RunManager(root)
            try:
                run = manager.create_run(RunSpec(prompt="replay me", adapter="fake"))
                self.wait_for_status(manager, run.run_id, "completed")
            finally:
                manager.shutdown()

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
            try:
                run = manager.create_run(RunSpec(adapter="fake"))
                with self.assertRaises(ValueError):
                    manager.resolve_permission(run.run_id, "perm-1", {"decision": "maybe"})
            finally:
                manager.shutdown()

    def test_queue_respects_worker_capacity_and_releases_next_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RunManager(
                Path(tmp),
                adapters={"fake": FakeAdapter(delay_seconds=0.05)},
                worker_capacity=1,
                worker_id="unit-worker",
            )
            try:
                first = manager.create_run(
                    RunSpec(
                        prompt=(
                            "one two three four five six seven eight nine ten "
                            "eleven twelve thirteen fourteen fifteen sixteen"
                        ),
                        adapter="fake",
                    )
                )
                second = manager.create_run(
                    RunSpec(
                        prompt="alpha beta gamma delta epsilon zeta eta theta",
                        adapter="fake",
                    )
                )
                self.wait_for_status_pair(
                    manager,
                    first.run_id,
                    "running",
                    second.run_id,
                    "queued",
                )

                snapshot = manager.queue_status()
                self.assertEqual(snapshot["counts"]["running"], 1)
                self.assertEqual(snapshot["counts"]["queued"], 1)
                self.assertEqual(snapshot["workers"][0]["capacity"], 1)
                self.assertEqual(snapshot["workers"][0]["active_count"], 1)

                self.wait_for_status(manager, first.run_id, "completed")
                self.wait_for_status(manager, second.run_id, "completed")
                second_events = [event.type for event in manager.store.events_since(second.run_id)]
                self.assertIn("run.queued", second_events)
                self.assertIn("lease.claimed", second_events)
            finally:
                manager.shutdown()

    def test_capacity_zero_keeps_run_queued_and_allows_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RunManager(Path(tmp), worker_capacity=0, worker_id="paused-worker")
            try:
                run = manager.create_run(RunSpec(prompt="wait in queue", adapter="fake"))
                self.assertEqual(manager.get_run(run.run_id).status, "queued")
                self.assertEqual(manager.queue_status()["counts"]["queued"], 1)

                manager.send_input(run.run_id, "too early")
                self.assertIn(
                    "input.rejected",
                    [event.type for event in manager.store.events_since(run.run_id)],
                )
                manager.cancel(run.run_id, "operator pause")
                self.assertEqual(manager.get_run(run.run_id).status, "cancelled")
                manager.shutdown()
                manager.shutdown()
            finally:
                manager.shutdown()

    def test_send_input_to_running_run_uses_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RunManager(
                Path(tmp),
                adapters={"fake": FakeAdapter(delay_seconds=0.01)},
                worker_id="input-worker",
            )
            try:
                run = manager.create_run(RunSpec(adapter="fake"))
                self.wait_for_status(manager, run.run_id, "running")
                manager.send_input(run.run_id, "manual prompt")
                self.wait_for_status(manager, run.run_id, "completed")
                events = [event.type for event in manager.store.events_since(run.run_id)]
                self.assertIn("input.accepted", events)
            finally:
                manager.shutdown()

    def test_git_workspace_uses_isolated_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "runtime@example.test"],
                cwd=source,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Runtime Test"],
                cwd=source,
                check=True,
            )
            (source / "README.md").write_text("hello workspace\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
            subprocess.run(
                ["git", "commit", "--no-verify", "-m", "seed"],
                cwd=source,
                check=True,
                capture_output=True,
            )

            artifact_root = root / "artifacts"
            manager = RunManager(artifact_root, worker_capacity=0, worker_id="workspace-worker")
            try:
                run = manager.create_run(
                    RunSpec(
                        prompt="queued workspace",
                        adapter="fake",
                        workspace=str(source),
                    )
                )
                current = manager.get_run(run.run_id)
                self.assertIsNotNone(current)
                workspace = Path(current.spec.workspace)
                self.assertNotEqual(workspace, source)
                self.assertTrue((workspace / "README.md").exists())
                self.assertTrue((workspace / ".git").exists())
                self.assertEqual(
                    current.spec.metadata["workspace_allocation"]["strategy"],
                    "git_worktree",
                )

                manifest = json.loads(
                    (artifact_root / run.run_id / "workspace.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(manifest["source_path"], str(source.resolve()))
                self.assertIn(
                    "workspace.prepared",
                    [event.type for event in manager.store.events_since(run.run_id)],
                )
            finally:
                manager.shutdown()

    def test_remote_repo_is_rejected_before_empty_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RunManager(Path(tmp), worker_capacity=0, worker_id="repo-worker")
            try:
                with self.assertRaisesRegex(ValueError, "supported local directory"):
                    manager.create_run(
                        RunSpec(
                            prompt="do not run empty",
                            adapter="fake",
                            repo="https://example.test/project.git",
                        )
                    )
                self.assertFalse((Path(tmp) / "workspaces").exists())
            finally:
                manager.shutdown()

    def test_resource_policy_is_resolved_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RunManager(
                Path(tmp),
                worker_capacity=0,
                worker_id="resource-worker",
                resource_config=ResourceLimitConfig(
                    default_cpus=1.0,
                    max_cpus=2.0,
                    default_memory_mb=512,
                    max_memory_mb=1024,
                    default_pids=128,
                    max_pids=256,
                    default_timeout_seconds=60,
                    max_timeout_seconds=120,
                ),
            )
            try:
                run = manager.create_run(
                    RunSpec(
                        prompt="queued resource run",
                        adapter="fake",
                        timeout_seconds=90,
                        sandbox={
                            "resources": {
                                "cpus": "1.5",
                                "memory": "768m",
                                "pids": 200,
                            }
                        },
                    )
                )
                current = manager.get_run(run.run_id)
                self.assertIsNotNone(current)
                self.assertEqual(
                    current.spec.sandbox["resources"],
                    {
                        "cpus": 1.5,
                        "memory_mb": 768,
                        "pids": 200,
                        "timeout_seconds": 90,
                    },
                )
                self.assertEqual(current.spec.timeout_seconds, 90)
                self.assertEqual(
                    current.spec.metadata["resource_policy"]["enforcement"][
                        "timeout_seconds"
                    ],
                    "run_manager_watchdog",
                )
                self.assertTrue((Path(tmp) / run.run_id / "resources.json").exists())
                self.assertIn(
                    "resources.resolved",
                    [event.type for event in manager.store.events_since(run.run_id)],
                )
            finally:
                manager.shutdown()

    def test_resource_policy_rejects_limits_above_worker_max(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RunManager(
                Path(tmp),
                worker_capacity=0,
                worker_id="resource-worker",
                resource_config=ResourceLimitConfig(max_memory_mb=512),
            )
            try:
                with self.assertRaisesRegex(ValueError, "memory_mb exceeds"):
                    manager.create_run(
                        RunSpec(
                            prompt="too much memory",
                            adapter="fake",
                            sandbox={"resources": {"memory_mb": 1024}},
                        )
                    )
                self.assertFalse((Path(tmp) / "workspaces").exists())
            finally:
                manager.shutdown()

    def test_timeout_watchdog_cancels_hanging_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = HangingAdapter()
            manager = RunManager(
                Path(tmp),
                adapters={"hang": adapter},
                worker_id="timeout-worker",
                resource_config=ResourceLimitConfig(
                    default_timeout_seconds=1,
                    max_timeout_seconds=1,
                ),
            )
            try:
                run = manager.create_run(RunSpec(prompt="wait", adapter="hang"))
                self.wait_for_status(manager, run.run_id, "cancelled")
                events = [event.type for event in manager.store.events_since(run.run_id)]
                self.assertIn("resources.timeout", events)
                self.assertIn("run.cancelled", events)
                self.assertEqual(adapter.cancelled, [run.run_id])
            finally:
                manager.shutdown()

    def test_cleanup_policy_deletes_terminal_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = RunManager(
                root,
                cleanup_policy=CleanupPolicy(
                    enabled=False,
                    workspace_retention_seconds=0,
                    artifact_retention_seconds=999999,
                ),
            )
            try:
                run = manager.create_run(RunSpec(prompt="cleanup workspace", adapter="fake"))
                self.wait_for_status(manager, run.run_id, "completed")
                current = manager.get_run(run.run_id)
                self.assertIsNotNone(current)
                workspace = Path(current.spec.workspace)
                self.assertTrue(workspace.exists())

                result = manager.cleanup_once()
                self.assertFalse(workspace.exists())
                self.assertTrue((root / run.run_id / "events.jsonl").exists())
                self.assertEqual(
                    result["workspaces_deleted"][0]["run_id"],
                    run.run_id,
                )
                self.assertIn(
                    "cleanup.workspace_deleted",
                    [event.type for event in manager.store.events_since(run.run_id)],
                )
            finally:
                manager.shutdown()

    def test_cleanup_policy_deletes_artifacts_but_keeps_db_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = RunManager(
                root,
                cleanup_policy=CleanupPolicy(
                    enabled=False,
                    workspace_retention_seconds=999999,
                    artifact_retention_seconds=0,
                ),
            )
            try:
                run = manager.create_run(RunSpec(prompt="cleanup artifacts", adapter="fake"))
                self.wait_for_status(manager, run.run_id, "completed")
                run_dir = root / run.run_id
                self.assertTrue(run_dir.exists())

                result = manager.cleanup_once()
                self.assertFalse(run_dir.exists())
                self.assertTrue((root / "runtime.db").exists())
                self.assertEqual(
                    result["artifacts_deleted"][0]["run_id"],
                    run.run_id,
                )
                self.assertIn(
                    "cleanup.artifacts_deleted",
                    [event.type for event in manager.store.events_since(run.run_id)],
                )
                self.assertEqual(manager.store.list_artifacts(run.run_id), [])

                replay = subprocess.run(
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
                replay_state = json.loads(replay.stdout)
                self.assertEqual(replay_state["status"], "completed")
                self.assertEqual(replay_state["last_event"], "cleanup.artifacts_deleted")

                workspace = Path(manager.get_run(run.run_id).spec.workspace)
                manager.cleanup_manager.policy = CleanupPolicy(
                    enabled=False,
                    workspace_retention_seconds=0,
                    artifact_retention_seconds=999999,
                )
                second_result = manager.cleanup_once()
                self.assertFalse(workspace.exists())
                self.assertFalse(run_dir.exists())
                self.assertEqual(
                    second_result["workspaces_deleted"][0]["run_id"],
                    run.run_id,
                )
            finally:
                manager.shutdown()

    def test_cleanup_policy_removes_git_worktree_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "runtime@example.test"],
                cwd=source,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Runtime Test"],
                cwd=source,
                check=True,
            )
            (source / "README.md").write_text("hello cleanup\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
            subprocess.run(
                ["git", "commit", "--no-verify", "-m", "seed"],
                cwd=source,
                check=True,
                capture_output=True,
            )

            artifact_root = root / "artifacts"
            manager = RunManager(
                artifact_root,
                worker_capacity=0,
                worker_id="cleanup-worker",
                cleanup_policy=CleanupPolicy(
                    enabled=False,
                    workspace_retention_seconds=0,
                    artifact_retention_seconds=999999,
                ),
            )
            try:
                run = manager.create_run(
                    RunSpec(
                        prompt="cleanup git worktree",
                        adapter="fake",
                        workspace=str(source),
                    )
                )
                workspace = Path(manager.get_run(run.run_id).spec.workspace)
                self.assertTrue(workspace.exists())
                manager.cancel(run.run_id, "terminal before cleanup")

                result = manager.cleanup_once()
                self.assertFalse(workspace.exists())
                self.assertEqual(
                    result["workspaces_deleted"][0]["strategy"],
                    "git_worktree",
                )
                worktrees = subprocess.run(
                    ["git", "-C", str(source), "worktree", "list", "--porcelain"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertNotIn(str(workspace), worktrees.stdout)
            finally:
                manager.shutdown()

    def test_cleanup_policy_skips_shared_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = root / "shared"
            shared.mkdir()
            manager = RunManager(
                root,
                worker_capacity=0,
                worker_id="cleanup-worker",
                cleanup_policy=CleanupPolicy(
                    enabled=False,
                    workspace_retention_seconds=0,
                    artifact_retention_seconds=999999,
                ),
            )
            try:
                run = manager.create_run(
                    RunSpec(
                        prompt="shared cleanup",
                        adapter="fake",
                        workspace=str(shared),
                        sandbox={"workspace_strategy": "shared"},
                    )
                )
                manager.cancel(run.run_id, "finish queued shared run")
                result = manager.cleanup_once()
                self.assertTrue(shared.exists())
                self.assertEqual(result["workspaces_deleted"], [])
            finally:
                manager.shutdown()

    def test_cleanup_policy_skips_non_terminal_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = RunManager(
                root,
                worker_capacity=0,
                worker_id="cleanup-worker",
                cleanup_policy=CleanupPolicy(
                    enabled=False,
                    workspace_retention_seconds=0,
                    artifact_retention_seconds=0,
                ),
            )
            try:
                run = manager.create_run(RunSpec(prompt="queued cleanup", adapter="fake"))
                current = manager.get_run(run.run_id)
                self.assertIsNotNone(current)
                workspace = Path(current.spec.workspace)

                result = manager.cleanup_once()
                self.assertTrue(workspace.exists())
                self.assertTrue((root / run.run_id / "events.jsonl").exists())
                self.assertEqual(result["workspaces_deleted"], [])
                self.assertEqual(result["artifacts_deleted"], [])
            finally:
                manager.shutdown()

    def test_profile_registry_persists_user_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = RunManager(root, worker_capacity=0, worker_id="profile-worker")
            try:
                profiles = manager.list_profiles()
                self.assertIn("planner", {profile["id"] for profile in profiles})
                custom = manager.create_profile(
                    {
                        "id": "security-reviewer",
                        "display_name": "Security Reviewer",
                        "runtime": {"preferred_adapter": "fake", "model": "audit"},
                        "artifacts": {"required": ["security-findings.md"]},
                    }
                )
                self.assertEqual(custom["version"], 1)
                updated = manager.create_profile(
                    {
                        "id": "security-reviewer",
                        "display_name": "Security Reviewer",
                        "runtime": {"preferred_adapter": "fake", "model": "audit-v2"},
                    }
                )
                self.assertEqual(updated["version"], 2)
                self.assertEqual(
                    manager.get_profile("security-reviewer")["runtime"]["model"],
                    "audit-v2",
                )
                with self.assertRaisesRegex(ValueError, "copy a built-in profile"):
                    manager.create_profile({"id": "coder", "display_name": "Override"})
            finally:
                manager.shutdown()

            restored = RunManager(root, worker_capacity=0, worker_id="profile-worker")
            try:
                restored_profile = restored.get_profile("security-reviewer")
                self.assertIsNotNone(restored_profile)
                self.assertEqual(restored_profile["version"], 2)
            finally:
                restored.shutdown()

    def test_sequential_mission_creates_profile_scoped_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = RunManager(
                root,
                adapters={"fake": FakeAdapter(delay_seconds=0.0)},
                worker_capacity=2,
                worker_id="mission-worker",
            )
            try:
                mission = manager.create_mission(
                    {
                        "goal": "Ship a tiny audited change",
                        "strategy": "sequential",
                        "adapter": "fake",
                    }
                )
                mission_id = mission["mission_id"]
                self.wait_for_mission_status(manager, mission_id, "completed", timeout=5)

                final = manager.get_mission(mission_id)
                self.assertIsNotNone(final)
                self.assertEqual(final["status"], "completed")
                self.assertEqual(len(final["tasks"]), 5)
                self.assertTrue(all(task["status"] == "completed" for task in final["tasks"]))
                self.assertEqual(final["completed_task_count"], 5)

                run_ids = [task["run_id"] for task in final["tasks"]]
                self.assertEqual(len(run_ids), len(set(run_ids)))
                for task in final["tasks"]:
                    run = manager.get_run(task["run_id"])
                    self.assertIsNotNone(run)
                    self.assertEqual(run.spec.metadata["mission_id"], mission_id)
                    self.assertEqual(run.spec.metadata["task_id"], task["task_id"])
                    self.assertIn("profile_snapshot", run.spec.metadata)

                event_names = [
                    event.type
                    for event in manager.store.mission_events_since(mission_id)
                ]
                self.assertIn("task.run_created", event_names)
                self.assertIn("mission.completed", event_names)
                artifacts = {
                    artifact["name"]
                    for artifact in manager.store.list_mission_artifacts(mission_id)
                }
                self.assertIn("mission_manifest.json", artifacts)
                self.assertIn("final_report.md", artifacts)
                self.assertTrue((root / "missions" / mission_id / "events.jsonl").exists())
            finally:
                manager.shutdown()

    def test_custom_mission_dependency_validation_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RunManager(
                Path(tmp),
                adapters={"fake": FakeAdapter(delay_seconds=0.0)},
                worker_capacity=1,
                worker_id="mission-worker",
            )
            try:
                with self.assertRaisesRegex(ValueError, "unknown task"):
                    manager.create_mission(
                        {
                            "goal": "bad graph",
                            "strategy": "custom",
                            "tasks": [
                                {
                                    "id": "a",
                                    "profile": "planner",
                                    "depends_on": ["missing"],
                                }
                            ],
                        }
                    )
                mission = manager.create_mission(
                    {
                        "goal": "bad adapter",
                        "strategy": "custom",
                        "adapter": "missing",
                        "tasks": [{"id": "a", "profile": "planner"}],
                    }
                    )
                self.wait_for_mission_status(manager, mission["mission_id"], "failed")
                failed = manager.get_mission(mission["mission_id"])
                self.assertEqual(failed["tasks"][0]["status"], "failed")

                with self.assertRaisesRegex(ValueError, "unknown profile"):
                    manager.create_mission(
                        {
                            "goal": "missing profile",
                            "strategy": "custom",
                            "tasks": [{"id": "x", "profile": "missing"}],
                        }
                    )
                self.assertIsNone(manager.get_mission("missing"))
            finally:
                manager.shutdown()

    def test_fanout_mission_hands_dependency_artifacts_to_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RunManager(
                Path(tmp),
                adapters={"fake": FakeAdapter(delay_seconds=0.0)},
                worker_capacity=4,
                worker_id="fanout-worker",
            )
            try:
                mission = manager.create_mission(
                    {
                        "goal": "Fan out safely and fan in",
                        "strategy": "fanout",
                        "adapter": "fake",
                    }
                )
                self.wait_for_mission_status(
                    manager,
                    mission["mission_id"],
                    "completed",
                    timeout=5,
                )
                final = manager.get_mission(mission["mission_id"])
                report_task = next(
                    task for task in final["tasks"] if task["task_id"] == "report"
                )
                report_run = manager.get_run(report_task["run_id"])
                refs = report_run.spec.metadata["dependency_artifacts"]
                self.assertEqual({ref["task_id"] for ref in refs}, {"code", "test", "review"})
                self.assertTrue(all(ref["artifacts"] for ref in refs))
            finally:
                manager.shutdown()

    def test_reviewer_gate_blocks_downstream_report_on_high_findings(self) -> None:
        gate_payload = {
            "decision": "block",
            "severity": "high",
            "reason": "security regression risk",
            "findings": [
                {
                    "id": "sec-001",
                    "severity": "high",
                    "category": "security",
                    "message": "host secret exposure risk",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = RunManager(
                root,
                adapters={"gate": GateAdapter(gate_payload)},
                worker_capacity=2,
                worker_id="review-gate-worker",
            )
            try:
                mission = manager.create_mission(review_gate_mission_payload("gate"))
                mission_id = mission["mission_id"]
                self.wait_for_mission_status(manager, mission_id, "blocked", timeout=5)

                blocked = manager.get_mission(mission_id)
                tasks = {task["task_id"]: task for task in blocked["tasks"]}
                self.assertEqual(tasks["plan"]["status"], "completed")
                self.assertEqual(tasks["review"]["status"], "completed")
                self.assertEqual(tasks["report"]["status"], "blocked")
                self.assertIsNone(tasks["report"]["run_id"])
                self.assertTrue(tasks["review"]["result"]["review_gate"]["blocks"])
                self.assertEqual(
                    tasks["review"]["result"]["review_gate"]["effective_decision"],
                    "block",
                )

                names = [
                    event.type
                    for event in manager.store.mission_events_since(mission_id)
                ]
                self.assertIn("review.gate_blocked", names)
                self.assertIn("mission.blocked", names)
                artifacts = {
                    artifact["name"]
                    for artifact in manager.store.list_mission_artifacts(mission_id)
                }
                self.assertIn("review_gate.json", artifacts)
                self.assertIn("final_report.md", artifacts)
            finally:
                manager.shutdown()

    def test_reviewer_gate_warning_allows_downstream_report(self) -> None:
        gate_payload = {
            "decision": "warn",
            "severity": "medium",
            "reason": "minor test coverage note",
            "findings": [
                {
                    "id": "test-001",
                    "severity": "medium",
                    "category": "tests",
                    "message": "consider adding an edge case",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            manager = RunManager(
                Path(tmp),
                adapters={"gate": GateAdapter(gate_payload)},
                worker_capacity=2,
                worker_id="review-gate-worker",
            )
            try:
                mission = manager.create_mission(review_gate_mission_payload("gate"))
                mission_id = mission["mission_id"]
                self.wait_for_mission_status(manager, mission_id, "completed", timeout=5)

                completed = manager.get_mission(mission_id)
                tasks = {task["task_id"]: task for task in completed["tasks"]}
                self.assertEqual(tasks["report"]["status"], "completed")
                self.assertIsNotNone(tasks["report"]["run_id"])
                self.assertFalse(tasks["review"]["result"]["review_gate"]["blocks"])
                names = [
                    event.type
                    for event in manager.store.mission_events_since(mission_id)
                ]
                self.assertIn("review.gate_warned", names)
                self.assertIn("mission.completed", names)

                review_run_id = tasks["review"]["run_id"]
                self.assertIsInstance(review_run_id, str)
                manager.missions.sync_task_from_run(review_run_id, None)
                replayed = [
                    event.type
                    for event in manager.store.mission_events_since(mission_id)
                ]
                self.assertEqual(replayed.count("review.gate_warned"), 1)
                replayed_review = next(
                    task
                    for task in manager.get_mission(mission_id)["tasks"]
                    if task["task_id"] == "review"
                )
                self.assertIn("review_gate", replayed_review["result"])
            finally:
                manager.shutdown()

    def test_missing_reviewer_gate_requires_human_and_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RunManager(
                Path(tmp),
                adapters={"gate": GateAdapter(gate_payload=None)},
                worker_capacity=2,
                worker_id="review-gate-worker",
            )
            try:
                mission = manager.create_mission(review_gate_mission_payload("gate"))
                mission_id = mission["mission_id"]
                self.wait_for_mission_status(manager, mission_id, "blocked", timeout=5)

                blocked = manager.get_mission(mission_id)
                review = next(task for task in blocked["tasks"] if task["task_id"] == "review")
                gate = review["result"]["review_gate"]
                self.assertEqual(gate["effective_decision"], "needs_human")
                self.assertFalse(gate["valid"])
                self.assertIn("missing required", gate["error"])
                names = [
                    event.type
                    for event in manager.store.mission_events_since(mission_id)
                ]
                self.assertIn("review.gate_needs_human", names)
                self.assertIn("mission.blocked", names)
            finally:
                manager.shutdown()

    def test_cancel_mission_cancels_running_and_pending_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = HangingAdapter()
            manager = RunManager(
                Path(tmp),
                adapters={"hang": adapter},
                worker_capacity=1,
                worker_id="cancel-mission-worker",
            )
            try:
                mission = manager.create_mission(
                    {
                        "goal": "cancel me",
                        "strategy": "custom",
                        "adapter": "hang",
                        "tasks": [
                            {"id": "first", "profile": "planner"},
                            {
                                "id": "second",
                                "profile": "reviewer",
                                "depends_on": ["first"],
                            },
                        ],
                    }
                )
                mission_id = mission["mission_id"]
                self.wait_for_task_status(manager, mission_id, "first", "running")
                cancelled = manager.cancel_mission(mission_id, "operator stop")
                self.assertEqual(cancelled["status"], "cancelled")
                self.assertEqual(adapter.cancelled, [cancelled["tasks"][0]["run_id"]])
                self.assertEqual(cancelled["tasks"][1]["status"], "cancelled")

                ignored = manager.cancel_mission(mission_id, "again")
                self.assertEqual(ignored["status"], "cancelled")
                names = [
                    event.type
                    for event in manager.store.mission_events_since(mission_id)
                ]
                self.assertIn("mission.cancel_ignored", names)
                self.assertEqual(names.count("mission.cancelled"), 1)
            finally:
                manager.shutdown()

    def test_expired_lease_is_recovered_to_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(Path(tmp))
            try:
                run = store.create_run(RunSpec(adapter="fake"))
                store.enqueue_run(run.run_id)
                store.register_worker("dead-worker", capacity=1, lease_ttl_seconds=1)
                job = store.claim_next_job("dead-worker", lease_ttl_seconds=30)
                self.assertIsNotNone(job)
                self.assertIsNone(store.claim_next_job("dead-worker", lease_ttl_seconds=30))
                self.assertEqual(store.queued_job_count(), 0)
                worker = store.heartbeat_worker(
                    "dead-worker",
                    capacity=1,
                    lease_ttl_seconds=30,
                )
                self.assertEqual(worker.active_count, 1)
                store._jobs[run.run_id].lease_expires_at = utc_now_plus(-1)
                store._persist_job(store._jobs[run.run_id])

                recovered = store.recover_expired_leases()
                self.assertEqual(recovered, [run.run_id])
                snapshot = store.queue_snapshot()
                self.assertEqual(snapshot["counts"]["queued"], 1)
                self.assertIn(
                    "lease.expired",
                    [event.type for event in store.events_since(run.run_id)],
                )
            finally:
                store.close()

    def test_positive_int_parsing(self) -> None:
        self.assertEqual(positive_int(None, "3", default=1), 3)
        self.assertEqual(positive_int(None, "bad", default=2), 2)
        self.assertEqual(positive_int(-1, None, default=2), 0)

    def wait_for_status(self, manager: RunManager, run_id: str, status: str) -> None:
        deadline = time.time() + 2
        while time.time() < deadline:
            current = manager.get_run(run_id)
            if current and current.status == status:
                return
            time.sleep(0.02)
        self.fail(f"run {run_id} did not reach {status}")

    def wait_for_mission_status(
        self,
        manager: RunManager,
        mission_id: str,
        status: str,
        timeout: float = 2,
    ) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            current = manager.get_mission(mission_id)
            if current and current["status"] == status:
                return
            time.sleep(0.02)
        self.fail(f"mission {mission_id} did not reach {status}")

    def wait_for_task_status(
        self,
        manager: RunManager,
        mission_id: str,
        task_id: str,
        status: str,
        timeout: float = 2,
    ) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            current = manager.get_mission(mission_id)
            if current:
                for task in current["tasks"]:
                    if task["task_id"] == task_id and task["status"] == status:
                        return
            time.sleep(0.02)
        self.fail(f"task {mission_id}/{task_id} did not reach {status}")

    def wait_for_status_pair(
        self,
        manager: RunManager,
        first_run_id: str,
        first_status: str,
        second_run_id: str,
        second_status: str,
    ) -> None:
        deadline = time.time() + 2
        while time.time() < deadline:
            first = manager.get_run(first_run_id)
            second = manager.get_run(second_run_id)
            if first and second and first.status == first_status and second.status == second_status:
                return
            time.sleep(0.02)
        self.fail(
            f"runs did not reach {first_status}/{second_status}: "
            f"{manager.get_run(first_run_id)} / {manager.get_run(second_run_id)}"
        )


def review_gate_mission_payload(adapter: str) -> dict[str, object]:
    return {
        "goal": "exercise reviewer gate",
        "strategy": "custom",
        "adapter": adapter,
        "tasks": [
            {"id": "plan", "profile": "planner", "prompt": "plan"},
            {
                "id": "review",
                "profile": "reviewer",
                "depends_on": ["plan"],
                "prompt": "review",
            },
            {
                "id": "report",
                "profile": "doc-writer",
                "depends_on": ["review"],
                "prompt": "report",
            },
        ],
    }


class GateAdapter(RuntimeAdapter):
    name = "gate"

    def __init__(self, gate_payload: dict[str, object] | None):
        self.gate_payload = gate_payload

    def capabilities(self) -> dict[str, object]:
        return {"name": self.name}

    def start(self, run, store) -> None:
        store.set_adapter_run_id(run.run_id, f"gate_{run.run_id}")
        store.append_event(run.run_id, "run.started", {"adapter": self.name})

    def send_input(self, run, prompt: str, store) -> None:
        prompt_number = store.increment_prompt_count(run.run_id)
        store.append_event(
            run.run_id,
            "input.accepted",
            {"prompt_number": prompt_number},
        )
        profile = run.spec.metadata.get("profile_snapshot")
        if (
            isinstance(profile, dict)
            and profile.get("id") == "reviewer"
            and self.gate_payload is not None
        ):
            store.write_json(run.run_id, "review_gate.json", self.gate_payload)
        store.write_json(
            run.run_id,
            f"final_{prompt_number}.json",
            {"prompt_number": prompt_number, "text": prompt},
        )
        store.append_event(run.run_id, "run.completed", {"prompt_number": prompt_number})

    def cancel(self, run, reason: str | None, store) -> None:
        store.append_event(run.run_id, "run.cancelled", {"reason": reason or "cancelled"})


class HangingAdapter(RuntimeAdapter):
    name = "hang"

    def __init__(self) -> None:
        self.cancelled: list[str] = []

    def capabilities(self) -> dict[str, object]:
        return {"name": self.name}

    def start(self, run, store) -> None:
        store.set_adapter_run_id(run.run_id, f"hang_{run.run_id}")
        store.append_event(run.run_id, "run.started", {"adapter": self.name})

    def send_input(self, run, prompt: str, store) -> None:
        prompt_number = store.increment_prompt_count(run.run_id)
        store.append_event(
            run.run_id,
            "input.accepted",
            {"prompt_number": prompt_number},
        )

    def cancel(self, run, reason: str | None, store) -> None:
        self.cancelled.append(run.run_id)
        store.append_event(
            run.run_id,
            "run.cancelled",
            {"adapter": self.name, "reason": reason or "cancelled"},
        )


if __name__ == "__main__":
    unittest.main()
