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
BASIC_AUTH_PASSWORD="${BASIC_AUTH_PASSWORD:-$(openssl rand -base64 18 | tr -d '=+/' | cut -c1-18)}"
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
RUNTIME_CPU_QUOTA="${RUNTIME_CPU_QUOTA:-100%}"
RUNTIME_MEMORY_MAX="${RUNTIME_MEMORY_MAX:-1G}"
RUNTIME_TASKS_MAX="${RUNTIME_TASKS_MAX:-512}"
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

ssh_cmd() {
  ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "$SSH_TARGET" "$@"
}

if [[ -n "$QWEN_SETTINGS_FILE" ]]; then
  scp -i "$SSH_KEY" \
    -o StrictHostKeyChecking=accept-new \
    "$QWEN_SETTINGS_FILE" \
    "$SSH_TARGET:/tmp/qwen-settings.json"
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
append_remote_env DEPLOY_RUNTIME_PRINT_SECRETS "$DEPLOY_RUNTIME_PRINT_SECRETS"

ssh_cmd "${REMOTE_ENV[*]} bash -s" <<'REMOTE'
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

if ! command -v git >/dev/null \
  || ! command -v python3 >/dev/null \
  || ! command -v npm >/dev/null \
  || ! command -v nginx >/dev/null; then
  apt-get update
  apt-get install -y git python3 npm nginx
fi

npm install -g "$NODE_PACKAGE"

if ! id cloudagents >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin cloudagents
fi

mkdir -p "$APP_DIR" "$STATE_DIR/artifacts" "$STATE_DIR/workspace"
install -d -m 700 -o cloudagents -g cloudagents /home/cloudagents/.qwen
if [[ "$HAS_QWEN_SETTINGS" == "1" ]]; then
  install -m 600 -o cloudagents -g cloudagents \
    /tmp/qwen-settings.json \
    /home/cloudagents/.qwen/settings.json
  rm -f /tmp/qwen-settings.json
fi

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
QWEN_SERVER_TOKEN=$QWEN_SERVER_TOKEN
QWEN_SERVE_URL=http://127.0.0.1:4170
QWEN_SERVE_TOKEN=$QWEN_SERVER_TOKEN
QWEN_SERVE_COMMAND=$QWEN_COMMAND
QWEN_SERVE_CWD=$STATE_DIR/workspace
QWEN_SERVE_STARTUP_TIMEOUT=30
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
HASH="$(openssl passwd -apr1 "$BASIC_AUTH_PASSWORD")"
printf '%s:%s\n' "$BASIC_AUTH_USER" "$HASH" > /etc/nginx/cloud-agents.htpasswd
chown root:www-data /etc/nginx/cloud-agents.htpasswd
chmod 640 /etc/nginx/cloud-agents.htpasswd
cat > /etc/nginx/snippets/cloud-agents-runtime-auth.conf <<EOF
proxy_set_header Authorization "Bearer $RUN_MANAGER_TOKEN";
EOF
chmod 640 /etc/nginx/snippets/cloud-agents-runtime-auth.conf
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
    echo "PUBLIC_DOMAIN=$PUBLIC_DOMAIN has no local letsencrypt certificate; skipping HTTPS block" >&2
  fi
fi
nginx -t
systemctl reload nginx

systemctl daemon-reload
systemctl enable --now cloud-agents-runtime
systemctl restart cloud-agents-runtime
sleep 3
systemctl --no-pager --full status cloud-agents-runtime

if [[ "$DEPLOY_RUNTIME_PRINT_SECRETS" == "1" ]]; then
  echo "RUN_MANAGER_TOKEN=$RUN_MANAGER_TOKEN"
  echo "BASIC_AUTH_USER=$BASIC_AUTH_USER"
  echo "BASIC_AUTH_PASSWORD=$BASIC_AUTH_PASSWORD"
else
  echo "credentials written to /etc/cloud-agents-runtime.env and nginx basic auth files"
fi
REMOTE
