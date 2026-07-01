from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .models import RunSpec


@dataclass(frozen=True)
class WorkspaceAllocation:
    run_id: str
    strategy: str
    path: str
    requested_workspace: str | None = None
    requested_repo: str | None = None
    source_path: str | None = None
    git_head: str | None = None
    isolated: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorkspaceAllocator:
    def __init__(self, artifact_root: Path):
        self.workspace_root = artifact_root / "workspaces"

    def prepare(self, run_id: str, spec: RunSpec) -> WorkspaceAllocation:
        requested_workspace = spec.workspace
        requested_repo = spec.repo
        qwen_bound_workspace = os.environ.get("QWEN_SERVE_CWD")
        if (
            spec.adapter == "qwen"
            and not requested_workspace
            and not requested_repo
            and qwen_bound_workspace
        ):
            allocation = WorkspaceAllocation(
                run_id=run_id,
                strategy="qwen_serve_shared",
                path=str(Path(qwen_bound_workspace).expanduser().resolve()),
                requested_workspace=qwen_bound_workspace,
                requested_repo=requested_repo,
                source_path=str(Path(qwen_bound_workspace).expanduser().resolve()),
                isolated=False,
            )
            apply_allocation(spec, allocation)
            return allocation

        source = source_path_for(spec)

        if source is None and requested_repo:
            raise ValueError(f"repo source is not a supported local directory: {requested_repo}")

        if requested_workspace and spec.sandbox.get("workspace_strategy") == "shared":
            allocation = WorkspaceAllocation(
                run_id=run_id,
                strategy="shared",
                path=str(Path(requested_workspace).expanduser().resolve()),
                requested_workspace=requested_workspace,
                requested_repo=requested_repo,
                source_path=str(Path(requested_workspace).expanduser().resolve()),
                isolated=False,
            )
            apply_allocation(spec, allocation)
            return allocation

        destination = self.workspace_root / run_id
        if destination.exists():
            raise RuntimeError(f"workspace already exists: {destination}")
        self.workspace_root.mkdir(parents=True, exist_ok=True)

        if source is None:
            destination.mkdir(parents=True)
            allocation = WorkspaceAllocation(
                run_id=run_id,
                strategy="empty",
                path=str(destination),
                requested_workspace=requested_workspace,
                requested_repo=requested_repo,
            )
        elif is_git_worktree(source):
            head = git_output(source, "rev-parse", "HEAD")
            subprocess.run(
                ["git", "-C", str(source), "worktree", "add", "--detach", str(destination), head],
                check=True,
                capture_output=True,
                text=True,
            )
            allocation = WorkspaceAllocation(
                run_id=run_id,
                strategy="git_worktree",
                path=str(destination),
                requested_workspace=requested_workspace,
                requested_repo=requested_repo,
                source_path=str(source),
                git_head=head,
            )
        elif source.is_dir():
            shutil.copytree(
                source,
                destination,
                ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv", "node_modules"),
            )
            allocation = WorkspaceAllocation(
                run_id=run_id,
                strategy="directory_copy",
                path=str(destination),
                requested_workspace=requested_workspace,
                requested_repo=requested_repo,
                source_path=str(source),
            )
        else:
            raise ValueError(f"workspace source is not a directory: {source}")

        apply_allocation(spec, allocation)
        return allocation


def apply_allocation(spec: RunSpec, allocation: WorkspaceAllocation) -> None:
    spec.workspace = allocation.path
    metadata = dict(spec.metadata)
    metadata["workspace_allocation"] = allocation.to_dict()
    spec.metadata = metadata


def source_path_for(spec: RunSpec) -> Path | None:
    if spec.workspace:
        return Path(spec.workspace).expanduser().resolve()
    if spec.repo and "://" not in spec.repo and "@" not in spec.repo:
        candidate = Path(spec.repo).expanduser().resolve()
        if candidate.exists():
            return candidate
    return None


def is_git_worktree(path: Path) -> bool:
    try:
        git_output(path, "rev-parse", "--is-inside-work-tree")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return True


def git_output(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()
