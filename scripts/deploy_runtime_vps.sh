#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <ssh-target> <ssh-key>" >&2
  exit 2
fi

SSH_TARGET="$1"
SSH_KEY="$2"
APP_DIR="${APP_DIR:-/opt/agent-research}"
STATE_DIR="${STATE_DIR:-/var/lib/cloud-agents-runtime}"
REPO_URL="${REPO_URL:-https://github.com/chiga0/agent-research.git}"
NODE_PACKAGE="${NODE_PACKAGE:-@qwen-code/qwen-code@0.19.3}"

ssh_cmd() {
  ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "$SSH_TARGET" "$@"
}

REMOTE_ENV=(
  "APP_DIR='$APP_DIR'"
  "STATE_DIR='$STATE_DIR'"
  "REPO_URL='$REPO_URL'"
  "NODE_PACKAGE='$NODE_PACKAGE'"
)

ssh_cmd "${REMOTE_ENV[*]} bash -s" <<'REMOTE'
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

if ! command -v git >/dev/null \
  || ! command -v python3 >/dev/null \
  || ! command -v npm >/dev/null; then
  apt-get update
  apt-get install -y git python3 npm
fi

npm install -g "$NODE_PACKAGE"

if ! id cloudagents >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin cloudagents
fi

mkdir -p "$APP_DIR" "$STATE_DIR/artifacts" "$STATE_DIR/workspace"
if [[ ! -d "$APP_DIR/.git" ]]; then
  git clone "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" fetch origin main
  git -C "$APP_DIR" reset --hard origin/main
fi

RUN_MANAGER_TOKEN="$(openssl rand -hex 32)"
QWEN_SERVER_TOKEN="$(openssl rand -hex 32)"
QWEN_COMMAND="qwen serve --hostname 127.0.0.1 --port 4170"
QWEN_COMMAND="$QWEN_COMMAND --workspace $STATE_DIR/workspace --no-web --require-auth"

cat > /etc/cloud-agents-runtime.env <<EOF
RUN_MANAGER_TOKEN=$RUN_MANAGER_TOKEN
QWEN_SERVER_TOKEN=$QWEN_SERVER_TOKEN
QWEN_SERVE_URL=http://127.0.0.1:4170
QWEN_SERVE_TOKEN=$QWEN_SERVER_TOKEN
QWEN_SERVE_COMMAND=$QWEN_COMMAND
QWEN_SERVE_CWD=$STATE_DIR/workspace
QWEN_SERVE_STARTUP_TIMEOUT=30
EOF

cp "$APP_DIR/deploy/systemd/cloud-agents-runtime.service" /etc/systemd/system/
chown -R cloudagents:cloudagents "$STATE_DIR"
chmod 600 /etc/cloud-agents-runtime.env

systemctl daemon-reload
systemctl enable --now cloud-agents-runtime
systemctl restart cloud-agents-runtime
sleep 3
systemctl --no-pager --full status cloud-agents-runtime

echo "RUN_MANAGER_TOKEN=$RUN_MANAGER_TOKEN"
REMOTE
