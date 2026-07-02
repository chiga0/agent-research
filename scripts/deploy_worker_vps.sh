#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <ssh-target> <ssh-key>" >&2
  exit 2
fi

SSH_TARGET="$1"
SSH_KEY="$2"
APP_DIR="${APP_DIR:-/opt/agent-research}"
STATE_DIR="${STATE_DIR:-/var/lib/cloud-agents-worker}"
REPO_URL="${REPO_URL:-https://github.com/chiga0/agent-research.git}"
NODE_PACKAGE="${NODE_PACKAGE:-@qwen-code/qwen-code@0.19.3}"
QWEN_SETTINGS_FILE="${QWEN_SETTINGS_FILE:-}"
RUN_WORKER_CONTROL_URL="${RUN_WORKER_CONTROL_URL:-}"
RUN_WORKER_TOKEN="${RUN_WORKER_TOKEN:-}"
RUN_WORKER_ID="${RUN_WORKER_ID:-}"
RUN_WORKER_CAPACITY="${RUN_WORKER_CAPACITY:-1}"
RUN_WORKER_LEASE_TTL_SECONDS="${RUN_WORKER_LEASE_TTL_SECONDS:-60}"
RUN_WORKER_POLL_INTERVAL_SECONDS="${RUN_WORKER_POLL_INTERVAL_SECONDS:-2}"
RUN_WORKER_HEARTBEAT_INTERVAL_SECONDS="${RUN_WORKER_HEARTBEAT_INTERVAL_SECONDS:-10}"
RUN_WORKER_RUN_WAIT_TIMEOUT_SECONDS="${RUN_WORKER_RUN_WAIT_TIMEOUT_SECONDS:-300}"
RUN_WORKER_METADATA_JSON="${RUN_WORKER_METADATA_JSON:-{}}"
QWEN_SERVE_URL="${QWEN_SERVE_URL:-}"
QWEN_SERVE_TOKEN="${QWEN_SERVE_TOKEN:-}"
DEPLOY_SSH_CONNECT_TIMEOUT_SECONDS="${DEPLOY_SSH_CONNECT_TIMEOUT_SECONDS:-30}"
DEPLOY_COMMAND_TIMEOUT_SECONDS="${DEPLOY_COMMAND_TIMEOUT_SECONDS:-900}"

if [[ -z "$RUN_WORKER_CONTROL_URL" ]]; then
  echo "RUN_WORKER_CONTROL_URL is required" >&2
  exit 2
fi
if [[ -z "$RUN_WORKER_TOKEN" ]]; then
  echo "RUN_WORKER_TOKEN is required" >&2
  exit 2
fi

shell_quote() {
  printf "%q" "$1"
}

REMOTE_ENV=(
  "APP_DIR=$(shell_quote "$APP_DIR")"
  "STATE_DIR=$(shell_quote "$STATE_DIR")"
  "REPO_URL=$(shell_quote "$REPO_URL")"
  "NODE_PACKAGE=$(shell_quote "$NODE_PACKAGE")"
  "HAS_QWEN_SETTINGS=$(shell_quote "$([[ -n "$QWEN_SETTINGS_FILE" ]] && echo 1 || echo 0)")"
  "RUN_WORKER_CONTROL_URL=$(shell_quote "$RUN_WORKER_CONTROL_URL")"
  "RUN_WORKER_TOKEN=$(shell_quote "$RUN_WORKER_TOKEN")"
  "RUN_WORKER_ID=$(shell_quote "$RUN_WORKER_ID")"
  "RUN_WORKER_CAPACITY=$(shell_quote "$RUN_WORKER_CAPACITY")"
  "RUN_WORKER_LEASE_TTL_SECONDS=$(shell_quote "$RUN_WORKER_LEASE_TTL_SECONDS")"
  "RUN_WORKER_POLL_INTERVAL_SECONDS=$(shell_quote "$RUN_WORKER_POLL_INTERVAL_SECONDS")"
  "RUN_WORKER_HEARTBEAT_INTERVAL_SECONDS=$(shell_quote "$RUN_WORKER_HEARTBEAT_INTERVAL_SECONDS")"
  "RUN_WORKER_RUN_WAIT_TIMEOUT_SECONDS=$(shell_quote "$RUN_WORKER_RUN_WAIT_TIMEOUT_SECONDS")"
  "RUN_WORKER_METADATA_JSON=$(shell_quote "$RUN_WORKER_METADATA_JSON")"
  "QWEN_SERVE_URL=$(shell_quote "$QWEN_SERVE_URL")"
  "QWEN_SERVE_TOKEN=$(shell_quote "$QWEN_SERVE_TOKEN")"
  "DEPLOY_COMMAND_TIMEOUT_SECONDS=$(shell_quote "$DEPLOY_COMMAND_TIMEOUT_SECONDS")"
)

SSH_OPTIONS=(
  -i "$SSH_KEY"
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout="$DEPLOY_SSH_CONNECT_TIMEOUT_SECONDS"
  -o ConnectionAttempts=1
)

if [[ -n "$QWEN_SETTINGS_FILE" ]]; then
  if [[ ! -f "$QWEN_SETTINGS_FILE" ]]; then
    echo "QWEN_SETTINGS_FILE does not exist: $QWEN_SETTINGS_FILE" >&2
    exit 2
  fi
  scp "${SSH_OPTIONS[@]}" "$QWEN_SETTINGS_FILE" "$SSH_TARGET:/tmp/qwen-settings.json"
fi

ssh "${SSH_OPTIONS[@]}" "$SSH_TARGET" "${REMOTE_ENV[*]} bash -s" <<'REMOTE'
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

log_step() {
  printf '[worker-deploy] %s\n' "$*"
}

run_timeout() {
  local label="$1"
  local timeout_seconds="$2"
  shift 2
  log_step "$label"
  timeout "$timeout_seconds" "$@"
}

if ! command -v git >/dev/null \
  || ! command -v python3 >/dev/null \
  || ! command -v npm >/dev/null; then
  run_timeout "apt-get update" "$DEPLOY_COMMAND_TIMEOUT_SECONDS" apt-get update
  run_timeout \
    "install worker host packages" \
    "$DEPLOY_COMMAND_TIMEOUT_SECONDS" \
    apt-get install -y git python3 npm
fi

run_timeout \
  "install node package $NODE_PACKAGE" \
  "$DEPLOY_COMMAND_TIMEOUT_SECONDS" \
  npm install -g "$NODE_PACKAGE"

if ! id cloudagents >/dev/null 2>&1; then
  log_step "create cloudagents user"
  useradd --system --create-home --shell /usr/sbin/nologin cloudagents
fi

mkdir -p "$APP_DIR" "$STATE_DIR/artifacts"
chown -R cloudagents:cloudagents "$STATE_DIR"
install -d -m 700 -o cloudagents -g cloudagents /home/cloudagents/.qwen
if [[ "$HAS_QWEN_SETTINGS" == "1" ]]; then
  install -m 600 -o cloudagents -g cloudagents \
    /tmp/qwen-settings.json \
    /home/cloudagents/.qwen/settings.json
  rm -f /tmp/qwen-settings.json
fi

if [[ ! -d "$APP_DIR/.git" ]]; then
  run_timeout \
    "clone runtime repository" \
    "$DEPLOY_COMMAND_TIMEOUT_SECONDS" \
    git clone "$REPO_URL" "$APP_DIR"
else
  run_timeout \
    "fetch runtime repository" \
    "$DEPLOY_COMMAND_TIMEOUT_SECONDS" \
    git -C "$APP_DIR" fetch origin main
  run_timeout \
    "reset runtime repository" \
    "$DEPLOY_COMMAND_TIMEOUT_SECONDS" \
    git -C "$APP_DIR" reset --hard origin/main
fi

if [[ -z "$RUN_WORKER_ID" ]]; then
  RUN_WORKER_ID="$(hostname -f 2>/dev/null || hostname)"
fi

cat > /etc/cloud-agents-worker.env <<EOF
RUN_WORKER_CONTROL_URL=$RUN_WORKER_CONTROL_URL
RUN_WORKER_TOKEN=$RUN_WORKER_TOKEN
RUN_WORKER_ID=$RUN_WORKER_ID
RUN_WORKER_CAPACITY=$RUN_WORKER_CAPACITY
RUN_WORKER_LEASE_TTL_SECONDS=$RUN_WORKER_LEASE_TTL_SECONDS
RUN_WORKER_POLL_INTERVAL_SECONDS=$RUN_WORKER_POLL_INTERVAL_SECONDS
RUN_WORKER_HEARTBEAT_INTERVAL_SECONDS=$RUN_WORKER_HEARTBEAT_INTERVAL_SECONDS
RUN_WORKER_RUN_WAIT_TIMEOUT_SECONDS=$RUN_WORKER_RUN_WAIT_TIMEOUT_SECONDS
RUN_WORKER_ARTIFACT_ROOT=$STATE_DIR/artifacts
RUN_WORKER_METADATA_JSON=$RUN_WORKER_METADATA_JSON
QWEN_SERVE_URL=$QWEN_SERVE_URL
QWEN_SERVE_TOKEN=$QWEN_SERVE_TOKEN
EOF
chmod 600 /etc/cloud-agents-worker.env

cp "$APP_DIR/deploy/systemd/cloud-agents-worker.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now cloud-agents-worker
systemctl restart cloud-agents-worker
sleep 3
if ! systemctl --no-pager --full status cloud-agents-worker; then
  journalctl -u cloud-agents-worker -n 120 --no-pager || true
  exit 3
fi

echo "worker $RUN_WORKER_ID registered through $RUN_WORKER_CONTROL_URL"
REMOTE
