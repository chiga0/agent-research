from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class RuntimeEvent:
    type: str
    run_id: str
    sequence: int
    data: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"evt_{uuid4().hex}")
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


TERMINAL_RUN_EVENTS = {"run.completed", "run.failed", "run.cancelled"}

