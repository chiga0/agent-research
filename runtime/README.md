# Cloud Agents Runtime POC

This directory contains the first P1 implementation slice from the roadmap: a
single SAEU Run Manager with a pluggable runtime adapter boundary.

The current implementation intentionally uses only the Python standard library.
It is small enough to audit and easy to replace once the API contract is proven.

## What works

- `POST /runs` creates a run.
- `POST /runs/{run_id}/input` submits a prompt.
- `GET /runs/{run_id}/events` streams canonical events as SSE.
- `POST /runs/{run_id}/cancel` cancels a run.
- `GET /runs/{run_id}` returns current state.
- `GET /health` and `GET /capabilities` expose runtime status.
- Raw run specs, inputs, canonical events, and adapter artifacts are written to
  `runtime/artifacts/`.

The default adapter is `fake`, which lets the full API run without a model or
qwen daemon. The `qwen` adapter can connect to an existing `qwen serve`
REST/SSE daemon through `QWEN_SERVE_URL` and `QWEN_SERVE_TOKEN`.

This is still a P1 prototype, not the cloud-ready MVP. The missing pieces are:

- starting and supervising the `qwen serve` process;
- validating the qwen adapter against a real daemon;
- auth on the Run Manager API;
- durable storage beyond local artifact files;
- deploy packaging such as systemd or Docker Compose.

## Run locally

```bash
python3 -m runtime.cloud_agents_runtime --host 127.0.0.1 --port 8765
```

Create a run:

```bash
curl -s http://127.0.0.1:8765/runs \
  -H 'content-type: application/json' \
  -d '{"prompt":"hello runtime","adapter":"fake"}'
```

Stream events:

```bash
curl -N http://127.0.0.1:8765/runs/<run_id>/events
```

Send another prompt:

```bash
curl -s http://127.0.0.1:8765/runs/<run_id>/input \
  -H 'content-type: application/json' \
  -d '{"prompt":"continue"}'
```

Cancel:

```bash
curl -s -X POST http://127.0.0.1:8765/runs/<run_id>/cancel \
  -H 'content-type: application/json' \
  -d '{"reason":"manual stop"}'
```

## Test

```bash
python3 -m unittest discover -s runtime/tests
```

## Validate fake adapter

```bash
python3 -m runtime.cloud_agents_runtime --host 127.0.0.1 --port 8765
```

In another terminal:

```bash
RUN_JSON=$(curl -s http://127.0.0.1:8765/runs \
  -H 'content-type: application/json' \
  -d '{"prompt":"hello runtime","adapter":"fake"}')
RUN_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])' <<< "$RUN_JSON")
curl -N "http://127.0.0.1:8765/runs/$RUN_ID/events"
```

Acceptance:

- `/health` returns `{"ok": true}`.
- `/capabilities` lists `fake` and `qwen`.
- `POST /runs` returns a `run_id`.
- SSE emits `run.created`, `run.started`, `input.accepted`,
  `message.delta`, `step.completed`, and `run.completed`.
- The run directory contains `run_spec.json`, `events.jsonl`,
  `raw_events.jsonl`, `input_1.json`, and `final_1.json`.

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
python3 -m runtime.cloud_agents_runtime --host 127.0.0.1 --port 8765
```

Create a qwen-backed run:

```bash
curl -s http://127.0.0.1:8765/runs \
  -H 'content-type: application/json' \
  -d '{"prompt":"say hello from qwen","adapter":"qwen"}'
```

Acceptance:

- The Run Manager creates a qwen session.
- SSE exposes canonical events.
- Raw qwen SSE frames are saved in `raw_events.jsonl`.
- `POST /runs/{run_id}/cancel` maps to qwen session cancel.

## Minimal cloud deployment target

The first cloud-runnable slice should include:

- Run Manager bound to `127.0.0.1` behind an authenticated reverse proxy.
- A separately managed `qwen serve` process for one workspace.
- Persistent artifact directory on disk.
- HTTPS, API token, process restart, and log collection.

Do not expose this POC directly to the internet. It does not yet include Run
Manager authentication, tenant isolation, or durable database-backed event
storage.
