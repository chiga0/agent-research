#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
from typing import Any


TERMINAL_EVENTS = {"run.completed", "run.failed", "run.cancelled"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay an AgentFlow Runtime run")
    parser.add_argument(
        "--artifact-root",
        type=pathlib.Path,
        default=pathlib.Path("runtime/artifacts"),
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--format", choices=["events", "state", "sse"], default="events")
    args = parser.parse_args(argv)

    replay_input = load_replay_input(args.artifact_root, args.run_id)
    if replay_input is None:
        return 1

    events, diagnostics = replay_input
    if args.format == "events":
        for event in events:
            print(json.dumps(event, ensure_ascii=False, sort_keys=True))
        return 0
    if args.format == "sse":
        for event in events:
            print(to_sse(event), end="")
        return 0
    print(json.dumps(rebuild_state(args.run_id, events, diagnostics), ensure_ascii=False))
    return 0


def load_replay_input(
    artifact_root: pathlib.Path,
    run_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    run_dir = artifact_root / run_id
    events_path = run_dir / "events.jsonl"
    if events_path.exists():
        return load_jsonl(events_path), load_json(run_dir / "diagnostics.json")

    db_path = artifact_root / "runtime.db"
    if not db_path.exists():
        print(f"events not found: {events_path}", file=sys.stderr)
        return None

    replay = load_from_db(db_path, run_id)
    if replay is None:
        print(f"events not found in artifacts or runtime.db for run: {run_id}", file=sys.stderr)
        return None
    return replay


def load_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"invalid event line in {path}")
            events.append(payload)
    return events


def load_json(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"invalid json object: {path}")
    return payload


def load_from_db(
    db_path: pathlib.Path,
    run_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        run = db.execute("select * from runs where run_id = ?", (run_id,)).fetchone()
        if run is None:
            return None
        rows = db.execute(
            """
            select sequence, event_id, type, data_json, created_at
            from run_events
            where run_id = ?
            order by sequence
            """,
            (run_id,),
        ).fetchall()
    spec = json.loads(run["spec_json"])
    events = [
        {
            "id": row["event_id"],
            "run_id": run_id,
            "sequence": row["sequence"],
            "type": row["type"],
            "created_at": row["created_at"],
            "data": json.loads(row["data_json"]),
        }
        for row in rows
    ]
    diagnostics = {
        "status": run["status"],
        "adapter": spec.get("adapter") if isinstance(spec, dict) else None,
        "adapter_run_id": run["adapter_run_id"],
        "event_count": run["event_count"],
        "prompt_count": run["prompt_count"],
        "artifact_dir": str(db_path.parent / run_id),
    }
    return events, diagnostics


def rebuild_state(
    run_id: str,
    events: list[dict[str, Any]],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    status = diagnostics.get("status") or "created"
    for event in events:
        event_type = event.get("type")
        if event_type == "run.started":
            status = "running"
        elif event_type == "input.accepted" and status == "created":
            status = "queued"
        elif event_type in TERMINAL_EVENTS:
            status = str(event_type).split(".", 1)[1]
    return {
        "run_id": run_id,
        "status": status,
        "event_count": len(events),
        "prompt_count": diagnostics.get("prompt_count", count_inputs(events)),
        "last_sequence": events[-1]["sequence"] if events else 0,
        "last_event": events[-1]["type"] if events else None,
        "adapter": diagnostics.get("adapter"),
        "adapter_run_id": diagnostics.get("adapter_run_id"),
    }


def count_inputs(events: list[dict[str, Any]]) -> int:
    return sum(1 for event in events if event.get("type") == "input.accepted")


def to_sse(event: dict[str, Any]) -> str:
    sequence = event.get("sequence", 0)
    event_type = event.get("type", "message")
    data = json.dumps(event, ensure_ascii=False, sort_keys=True)
    return f"id: {sequence}\nevent: {event_type}\ndata: {data}\n\n"


if __name__ == "__main__":
    raise SystemExit(main())
