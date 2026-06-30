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
| P1 | 单 SAEU 最小闭环 | `in_progress` | 一个 qwen serve run 可创建、输入、订阅、取消、产出 artifact，adapter 不泄漏 qwen 私有 API |
| P2 | 审计、权限、恢复硬化 | `not_started` | Event Store、Permission Service、Artifact Collector 可用 |
| P3 | 多 SAEU 并发与任务队列 | `not_started` | 1-2 个 SAEU 并发运行，队列限流生效 |
| P4 | Supervisor + Profile + SAEU 编排 | `not_started` | 常驻 supervisor 可基于 profile 创建一个或多个 SAEU run；SubAgent 仅作为 SAEU 内部优化 |
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

状态：`in_progress`

目标：

- 用 `qwen serve` 启动一个单 Agent 执行单元。
- 对外只暴露 Run Manager API，不暴露 qwen 原始接口。
- 能创建 run、发送 prompt、订阅事件、取消 run、收集 artifact。
- 定义可扩展到 ACP-compatible Agent 的 adapter contract。

任务：

| 任务 | 状态 | 验收 |
| --- | --- | --- |
| 定义 `run_spec` JSON schema | `done` | POC 已包含 repo、workspace、prompt、model、sandbox、timeout、metadata |
| 实现 Worker Supervisor 启动 qwen serve | `in_progress` | 已有 qwen REST/SSE adapter；进程 supervisor 与真实 daemon 启动待补 |
| 定义 runtime adapter capability schema | `done` | `/capabilities` 可表达 fake/qwen adapter 能力 |
| 实现 `POST /runs` | `done` | 创建 run 并返回 run_id |
| 实现 `POST /runs/:id/input` | `done` | fake 可异步接受；qwen adapter 可映射到 `/session/:id/prompt` |
| 实现 `GET /runs/:id/events` | `done` | Run Manager SSE 可返回 canonical events，支持 `Last-Event-ID` |
| 实现 `POST /runs/:id/cancel` | `done` | fake 可取消；qwen adapter 可映射到 `/session/:id/cancel` |
| 实现基础 artifact 收集 | `in_progress` | 已保存 run_spec、input、canonical events、raw events、final report；stdout/stderr 待真实 worker 接入 |

当前实现：

- `runtime/`：stdlib Run Manager POC。
- 默认 `fake` adapter 可完整跑通创建、输入、SSE、取消和 artifact。
- `qwen` adapter 已支持通过 `QWEN_SERVE_URL` / `QWEN_SERVE_TOKEN` 连接 `qwen serve` REST/SSE；下一步需要在真实 daemon 上做联调并补 worker supervisor 启动策略。

## P2：审计、权限、恢复硬化

状态：`not_started`

目标：

- 所有 run 都有完整审计链。
- 权限请求可审批、拒绝、超时。
- qwen/Supervisor/client 断线有恢复策略。

任务：

| 任务 | 状态 | 验收 |
| --- | --- | --- |
| Postgres `run_events` append-only 表 | `not_started` | 任意 run 可从事件表重建状态 |
| Permission Service | `not_started` | permission request/resolution 全量入库 |
| qwen SSE adapter | `not_started` | raw event -> canonical event 映射稳定 |
| Last-Event-ID reconnect | `not_started` | Supervisor 断线后可追上事件 |
| event gap detection | `not_started` | gap 写入 `event.gap_detected` |
| diagnostics artifact | `not_started` | start/end/crash diagnostics 保存 |
| replay CLI | `not_started` | 支持 UI replay 和 state replay |

硬性规则：

- Event Store 写入失败时，run 进入 `degraded` 或 `paused`，不能继续无审计执行。
- permission timeout 默认 `cancel` 或 `deny`。
- artifact 收集失败必须体现在 terminal failure reason 中。

## P3：多 SAEU 并发与任务队列

状态：`not_started`

目标：

- 在一台 VPS 上安全运行 1-2 个 SAEU。
- 队列、租约、心跳、限流可用。
- 第二台 VPS 可作为 sandbox worker。

任务：

| 任务 | 状态 | 验收 |
| --- | --- | --- |
| jobs/leases 表 | `not_started` | worker 宕机后 lease 可回收 |
| worker heartbeat | `not_started` | dashboard 能看到 worker 状态 |
| per-worker capacity | `not_started` | 超容量 run 排队 |
| per-run workspace | `not_started` | 并发 run 文件隔离 |
| resource limits | `not_started` | CPU/memory/pids 限制生效 |
| cleanup policy | `not_started` | workspace/artifact 按保留策略清理 |
| 双 VPS worker 模式 | `deferred` | control plane 与 sandbox worker 分离 |

## P4：Supervisor + Profile + SAEU 编排

状态：`not_started`

目标：

- 多 Agent 编排不直接编排 CLI，也不直接编排 runtime 内部 SubAgent。
- 实现 Profile Registry，内置 planner/coder/reviewer/tester/doc-writer，并允许用户复制、编辑、新增 profile。
- 实现常驻 Project/Supervisor SAEU，根据 profile、任务依赖和资源策略创建 SAEU run。
- 实现 planner -> coder -> tester -> reviewer -> final report。

任务：

| 任务 | 状态 | 验收 |
| --- | --- | --- |
| mission/task 数据模型 | `not_started` | mission 可拆多个 task |
| Profile Registry | `not_started` | 系统内置 profile + 用户自定义 profile + 版本化 resolved profile |
| profile 定义 | `not_started` | planner/coder/tester/reviewer/doc-writer 权限、模型、workspace、artifact 要求不同 |
| profile -> agent instance 映射 | `not_started` | 一个 profile 可启动多个 SAEU instance |
| task -> SAEU 调度策略 | `not_started` | mission/task DAG 中的 task 默认创建 SAEU run |
| runtime SubAgent 内部优化策略 | `deferred` | 仅作为 SAEU 内部能力；不进入 MVP 平台调度 |
| task dependency | `not_started` | 支持串行和 fan-out/fan-in |
| artifact handoff | `not_started` | 子任务只通过 artifact 传递结果 |
| reviewer gate | `not_started` | 高风险 finding 阻塞合并 |
| final report | `not_started` | 汇总所有子任务和 artifact 引用 |

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

近期只做 P1 和 P2，不提前铺太大：

1. qwen serve SAEU adapter。
2. canonical event schema。
3. Event Store。
4. Permission Service。
5. Artifact Collector。
6. runtime adapter capability schema。
7. 基础 replay。

这些完成前，不建议投入复杂多 Agent supervisor，也不建议接入 Temporal 或云沙箱。
