from __future__ import annotations

import threading
import time
from typing import Any

from .base import RuntimeAdapter
from ..models import RunState
from ..review_gate import is_review_gate_task, review_gate_artifact_name
from ..store import RunStore


class FakeAdapter(RuntimeAdapter):
    name = "fake"

    def __init__(self, delay_seconds: float = 0.02):
        self.delay_seconds = delay_seconds
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    def capabilities(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "mode": "in_process_fake",
            "features": ["start", "input", "events", "cancel", "artifacts"],
        }

    def start(self, run: RunState, store: RunStore) -> None:
        store.set_adapter_run_id(run.run_id, f"fake_{run.run_id}")
        store.append_event(run.run_id, "run.started", {"adapter": self.name})

    def send_input(self, run: RunState, prompt: str, store: RunStore) -> None:
        prompt_number = store.increment_prompt_count(run.run_id)
        store.write_json(
            run.run_id,
            f"input_{prompt_number}.json",
            {"prompt": prompt, "prompt_number": prompt_number},
        )
        store.append_event(
            run.run_id,
            "input.accepted",
            {"prompt_number": prompt_number, "prompt_preview": prompt[:120]},
        )
        thread = threading.Thread(
            target=self._complete_prompt,
            args=(run.run_id, prompt_number, prompt, dict(run.spec.metadata), store),
            daemon=True,
        )
        thread.start()

    def cancel(self, run: RunState, reason: str | None, store: RunStore) -> None:
        with self._lock:
            self._cancelled.add(run.run_id)
        store.append_event(run.run_id, "run.cancelled", {"reason": reason or "cancelled"})

    def _complete_prompt(
        self,
        run_id: str,
        prompt_number: int,
        prompt: str,
        metadata: dict[str, Any],
        store: RunStore,
    ) -> None:
        store.append_event(run_id, "step.started", {"prompt_number": prompt_number})
        for index, chunk in enumerate(self._chunks(prompt), start=1):
            time.sleep(self.delay_seconds)
            if self._is_cancelled(run_id):
                return
            store.append_raw_event(
                run_id,
                self.name,
                {"kind": "chunk", "prompt_number": prompt_number, "index": index, "text": chunk},
            )
            store.append_event(
                run_id,
                "message.delta",
                {"prompt_number": prompt_number, "index": index, "text": chunk},
            )
        if self._is_cancelled(run_id):
            return
        final_text = f"fake adapter completed prompt {prompt_number}: {prompt}"
        self._write_fake_review_gate_if_needed(run_id, metadata, store)
        store.write_json(
            run_id,
            f"final_{prompt_number}.json",
            {"prompt_number": prompt_number, "text": final_text},
        )
        store.append_event(run_id, "step.completed", {"prompt_number": prompt_number})
        store.append_event(
            run_id,
            "run.completed",
            {"prompt_number": prompt_number, "final_artifact": f"final_{prompt_number}.json"},
        )

    def _is_cancelled(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._cancelled

    def _write_fake_review_gate_if_needed(
        self,
        run_id: str,
        metadata: dict[str, Any],
        store: RunStore,
    ) -> None:
        profile = metadata.get("profile_snapshot")
        if not isinstance(profile, dict) or not is_review_gate_task(profile):
            return
        store.write_json(
            run_id,
            review_gate_artifact_name(profile),
            {
                "decision": "pass",
                "severity": "none",
                "reason": "fake adapter reviewer gate passed",
                "findings": [],
            },
        )

    @staticmethod
    def _chunks(prompt: str) -> list[str]:
        if not prompt:
            return ["empty prompt"]
        words = prompt.split()
        if not words:
            return [prompt]
        return [" ".join(words[index : index + 4]) for index in range(0, len(words), 4)]
