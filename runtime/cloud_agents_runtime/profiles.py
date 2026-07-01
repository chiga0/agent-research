from __future__ import annotations

from typing import Any

from .models import AgentProfile


BUILTIN_PROFILE_PAYLOADS: list[dict[str, Any]] = [
    {
        "id": "planner",
        "display_name": "Planner",
        "description": "Break a mission into auditable tasks and decisions.",
        "runtime": {"preferred_adapter": "qwen", "model": "default"},
        "tools": {
            "allow": ["read_file", "grep", "list_files", "web_fetch"],
            "deny": ["write_file", "shell_write", "git_push", "deploy_prod"],
        },
        "approval": {"mode": "ask", "required_for": ["network"]},
        "limits": {"max_turns": 20, "timeout_seconds": 1800, "max_parallel_instances": 1},
        "workspace": {"strategy": "shared_readonly", "write_scope": "none"},
        "artifacts": {"required": ["plan.md"]},
    },
    {
        "id": "coder",
        "display_name": "Coder",
        "description": "Implement changes in an isolated workspace.",
        "runtime": {"preferred_adapter": "qwen", "model": "default"},
        "tools": {
            "allow": ["read_file", "write_file", "shell", "git_diff"],
            "deny": ["secrets_read", "deploy_prod", "git_push"],
        },
        "approval": {"mode": "ask", "required_for": ["shell", "network", "git_push"]},
        "limits": {"max_turns": 40, "timeout_seconds": 3600, "max_parallel_instances": 2},
        "workspace": {"strategy": "git_worktree", "write_scope": "isolated_branch"},
        "artifacts": {"required": ["diff.patch", "implementation-notes.md"]},
    },
    {
        "id": "tester",
        "display_name": "Tester",
        "description": "Validate behavior and produce reproducible test evidence.",
        "runtime": {"preferred_adapter": "qwen", "model": "default"},
        "tools": {
            "allow": ["read_file", "shell", "write_temp", "test_command"],
            "deny": ["source_write", "secrets_read", "deploy_prod"],
        },
        "approval": {"mode": "ask", "required_for": ["network", "long_running_shell"]},
        "limits": {"max_turns": 30, "timeout_seconds": 3600, "max_parallel_instances": 2},
        "workspace": {"strategy": "git_worktree", "write_scope": "temporary"},
        "artifacts": {"required": ["test-report.md"]},
    },
    {
        "id": "reviewer",
        "display_name": "Reviewer",
        "description": "Review outputs, diffs, and risks before merge.",
        "runtime": {"preferred_adapter": "qwen", "model": "default"},
        "tools": {
            "allow": ["read_file", "grep", "git_diff", "lightweight_shell"],
            "deny": ["write_file", "deploy_prod", "git_push"],
        },
        "approval": {"mode": "ask", "required_for": ["shell", "network"]},
        "limits": {"max_turns": 25, "timeout_seconds": 2400, "max_parallel_instances": 2},
        "workspace": {"strategy": "shared_readonly", "write_scope": "none"},
        "artifacts": {
            "required": ["review-findings.md", "review_gate.json"],
            "gate": {"type": "reviewer", "artifact": "review_gate.json"},
        },
    },
    {
        "id": "doc-writer",
        "display_name": "Doc Writer",
        "description": "Summarize mission results and produce durable documentation.",
        "runtime": {"preferred_adapter": "qwen", "model": "default"},
        "tools": {
            "allow": ["read_file", "write_docs", "grep"],
            "deny": ["source_write", "deploy_prod", "git_push"],
        },
        "approval": {"mode": "ask", "required_for": ["source_write", "network"]},
        "limits": {"max_turns": 20, "timeout_seconds": 1800, "max_parallel_instances": 1},
        "workspace": {"strategy": "git_worktree", "write_scope": "docs_only"},
        "artifacts": {"required": ["final-report.md", "docs-changes.md"]},
    },
    {
        "id": "release-gate",
        "display_name": "Release Gate",
        "description": "Approve, warn, or block merge and deployment readiness.",
        "runtime": {"preferred_adapter": "qwen", "model": "default"},
        "tools": {
            "allow": ["read_file", "grep", "git_diff", "lightweight_shell"],
            "deny": ["write_file", "source_write", "git_push", "deploy_prod"],
        },
        "approval": {"mode": "ask", "required_for": ["shell", "network"]},
        "limits": {"max_turns": 20, "timeout_seconds": 1800, "max_parallel_instances": 1},
        "workspace": {"strategy": "shared_readonly", "write_scope": "none"},
        "artifacts": {
            "required": ["release_gate.json"],
            "gate": {"type": "merge_deploy", "artifact": "release_gate.json"},
        },
    },
]


def builtin_profiles() -> dict[tuple[str, int], AgentProfile]:
    profiles: dict[tuple[str, int], AgentProfile] = {}
    for payload in BUILTIN_PROFILE_PAYLOADS:
        profile = AgentProfile.from_payload(payload, version=1, source="system")
        profiles[(profile.id, profile.version)] = profile
    return profiles


def latest_profiles(profiles: list[AgentProfile]) -> list[AgentProfile]:
    latest: dict[str, AgentProfile] = {}
    for profile in profiles:
        current = latest.get(profile.id)
        if current is None or profile.version > current.version:
            latest[profile.id] = profile
    return sorted(latest.values(), key=lambda profile: (profile.source, profile.id))
