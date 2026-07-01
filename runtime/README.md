# Cloud Agents Runtime

This directory contains the P1/P2 implementation slice from the roadmap: a
single SAEU Run Manager with a pluggable runtime adapter boundary, durable event
storage, audit artifacts, permission resolution, replay tooling, and cloud
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
- `GET /` serves the browser management console.
- `GET /runs/{run_id}/events.json` returns canonical events for UI replay.
- `GET /runs/{run_id}/artifacts` lists artifact files for the run.
- Raw run specs, inputs, canonical events, and adapter artifacts are written to
  `runtime/artifacts/`.
- Canonical events are persisted in `runtime.db` and `events.jsonl`.
- `diagnostics.json` is maintained per run.
- `scripts/replay_run.py` can replay events, SSE frames, or rebuilt state from
  artifacts.

The default adapter is `fake`, which lets the full API run without a model or
qwen daemon. The `qwen` adapter can connect to an existing `qwen serve`
REST/SSE daemon through `QWEN_SERVE_URL` and `QWEN_SERVE_TOKEN`.

The service is intended to bind to `127.0.0.1` and sit behind an authenticated
reverse proxy. Do not expose the Run Manager directly to the public internet.

## Run locally

```bash
export RUN_MANAGER_TOKEN=dev-token
python3 -m runtime.cloud_agents_runtime \
  --host 127.0.0.1 \
  --port 8765 \
  --token "$RUN_MANAGER_TOKEN"
```

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

Replay from artifacts:

```bash
python3 scripts/replay_run.py \
  --artifact-root runtime/artifacts \
  --run-id <run_id> \
  --format state
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
- API routes other than `/health` require `Authorization: Bearer ...` when
  `RUN_MANAGER_TOKEN` is set.
- `POST /runs` returns a `run_id`.
- SSE emits `run.created`, `run.started`, `input.accepted`,
  `message.delta`, `step.completed`, and `run.completed`.
- SSE honors `Last-Event-ID`; if the client asks for an event sequence beyond
  what the store has, the server records and streams `event.gap_detected`.
- `POST /runs/{run_id}/permissions/{permission_id}` records
  `permission.resolved` in the same audit trail.
- The run directory contains `run_spec.json`, `events.jsonl`,
  `raw_events.jsonl`, `input_1.json`, `diagnostics.json`, and `final_1.json`.
- The artifact root contains `runtime.db`.

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

## Validate a running service

```bash
python3 scripts/validate_runtime.py \
  --base-url http://127.0.0.1:8765 \
  --token "$RUN_MANAGER_TOKEN" \
  --adapter fake \
  --artifact-root runtime/artifacts
```

Use `--adapter qwen` after starting `qwen serve`.

## Minimal cloud deployment target

The current cloud-runnable slice includes:

- Run Manager bound to `127.0.0.1` with bearer-token auth.
- Managed `qwen serve` process for one workspace when `QWEN_SERVE_COMMAND` is
  configured.
- Persistent artifact directory on disk with `runtime.db` and JSONL artifacts.
- systemd unit and Docker Compose assets.
- CI gates for style, compile, 90%+ runtime coverage, and MkDocs strict build.
- Validation script for fake/qwen runs and required artifacts.

HTTPS/reverse proxy and multi-tenant isolation remain deployment-layer concerns
for the next hardening phase.

### Docker Compose

```bash
export RUN_MANAGER_TOKEN="$(openssl rand -hex 32)"
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
Run Manager bearer token.

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
