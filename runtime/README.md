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
qwen daemon. The `qwen` adapter is wired as a boundary for `qwen serve` REST/SSE
and currently records a clear `adapter.unimplemented` event until the exact
daemon integration is enabled.

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
