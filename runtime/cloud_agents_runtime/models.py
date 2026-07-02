from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
from typing import Any
from uuid import uuid4

from .events import utc_now


@dataclass
class RunSpec:
    prompt: str | None = None
    adapter: str = "fake"
    repo: str | None = None
    workspace: str | None = None
    model: str | None = None
    sandbox: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "RunSpec":
        return cls(
            prompt=payload.get("prompt"),
            adapter=payload.get("adapter") or "fake",
            repo=payload.get("repo"),
            workspace=payload.get("workspace"),
            model=payload.get("model"),
            sandbox=payload.get("sandbox") or {},
            timeout_seconds=payload.get("timeout_seconds"),
            metadata=payload.get("metadata") or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunState:
    run_id: str
    spec: RunSpec
    status: str = "created"
    adapter_run_id: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    event_count: int = 0
    prompt_count: int = 0

    @classmethod
    def create(cls, spec: RunSpec, run_id: str | None = None) -> "RunState":
        return cls(run_id=run_id or f"run_{uuid4().hex}", spec=spec)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["spec"] = self.spec.to_dict()
        return data


@dataclass
class AgentProfile:
    id: str
    display_name: str
    description: str = ""
    version: int = 1
    source: str = "system"
    runtime: dict[str, Any] = field(default_factory=dict)
    tools: dict[str, Any] = field(default_factory=dict)
    approval: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    workspace: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        version: int,
        source: str = "user",
    ) -> "AgentProfile":
        profile_id = clean_identifier(payload.get("id"), "profile id")
        display_name = payload.get("display_name") or profile_id.replace("-", " ").title()
        return cls(
            id=profile_id,
            display_name=str(display_name),
            description=str(payload.get("description") or ""),
            version=version,
            source=source,
            runtime=dict(payload.get("runtime") or {}),
            tools=dict(payload.get("tools") or {}),
            approval=dict(payload.get("approval") or {}),
            limits=dict(payload.get("limits") or {}),
            workspace=dict(payload.get("workspace") or {}),
            artifacts=dict(payload.get("artifacts") or {}),
            metadata=dict(payload.get("metadata") or {}),
            created_at=payload.get("created_at") or utc_now(),
            updated_at=payload.get("updated_at") or utc_now(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MissionSpec:
    goal: str
    strategy: str = "sequential"
    adapter: str = "fake"
    repo: str | None = None
    workspace: str | None = None
    model: str | None = None
    sandbox: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    tasks: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "MissionSpec":
        goal = payload.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal is required")
        strategy = payload.get("strategy") or "sequential"
        if strategy not in {"sequential", "fanout", "custom"}:
            raise ValueError("strategy must be sequential, fanout, or custom")
        tasks = payload.get("tasks") or []
        if not isinstance(tasks, list):
            raise ValueError("tasks must be a list")
        if strategy == "custom" and not tasks:
            raise ValueError("custom strategy requires tasks")
        return cls(
            goal=goal.strip(),
            strategy=strategy,
            adapter=payload.get("adapter") or "fake",
            repo=payload.get("repo"),
            workspace=payload.get("workspace"),
            model=payload.get("model"),
            sandbox=payload.get("sandbox") or {},
            timeout_seconds=payload.get("timeout_seconds"),
            metadata=payload.get("metadata") or {},
            tasks=tasks,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MissionState:
    mission_id: str
    spec: MissionSpec
    status: str = "created"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    event_count: int = 0
    task_count: int = 0
    completed_task_count: int = 0
    failed_task_count: int = 0

    @classmethod
    def create(cls, spec: MissionSpec, mission_id: str | None = None) -> "MissionState":
        return cls(mission_id=mission_id or f"mission_{uuid4().hex}", spec=spec)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["spec"] = self.spec.to_dict()
        return data


@dataclass
class MissionTask:
    mission_id: str
    task_id: str
    title: str
    profile_id: str
    profile_version: int
    prompt: str
    order: int
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"
    run_id: str | None = None
    profile_snapshot: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MissionEvent:
    type: str
    mission_id: str
    sequence: int
    data: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"mevt_{uuid4().hex}")
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunJob:
    run_id: str
    status: str = "queued"
    worker_id: str | None = None
    queued_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    completed_at: str | None = None
    heartbeat_at: str | None = None
    lease_expires_at: str | None = None
    attempts: int = 0
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkerState:
    worker_id: str
    status: str = "active"
    capacity: int = 1
    active_count: int = 0
    lease_ttl_seconds: int = 60
    heartbeat_at: str = field(default_factory=utc_now)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutorLease:
    executor_id: str
    run_id: str
    adapter: str
    strategy: str
    status: str = "starting"
    base_url: str | None = None
    token: str | None = None
    workspace: str | None = None
    port: int | None = None
    pid: int | None = None
    command: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=utc_now)
    heartbeat_at: str | None = None
    released_at: str | None = None
    exit_code: int | None = None
    last_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data.get("token"):
            data["token"] = "configured"
        return data


@dataclass
class AccessProject:
    project_id: str
    display_name: str
    description: str = ""
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AccessProject":
        project_id = clean_identifier(payload.get("project_id") or payload.get("id"), "project id")
        display_name = str(payload.get("display_name") or payload.get("name") or project_id)
        status = str(payload.get("status") or "active")
        if status not in {"active", "archived"}:
            raise ValueError("project status must be active or archived")
        return cls(
            project_id=project_id,
            display_name=display_name,
            description=str(payload.get("description") or ""),
            status=status,
            metadata=dict(payload.get("metadata") or {}),
            created_at=str(payload.get("created_at") or utc_now()),
            updated_at=str(payload.get("updated_at") or utc_now()),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApiToken:
    token_id: str
    name: str
    principal_id: str
    scopes: list[str]
    project_id: str | None = None
    status: str = "active"
    token_prefix: str = ""
    token_hash: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    revoked_at: str | None = None
    last_used_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        payload: dict[str, Any],
        *,
        plain_token: str,
        default_principal: str,
    ) -> "ApiToken":
        token_id = clean_identifier(
            payload.get("token_id") or payload.get("id") or f"token_{uuid4().hex[:12]}",
            "token id",
        )
        name = str(payload.get("name") or token_id)
        principal_id = clean_principal_id(
            payload.get("principal_id") or default_principal,
        )
        scopes = payload.get("scopes") or ["runs:*", "missions:*", "events:read"]
        if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
            raise ValueError("scopes must be a list of strings")
        project_id = payload.get("project_id")
        if project_id is not None:
            project_id = clean_identifier(project_id, "project id")
        return cls(
            token_id=token_id,
            name=name,
            principal_id=principal_id,
            project_id=project_id,
            scopes=list(scopes),
            token_prefix=plain_token[:12],
            token_hash=hash_token(plain_token),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("token_hash", None)
        return data


def hash_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def clean_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    candidate = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    if any(character not in allowed for character in candidate):
        raise ValueError(f"{label} may only contain letters, numbers, underscore, or hyphen")
    return candidate


def clean_principal_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("principal id is required")
    principal = value.strip()
    if len(principal) > 200 or any(character < " " for character in principal):
        raise ValueError("principal id is invalid")
    return principal
