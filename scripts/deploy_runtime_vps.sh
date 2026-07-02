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
QWEN_SETTINGS_FILE="${QWEN_SETTINGS_FILE:-}"
PUBLIC_HOST="${PUBLIC_HOST:-_}"
PUBLIC_DOMAIN="${PUBLIC_DOMAIN:-}"
BASIC_AUTH_USER="${BASIC_AUTH_USER:-cloudagents}"
BASIC_AUTH_PASSWORD="${BASIC_AUTH_PASSWORD:-}"
BASIC_AUTH_FORCE_ROTATE="${BASIC_AUTH_FORCE_ROTATE:-0}"
RUN_MANAGER_DEFAULT_CPUS="${RUN_MANAGER_DEFAULT_CPUS:-1.0}"
RUN_MANAGER_MAX_CPUS="${RUN_MANAGER_MAX_CPUS:-$RUN_MANAGER_DEFAULT_CPUS}"
RUN_MANAGER_DEFAULT_MEMORY_MB="${RUN_MANAGER_DEFAULT_MEMORY_MB:-1024}"
RUN_MANAGER_MAX_MEMORY_MB="${RUN_MANAGER_MAX_MEMORY_MB:-$RUN_MANAGER_DEFAULT_MEMORY_MB}"
RUN_MANAGER_DEFAULT_PIDS="${RUN_MANAGER_DEFAULT_PIDS:-512}"
RUN_MANAGER_MAX_PIDS="${RUN_MANAGER_MAX_PIDS:-$RUN_MANAGER_DEFAULT_PIDS}"
RUN_MANAGER_DEFAULT_TIMEOUT_SECONDS="${RUN_MANAGER_DEFAULT_TIMEOUT_SECONDS:-3600}"
if [[ -z "${RUN_MANAGER_MAX_TIMEOUT_SECONDS:-}" ]]; then
  RUN_MANAGER_MAX_TIMEOUT_SECONDS="$RUN_MANAGER_DEFAULT_TIMEOUT_SECONDS"
fi
RUN_MANAGER_CLEANUP_ENABLED="${RUN_MANAGER_CLEANUP_ENABLED:-1}"
RUN_MANAGER_WORKSPACE_RETENTION_SECONDS="${RUN_MANAGER_WORKSPACE_RETENTION_SECONDS:-604800}"
RUN_MANAGER_ARTIFACT_RETENTION_SECONDS="${RUN_MANAGER_ARTIFACT_RETENTION_SECONDS:-2592000}"
RUN_MANAGER_CLEANUP_INTERVAL_SECONDS="${RUN_MANAGER_CLEANUP_INTERVAL_SECONDS:-3600}"
RUN_MANAGER_PERMISSION_STALL_SECONDS="${RUN_MANAGER_PERMISSION_STALL_SECONDS:-300}"
RUN_MANAGER_PERMISSION_STALL_ACTION="${RUN_MANAGER_PERMISSION_STALL_ACTION:-audit}"
RUN_MANAGER_STALE_WORKER_SECONDS="${RUN_MANAGER_STALE_WORKER_SECONDS:-300}"
RUN_MANAGER_BACKUP_RETENTION_COUNT="${RUN_MANAGER_BACKUP_RETENTION_COUNT:-10}"
QWEN_EXECUTOR_STRATEGY="${QWEN_EXECUTOR_STRATEGY:-shared}"
QWEN_EXECUTOR_HOST="${QWEN_EXECUTOR_HOST:-127.0.0.1}"
QWEN_EXECUTOR_PORT_START="${QWEN_EXECUTOR_PORT_START:-4210}"
QWEN_EXECUTOR_PORT_END="${QWEN_EXECUTOR_PORT_END:-4310}"
QWEN_EXECUTOR_COMMAND="${QWEN_EXECUTOR_COMMAND:-}"
QWEN_EXECUTOR_STARTUP_TIMEOUT="${QWEN_EXECUTOR_STARTUP_TIMEOUT:-20}"
QWEN_EXECUTOR_STOP_TIMEOUT="${QWEN_EXECUTOR_STOP_TIMEOUT:-5}"
QWEN_CONTAINER_COMMAND="${QWEN_CONTAINER_COMMAND:-}"
QWEN_CONTAINER_IMAGE="${QWEN_CONTAINER_IMAGE:-}"
QWEN_CONTAINER_NETWORK="${QWEN_CONTAINER_NETWORK:-bridge}"
QWEN_CONTAINER_CPUS="${QWEN_CONTAINER_CPUS:-1}"
QWEN_CONTAINER_MEMORY_MB="${QWEN_CONTAINER_MEMORY_MB:-1024}"
QWEN_CONTAINER_PIDS="${QWEN_CONTAINER_PIDS:-256}"
QWEN_CONTAINER_EXTRA_ARGS="${QWEN_CONTAINER_EXTRA_ARGS:-}"
QWEN_CONTAINER_BUILD="${QWEN_CONTAINER_BUILD:-0}"
case "${QWEN_CONTAINER_BUILD,,}" in
  1 | true | yes)
    QWEN_CONTAINER_BUILD=1
    ;;
  *)
    QWEN_CONTAINER_BUILD=0
    ;;
esac
QWEN_CONTAINER_BASE_IMAGE="${QWEN_CONTAINER_BASE_IMAGE:-node:22-bookworm-slim}"
QWEN_CONTAINER_NODE_PACKAGE="${QWEN_CONTAINER_NODE_PACKAGE:-$NODE_PACKAGE}"
if [[ "$QWEN_EXECUTOR_STRATEGY" == "container" \
  && "$QWEN_CONTAINER_BUILD" == "1" \
  && -z "$QWEN_CONTAINER_IMAGE" ]]; then
  QWEN_CONTAINER_IMAGE="cloud-agents-qwen:local"
fi
RUNTIME_CPU_QUOTA="${RUNTIME_CPU_QUOTA:-100%}"
RUNTIME_MEMORY_MAX="${RUNTIME_MEMORY_MAX:-1G}"
RUNTIME_TASKS_MAX="${RUNTIME_TASKS_MAX:-512}"
DEPLOY_SSH_SERVER_ALIVE_INTERVAL="${DEPLOY_SSH_SERVER_ALIVE_INTERVAL:-30}"
DEPLOY_SSH_SERVER_ALIVE_COUNT_MAX="${DEPLOY_SSH_SERVER_ALIVE_COUNT_MAX:-60}"
DEPLOY_SSH_CONNECT_TIMEOUT_SECONDS="${DEPLOY_SSH_CONNECT_TIMEOUT_SECONDS:-30}"
DEPLOY_SCP_ATTEMPTS="${DEPLOY_SCP_ATTEMPTS:-4}"
DEPLOY_SCP_RETRY_DELAY_SECONDS="${DEPLOY_SCP_RETRY_DELAY_SECONDS:-10}"
DEPLOY_COMMAND_TIMEOUT_SECONDS="${DEPLOY_COMMAND_TIMEOUT_SECONDS:-900}"
DEPLOY_DOCKER_BUILD_TIMEOUT_SECONDS="${DEPLOY_DOCKER_BUILD_TIMEOUT_SECONDS:-1800}"
DEPLOY_RUNTIME_PRINT_SECRETS="${DEPLOY_RUNTIME_PRINT_SECRETS:-1}"

case "$PUBLIC_HOST" in
  *[!A-Za-z0-9._-]*)
    echo "PUBLIC_HOST may only contain letters, numbers, dots, underscores, or hyphens" >&2
    exit 2
    ;;
esac

case "$PUBLIC_DOMAIN" in
  *[!A-Za-z0-9._-]*)
    echo "PUBLIC_DOMAIN may only contain letters, numbers, dots, underscores, or hyphens" >&2
    exit 2
    ;;
esac

case "$BASIC_AUTH_USER" in
  *[!A-Za-z0-9._-]* | "")
    echo "BASIC_AUTH_USER may only contain letters, numbers, dots, underscores, or hyphens" >&2
    exit 2
    ;;
esac

shell_quote() {
  printf "%q" "$1"
}

append_remote_env() {
  REMOTE_ENV+=("$1=$(shell_quote "$2")")
}

SSH_OPTIONS=(
  -i "$SSH_KEY"
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout="$DEPLOY_SSH_CONNECT_TIMEOUT_SECONDS"
  -o ConnectionAttempts=1
  -o ServerAliveInterval="$DEPLOY_SSH_SERVER_ALIVE_INTERVAL"
  -o ServerAliveCountMax="$DEPLOY_SSH_SERVER_ALIVE_COUNT_MAX"
  -o TCPKeepAlive=yes
)

ssh_cmd() {
  ssh "${SSH_OPTIONS[@]}" "$SSH_TARGET" "$@"
}

scp_qwen_settings() {
  local attempt=1
  local exit_code=0
  if [[ ! -f "$QWEN_SETTINGS_FILE" ]]; then
    echo "QWEN_SETTINGS_FILE does not exist: $QWEN_SETTINGS_FILE" >&2
    exit 2
  fi
  while (( attempt <= DEPLOY_SCP_ATTEMPTS )); do
    printf '[deploy-local] upload qwen settings attempt %s/%s\n' \
      "$attempt" \
      "$DEPLOY_SCP_ATTEMPTS"
    if scp "${SSH_OPTIONS[@]}" \
      "$QWEN_SETTINGS_FILE" \
      "$SSH_TARGET:/tmp/qwen-settings.json"; then
      return 0
    fi
    exit_code=$?
    if (( attempt == DEPLOY_SCP_ATTEMPTS )); then
      return "$exit_code"
    fi
    sleep "$DEPLOY_SCP_RETRY_DELAY_SECONDS"
    attempt=$((attempt + 1))
  done
}

if [[ -n "$QWEN_SETTINGS_FILE" ]]; then
  scp_qwen_settings
fi

REMOTE_ENV=(
  "APP_DIR=$(shell_quote "$APP_DIR")"
  "STATE_DIR=$(shell_quote "$STATE_DIR")"
  "REPO_URL=$(shell_quote "$REPO_URL")"
  "NODE_PACKAGE=$(shell_quote "$NODE_PACKAGE")"
  "HAS_QWEN_SETTINGS=$(shell_quote "$([[ -n "$QWEN_SETTINGS_FILE" ]] && echo 1 || echo 0)")"
  "PUBLIC_HOST=$(shell_quote "$PUBLIC_HOST")"
  "PUBLIC_DOMAIN=$(shell_quote "$PUBLIC_DOMAIN")"
  "BASIC_AUTH_USER=$(shell_quote "$BASIC_AUTH_USER")"
  "BASIC_AUTH_PASSWORD=$(shell_quote "$BASIC_AUTH_PASSWORD")"
  "RUN_MANAGER_DEFAULT_CPUS=$(shell_quote "$RUN_MANAGER_DEFAULT_CPUS")"
  "RUN_MANAGER_MAX_CPUS=$(shell_quote "$RUN_MANAGER_MAX_CPUS")"
  "RUN_MANAGER_DEFAULT_MEMORY_MB=$(shell_quote "$RUN_MANAGER_DEFAULT_MEMORY_MB")"
  "RUN_MANAGER_MAX_MEMORY_MB=$(shell_quote "$RUN_MANAGER_MAX_MEMORY_MB")"
  "RUN_MANAGER_DEFAULT_PIDS=$(shell_quote "$RUN_MANAGER_DEFAULT_PIDS")"
  "RUN_MANAGER_MAX_PIDS=$(shell_quote "$RUN_MANAGER_MAX_PIDS")"
  "RUN_MANAGER_DEFAULT_TIMEOUT_SECONDS=$(shell_quote "$RUN_MANAGER_DEFAULT_TIMEOUT_SECONDS")"
  "RUN_MANAGER_MAX_TIMEOUT_SECONDS=$(shell_quote "$RUN_MANAGER_MAX_TIMEOUT_SECONDS")"
  "RUNTIME_CPU_QUOTA=$(shell_quote "$RUNTIME_CPU_QUOTA")"
  "RUNTIME_MEMORY_MAX=$(shell_quote "$RUNTIME_MEMORY_MAX")"
  "RUNTIME_TASKS_MAX=$(shell_quote "$RUNTIME_TASKS_MAX")"
)
append_remote_env RUN_MANAGER_CLEANUP_ENABLED "$RUN_MANAGER_CLEANUP_ENABLED"
append_remote_env \
  RUN_MANAGER_WORKSPACE_RETENTION_SECONDS \
  "$RUN_MANAGER_WORKSPACE_RETENTION_SECONDS"
append_remote_env \
  RUN_MANAGER_ARTIFACT_RETENTION_SECONDS \
  "$RUN_MANAGER_ARTIFACT_RETENTION_SECONDS"
append_remote_env \
  RUN_MANAGER_CLEANUP_INTERVAL_SECONDS \
  "$RUN_MANAGER_CLEANUP_INTERVAL_SECONDS"
append_remote_env \
  RUN_MANAGER_PERMISSION_STALL_SECONDS \
  "$RUN_MANAGER_PERMISSION_STALL_SECONDS"
append_remote_env \
  RUN_MANAGER_PERMISSION_STALL_ACTION \
  "$RUN_MANAGER_PERMISSION_STALL_ACTION"
append_remote_env \
  RUN_MANAGER_STALE_WORKER_SECONDS \
  "$RUN_MANAGER_STALE_WORKER_SECONDS"
append_remote_env \
  RUN_MANAGER_BACKUP_RETENTION_COUNT \
  "$RUN_MANAGER_BACKUP_RETENTION_COUNT"
append_remote_env QWEN_EXECUTOR_STRATEGY "$QWEN_EXECUTOR_STRATEGY"
append_remote_env QWEN_EXECUTOR_HOST "$QWEN_EXECUTOR_HOST"
append_remote_env QWEN_EXECUTOR_PORT_START "$QWEN_EXECUTOR_PORT_START"
append_remote_env QWEN_EXECUTOR_PORT_END "$QWEN_EXECUTOR_PORT_END"
append_remote_env QWEN_EXECUTOR_COMMAND "$QWEN_EXECUTOR_COMMAND"
append_remote_env QWEN_EXECUTOR_STARTUP_TIMEOUT "$QWEN_EXECUTOR_STARTUP_TIMEOUT"
append_remote_env QWEN_EXECUTOR_STOP_TIMEOUT "$QWEN_EXECUTOR_STOP_TIMEOUT"
append_remote_env QWEN_CONTAINER_COMMAND "$QWEN_CONTAINER_COMMAND"
append_remote_env QWEN_CONTAINER_IMAGE "$QWEN_CONTAINER_IMAGE"
append_remote_env QWEN_CONTAINER_NETWORK "$QWEN_CONTAINER_NETWORK"
append_remote_env QWEN_CONTAINER_CPUS "$QWEN_CONTAINER_CPUS"
append_remote_env QWEN_CONTAINER_MEMORY_MB "$QWEN_CONTAINER_MEMORY_MB"
append_remote_env QWEN_CONTAINER_PIDS "$QWEN_CONTAINER_PIDS"
append_remote_env QWEN_CONTAINER_EXTRA_ARGS "$QWEN_CONTAINER_EXTRA_ARGS"
append_remote_env QWEN_CONTAINER_BUILD "$QWEN_CONTAINER_BUILD"
append_remote_env QWEN_CONTAINER_BASE_IMAGE "$QWEN_CONTAINER_BASE_IMAGE"
append_remote_env QWEN_CONTAINER_NODE_PACKAGE "$QWEN_CONTAINER_NODE_PACKAGE"
append_remote_env DEPLOY_RUNTIME_PRINT_SECRETS "$DEPLOY_RUNTIME_PRINT_SECRETS"
append_remote_env BASIC_AUTH_FORCE_ROTATE "$BASIC_AUTH_FORCE_ROTATE"
append_remote_env DEPLOY_COMMAND_TIMEOUT_SECONDS "$DEPLOY_COMMAND_TIMEOUT_SECONDS"
append_remote_env DEPLOY_DOCKER_BUILD_TIMEOUT_SECONDS "$DEPLOY_DOCKER_BUILD_TIMEOUT_SECONDS"

ssh_cmd "${REMOTE_ENV[*]} bash -s" <<'REMOTE'
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

log_step() {
  printf '[deploy] %s\n' "$*"
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
  || ! command -v npm >/dev/null \
  || ! command -v nginx >/dev/null; then
  run_timeout "apt-get update" "$DEPLOY_COMMAND_TIMEOUT_SECONDS" apt-get update
  run_timeout \
    "install host packages" \
    "$DEPLOY_COMMAND_TIMEOUT_SECONDS" \
    apt-get install -y git python3 npm nginx
fi

run_timeout \
  "install node package $NODE_PACKAGE" \
  "$DEPLOY_COMMAND_TIMEOUT_SECONDS" \
  npm install -g "$NODE_PACKAGE"

if [[ "$QWEN_EXECUTOR_STRATEGY" == "container" ]]; then
  if ! command -v docker >/dev/null; then
    run_timeout "apt-get update for docker" "$DEPLOY_COMMAND_TIMEOUT_SECONDS" apt-get update
    run_timeout \
      "install docker" \
      "$DEPLOY_COMMAND_TIMEOUT_SECONDS" \
      apt-get install -y docker.io
  fi
  log_step "enable docker service"
  systemctl enable --now docker
fi

if ! id cloudagents >/dev/null 2>&1; then
  log_step "create cloudagents user"
  useradd --system --create-home --shell /usr/sbin/nologin cloudagents
fi
if [[ "$QWEN_EXECUTOR_STRATEGY" == "container" ]]; then
  usermod -aG docker cloudagents
  CLOUDAGENTS_UID="$(id -u cloudagents)"
  CLOUDAGENTS_GID="$(id -g cloudagents)"
  RUN_AS_ARGS="--user $CLOUDAGENTS_UID:$CLOUDAGENTS_GID -e HOME=/home/cloudagents"
  QWEN_CONTAINER_EXTRA_ARGS="$QWEN_CONTAINER_EXTRA_ARGS $RUN_AS_ARGS"
  QWEN_CONTAINER_EXTRA_ARGS="${QWEN_CONTAINER_EXTRA_ARGS# }"
fi

mkdir -p "$APP_DIR" "$STATE_DIR/artifacts" "$STATE_DIR/workspace"
install -d -m 700 -o cloudagents -g cloudagents /home/cloudagents/.qwen
if [[ "$HAS_QWEN_SETTINGS" == "1" ]]; then
  install -m 600 -o cloudagents -g cloudagents \
    /tmp/qwen-settings.json \
    /home/cloudagents/.qwen/settings.json
  rm -f /tmp/qwen-settings.json
fi
if [[ "$QWEN_EXECUTOR_STRATEGY" == "container" && "$HAS_QWEN_SETTINGS" == "1" ]]; then
  SETTINGS_MOUNTS=(
    "-v /home/cloudagents/.qwen/settings.json:/root/.qwen/settings.json:ro"
    "-v /home/cloudagents/.qwen/settings.json:/home/cloudagents/.qwen/settings.json:ro"
  )
  QWEN_CONTAINER_EXTRA_ARGS="$QWEN_CONTAINER_EXTRA_ARGS ${SETTINGS_MOUNTS[*]}"
  QWEN_CONTAINER_EXTRA_ARGS="${QWEN_CONTAINER_EXTRA_ARGS# }"
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
if [[ "$QWEN_EXECUTOR_STRATEGY" == "container" ]]; then
  if [[ "$QWEN_CONTAINER_BUILD" == "1" ]]; then
    run_timeout \
      "build qwen executor image $QWEN_CONTAINER_IMAGE" \
      "$DEPLOY_DOCKER_BUILD_TIMEOUT_SECONDS" \
      docker build \
      --build-arg "BASE_IMAGE=$QWEN_CONTAINER_BASE_IMAGE" \
      --build-arg "NODE_PACKAGE=$QWEN_CONTAINER_NODE_PACKAGE" \
      -t "$QWEN_CONTAINER_IMAGE" \
      -f "$APP_DIR/deploy/Dockerfile.qwen-executor" \
      "$APP_DIR"
  elif [[ -n "$QWEN_CONTAINER_IMAGE" ]]; then
    run_timeout \
      "pull qwen executor image $QWEN_CONTAINER_IMAGE" \
      "$DEPLOY_DOCKER_BUILD_TIMEOUT_SECONDS" \
      docker pull "$QWEN_CONTAINER_IMAGE"
  elif [[ -z "$QWEN_CONTAINER_COMMAND" ]]; then
    echo "container executor requires QWEN_CONTAINER_IMAGE or QWEN_CONTAINER_COMMAND" >&2
    exit 2
  fi
fi

RUN_MANAGER_TOKEN="$(openssl rand -hex 32)"
QWEN_SERVER_TOKEN="$(openssl rand -hex 32)"
QWEN_COMMAND="qwen serve --hostname 127.0.0.1 --port 4170"
QWEN_COMMAND="$QWEN_COMMAND --workspace $STATE_DIR/workspace --no-web --require-auth"

cat > /etc/cloud-agents-runtime.env <<EOF
RUN_MANAGER_TOKEN=$RUN_MANAGER_TOKEN
RUN_MANAGER_WORKER_ID=cloud-agents-runtime
RUN_MANAGER_WORKER_CAPACITY=1
RUN_MANAGER_LEASE_TTL_SECONDS=60
RUN_MANAGER_DEFAULT_CPUS=$RUN_MANAGER_DEFAULT_CPUS
RUN_MANAGER_MAX_CPUS=$RUN_MANAGER_MAX_CPUS
RUN_MANAGER_DEFAULT_MEMORY_MB=$RUN_MANAGER_DEFAULT_MEMORY_MB
RUN_MANAGER_MAX_MEMORY_MB=$RUN_MANAGER_MAX_MEMORY_MB
RUN_MANAGER_DEFAULT_PIDS=$RUN_MANAGER_DEFAULT_PIDS
RUN_MANAGER_MAX_PIDS=$RUN_MANAGER_MAX_PIDS
RUN_MANAGER_DEFAULT_TIMEOUT_SECONDS=$RUN_MANAGER_DEFAULT_TIMEOUT_SECONDS
RUN_MANAGER_MAX_TIMEOUT_SECONDS=$RUN_MANAGER_MAX_TIMEOUT_SECONDS
RUN_MANAGER_CLEANUP_ENABLED=$RUN_MANAGER_CLEANUP_ENABLED
RUN_MANAGER_WORKSPACE_RETENTION_SECONDS=$RUN_MANAGER_WORKSPACE_RETENTION_SECONDS
RUN_MANAGER_ARTIFACT_RETENTION_SECONDS=$RUN_MANAGER_ARTIFACT_RETENTION_SECONDS
RUN_MANAGER_CLEANUP_INTERVAL_SECONDS=$RUN_MANAGER_CLEANUP_INTERVAL_SECONDS
RUN_MANAGER_PERMISSION_STALL_SECONDS=$RUN_MANAGER_PERMISSION_STALL_SECONDS
RUN_MANAGER_PERMISSION_STALL_ACTION=$RUN_MANAGER_PERMISSION_STALL_ACTION
RUN_MANAGER_STALE_WORKER_SECONDS=$RUN_MANAGER_STALE_WORKER_SECONDS
RUN_MANAGER_BACKUP_RETENTION_COUNT=$RUN_MANAGER_BACKUP_RETENTION_COUNT
QWEN_SERVER_TOKEN=$QWEN_SERVER_TOKEN
QWEN_SERVE_URL=http://127.0.0.1:4170
QWEN_SERVE_TOKEN=$QWEN_SERVER_TOKEN
QWEN_SERVE_COMMAND=$QWEN_COMMAND
QWEN_SERVE_CWD=$STATE_DIR/workspace
QWEN_SERVE_STARTUP_TIMEOUT=30
QWEN_EXECUTOR_STRATEGY=$QWEN_EXECUTOR_STRATEGY
QWEN_EXECUTOR_HOST=$QWEN_EXECUTOR_HOST
QWEN_EXECUTOR_PORT_START=$QWEN_EXECUTOR_PORT_START
QWEN_EXECUTOR_PORT_END=$QWEN_EXECUTOR_PORT_END
QWEN_EXECUTOR_COMMAND=$QWEN_EXECUTOR_COMMAND
QWEN_EXECUTOR_STARTUP_TIMEOUT=$QWEN_EXECUTOR_STARTUP_TIMEOUT
QWEN_EXECUTOR_STOP_TIMEOUT=$QWEN_EXECUTOR_STOP_TIMEOUT
QWEN_CONTAINER_COMMAND=$QWEN_CONTAINER_COMMAND
QWEN_CONTAINER_IMAGE=$QWEN_CONTAINER_IMAGE
QWEN_CONTAINER_NETWORK=$QWEN_CONTAINER_NETWORK
QWEN_CONTAINER_CPUS=$QWEN_CONTAINER_CPUS
QWEN_CONTAINER_MEMORY_MB=$QWEN_CONTAINER_MEMORY_MB
QWEN_CONTAINER_PIDS=$QWEN_CONTAINER_PIDS
QWEN_CONTAINER_EXTRA_ARGS=$QWEN_CONTAINER_EXTRA_ARGS
QWEN_CONTAINER_BUILD=$QWEN_CONTAINER_BUILD
QWEN_CONTAINER_BASE_IMAGE=$QWEN_CONTAINER_BASE_IMAGE
QWEN_CONTAINER_NODE_PACKAGE=$QWEN_CONTAINER_NODE_PACKAGE
EOF

cp "$APP_DIR/deploy/systemd/cloud-agents-runtime.service" /etc/systemd/system/
install -d -m 755 /etc/systemd/system/cloud-agents-runtime.service.d
cat > /etc/systemd/system/cloud-agents-runtime.service.d/resource-limits.conf <<EOF
[Service]
CPUQuota=$RUNTIME_CPU_QUOTA
MemoryMax=$RUNTIME_MEMORY_MAX
TasksMax=$RUNTIME_TASKS_MAX
EOF
chown -R cloudagents:cloudagents "$STATE_DIR"
chmod 600 /etc/cloud-agents-runtime.env

install -d -m 755 /etc/nginx/snippets
if [[ "$BASIC_AUTH_FORCE_ROTATE" != "1" \
  && -f /etc/cloud-agents-runtime.preserve-basic-auth \
  && -f /etc/nginx/cloud-agents.htpasswd ]]; then
  echo "preserving existing nginx basic auth password"
elif [[ -n "$BASIC_AUTH_PASSWORD" ]]; then
  HASH="$(openssl passwd -apr1 "$BASIC_AUTH_PASSWORD")"
  printf '%s:%s\n' "$BASIC_AUTH_USER" "$HASH" > /etc/nginx/cloud-agents.htpasswd
  chown root:www-data /etc/nginx/cloud-agents.htpasswd
  chmod 640 /etc/nginx/cloud-agents.htpasswd
  touch /etc/cloud-agents-runtime.preserve-basic-auth
  chmod 600 /etc/cloud-agents-runtime.preserve-basic-auth
elif [[ ! -f /etc/nginx/cloud-agents.htpasswd ]]; then
  BASIC_AUTH_PASSWORD="$(openssl rand -base64 18 | tr -d '=+/' | cut -c1-18)"
  HASH="$(openssl passwd -apr1 "$BASIC_AUTH_PASSWORD")"
  printf '%s:%s\n' "$BASIC_AUTH_USER" "$HASH" > /etc/nginx/cloud-agents.htpasswd
  chown root:www-data /etc/nginx/cloud-agents.htpasswd
  chmod 640 /etc/nginx/cloud-agents.htpasswd
  touch /etc/cloud-agents-runtime.preserve-basic-auth
  chmod 600 /etc/cloud-agents-runtime.preserve-basic-auth
else
  echo "preserving existing nginx basic auth password"
fi
cat > /etc/nginx/snippets/cloud-agents-runtime-auth.conf <<EOF
proxy_set_header Authorization "Bearer $RUN_MANAGER_TOKEN";
EOF
chmod 640 /etc/nginx/snippets/cloud-agents-runtime-auth.conf

if [[ -z "$PUBLIC_DOMAIN" && -f /etc/cloud-agents-runtime.public-domain ]]; then
  PUBLIC_DOMAIN="$(cat /etc/cloud-agents-runtime.public-domain)"
  echo "preserving existing PUBLIC_DOMAIN=$PUBLIC_DOMAIN"
fi
if [[ -n "$PUBLIC_DOMAIN" ]]; then
  printf '%s\n' "$PUBLIC_DOMAIN" > /etc/cloud-agents-runtime.public-domain
  chmod 600 /etc/cloud-agents-runtime.public-domain
fi

sed "s/__PUBLIC_HOST__/$PUBLIC_HOST/g" \
  "$APP_DIR/deploy/nginx/cloud-agents-runtime.conf.example" \
  > /etc/nginx/conf.d/cloud-agents-runtime.conf
if [[ -n "$PUBLIC_DOMAIN" ]]; then
  if [[ -f "/etc/letsencrypt/live/$PUBLIC_DOMAIN/fullchain.pem" \
    && -f "/etc/letsencrypt/live/$PUBLIC_DOMAIN/privkey.pem" ]]; then
    cat >> /etc/nginx/conf.d/cloud-agents-runtime.conf <<EOF

server {
    listen 80;
    listen [::]:80;
    server_name $PUBLIC_DOMAIN;

    location ^~ /.well-known/acme-challenge/ {
        root /var/www/letsencrypt;
        default_type "text/plain";
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name $PUBLIC_DOMAIN;

    ssl_certificate /etc/letsencrypt/live/$PUBLIC_DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$PUBLIC_DOMAIN/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    location = /cloud-agents {
        return 302 /cloud-agents/;
    }

    location /cloud-agents/ {
        auth_basic "Cloud Agents Runtime";
        auth_basic_user_file /etc/nginx/cloud-agents.htpasswd;

        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Remote-User \$remote_user;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        include /etc/nginx/snippets/cloud-agents-runtime-auth.conf;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        rewrite ^/cloud-agents/(.*)\$ /\$1 break;
        proxy_pass http://127.0.0.1:8765;
    }

    location / {
        return 302 /cloud-agents/;
    }
}
EOF
  else
    echo \
      "PUBLIC_DOMAIN=$PUBLIC_DOMAIN has no local letsencrypt certificate; skipping HTTPS block" \
      >&2
  fi
fi
nginx -t
systemctl reload nginx

systemctl daemon-reload
systemctl enable --now cloud-agents-runtime
systemctl restart cloud-agents-runtime
sleep 3
if ! systemctl --no-pager --full status cloud-agents-runtime; then
  journalctl -u cloud-agents-runtime -n 120 --no-pager || true
  exit 3
fi

if [[ "$DEPLOY_RUNTIME_PRINT_SECRETS" == "1" ]]; then
  echo "RUN_MANAGER_TOKEN=$RUN_MANAGER_TOKEN"
  echo "BASIC_AUTH_USER=$BASIC_AUTH_USER"
  echo "BASIC_AUTH_PASSWORD=$BASIC_AUTH_PASSWORD"
else
  echo "credentials written to /etc/cloud-agents-runtime.env and nginx basic auth files"
fi
REMOTE
