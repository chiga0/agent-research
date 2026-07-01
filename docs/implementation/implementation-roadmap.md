# 实施 Roadmap

> 目标：跟踪从 `qwen serve` 第一版 SAEU 到 ACP-compatible 多执行器、多 Agent 编排系统的实施状态。状态字段可持续更新，作为后续开发、审计和复盘入口。

## 状态说明

| 状态 | 含义 |
| --- | --- |
| `not_started` | 尚未开始 |
| `research_done` | 调研完成，方案已定 |
| `design_ready` | 设计完成，可进入实现 |
| `in_progress` | 正在实现 |
| `blocked` | 被外部条件阻塞 |
| `done` | 已完成 |
| `deferred` | 暂缓 |

## 总览

| 阶段 | 目标 | 当前状态 | 退出标准 |
| --- | --- | --- | --- |
| P0 | 文档、边界和审计定稿 | `done` | 方案、审计、Roadmap 已入库并部署 |
| P1 | 单 SAEU 最小闭环 | `done` | 一个 qwen serve run 可创建、输入、订阅、取消、产出 artifact，adapter 不泄漏 qwen 私有 API |
| P2 | 审计、权限、恢复硬化 | `done` | Event Store、Permission Service、Artifact Collector 可用 |
| P3 | 多 SAEU 并发与任务队列 | `done` | 1-2 个 SAEU 并发运行，队列限流生效 |
| P4 | Supervisor + Profile + SAEU 编排 | `done` | 常驻 supervisor 可基于 profile 创建一个或多个 SAEU run；SubAgent 仅作为 SAEU 内部优化 |
| P5 | 外部协议与替代组件评估 | `not_started` | ACP Streamable HTTP、A2A Gateway、E2B/Daytona、Temporal/LangGraph/Airflow 完成试点评估 |
| P6 | Beta 稳定化 | `not_started` | 故障演练、回放、监控、备份、部署脚本完成 |

## P0：设计与审计

状态：`done`

已完成：

- 定义稳定单 Agent 执行单元 SAEU。
- 修正 SAEU 与 qwen serve 的关系：qwen serve 是第一版实现，不是额外要替代的 worker。
- 明确 SubAgent 与 SAEU 的边界。
- 设计基于 `qwen serve` 的云端单 Agent 单元。
- 设计从单 Agent 到多 Agent 编排路线。
- 完成协议边界：SAEU、ACP、A2A、MCP。
- 完成多方向审计：对比、正向、无方向、反向。
- 建立 Roadmap 状态跟踪。

产物：

- [稳定单 Agent 执行单元](stable-agent-execution-unit.md)
- [SubAgent 与独立执行单元边界](subagent-vs-execution-unit.md)
- [基于 qwen-code serve 的云端单 Agent 单元方案](qwen-serve-single-agent-cloud-unit.md)
- [从单 Agent 执行单元到多 Agent 编排](single-to-multi-agent-implementation-plan.md)
- [外部方案对比与多方向审计](alternative-solutions-comparative-audit.md)
- [方案审计与 Review 记录](review-and-audit-record.md)

## P1：单 SAEU 最小闭环

状态：`done`

目标：

- 用 `qwen serve` 启动一个单 Agent 执行单元。
- 对外只暴露 Run Manager API，不暴露 qwen 原始接口。
- 能创建 run、发送 prompt、订阅事件、取消 run、收集 artifact。
- 定义可扩展到 ACP-compatible Agent 的 adapter contract。

任务：

| 任务 | 状态 | 验收 |
| --- | --- | --- |
| 定义 `run_spec` JSON schema | `done` | POC 已包含 repo、workspace、prompt、model、sandbox、timeout、metadata |
| 实现 Worker Supervisor 启动 qwen serve | `done` | 支持 `QWEN_SERVE_COMMAND` 托管 qwen serve，并已在 ECS 上用真实配置验证 |
| 定义 runtime adapter capability schema | `done` | `/capabilities` 可表达 fake/qwen adapter 能力 |
| 实现 `POST /runs` | `done` | 创建 run 并返回 run_id |
| 实现 `POST /runs/:id/input` | `done` | fake 可异步接受；qwen adapter 可映射到 `/session/:id/prompt` |
| 实现 `GET /runs/:id/events` | `done` | Run Manager SSE 可返回 canonical events，支持 `Last-Event-ID` |
| 实现 `POST /runs/:id/cancel` | `done` | fake 可取消；qwen adapter 可映射到 `/session/:id/cancel` |
| 实现基础 artifact 收集 | `done` | 已保存 run_spec、input、canonical events、raw events、diagnostics、final report |

当前实现：

- `runtime/`：stdlib Run Manager POC。
- 默认 `fake` adapter 可完整跑通创建、输入、SSE、取消和 artifact。
- `qwen` adapter 已支持通过 `QWEN_SERVE_URL` / `QWEN_SERVE_TOKEN` 连接 `qwen serve` REST/SSE；`QWEN_SERVE_COMMAND` 可由 Run Manager supervisor 托管真实 daemon。

P1 当前判断：

- 已完成：Run Manager API、fake adapter 端到端、qwen adapter 真实联调、artifact 文件、adapter contract、Run Manager bearer auth、qwen supervisor、systemd/Docker Compose 部署产物。
- 当前最小云端形态是单控制面、单 qwen worker、单租户的可验收 MVP。
- 未纳入 P1：公网 HTTPS 反代、多租户隔离、多 worker 队列和资源配额。

P1 cloud-ready 最小验收：

| 验收项 | 标准 |
| --- | --- |
| 真实 qwen run | `done`：`adapter=qwen` 能创建 session、发送 prompt、流式接收事件、完成 run |
| 进程管理 | `done`：Run Manager 可启动/发现/停止一个 `qwen serve` daemon |
| API 安全 | `done`：Run Manager 支持 bearer token；部署默认绑定 `127.0.0.1` |
| Artifact | `done`：每个 run 保存 run_spec、input、canonical events、raw qwen events、diagnostics、final report |
| Cancel | `done`：`POST /runs/:id/cancel` 能取消 qwen session，并产生 terminal event |
| 部署 | `done`：提供 systemd 与 Docker Compose，已在 VPS 上重启验证 |
| 验证脚本 | `done`：脚本可跑通 health -> capabilities -> create run -> SSE -> artifact check |

本轮新增工程门禁：

- Runtime CI：style、compile、90%+ runtime coverage、MkDocs strict build。
- 集成测试：Run Manager HTTP、SSE、auth、fake adapter、qwen fake daemon adapter。
- 部署产物：Dockerfile、Docker Compose、systemd service/env example。
- 验收脚本：`scripts/validate_runtime.py`。

## P2：审计、权限、恢复硬化

状态：`done`

目标：

- 所有 run 都有完整审计链。
- 权限请求可审批、拒绝、超时。
- qwen/Supervisor/client 断线有恢复策略。

任务：

| 任务 | 状态 | 验收 |
| --- | --- | --- |
| SQLite `run_events` append-only 表 | `done` | 任意 run 可从事件表和 JSONL 重建状态 |
| Postgres `run_events` append-only 表 | `deferred` | 多控制面/高并发阶段替换 SQLite |
| Permission Service | `done` | permission request/resolution 全量入库和 artifact |
| qwen SSE adapter | `done` | raw event -> canonical event 映射稳定，并保存 raw events |
| Last-Event-ID reconnect | `done` | 客户端重连可按 sequence 追事件 |
| event gap detection | `done` | gap 写入 `event.gap_detected` |
| diagnostics artifact | `done` | 每个 run 维护 `diagnostics.json` |
| replay CLI | `done` | 支持 events、SSE frame 和 state replay |

P2 当前判断：

- 当前实现满足单实例云端 MVP 的审计、恢复和回放要求。
- SQLite 是 MVP 的 durable event store；迁移到 Postgres 的条件是多控制面实例、跨机器 worker lease 或高并发写入。
- Permission Service 当前完成“决策入库/入 artifact/入 SSE audit trail”；超时自动 deny/cancel 会放入 P3/P6 的任务生命周期治理。
- Artifact Collector 当前覆盖 run_spec、input、canonical events、raw events、diagnostics、final；stdout/stderr 类 worker 日志将在独立 sandbox worker 引入后扩展。

硬性规则：

- Event Store 写入失败时，run 进入 `degraded` 或 `paused`，不能继续无审计执行。
- permission timeout 默认 `cancel` 或 `deny`。
- artifact 收集失败必须体现在 terminal failure reason 中。

## P3：多 SAEU 并发与任务队列

状态：`done`

目标：

- 在一台 VPS 上安全运行 1-2 个 SAEU。
- 队列、租约、心跳、限流可用。
- 第二台 VPS 可作为 sandbox worker。

任务：

| 任务 | 状态 | 验收 |
| --- | --- | --- |
| jobs/leases 表 | `done` | SQLite `run_jobs` 表持久化 queued/running/terminal job，过期 lease 可回收到队列 |
| worker heartbeat | `done` | SQLite `workers` 表、`/queue`、`/workers` 和浏览器控制台可见 worker 状态 |
| per-worker capacity | `done` | `RUN_MANAGER_WORKER_CAPACITY` / `--worker-capacity` 生效，超容量 run 保持 queued |
| per-run workspace | `done` | 每个 run 默认分配 `artifact_root/workspaces/<run_id>`；local git source 优先使用 detached worktree |
| resource limits | `done` | Run Manager 解析并审计 resource policy；timeout watchdog 生效；Docker/systemd 对执行单元施加 CPU/memory/pids cgroup 限制 |
| cleanup policy | `done` | terminal run 的 workspace/artifact 可按保留策略清理；SQLite 审计事件保留 |
| 双 VPS worker 模式 | `deferred` | control plane 与 sandbox worker 分离 |

P3 当前实现：

- `POST /runs` 先写入 `run.queued`，由本地 worker 在容量允许时 claim lease。
- 每个 run 在入队前写入 `workspace.prepared`，并保存 `workspace.json` 和 resolved workspace metadata。
- 每个 run 在入队前写入 `resources.resolved`，并保存 `resources.json`；`timeout_seconds` 由 Run Manager watchdog 取消超时 run。
- `lease.claimed`、`lease.expired`、`run.completed` / `run.failed` / `run.cancelled` 都写入同一 canonical event stream。
- `GET /queue` 返回 job counts、jobs 和 workers；`GET /workers` 返回 worker heartbeat 视图。
- 浏览器控制台增加 Queue 面板，显示 queued/running/capacity/active 和 worker heartbeat。
- 单进程 Run Manager 现在可以用 capacity=1 验证排队，用 capacity=2 在单 VPS 上跑 1-2 个 SAEU。
- Docker Compose 和 systemd 部署默认把整个执行单元限制在 1 CPU / 1G memory / 512 tasks/pids。
- cleanup policy 默认启用：workspace 保留 7 天，artifact 保留 30 天，后台每小时扫描；`POST /cleanup` 可手动触发一次清理。

P3 剩余风险：

- 当前 worker 仍在 Run Manager 进程内，跨 VPS worker 需要把 claim/heartbeat 迁移到远程 worker loop。
- 远程 repo clone/credential policy 尚未接入，当前会显式拒绝而不是静默创建空 workspace。
- qwen adapter 仍连接一个 `qwen serve` endpoint；强隔离 qwen 并发需要 per-workspace daemon registry 或容器 worker。
- CPU/memory/pids 当前是执行单元级限制；严格的 per-run cgroup/Docker 限制需要引入容器 worker。

## P4：Supervisor + Profile + SAEU 编排

状态：`done`

目标：

- 多 Agent 编排不直接编排 CLI，也不直接编排 runtime 内部 SubAgent。
- 实现 Profile Registry，内置 planner/coder/reviewer/tester/doc-writer，并允许用户复制、编辑、新增 profile。
- 实现常驻 Project/Supervisor SAEU，根据 profile、任务依赖和资源策略创建 SAEU run。
- 实现 planner -> coder -> tester -> reviewer -> final report。

任务：

| 任务 | 状态 | 验收 |
| --- | --- | --- |
| mission/task 数据模型 | `done` | `missions`、`mission_tasks`、`mission_events` 持久化，mission 可拆多个 task |
| Profile Registry | `done` | 系统内置 profile + 用户自定义 profile + 版本化 resolved profile |
| profile 定义 | `done` | planner/coder/tester/reviewer/doc-writer 权限、模型、workspace、artifact 要求不同 |
| profile -> agent instance 映射 | `done` | 一个 profile 可启动多个 SAEU instance；每个 task run 带 profile snapshot |
| task -> SAEU 调度策略 | `done` | mission/task DAG 中的 ready task 默认创建 SAEU run |
| runtime SubAgent 内部优化策略 | `deferred` | 仅作为 SAEU 内部能力；不进入 MVP 平台调度 |
| task dependency | `done` | 支持串行和 fan-out/fan-in |
| artifact handoff | `done` | 子任务只通过 artifact 引用和事件传递结果 |
| reviewer gate | `done` | `review_gate.json` 可触发 pass/warn/block/needs_human，阻塞下游 task |
| final report | `done` | 汇总所有子任务和 artifact 引用 |

P4 当前实现：

- `GET /profiles`、`GET /profiles/{profile_id}`、`POST /profiles` 提供 profile registry。
- 内置 `planner`、`coder`、`tester`、`reviewer`、`doc-writer` profile。
- `POST /missions` 创建 mission，并支持 `sequential`、`fanout`、`custom` 三种策略。
- 每个 ready task 都创建一个普通 SAEU run，继承 P1-P3 的 workspace、resource、queue、event、artifact、cleanup 能力。
- task run spec 写入 `mission_id`、`task_id`、`task_profile`、`profile_snapshot` 和 dependency artifact refs。
- `GET /missions/{mission_id}`、`events.json`、`artifacts` 可恢复 mission 状态和审计链。
- `POST /missions/{mission_id}/cancel` 会取消 active child run，并把 pending task 标为 cancelled。
- reviewer task 可通过 `review_gate.json` 输出结构化 gate；高/严重 finding、`block`、`needs_human`、缺失或非法 gate 都会让 mission 进入 `blocked`。
- mission artifact 存放在 `artifact_root/missions/<mission_id>/`，包含 manifest、events、task JSON 和 final report。

P4 剩余风险：

- Supervisor 目前是确定性 in-process controller，还不是有长期记忆的 Project Agent SAEU。
- reviewer gate 已支持结构化阻塞；更细的 finding 分类、人工 override、merge/deploy gate 仍属于 P6 hardening。
- artifact handoff 当前传递稳定引用，不复制 sibling workspace，也不做 patch merge。
- qwen adapter 仍是单 `qwen serve` endpoint；强隔离多 qwen daemon registry 属于后续 worker/container 化。

## P5：外部协议与替代组件评估

状态：`not_started`

目标：

- 验证当前架构不会锁死。
- 评估更成熟组件是否值得替换局部模块。

任务：

| 任务 | 状态 | 验收 |
| --- | --- | --- |
| A2A Gateway POC | `not_started` | 外部 task 可映射成 mission/run |
| ACP Streamable HTTP POC | `not_started` | 一个非 qwen worker 可通过 ACP 远程协议接入 |
| E2B sandbox adapter POC | `not_started` | 一个 SAEU 可跑在 E2B sandbox |
| Daytona sandbox adapter POC | `not_started` | 一个 SAEU 可跑在 Daytona sandbox |
| Temporal workflow POC | `not_started` | `AgentRunWorkflow` 可管理单 run |
| LangGraph supervisor POC | `not_started` | 一个 mission DAG 可恢复执行 |
| Airflow outer scheduler POC | `deferred` | Airflow 作为外层 batch scheduler 调用 mission API，不进入 Agent session 控制面 |
| OpenHands SAEU adapter 评估 | `deferred` | 能映射到 SAEU contract |

评估门槛：

- 不破坏 SAEU contract。
- 不要求第一天迁移所有 run。
- 不降低审计、权限、恢复能力。
- 能在成本和运维上解释收益。

## P6：Beta 稳定化

状态：`not_started`

目标：

- 可以稳定运行真实任务。
- 有监控、告警、备份、演练和回放工具。

任务：

| 任务 | 状态 | 验收 |
| --- | --- | --- |
| 故障演练 | `not_started` | daemon crash、Supervisor restart、DB unavailable 都有记录 |
| 备份策略 | `not_started` | Postgres 和 artifact 可恢复 |
| 监控指标 | `not_started` | run count、failure kind、token、latency、permission pending |
| 成本预算 | `not_started` | model proxy 可限额 |
| 安全检查 | `not_started` | no Docker socket、no host secrets、egress policy |
| 发布脚本 | `not_started` | systemd/docker compose 可一键部署 |
| 回归样例 | `not_started` | 至少 10 个 JSONL/replay case |

## 决策检查点

| 检查点 | 时间 | 决策 |
| --- | --- | --- |
| P1 完成后 | 单 SAEU 跑通后 | qwen serve adapter 是否足够，是否需要优先做 `/acp` 或非 qwen adapter |
| P2 完成后 | 审计/恢复跑通后 | 是否引入 Temporal 或继续 Postgres queue |
| P3 完成后 | 并发稳定后 | 是否需要第二台 VPS 或 E2B/Daytona |
| P4 完成后 | 多 Agent 闭环后 | 是否开放 A2A Gateway |
| P5 完成后 | 替代方案 POC 后 | 哪些模块替换，哪些保留 |

## 当前优先级

近期主线：

1. 用 fake adapter 跑通 P4 mission/profile smoke。
2. 等部署密钥可用后，用 qwen serve 验收单 run 和两 task mission。
3. 设计 reviewer gate 人工 override 和 merge/deploy gate。
4. 评估 P5 的 ACP Streamable HTTP adapter 和 A2A Gateway。
5. 评估 Temporal/LangGraph 是否接管 durable mission workflow。

P4 已能证明 `mission -> task -> profile -> SAEU run` 的基础编排闭环；P5/P6 再决定是否把 mission workflow 迁移到 Temporal/LangGraph，或引入云沙箱。
