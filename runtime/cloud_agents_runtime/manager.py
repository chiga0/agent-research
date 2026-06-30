from __future__ import annotations

from pathlib import Path
from typing import Any

from .adapters import FakeAdapter, QwenServeAdapter, RuntimeAdapter
from .models import RunSpec, RunState
from .store import RunStore


class RunManager:
    def __init__(
        self,
        artifact_root: Path,
        adapters: dict[str, RuntimeAdapter] | None = None,
    ):
        self.store = RunStore(artifact_root)
        self.adapters = adapters or {
            "fake": FakeAdapter(),
            "qwen": QwenServeAdapter(),
        }

    def capabilities(self) -> dict[str, Any]:
        return {
            "v": 1,
            "mode": "saeu-run-manager-poc",
            "features": [
                "run_create",
                "run_input",
                "run_events_sse",
                "run_cancel",
                "artifact_files",
                "runtime_adapter_capabilities",
            ],
            "adapters": {
                name: adapter.capabilities() for name, adapter in sorted(self.adapters.items())
            },
        }

    def create_run(self, spec: RunSpec) -> RunState:
        adapter = self._adapter(spec.adapter)
        run = self.store.create_run(spec)
        adapter.start(run, self.store)
        if spec.prompt:
            adapter.send_input(run, spec.prompt, self.store)
        return run

    def send_input(self, run_id: str, prompt: str) -> None:
        run = self._require_run(run_id)
        self._adapter(run.spec.adapter).send_input(run, prompt, self.store)

    def cancel(self, run_id: str, reason: str | None = None) -> None:
        run = self._require_run(run_id)
        if self.store.is_terminal(run_id):
            self.store.append_event(run_id, "cancel.ignored", {"reason": "run already terminal"})
            return
        self._adapter(run.spec.adapter).cancel(run, reason, self.store)

    def get_run(self, run_id: str) -> RunState | None:
        return self.store.get_run(run_id)

    def _require_run(self, run_id: str) -> RunState:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    def _adapter(self, name: str) -> RuntimeAdapter:
        adapter = self.adapters.get(name)
        if adapter is None:
            raise ValueError(f"unknown adapter: {name}")
        return adapter

