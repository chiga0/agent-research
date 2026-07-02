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
| P5 | 外部协议与替代组件评估 | `poc_done` | ACP/A2A/Temporal POC 已接入 SAEU contract；E2B/Daytona/LangGraph/Airflow 已形成可配置/暂缓决策 |
| P6 | Beta 稳定化 | `beta_ready` | 故障演练、回放、监控、备份、部署脚本、产品级 Web 管理台和 CI/E2E 门禁完成 |
| P7 | 产品化控制台与企业基础 | `in_progress` | Mission Detail/DAG、Runner Chat v2、Profile Editor、Access/RBAC foundation、真实 qwen mission 验收脚本和 executor isolation 路线可用 |

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
| reviewer override | `done` | blocked mission 可记录人工 approve/deny；approve 后恢复下游 task |
| merge/deploy gate | `done` | `release-gate` profile 通过 `release_gate.json` 触发 `merge_deploy.gate_*` |
| final report | `done` | 汇总所有子任务和 artifact 引用 |

P4 当前实现：

- `GET /profiles`、`GET /profiles/{profile_id}`、`POST /profiles` 提供 profile registry。
- 内置 `planner`、`coder`、`tester`、`reviewer`、`release-gate`、`doc-writer` profile。
- `POST /missions` 创建 mission，并支持 `sequential`、`fanout`、`custom` 三种策略。
- 每个 ready task 都创建一个普通 SAEU run，继承 P1-P3 的 workspace、resource、queue、event、artifact、cleanup 能力。
- task run spec 写入 `mission_id`、`task_id`、`task_profile`、`profile_snapshot` 和 dependency artifact refs。
- `GET /missions/{mission_id}`、`events.json`、`artifacts` 可恢复 mission 状态和审计链。
- `POST /missions/{mission_id}/cancel` 会取消 active child run，并把 pending task 标为 cancelled。
- reviewer task 可通过 `review_gate.json` 输出结构化 gate；高/严重 finding、`block`、`needs_human`、缺失或非法 gate 都会让 mission 进入 `blocked`。
- `POST /missions/{mission_id}/review-gate/override` 可记录人工 approve/deny；approve 会让 blocked mission 恢复并继续调度未启动下游 task。
- `release-gate` 可通过 `release_gate.json` 输出 merge/deploy gate，事件前缀为 `merge_deploy.gate_*`。
- qwen adapter 会从 gate task 最终文本中的 fenced JSON 抽取 gate artifact，降低真实 qwen reviewer 只输出文本时的接入摩擦。
- mission artifact 存放在 `artifact_root/missions/<mission_id>/`，包含 manifest、events、task JSON 和 final report。

P4 剩余风险：

- Supervisor 目前是确定性 in-process controller，还不是有长期记忆的 Project Agent SAEU。
- reviewer gate 已支持结构化阻塞、人工 override 和 release gate；更细的 finding 分类、审批超时和策略化 merge/deploy gate 仍属于 P6 hardening。
- artifact handoff 当前传递稳定引用，不复制 sibling workspace，也不做 patch merge。
- qwen adapter 仍是单 `qwen serve` endpoint；强隔离多 qwen daemon registry 属于后续 worker/container 化。

## P5：外部协议与替代组件评估

状态：`poc_done`

目标：

- 验证当前架构不会锁死。
- 评估更成熟组件是否值得替换局部模块。

任务：

| 任务 | 状态 | 验收 |
| --- | --- | --- |
| A2A Gateway POC | `poc_done` | `/.well-known/agent-card.json`、`/a2a/tasks`、task status/events/artifacts 可把外部 task 映射成 mission |
| ACP JSON-RPC-over-HTTP POC | `poc_done` | `/acp` 可 create/status/input/cancel run，并可读 run/mission events/artifacts |
| E2B sandbox adapter POC | `evaluated_config_gate` | 保留为 sandbox provider adapter；无 `E2B_API_KEY` 时不启用 |
| Daytona sandbox adapter POC | `evaluated_config_gate` | 保留为 sandbox provider adapter；无 `DAYTONA_API_KEY` 时不启用 |
| Temporal workflow POC | `poc_done` | 可导出 `AgentRunWorkflow` / `MissionWorkflow` plan；尚未接 Temporal worker |
| LangGraph supervisor POC | `evaluated_config_gate` | 保留为可选 supervisor adapter；默认继续使用内置 mission controller |
| Airflow outer scheduler POC | `deferred` | Airflow 作为外层 batch scheduler 调用 mission API，不进入 Agent session 控制面 |
| OpenHands SAEU adapter 评估 | `deferred` | 能映射到 SAEU contract |

评估门槛：

- 不破坏 SAEU contract。
- 不要求第一天迁移所有 run。
- 不降低审计、权限、恢复能力。
- 能在成本和运维上解释收益。

## P6：Beta 稳定化

状态：`beta_ready`

目标：

- 可以稳定运行真实任务。
- 有监控、告警、备份、演练和回放工具。

任务：

| 任务 | 状态 | 验收 |
| --- | --- | --- |
| 故障演练 | `done` | `/ops/drills` 检查 runtime DB、artifact root、queue leases、backup writable、security posture |
| 备份策略 | `done` | `/ops/backups` 生成 runtime DB + artifact tar.gz，支持保留数量配置和下载 |
| 监控指标 | `done` | `/metrics.json` 覆盖 run/mission count、failure kind、latency、permission pending/stalled、worker stale |
| 公网可用性监控 | `done` | `scripts/monitor_runtime.py` + `Runtime Monitor` workflow；部署完成后自动检测，且每 15 分钟巡检 Basic Auth、console、health、capabilities、queue、access policy |
| 成本预算 | `foundation_done` | `/cost/status` 和 `cost.quoted` 提供估算预算、月度阈值、run artifact；真实 provider billing/API 仍是后续替换点 |
| 安全检查 | `done` | `/ops/status` 暴露 docker socket、token/Basic Auth/Nginx posture；部署脚本默认公网 Basic Auth + bearer 注入 |
| 发布脚本 | `done` | systemd、Docker Compose、VPS deploy script、domain HTTPS preserve、CI deploy workflow 可用 |
| 回归样例 | `done` | runtime unittest/coverage、fake/qwen adapter、mission/profile、ACP/A2A/Temporal、Playwright E2E 覆盖 |
| 产品级 Web 管理台 | `done` | React + Tailwind + TanStack Router/Form/Query，黑白主题、移动端导航、运行/任务/Profile/Ops 完整管理流 |

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

1. `done`：在 VPS 上验收 React 管理台、部署后公网 Monitor、fake smoke。
2. `done`：用真实 qwen + `per_run_process` 跑通单 run 和 `mission_task_count=1` 轻量 mission。
3. `in_progress`：在 VPS 上用 `container` strategy + `qwen_container_build=true` 做 Docker/cgroup/network 实机验收；第一轮 run `28592133076` 暴露诊断不足和 Docker argv token 风险，第二轮 run `28593133445` 证明 qwen 容器已启动监听但 readiness probe 被瞬时 reset 误杀；第三轮 run `28593787794` 未进入 qwen acceptance，阻塞在长时间远端 deploy 的 SSH broken pipe；已补 health auth、reset retry、deploy SSH keepalive 和单测，下一步恢复 stable deploy 后重跑 container workflow。
4. 决定 ACP/A2A POC 是否升级到官方完整协议实现或 SDK。
5. 决定是否引入 model proxy 做预算、token、provider audit。

P6 已能提供单租户云端 beta-ready 管理面；下一阶段重点从“单控制面可用”转向“多租户、远程 worker、成本治理和更完整协议兼容”。

## P7：产品化控制台与企业基础

状态：`in_progress`

目标：

- 把管理台从 demo 控制面升级为可交付产品体验。
- 让“复杂需求 -> Mission DAG -> 多 SAEU run -> 实时进展 -> artifact/report”可视化闭环。
- 为企业多用户、团队权限、审计和多 executor 隔离预留稳定接口。

任务：

| 任务 | 状态 | 验收 |
| --- | --- | --- |
| Mission Detail + DAG | `done` | 可查看 mission 状态、任务 DAG、依赖、子 run、mission events、mission artifacts 和 review gate override |
| Runner Chat v2 | `done` | 支持 Agent/permission/warning/error 过滤、stalled signal、一键下载可读执行报告、工具事件摘要 |
| Profile Editor | `done` | 系统 profile 可复制；用户可新建/编辑 profile JSON policy；保存后形成新版本 |
| qwen mission 验收脚本 | `done` | `scripts/validate_qwen_mission.py` 可创建 qwen-backed single-run 和轻量 mission，校验 events/artifacts/final report，并在失败时拉取 executor stdout/stderr/executor/diagnostics artifact 尾部且遮罩 token |
| Access/RBAC foundation | `done` | `/access/policy` 和 Access 页面展示当前 principal、role matrix、scope、审计边界 |
| 部署后可用性验收 | `done` | `Runtime Monitor` 在 `Deploy Runtime` 成功后自动运行；巡检 health/capabilities/queue/executors/access，手动 dispatch 可加 `deep_run` 创建 fake run 并校验 SSE completion |
| 多 SAEU executor isolation | `foundation_done` | backend Executor Registry、per-run qwen serve process、默认 Docker container command builder、executor artifacts/events/API/UI、VPS deploy 参数透传和手动 qwen deep acceptance 已落地；container 实机第一轮已暴露 qwen 容器启动失败，已补诊断，仍需重跑真实 Docker image 验收 |
| Executor 管理台 | `done` | `/executors` 页面展示 strategy、active/failed、container config、lease、pid/port/workspace/run 关联 |
| 重启孤儿 run 恢复 | `done` | 同一 worker 重启时自动将无法继续执行的 running job 标记为 `lease.orphaned` + `run.failed`，释放 capacity，避免部署 smoke 被旧 qwen run 卡住 |
| 团队/项目/IAM | `foundation_done` | SQLite project registry、API token hash 存储、token revoke、token bearer 鉴权、Access 页面管理基础可用；真实 SSO/org/role assignment 后续替换 |
| 成本治理 | `foundation_done` | run 创建时执行 budget quote，写入 `cost.json`/`cost.quoted`，`/cost/status` 和 Operations 成本卡展示预算状态 |
| ACP/A2A 正式化接口 | `compat_done` | ACP JSON-RPC 增加 capabilities/executor/permission/access/cost 方法；A2A card 增加 protocolVersion/endpoints/security/capabilities |
| 远程 Worker Registry | `foundation_done` | Worker metadata 持久化；`/workers/{id}/heartbeat`、`/claim`、run event 回传和轻量 artifact 上传 API；lease 归属校验防止跨 worker 写入 |

Executor isolation 决策：

- 当前单 VPS qwen deployment 仍可使用共享 `qwen serve`，适合最小成本 beta。
- P7.1/P7.2 已把 “一个 adapter endpoint” 升级为 “Executor Registry”：
  - `executor_profile`: qwen/codex/claude/opencode/fake。
  - `workspace_strategy`: per-run worktree、persistent workspace、ephemeral container。
  - `process_strategy`: shared daemon、per-run daemon、container worker、remote worker。
  - `resource_policy`: CPU/memory/pids/timeout/concurrency。
- 已支持 `QWEN_EXECUTOR_STRATEGY=per_run_process`，每个 qwen run 独立启动
  `qwen serve`、分配端口、写入 `executor.json` 和 executor lifecycle 事件。
- `container` 已支持两种启动方式：
  - `QWEN_CONTAINER_COMMAND` 自定义命令模板。
  - `QWEN_CONTAINER_IMAGE` 默认 Docker foreground worker，自动注入 `--cpus`、`--memory`、`--pids-limit`、端口映射、workspace mount、qwen token env 名称，并在 VPS deploy 时把宿主 `.qwen/settings.json` 凭据只读挂进容器。
- `Deploy Runtime` workflow_dispatch 已支持选择 executor strategy、container image/local build、container resource limit，并可打开真实 qwen single-run + bounded mission acceptance。
- Container 第一轮实机验收 run `28592133076` 已证明部署脚本、Docker build、service reload 和 fake smoke 可用，并暴露诊断不足和 token argv 风险；第二轮实机验收 run `28593133445` 通过新增 artifact 证明容器内 `qwen serve` 已启动监听，失败点收敛为 runtime readiness probe 对瞬时 TCP reset 的误判；第三轮 run `28593787794` 阻塞在远端 deploy SSH broken pipe，尚未进入 qwen acceptance；默认 deploy run `28595774630` 进一步定位到 qwen settings `scp` 阶段 SSH reset；当前已补 health auth、reset retry、deploy SSH keepalive、scp retry/connect timeout、远端 deploy 阶段日志/timeout 和回归测试，下一步恢复 stable deploy 后重跑真实 Docker image 验收。
- 下一步仍必须在 VPS 上用真实镜像或 `qwen_container_build=true` 做到 Docker/cgroup/network qwen acceptance 验收通过；当前自动 push 部署仍保持 shared strategy 以降低生产风险。

P7 当前判断：

- 1. CI 状态：`Runtime CI`、`Deploy MkDocs`、`Deploy Runtime` 和部署后 `Runtime Monitor` 已可通过 `gh` 查询；最新 push 全绿。
- 2. 真实 VPS/qwen 验收：`scripts/validate_qwen_mission.py` 已支持 `--validate-single-run`、`--expect-executor-strategy` 和 `--mission-task-count`；2026-07-02 已用 `per_run_process`、`validate_qwen=true`、`qwen_validate_mission=true`、`qwen_mission_task_count=1` 跑通真实单 run + 轻量 mission。
- 3. Container worker：默认 Docker 命令生成、可选本地镜像构建、VPS Docker 安装/启动、`cloudagents` docker group、`.qwen/settings.json` 只读挂载和审计 metadata 已落地；第二轮实机 container qwen acceptance 证明 qwen 容器已启动监听，第三轮暴露 deploy SSH keepalive 缺口；当前需要先确认 stable deploy 恢复，再重跑 container 验收确认 qwen single-run 可完成。
- 4. Executor UI：`/executors` 页面可排查 lease、pid、port、workspace、strategy、失败原因。
- 5. IAM/API token：当前是单租户 foundation，可管理 project 和 API token，token 只保存 hash。
- 6. Cost governance：当前是估算预算 foundation，后续可替换为 model proxy/provider billing ledger。
- 7. ACP/A2A：当前是兼容 facade，不声明完全实现某一官方 SDK；已覆盖管理、通信、状态、权限、executor、成本查询。
- 8. Remote worker：控制面 API 和 worker daemon CLI foundation 已落地；第二台 VPS 或同机独立进程可用 token 注册、poll claim、启动本地 fake/qwen adapter、回传 events/artifacts。

## P8：远程 Worker 与多 VPS 调度

状态：`planned`

目标：

- 把单进程本地 worker 拆成 control-plane + remote worker。
- 支持 1-2 台 VPS 场景：主 VPS 跑控制面和 UI，第二台或同台不同进程跑 worker。
- Worker 可以声明 adapter/executor/resource/sandbox 能力，中心只分配符合能力的 run。

任务：

| 任务 | 状态 | 验收 |
| --- | --- | --- |
| Worker Registry API | `foundation_done` | 远程 worker 可 heartbeat、claim run、回传 run events、上传轻量 artifact；worker metadata 可在 `/workers` 查询 |
| Worker Daemon CLI | `foundation_done` | `python -m cloud_agents_runtime.worker --control-url ... --token ...` 可长期 poll claim，并已覆盖 HTTP control-plane 下 fake run 完整执行/回传测试；qwen 依赖 worker 本地 `QWEN_SERVE_URL` |
| 能力匹配调度 | `next` | run metadata 可指定 required adapters/labels/resources；claim 只返回匹配 worker 的任务 |
| Artifact streaming | `next` | worker 端 executor stdout/stderr/events 可分片上传，中心 artifacts 完整可审计 |
| 多 VPS deploy | `next` | 新增 `deploy_worker_vps.sh` 或 workflow_dispatch，第二台 VPS 可注册到主控制面 |
| 安全边界 | `next` | worker token scope、per-worker revoke、mTLS/Basic Auth/Nginx allowlist 策略明确 |
