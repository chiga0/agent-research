from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    def create(cls, spec: RunSpec) -> "RunState":
        return cls(run_id=f"run_{uuid4().hex}", spec=spec)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["spec"] = self.spec.to_dict()
        return data

