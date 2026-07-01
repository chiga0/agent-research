# Cloud Agents Runtime

This directory contains the P1/P2 implementation slice plus the local P3/P4
runtime slice from the roadmap: a single SAEU Run Manager with a pluggable
runtime adapter boundary, durable event storage, audit artifacts, permission
resolution, run queue leases, worker heartbeat, resource policy, cleanup policy,
profile registry, mission/task orchestration, replay tooling, and cloud
deployment assets.

The current implementation intentionally uses only the Python standard library.
It is small enough to audit and easy to replace once the API contract is proven.
For the MVP, the durable event store is SQLite plus append-only JSONL artifacts.
The schema mirrors the planned append-only event table and can be moved to
Postgres when multiple control-plane instances are required.

## What works

- `POST /runs` creates a run.
- `POST /runs/{run_id}/input` submits a prompt.
- `GET /runs/{run_id}/events` streams canonical events as SSE.
- `POST /runs/{run_id}/cancel` cancels a run.
- `POST /runs/{run_id}/permissions/{permission_id}` records a permission
  decision.
- `GET /runs/{run_id}` returns current state.
- `GET /health` and `GET /capabilities` expose runtime status.
- `GET /queue` exposes queued/running job leases and worker status.
- `GET /workers` exposes worker heartbeat and capacity.
- `GET /` serves the browser management console.
- `GET /runs/{run_id}/events.json` returns canonical events for UI replay.
- `GET /runs/{run_id}/artifacts` lists artifact files for the run.
- `POST /cleanup` triggers one retention-policy cleanup pass.
- `GET /profiles` and `GET /profiles/{profile_id}` expose built-in and custom
  agent profiles.
- `POST /profiles` creates a versioned user profile. Built-in profiles must be
  copied to a new id before editing.
- `POST /missions` creates a mission DAG and schedules each ready task as a
  normal SAEU run.
- `GET /missions`, `GET /missions/{mission_id}`,
  `GET /missions/{mission_id}/events.json`, and
  `GET /missions/{mission_id}/artifacts` expose mission state, audit events,
  and final report artifacts.
- `POST /missions/{mission_id}/cancel` cancels active child runs and marks
  pending tasks cancelled.
- `POST /missions/{mission_id}/review-gate/override` records a human override
  for a blocked gate and can resume downstream pending work.
- `GET /.well-known/agent-card.json`, `POST /a2a/tasks`, and
  `GET /a2a/tasks/{task_id}` expose the P5.2 A2A gateway POC.
- `GET /acp` and `POST /acp` expose the P5.1 ACP JSON-RPC-over-HTTP POC.
- `GET /temporal/workflows/missions/{mission_id}/plan` and
  `GET /temporal/workflows/runs/{run_id}/plan` expose the P5.3 Temporal
  workflow-plan POC.
- Raw run specs, inputs, canonical events, and adapter artifacts are written to
  `runtime/artifacts/`.
- Canonical events are persisted in `runtime.db` and `events.jsonl`.
- Run queue state is persisted in `run_jobs`; local worker state is persisted in
  `workers`.
- Built-in profiles currently include `planner`, `coder`, `tester`, `reviewer`,
  `release-gate`, and `doc-writer`. Each task stores a resolved profile
  snapshot before its run starts, so later profile edits do not change
  historical audit meaning.
- The built-in `reviewer` profile is reviewer-gate enabled. A reviewer run must
  publish `review_gate.json`; the supervisor records `review.gate_*` events and
  blocks the mission on `block`, `needs_human`, invalid/missing gate artifacts,
  or high/critical findings.
- The built-in `release-gate` profile is merge/deploy-gate enabled. It publishes
  `release_gate.json` and emits `merge_deploy.gate_*` events.
- Mission artifacts are written to `runtime/artifacts/missions/<mission_id>/`.
  They include `mission_spec.json`, `mission_manifest.json`, `events.jsonl`,
  `task_<task_id>.json`, `review_gate.json` when a reviewer gate runs, and
  `final_report.md`.
- Each run receives a resolved workspace before it is queued. Local git sources
  use a detached worktree under `artifact_root/workspaces/<run_id>`; runs
  without a source receive an empty isolated directory. Remote repo cloning is
  intentionally rejected until credentials, checkout policy, and audit metadata
  are implemented.
- Each run receives a resolved resource policy before it is queued. The policy is
  written to `resources.json`, exposed in diagnostics, and emits
  `resources.resolved`. `timeout_seconds` is enforced by a Run Manager watchdog;
  CPU, memory, and pids are enforced at the Docker/systemd execution-unit layer
  in the current P3 slice.
- Cleanup policy is enabled by default. Terminal run workspaces are retained for
  7 days, run artifact directories are retained for 30 days, and canonical DB
  events remain in `runtime.db` after artifact cleanup.
- `diagnostics.json` is maintained per run.
- `scripts/replay_run.py` can replay events, SSE frames, or rebuilt state from
  artifacts, and falls back to `runtime.db` after artifact JSONL cleanup.

The default adapter is `fake`, which lets the full API run without a model or
qwen daemon. The `qwen` adapter can connect to an existing `qwen serve`
REST/SSE daemon through `QWEN_SERVE_URL` and `QWEN_SERVE_TOKEN`.

The service is intended to bind to `127.0.0.1` and sit behind an authenticated
reverse proxy. Do not expose the Run Manager directly to the public internet.

## Run locally

```bash
export RUN_MANAGER_TOKEN=dev-token
export RUN_MANAGER_WORKER_CAPACITY=1
export RUN_MANAGER_DEFAULT_CPUS=1.0
export RUN_MANAGER_DEFAULT_MEMORY_MB=1024
export RUN_MANAGER_DEFAULT_PIDS=512
export RUN_MANAGER_DEFAULT_TIMEOUT_SECONDS=3600
export RUN_MANAGER_CLEANUP_ENABLED=1
export RUN_MANAGER_WORKSPACE_RETENTION_SECONDS=604800
export RUN_MANAGER_ARTIFACT_RETENTION_SECONDS=2592000
export RUN_MANAGER_CLEANUP_INTERVAL_SECONDS=3600
python3 -m runtime.cloud_agents_runtime \
  --host 127.0.0.1 \
  --port 8765 \
  --token "$RUN_MANAGER_TOKEN"
```

Set `RUN_MANAGER_WORKER_CAPACITY=2` or pass `--worker-capacity 2` to allow two
concurrent SAEU runs on the same VPS. Keep it at `1` for the smallest qwen
deployment until workspace and resource isolation are configured.

Create a run:

```bash
curl -s http://127.0.0.1:8765/runs \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"prompt":"hello runtime","adapter":"fake"}'
```

Stream events:

```bash
curl -N http://127.0.0.1:8765/runs/<run_id>/events \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN"
```

Send another prompt:

```bash
curl -s http://127.0.0.1:8765/runs/<run_id>/input \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"prompt":"continue"}'
```

Cancel:

```bash
curl -s -X POST http://127.0.0.1:8765/runs/<run_id>/cancel \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"reason":"manual stop"}'
```

Resolve a permission request:

```bash
curl -s -X POST http://127.0.0.1:8765/runs/<run_id>/permissions/<permission_id> \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"decision":"approve","decided_by":"operator","reason":"reviewed"}'
```

Inspect queue and workers:

```bash
curl -s http://127.0.0.1:8765/queue \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN"
curl -s http://127.0.0.1:8765/workers \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN"
```

Trigger one cleanup pass:

```bash
curl -s -X POST http://127.0.0.1:8765/cleanup \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN" \
  -H 'content-type: application/json' \
  -d '{}'
```

Replay from artifacts:

```bash
python3 scripts/replay_run.py \
  --artifact-root runtime/artifacts \
  --run-id <run_id> \
  --format state
```

Create a two-task mission:

```bash
curl -s http://127.0.0.1:8765/missions \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN" \
  -H 'content-type: application/json' \
  -d '{
    "goal": "validate mission orchestration",
    "strategy": "custom",
    "adapter": "fake",
    "tasks": [
      {"id": "plan", "profile": "planner", "prompt": "plan the work"},
      {
        "id": "review",
        "profile": "reviewer",
        "depends_on": ["plan"],
        "prompt": "review the plan"
      }
    ]
  }'
```

Inspect mission state:

```bash
curl -s http://127.0.0.1:8765/missions/<mission_id> \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN"
curl -s http://127.0.0.1:8765/missions/<mission_id>/events.json \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN"
```

Reviewer gate artifact schema:

```json
{
  "decision": "pass",
  "severity": "none",
  "reason": "review passed",
  "findings": [
    {
      "id": "finding-001",
      "severity": "low",
      "category": "tests",
      "message": "optional follow-up",
      "evidence": {}
    }
  ]
}
```

Allowed decisions are `pass`, `warn`, `block`, and `needs_human`. Allowed
severities are `none`, `low`, `medium`, `high`, and `critical`. A high or
critical finding blocks the mission even if the decision says `pass` or `warn`.
Missing or invalid `review_gate.json` is treated as `needs_human` and blocks
downstream tasks.

Override a blocked reviewer gate:

```bash
curl -s -X POST http://127.0.0.1:8765/missions/<mission_id>/review-gate/override \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN" \
  -H 'content-type: application/json' \
  -d '{
    "decision": "approve",
    "decided_by": "operator@example.test",
    "reason": "accepted for controlled rollout"
  }'
```

`approve` resumes blocked downstream tasks that have not started. `deny` records
the decision and keeps the mission blocked. The runtime writes
`review_gate_override.json` and emits `review.gate_override_recorded`; approved
overrides also emit `task.unblocked` and `mission.resumed`.

ACP POC example:

```bash
curl -s http://127.0.0.1:8765/acp \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
```

A2A gateway POC example:

```bash
curl -s http://127.0.0.1:8765/.well-known/agent-card.json \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN"
curl -s http://127.0.0.1:8765/a2a/tasks \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"goal":"external gateway task","adapter":"fake"}'
```

Temporal workflow-plan POC example:

```bash
curl -s http://127.0.0.1:8765/temporal/workflows/missions/<mission_id>/plan \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN"
```

## Test

```bash
python3 -m unittest discover -s runtime/tests
python3 scripts/check_runtime_coverage.py
python3 scripts/check_style.py
```

## Validate fake adapter

```bash
export RUN_MANAGER_TOKEN=dev-token
python3 -m runtime.cloud_agents_runtime \
  --host 127.0.0.1 \
  --port 8765 \
  --token "$RUN_MANAGER_TOKEN"
```

In another terminal:

```bash
RUN_JSON=$(curl -s http://127.0.0.1:8765/runs \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"prompt":"hello runtime","adapter":"fake"}')
RUN_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])' <<< "$RUN_JSON")
curl -N "http://127.0.0.1:8765/runs/$RUN_ID/events" \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN"
```

Acceptance:

- `/health` returns `{"ok": true}`.
- `/capabilities` lists `fake` and `qwen`.
- `/capabilities` exposes runtime resource defaults and maximums.
- `/capabilities` exposes cleanup retention policy.
- `/capabilities` exposes built-in profiles and mission orchestration features.
- `/queue` returns job counts, job leases, and worker heartbeat records.
- `/workers` returns active worker capacity and heartbeat time.
- `/profiles` returns `planner`, `coder`, `tester`, `reviewer`,
  `release-gate`, and `doc-writer`.
- API routes other than `/health` require `Authorization: Bearer ...` when
  `RUN_MANAGER_TOKEN` is set.
- `POST /runs` returns a `run_id`.
- `POST /missions` returns a `mission_id`; each task has a profile snapshot and
  a child SAEU `run_id` once scheduled.
- SSE emits `run.created`, `workspace.prepared`, `resources.resolved`,
  `run.queued`, `lease.claimed`, `run.started`, `input.accepted`,
  `message.delta`, `step.completed`, and `run.completed`.
- SSE honors `Last-Event-ID`; if the client asks for an event sequence beyond
  what the store has, the server records and streams `event.gap_detected`.
- `POST /runs/{run_id}/permissions/{permission_id}` records
  `permission.resolved` in the same audit trail.
- The run directory contains `run_spec.json`, `events.jsonl`,
  `raw_events.jsonl`, `input_1.json`, `workspace.json`, `resources.json`,
  `diagnostics.json`, and `final_1.json`.
- The artifact root contains `runtime.db` with `runs`, `run_events`,
  `raw_events`, `run_jobs`, `workers`, `agent_profiles`, `missions`,
  `mission_tasks`, and `mission_events`.
- A completed mission directory contains `mission_manifest.json`,
  `events.jsonl`, task JSON files, and `final_report.md`.
- A reviewer-gated mission emits one of `review.gate_passed`,
  `review.gate_warned`, `review.gate_blocked`, or
  `review.gate_needs_human`. Blocked reviewer gates set mission status to
  `blocked` and prevent downstream pending tasks from starting.
- `release-gate` emits `merge_deploy.gate_*` events and uses the same
  conservative block semantics.
- ACP/A2A/Temporal POC endpoints are present in `/capabilities`.

## Validate qwen adapter

Start `qwen serve` separately in the target workspace:

```bash
cd /path/to/workspace
qwen serve --hostname 127.0.0.1 --port 4170
```

Then start the Run Manager:

```bash
export QWEN_SERVE_URL=http://127.0.0.1:4170
export QWEN_SERVE_TOKEN=
export RUN_MANAGER_TOKEN=dev-token
python3 -m runtime.cloud_agents_runtime \
  --host 127.0.0.1 \
  --port 8765 \
  --token "$RUN_MANAGER_TOKEN" \
  --qwen-url "$QWEN_SERVE_URL"
```

Create a qwen-backed run:

```bash
curl -s http://127.0.0.1:8765/runs \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"prompt":"say hello from qwen","adapter":"qwen"}'
```

Acceptance:

- The Run Manager creates a qwen session.
- SSE exposes canonical events.
- Raw qwen SSE frames are saved in `raw_events.jsonl`.
- `POST /runs/{run_id}/cancel` maps to qwen session cancel.
- When a qwen reviewer or release-gate run includes a valid fenced JSON gate in
  its final text, the adapter extracts it into `review_gate.json` or
  `release_gate.json` before `run.completed`.

## Validate a running service

```bash
python3 scripts/validate_runtime.py \
  --base-url http://127.0.0.1:8765 \
  --token "$RUN_MANAGER_TOKEN" \
  --adapter fake \
  --artifact-root runtime/artifacts \
  --validate-mission
```

Use `--adapter qwen` after starting `qwen serve`. Leave off
`--validate-mission` for quick qwen smoke tests, because it creates multiple
child runs.

## Minimal cloud deployment target

The current cloud-runnable slice includes:

- Run Manager bound to `127.0.0.1` with bearer-token auth.
- Local worker queue with persisted `run_jobs`, worker heartbeat, lease
  reclamation, and per-worker capacity.
- Per-run workspace allocation with `workspace.prepared` audit event and
  `workspace.json` artifact.
- Per-run resource policy with `resources.resolved`, `resources.json`, and a
  timeout watchdog.
- Cleanup policy for terminal run workspaces and artifact directories.
- Profile registry and mission/task DAG tables, with profile snapshots copied
  into each child run spec.
- Mission supervisor that maps `mission -> task -> profile -> SAEU run`,
  supports sequential and fan-out/fan-in DAGs, artifact reference handoff, and a
  final report artifact.
- Reviewer gate enforcement through structured `review_gate.json` artifacts,
  mission-level gate events, and automatic blocked mission status.
- Human review-gate override and merge/deploy gate support.
- P5 POC endpoints for ACP JSON-RPC-over-HTTP, A2A task gateway, and Temporal
  workflow-plan export.
- Managed `qwen serve` process for one workspace when `QWEN_SERVE_COMMAND` is
  configured.
- Persistent artifact directory on disk with `runtime.db` and JSONL artifacts.
- systemd unit and Docker Compose assets with execution-unit CPU/memory/pids
  limits.
- CI gates for style, compile, 90%+ runtime coverage, and MkDocs strict build.
- Validation script for fake/qwen runs and required artifacts.
- Optional validation script coverage for P4 mission/profile orchestration via
  `--validate-mission`.

HTTPS/reverse proxy and multi-tenant isolation remain deployment-layer concerns
for the next hardening phase.

P4 limits in this MVP:

- The supervisor is a deterministic in-process controller, not yet a long-lived
  Project Agent with its own memory model.
- Reviewer gate is schema-based; it does not infer risk from free-form markdown
  findings. Real qwen reviewer runs must write `review_gate.json`.
- ACP/A2A/Temporal support is intentionally POC-level and does not claim full
  protocol compliance yet.
- Artifact handoff passes stable artifact references into child run prompts; it
  does not copy sibling workspaces or expose uncontrolled shared memory.

### Docker Compose

```bash
export RUN_MANAGER_TOKEN="$(openssl rand -hex 32)"
export RUNTIME_CPUS=1.0
export RUNTIME_MEMORY_LIMIT=1g
export RUNTIME_PIDS_LIMIT=512
docker compose -f deploy/docker-compose.runtime.yml up -d --build
python3 scripts/validate_runtime.py \
  --base-url http://127.0.0.1:8765 \
  --token "$RUN_MANAGER_TOKEN" \
  --adapter fake
```

### systemd

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin cloudagents
sudo mkdir -p /opt/agent-research /var/lib/cloud-agents-runtime/artifacts
sudo chown -R cloudagents:cloudagents /var/lib/cloud-agents-runtime
sudo cp deploy/systemd/cloud-agents-runtime.env.example /etc/cloud-agents-runtime.env
sudo cp deploy/systemd/cloud-agents-runtime.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cloud-agents-runtime
```

For a qwen-backed deployment, place the qwen settings file at:

```text
/home/cloudagents/.qwen/settings.json
```

with owner `cloudagents:cloudagents` and mode `600`.

The helper script can do that automatically:

```bash
QWEN_SETTINGS_FILE=/path/to/settings.json \
  bash scripts/deploy_runtime_vps.sh root@<host> /path/to/key.pem
```

### Browser access through Nginx

The runtime should remain bound to `127.0.0.1`. For browser access, put Nginx in
front of it, require Basic Auth at the edge, and let Nginx inject the internal
Run Manager bearer token. Do not expose this HTTP listener directly on the
public internet; terminate TLS at Nginx, Cloudflare, a load balancer, or keep the
route behind a VPN such as WireGuard/Tailscale.

Use `deploy/nginx/cloud-agents-runtime.conf.example` as the starting point and
write the backend auth header into:

```text
/etc/nginx/snippets/cloud-agents-runtime-auth.conf
```

with content:

```nginx
proxy_set_header Authorization "Bearer <RUN_MANAGER_TOKEN>";
```

The public route is `/cloud-agents/`; API paths are forwarded without that
prefix, for example `/cloud-agents/health` -> `http://127.0.0.1:8765/health`.
The same route serves the management console from the runtime root.
