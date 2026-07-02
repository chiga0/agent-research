# Cloud Agents 操作手册

> 适用范围：当前单租户 beta 版 Cloud Agents Runtime。它适合单控制面、1 台主 VPS、1-2 个本地或远程 worker、fake/qwen run、轻量 mission 和人工审计闭环。

## 1. 访问入口

### Web 管理台

优先使用域名入口：

```text
https://doubaofans.site/cloud-agents/
```

如果域名或 HTTPS 还未恢复，可临时使用 IP 入口：

```text
http://47.243.94.91/cloud-agents/
```

浏览器入口经过 Nginx：

- `/cloud-agents/`：给人使用的 Web 管理台，默认应用登录页和签名 session cookie。
- `/cloud-agents-worker/`：给远程 worker 使用的 API 入口，透传 worker Bearer token，不给人直接操作。

### 登录信息

默认登录用户名通常是：

```text
cloudagents
```

密码来自部署脚本输出、GitHub Actions secret，或服务器上的 `/etc/cloud-agents-runtime.env`。不要把密码写入仓库或聊天记录。

### 服务器常用路径

```text
/opt/agent-research
/etc/cloud-agents-runtime.env
/etc/cloud-agents-worker.env
/var/lib/cloud-agents-runtime
/var/lib/cloud-agents-runtime/artifacts
```

## 2. 管理台页面说明

### Overview

用于看系统总览：

- 当前 runs、missions、queue、workers 的概况。
- 最近 runs 和 missions。
- 运行状态、失败态、等待态的分布。

日常第一步先看这里：如果 queue 堆积、worker stale 或 run failed 会比较直观。

### Runs

用于创建和管理单个 SAEU run。

可以做：

- 创建 fake/qwen run。
- 查看 run 状态。
- 打开 run detail。
- 取消正在运行的 run。

常见 adapter：

| Adapter | 用途 |
| --- | --- |
| `fake` | 低成本 smoke test、验证平台链路 |
| `qwen` | 真实 qwen-code 执行 |

### Units

用于管理长期运行的稳定执行单元，也就是本地或远程 worker。

可以做：

- 查看 worker 心跳、容量、状态、资源标签和 adapter 能力。
- 生成远程 worker 注册 token 和部署命令。
- Drain 单元，让它不再认领新任务。
- Resume 单元，让它重新接收任务。
- Retry 单元上的 running run，把租约重排回队列。

对 2C2G VPS 的建议：

- 每台先设置 `capacity=1`。
- 标签建议设置 `region=hk`、`tier=2c2g`。
- 资源建议声明 `cpus=2`、`memory_gb=2`。
- 真实 qwen 任务先用 1 台主控 + 2 台 remote worker 的形态跑 smoke，再逐步提高并发。

### Run Detail

这是最重要的排障页面。

你可以查看：

- Runner Chat：实时事件、模型输出、工具事件摘要、warning、error。
- Event Stream：完整 canonical event。
- Artifacts：下载 `events.jsonl`、`raw_events.jsonl`、`diagnostics.json`、`final_*.json`、executor 日志等。
- Audit Bundle：下载完整审计包。
- Permission：处理 pending permission request。

如果你觉得“卡住了”，优先看：

1. Runner Chat 是否停在 permission。
2. Event Stream 最后一条 event 是什么。
3. Artifacts 里是否有 `diagnostics.json`、`executor.stderr.log`。
4. Executor 页面里对应 lease 是否 failed/orphaned/running。

### Missions

用于创建复杂任务。

Mission 会把一个目标拆成多个 task，每个 task 通过 profile 创建一个或多个 SAEU run。当前支持串行、fan-out/fan-in、自定义 DAG。

适合：

- 复杂需求分析。
- 多阶段研发任务。
- planner -> coder -> tester -> reviewer。
- 需要 reviewer/release gate 的任务。

### Mission Detail

用于查看复杂任务的整体进展：

- Task DAG。
- 每个 task 的 profile、依赖、run_id。
- Mission events。
- Mission artifacts。
- Reviewer gate 状态。
- 人工 override。

如果 mission blocked，通常原因是：

- reviewer 输出 `block` 或 `needs_human`。
- `review_gate.json` 缺失或非法。
- high/critical findings。

### Profiles

Profile 是“执行模板”，不是 Agent 实例。

内置 profile：

| Profile | 用途 |
| --- | --- |
| `planner` | 拆任务、读代码、设计方案 |
| `coder` | 代码实现 |
| `tester` | 测试和复现 |
| `reviewer` | 代码审查和风险识别 |
| `release-gate` | 合并/部署前 gate |
| `doc-writer` | 文档输出 |

你可以：

- 复制系统 profile。
- 新建用户 profile。
- 编辑 runtime/tools/approval/limits/workspace/artifacts JSON policy。
- 保存为新版本。

建议：先复制内置 profile，再改副本，不直接依赖临时 prompt 模拟角色。

### Access

用于管理单租户 RBAC foundation：

- 当前 principal。
- role/scope matrix。
- projects。
- API tokens。
- token revoke。

当前 token 只保存 hash，明文 token 只在创建时显示一次。

常用 token scope：

| Scope | 用途 |
| --- | --- |
| `runs:read` | 只读 run |
| `runs:create` | 创建 run |
| `missions:*` | 操作 mission |
| `workers:*` | 远程 worker 注册、claim、上报 |
| `ops:*` | 备份、drill、清理等运维操作 |
| `access:*` | 管理项目和 token |

### Executors

用于排查 qwen 执行器：

- executor strategy。
- active/failed leases。
- pid、port、workspace。
- container config。
- run 关联。
- last_error。

常见策略：

| Strategy | 含义 |
| --- | --- |
| `shared` | 共用一个 qwen serve |
| `per_run_process` | 每个 qwen run 启动独立 qwen serve |
| `container` | 每个 run 使用 Docker 容器隔离 |

当前稳定建议：普通 beta 运行优先 `shared` 或 `per_run_process`；`container` 仍需要继续做真实 qwen 实机验收。

### Operations

用于运维：

- Failure drills。
- Backups。
- Runtime status。
- P5 evaluations。
- Cost budget。
- 备份下载。

日常建议：

- 部署后先跑一次 drill。
- 重要任务前创建 backup。
- 出问题时下载 audit bundle + backup。

## 3. 创建单个 Run

### Web 操作

1. 进入 `/cloud-agents/`。
2. 打开 Runs。
3. 选择 adapter：
   - 快速验证选 `fake`。
   - 真实任务选 `qwen`。
4. 输入 prompt。
5. 提交。
6. 打开 Run Detail 查看实时进展。

### API 操作

通过公网 Nginx 管理入口时，先登录保存 cookie，再调用 API：

```bash
cookie_jar=/tmp/cloud-agents.cookies
curl -s -c "$cookie_jar" \
  -H 'content-type: application/json' \
  -d '{"username":"cloudagents","password":"<password>"}' \
  https://doubaofans.site/cloud-agents/auth/login

curl -s https://doubaofans.site/cloud-agents/runs \
  -b "$cookie_jar" \
  -H 'content-type: application/json' \
  -d '{"prompt":"hello runtime","adapter":"fake"}'
```

直连 runtime 本地端口时，使用 master token：

```bash
RUN_MANAGER_TOKEN="$(awk -F= '$1=="RUN_MANAGER_TOKEN"{print $2}' \
  /etc/cloud-agents-runtime.env)"

curl -s http://127.0.0.1:8765/runs \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"prompt":"hello runtime","adapter":"fake"}'
```

## 4. 创建 Mission

### Web 操作

1. 打开 Missions。
2. 输入 goal。
3. 选择策略：
   - `sequential`：串行任务。
   - `fanout`：并行后汇总。
   - `custom`：自定义 task DAG。
4. 选择 adapter。
5. 提交。
6. 打开 Mission Detail 查看 DAG 和子 run。

### API 示例

```bash
curl -s https://doubaofans.site/cloud-agents/missions \
  -b "$cookie_jar" \
  -H 'content-type: application/json' \
  -d '{
    "goal": "验证一个两阶段任务",
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

## 5. 处理权限请求

当 Agent 触发需要人工审批的工具或操作时，run 会产生 `permission.requested`。

处理方式：

1. 打开 Run Detail。
2. 在 Runner Chat 或 Permission 区域查看请求内容。
3. 选择 approve 或 deny。
4. 填写 reason。
5. 提交后会产生 `permission.resolved` 事件。

如果长时间未处理：

- 系统会产生 `permission.stalled`。
- 默认策略是 audit。
- 可通过环境变量改为 `deny` 或 `cancel`。

```text
RUN_MANAGER_PERMISSION_STALL_SECONDS=300
RUN_MANAGER_PERMISSION_STALL_ACTION=audit
```

## 6. 下载日志和审计材料

Run Detail 中常用下载：

| 文件 | 用途 |
| --- | --- |
| `events.jsonl` | canonical event stream |
| `raw_events.jsonl` | adapter raw events |
| `diagnostics.json` | 诊断信息 |
| `executor.json` | executor lease |
| `executor.stdout.log` | executor stdout |
| `executor.stderr.log` | executor stderr |
| `audit.json` | 完整 run 审计包 |

API 下载示例：

```bash
curl -s https://doubaofans.site/cloud-agents/runs/<run_id>/audit.json \
  -b "$cookie_jar" \
  -o run-audit.json
```

Mission 常用下载：

| 文件 | 用途 |
| --- | --- |
| `mission_manifest.json` | mission 总览 |
| `events.jsonl` | mission event stream |
| `task_<task_id>.json` | task 状态 |
| `final_report.md` | 最终报告 |

## 7. 运维操作

### 查看服务状态

```bash
ssh root@47.243.94.91
systemctl status cloud-agents-runtime --no-pager --full
journalctl -u cloud-agents-runtime -n 120 --no-pager
```

### 重启服务

```bash
systemctl restart cloud-agents-runtime
systemctl status cloud-agents-runtime --no-pager --full
```

### 查看运行时健康

```bash
curl -s http://127.0.0.1:8765/health
```

如果开启 `--protect-health`，公网 `/health` 也可能需要认证。

### 查看队列和 worker

```bash
RUN_MANAGER_TOKEN="$(awk -F= '$1=="RUN_MANAGER_TOKEN"{print $2}' \
  /etc/cloud-agents-runtime.env)"

curl -s http://127.0.0.1:8765/queue \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN"

curl -s http://127.0.0.1:8765/workers \
  -H "authorization: Bearer $RUN_MANAGER_TOKEN"
```

### 创建备份

Web：

1. 打开 Operations。
2. 找到 Backups。
3. 点击 Create。
4. 下载生成的 tar.gz。

API：

```bash
curl -s -X POST https://doubaofans.site/cloud-agents/ops/backups \
  -b "$cookie_jar" \
  -H 'content-type: application/json' \
  -d '{}'
```

### 运行故障演练

```bash
curl -s -X POST https://doubaofans.site/cloud-agents/ops/drills \
  -b "$cookie_jar" \
  -H 'content-type: application/json' \
  -d '{}'
```

## 8. 部署和更新

### 主 Runtime VPS 部署

```bash
QWEN_SETTINGS_FILE=/Users/chigao/Documents/works/settings.json \
PUBLIC_DOMAIN=doubaofans.site \
BASIC_AUTH_USER=cloudagents \
BASIC_AUTH_PASSWORD=<password> \
  bash scripts/deploy_runtime_vps.sh \
  root@47.243.94.91 \
  /Users/chigao/Documents/works/ecs/aliyun-hongkong.pem
```

部署脚本会：

- 安装 git/python3/npm/nginx。
- 安装 qwen CLI。
- 拉取仓库 main。
- 写 `/etc/cloud-agents-runtime.env`。
- 安装 systemd service。
- 配置 Nginx。
- 重启 runtime。

### 远程 Worker VPS 部署

推荐从 Web 管理台生成注册命令：

1. 打开 Units。
2. 填写 Unit ID，例如 `hk-2c2g-a`。
3. Worker control URL 使用 `https://doubaofans.site/cloud-agents-worker`。
4. Capacity 填 `1`。
5. CPUs 填 `2`，Memory GB 填 `2`。
6. Region label 填 `hk`。
7. 点击 Generate。
8. 复制生成的部署命令，把 `root@<worker-ip>` 和 `/path/to/key.pem` 替换成目标 VPS。

也可以在 Access 页面或 API 创建 worker token：

```bash
curl -s https://doubaofans.site/cloud-agents/access/tokens \
  -b "$cookie_jar" \
  -H 'content-type: application/json' \
  -d '{"name":"worker-vps-a","scopes":["workers:*"]}'
```

然后部署 worker：

```bash
RUN_WORKER_CONTROL_URL=https://doubaofans.site/cloud-agents-worker \
RUN_WORKER_TOKEN=cat_... \
RUN_WORKER_ID=vps-a \
RUN_WORKER_METADATA_JSON='{"region":"hk","labels":{"tier":"sandbox"}}' \
QWEN_SETTINGS_FILE=/Users/chigao/Documents/works/settings.json \
  bash scripts/deploy_worker_vps.sh \
  root@<worker-host> \
  /path/to/key.pem
```

Worker VPS 服务状态：

```bash
systemctl status cloud-agents-worker --no-pager --full
journalctl -u cloud-agents-worker -n 120 --no-pager
```

远程 worker 控制说明：

- `POST /workers/<worker_id>/drain`：进入排空状态，不再认领新任务。
- `POST /workers/<worker_id>/resume`：恢复认领任务。
- `POST /workers/<worker_id>/retry`：将该 worker 上的 running lease 重新放回队列。
- `GET /workers/<worker_id>/control`：worker 下行控制面，包含 cancel 和 permission resolution。
- `POST /runs/<run_id>/cancel`：远程 run 会记录 `run.cancel_requested`，由 worker 拉取后执行。
- `POST /runs/<run_id>/permissions/<permission_id>`：远程 run 会记录 `permission.resolve_requested`，由 worker 拉取后转发给 adapter。

## 9. 验收流程

### 快速 smoke

```bash
python3 scripts/monitor_runtime.py \
  --base-url https://doubaofans.site/cloud-agents \
  --basic-user cloudagents \
  --basic-password <password> \
  --json
```

### fake run 验收

```bash
python3 scripts/validate_runtime.py \
  --base-url http://127.0.0.1:8765 \
  --token "$RUN_MANAGER_TOKEN" \
  --adapter fake
```

### qwen 单 run 验收

```bash
python3 scripts/validate_qwen_mission.py \
  --base-url http://127.0.0.1:8765 \
  --token "$RUN_MANAGER_TOKEN" \
  --validate-single-run \
  --expect-executor-strategy per_run_process \
  --timeout 600
```

### qwen 轻量 mission 验收

```bash
python3 scripts/validate_qwen_mission.py \
  --base-url http://127.0.0.1:8765 \
  --token "$RUN_MANAGER_TOKEN" \
  --validate-single-run \
  --validate-mission \
  --mission-task-count 1 \
  --expect-executor-strategy per_run_process \
  --timeout 900
```

## 10. 常见问题处理

### 页面白屏

检查：

1. 浏览器是否打开 `/cloud-agents/`，不是裸域 API 路径。
2. DevTools Network 是否有 JS 资源 404。
3. Nginx 是否配置了 hash routing。
4. 服务是否正常：

```bash
systemctl status cloud-agents-runtime --no-pager --full
nginx -t
journalctl -u cloud-agents-runtime -n 120 --no-pager
```

### Run 一直 queued

检查：

1. `/queue` 里是否有可用 worker。
2. worker capacity 是否为 0。
3. worker 是否 stale。
4. run 的 `worker_requirements` 是否没有任何 worker 满足。

### Run 卡在 permission

打开 Run Detail，处理 permission request。也可以检查 event：

```bash
curl -s https://doubaofans.site/cloud-agents/runs/<run_id>/events.json \
  -b "$cookie_jar"
```

### qwen run failed

优先下载：

- `executor.stderr.log`
- `executor.stdout.log`
- `executor.json`
- `diagnostics.json`
- `audit.json`

再检查：

```bash
journalctl -u cloud-agents-runtime -n 200 --no-pager
```

### Worker 无法注册

检查：

1. worker 是否使用 `/cloud-agents-worker/`。
2. token scope 是否包含 `workers:*` 或 `workers:write`。
3. token 是否已 revoke。
4. 主控制面 Nginx 是否包含 worker route。
5. worker journal：

```bash
journalctl -u cloud-agents-worker -n 120 --no-pager
```

### SSH 部署中断

部署脚本已经有 keepalive 和 scp retry，但小 VPS 网络仍可能断。可重跑部署脚本；它会 fetch/reset 到最新 main 并覆盖 systemd/env 配置。

## 11. 使用建议

推荐日常节奏：

1. 先用 `fake` run 验证平台链路。
2. 再用 qwen single-run 验证真实执行。
3. 复杂任务用 Mission，不要一开始就写超长 prompt。
4. 每次重要任务后下载 audit bundle。
5. 失败时先看 Run Detail，再看 Executor，再看 journal。
6. 远程 worker 先跑 fake，再跑 qwen。
7. 容器 executor 暂时作为手动验收路径，不作为默认生产策略。

当前最稳运行形态：

```text
主 VPS:
  Nginx + Cloud Agents Runtime + local worker

执行策略:
  fake smoke
  qwen shared / per_run_process

可选:
  第二台 VPS remote worker
```

暂不建议作为默认生产形态：

```text
多租户公开服务
无登录保护直接暴露 runtime
未验收的 qwen container strategy
高并发多控制面
大文件/二进制 artifact 密集任务
```
