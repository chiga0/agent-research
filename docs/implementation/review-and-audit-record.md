# 方案审计与 Review 记录

> 本记录用于审计“SAEU contract -> qwen serve 第一实现 -> ACP-compatible 多执行器 -> Supervisor/SubAgent/SAEU 编排”的方案可行性。结论：方案可以作为 MVP 到 Beta 的实施路线，但必须坚持 SAEU contract、外部 Event Store、权限服务和 workspace 隔离四条底线。

## Review 结论

最终方案通过四轮 review：

1. 架构边界 review：确认外部编排只依赖 SAEU contract，不直接绑定 qwen serve 私有 API。
2. 协议能力 review：确认 ACP/A2A/MCP 各自边界，ACP 用于内部执行器控制，A2A 用于外部互操作。
3. 安全与权限 review：确认 qwen serve 不公网暴露，密钥不进容器，权限请求一等建模。
4. 恢复与排障 review：确认 event ring 不是审计日志，必须有外部 Event Store 和 artifact 包。

## 第一轮：架构边界 Review

### 审查问题

是否可以抽象出“稳定单 Agent 执行单元”，并把它作为外部编排、调度、A2A Gateway 的基础单元？

### 发现

可以，而且必须这样做。否则多 Agent 编排会直接依赖某个 CLI 或 daemon 的私有语义，导致后续无法替换 worker。

风险点：

- qwen serve 是一 daemon 一 workspace，天然就是 Qwen 路线的 SAEU 实现，但不能代表所有 worker。
- qwen SSE event ring 是短期重连缓冲，不是长期状态。
- 如果外部编排直接调用 qwen serve 私有 API，后续接入 Claude Code/Codex/OpenCode/Gemini CLI 会非常痛。

### 修正

新增 SAEU contract：

- 统一生命周期。
- 统一事件 schema。
- 统一权限面。
- 统一 artifact 和 diagnostics。
- 统一恢复语义。

### 结论

通过。多 Agent 编排的调度原子确定为 SAEU contract；Qwen 的第一实现是受管 `qwen serve` daemon。

## 第二轮：协议能力 Review

### 审查问题

A2A 是否足够管理 Agent、通信、获取实时状态、暴露权限请求？

### 发现

A2A 可以覆盖开放式 Agent-to-Agent 的主干能力：

- Agent Card。
- task 创建。
- task status。
- streaming update。
- artifact。
- cancel。
- push notification。

但 A2A 不应承担内部执行器控制的所有细节：

- workspace 诊断。
- qwen daemon status。
- event ring gap。
- session load/resume。
- tool stdout/stderr。
- coding-agent permission option schema。

权限请求可以通过 A2A 的状态、message、metadata 或 extension 表达，但不能假设所有 A2A 客户端都理解内部 permission schema。

### 修正

协议策略调整为：

```text
外部互操作: A2A Gateway
内部执行单元控制: SAEU contract
内部执行器控制: ACP-first SAEU contract
qwen 第一实现: qwen serve HTTP/SSE + ACP bridge，后续优先 `/acp` Streamable HTTP
工具接入: MCP Gateway
```

### 结论

通过。A2A 作为系统边界协议，不作为内部 worker contract；内部 worker contract 应向 ACP-compatible 方向收敛。

## 第二点五轮：SubAgent 边界 Review

### 审查问题

既然 Qwen Code、Claude Code、Codex、OpenCode 本身支持 SubAgent 和并行调度，是否还需要独立 SAEU？

### 发现

需要，但不能滥用。SubAgent 是 Agent runtime 内部的协作机制，适合短周期探索、阅读、review、总结和共享上下文的轻量任务。SAEU 是平台治理边界，适合长运行、高风险、可恢复、可审计、跨客户端和跨机器任务。

如果把所有子任务都拆成独立 SAEU，会损失常驻 Agent 的上下文连续性，增加端口、workspace、daemon 和恢复复杂度。如果完全依赖 SubAgent，又缺少平台级审计、资源隔离和生命周期管理。

### 修正

架构策略调整为：

```text
常驻 Project/Supervisor Agent
  -> 使用 SubAgent 做轻量并行
  -> 对长任务/高风险/可审计任务创建 SAEU run
  -> 通过 Event Store / Artifact Store / Memory Store 汇总上下文
```

### 结论

通过。多 Agent 编排不等于“每个子 Agent 都是 SAEU”；而是 Supervisor 按治理需求在 SubAgent 和 SAEU 之间选择。

## 第三轮：安全与权限 Review

### 审查问题

基于 qwen serve 的单 Agent 单元是否能做到可控、安全和可审计？

### 发现

qwen serve 具备很好的起点：

- bearer token。
- non-loopback bind 安全门槛。
- permission mediation。
- 多客户端事件 attribution。
- remote runtime control。
- capability discovery。

但生产部署必须补强：

- 不直接暴露公网。
- 不把真实模型 key 放入容器。
- 不把 Docker socket、SSH key、云 credential 挂入容器。
- 高风险工具必须进入 permission flow。
- 所有 permission decision 必须写入外部 Event Store。

### 修正

在 qwen serve 云端单元方案中加入：

- per-unit token。
- model proxy。
- egress proxy。
- tool policy。
- artifact manifest。
- permission audit。
- container sandbox policy。

### 结论

通过。安全基线可行，但 qwen serve 只能作为内部 runtime，不应直接暴露公网。

## 第四轮：恢复、重放与排障 Review

### 审查问题

这个单 Agent 单元是否可以长期运行、重放、恢复和排障？

### 发现

qwen serve 已有：

- SSE `Last-Event-ID`。
- event ring。
- `/session/:id/load`。
- `/session/:id/resume`。
- `/daemon/status`。
- `/session/:id/status`。
- stats/context/tasks 等诊断端点。

但仍有生产缺口：

- event ring 有界，不能长期审计。
- qwen session 恢复不等于完整 run 恢复。
- tool output 和 workspace snapshot 需要外部保存。
- daemon 崩溃后必须明确恢复成功或失败，不能静默重跑。

### 修正

要求每个 run 产出 artifact 包：

- `manifest.json`
- `qwen-sse.raw.jsonl`
- `events.canonical.jsonl`
- `transcript.jsonl`
- `permissions.jsonl`
- `diagnostics.start.json`
- `diagnostics.end.json`
- `stdout.log`
- `stderr.log`
- `diff.patch`
- `final.md`

恢复策略分为：

- 客户端断线：从 Event Store 恢复。
- Supervisor SSE 断线：用 qwen `Last-Event-ID` 重连。
- qwen daemon 崩溃：重启并尝试 load/resume。
- worker 节点重启：扫描 running runs 并 attach/recover。

### 结论

通过。可恢复性依赖外部 Event Store 和 artifact，不依赖 qwen serve 单点缓存。

## Open Risks

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| qwen serve 仍是 experimental/local-first | 高 | 只作为内部 worker，用 Supervisor 管理；优先 adapter 和 ACP 收敛，必要时 fork 小边界 |
| 执行器接口锁死 qwen | 高 | 采用 ACP-compatible adapter contract，至少预留 Claude/Codex/OpenCode 接入 |
| 权限请求映射到 A2A 没有统一标准 | 中 | 内部 Permission Service 为准，A2A 只暴露 blocked/input-required 状态 |
| 小 VPS 资源不足 | 中 | 并发 1-2，队列限流，第二台 VPS 做 sandbox worker |
| 恢复无法确定性重放 | 中 | 保存 workspace snapshot、model/tool fixtures，先实现 UI/transcript replay |
| 多 Agent patch 冲突 | 中 | 每个 coder 独立 worktree，merge agent 独立处理 |
| Event Store 写入失败 | 高 | 写入失败时暂停 run 或标记 degraded，不能继续无审计执行 |

## 2026-07-01 P1/P2 实施审计

### 已落地能力

- P1 单 SAEU 闭环：Run Manager API、fake adapter、qwen serve adapter、canonical SSE、input、cancel、artifact 输出。
- 云端运行：systemd service、Docker Compose、bearer token、`qwen serve` supervisor、VPS 部署脚本。
- P2 审计硬化：SQLite `runtime.db`、`run_events` append-only 表、`raw_events` 表、per-run `events.jsonl` 和 `diagnostics.json`。
- 权限决策：`POST /runs/{run_id}/permissions/{permission_id}` 写入 `permission.resolved`，同时生成权限 artifact。
- 恢复与重连：SSE 支持 `Last-Event-ID`；超出可用序号时写入 `event.gap_detected`。
- 回放：`scripts/replay_run.py` 支持 events、SSE frame、state 三种输出。

### 验证门禁

- `python3 scripts/check_style.py`
- `python3 -m compileall -q runtime scripts`
- `python3 scripts/check_runtime_coverage.py`，当前 runtime 覆盖率高于 90%。
- `python3 scripts/validate_runtime.py` 可验证 health、capabilities、run、SSE、artifact、`runtime.db`。
- ECS 验收需覆盖 fake run、qwen run、systemd restart recovery、artifact replay。

### 实施取舍

- MVP 使用 SQLite 作为 durable event store。它满足单控制面、单 VPS 的可部署验收，但不是多控制面/高并发最终方案。
- Postgres、jobs/leases、worker heartbeat、resource limit 和多 worker 调度进入 P3。
- Permission timeout 的自动 deny/cancel 尚未进入 P2 MVP；当前先保证人工决策的完整审计链。
- stdout/stderr 类 worker 进程日志将在独立 sandbox worker 引入后纳入 artifact manifest。

## 2026-07-01 P3 实施审计

### 项目测试要求

- Runtime CI 必须通过 `python3 scripts/check_style.py`。
- Runtime CI 必须通过 `python3 -m compileall -q runtime scripts`。
- Runtime CI 必须通过 `python3 scripts/check_runtime_coverage.py`，runtime 覆盖率门槛为 90%。
- Runtime CI 必须通过 `mkdocs build --strict`。
- 本地 smoke 需要覆盖 health、capabilities、queue、create run、SSE、artifact、`runtime.db`。

### 多轮审计结论

- 正向审计：jobs/leases、worker heartbeat、capacity、per-run workspace、resource policy、timeout watchdog、cleanup policy 都有 runtime tests 覆盖。
- 反向审计：覆盖 unknown adapter、远程 repo 拒绝、超资源请求拒绝、queued run 不清理、shared workspace 不清理、unauthorized cleanup。
- 恢复审计：artifact 被清理后，canonical events 仍保留在 SQLite；`scripts/replay_run.py` 已支持从 `runtime.db` fallback replay。
- 隔离审计：local git source 使用 detached worktree；cleanup 现在优先用 `git worktree remove --force`，避免源仓库残留 stale worktree metadata。
- 运维审计：`POST /cleanup` 需要 bearer token；默认 retention 下不会误删新完成 run。

### 新增回归覆盖

- artifact cleanup 后仍可通过 DB fallback 运行 `scripts/replay_run.py --format state`。
- artifact 先清理、workspace 后清理时，不会重新留下 artifact 目录。
- git worktree cleanup 后，`git worktree list --porcelain` 不再包含已删除 workspace。
- cleanup policy 在 API 层受 auth 保护，并通过 `/capabilities` 暴露。

### P3 剩余风险

- CPU/memory/pids 当前是执行单元级限制；严格 per-run cgroup 需要容器 worker。
- 双 VPS worker 模式仍是 deferred；当前 P3 完成的是单 VPS / local worker 主线。
- SQLite 适合当前单控制面 MVP；多控制面需要迁移到 Postgres 或等价外部事件库。

## 2026-07-01 P4 实施审计

### 已落地能力

- Profile Registry：内置 `planner`、`coder`、`tester`、`reviewer`、`doc-writer`，并支持用户 profile 版本化。
- Mission 数据模型：`missions`、`mission_tasks`、`mission_events` 与 run 表共存于 SQLite。
- Supervisor MVP：确定性 `MissionManager` 负责 ready task 识别、创建 SAEU run、监听 run terminal event 并推进依赖。
- DAG 策略：支持 `sequential`、`fanout`、`custom`；custom DAG 做 duplicate、missing dependency、cycle 校验。
- Artifact handoff：下游 task run spec 写入 dependency artifact refs，不共享 sibling workspace。
- Final report：mission 完成后生成 `final_report.md`，并持久化 `mission_manifest.json` 与 task JSON。
- API：`/profiles`、`/missions`、mission events/artifacts、mission cancel 已接入 Run Manager HTTP 层。
- 验收脚本：`scripts/validate_runtime.py --validate-mission` 可做两 task mission smoke。

### 多轮审计结论

- 正向审计：`mission -> task -> profile -> SAEU run` 已复用 P1-P3 的 run queue、workspace、resource、event、artifact 和 cleanup 能力。
- 反向审计：覆盖 unknown profile、unknown adapter、bad DAG、cycle、duplicate task、bad mission payload。
- 取消审计：测试发现 active child run 先取消会把 pending dependent task 标成 `blocked`；已修复为先取消无 run 的 pending task，再取消 active child run。
- 恢复审计：mission/task/profile 进入 SQLite；`MissionManager.reconcile()` 会在启动时同步已有 task run 状态并继续调度 ready task。
- 隔离审计：P4 没有引入跨 task 共享可写 workspace；profile 里的 workspace policy 先作为审计模板，不绕过 P3 workspace allocator。
- 覆盖率审计：新增 P4 后 runtime coverage 维持在 90% 以上，满足 CI 门槛。

### P4 剩余风险

- Supervisor 仍是规则控制器，不是长期 Project Agent；长期 memory、目标管理和人机协作需要后续设计。
- Reviewer gate 已支持结构化 JSON 阻塞；人工 override 和 merge/deploy gate 尚未实现。
- Artifact handoff 只传 artifact 引用，不做 patch merge、diff conflict 解决或 artifact 内容内联。
- qwen 多 workspace 强隔离仍需 per-workspace daemon registry 或容器 worker；当前 mission 多 task 可以用 fake 完整验收，qwen 真实多 task 要等部署密钥恢复后再跑。

## 2026-07-01 P4.1 Reviewer Gate 实施审计

### 已落地能力

- 内置 `reviewer` profile 要求 `review_gate.json`，并标记 `artifacts.gate.type=reviewer`。
- 新增 reviewer gate schema：`decision`、`severity`、`reason`、`findings`。
- 支持 `pass`、`warn`、`block`、`needs_human` 四种 decision。
- 高/严重 finding 会把 `pass` 或 `warn` 保守提升为 `block`。
- 缺失或非法 `review_gate.json` 会进入 `needs_human`，并阻塞下游 pending task。
- Mission Supervisor 产出 `review.gate_passed`、`review.gate_warned`、`review.gate_blocked`、`review.gate_needs_human` 事件。
- gate 阻塞时 mission 状态为 `blocked`，并写入 mission 级 `review_gate.json` 和 `final_report.md`。
- fake adapter 会为 reviewer task 生成 pass gate，保证本地 P4/P4.1 smoke 可重复验证。

### 多轮审计结论

- 正向审计：warn gate 允许下游 report task 继续，mission 最终 `completed`。
- 反向审计：block/high finding 会阻塞 report task，mission 进入 `blocked`。
- 保守审计：缺失 gate artifact 被判定为 `needs_human`，不会静默放行。
- schema 审计：非法 decision、非法 finding、未知 severity 都不会降低风险等级。
- 恢复审计：gate 结果写入 task result、mission event、mission artifact；重启后不会重复评估已写入的 gate。

### P4.1 剩余风险

- 当前 gate 只解析结构化 JSON，不从自由文本 `review-findings.md` 推断风险。
- 人工 override、审批超时、merge/deploy gate 尚未实现。
- qwen reviewer 必须按提示写出 `review_gate.json`；否则 mission 会保守阻塞为 `needs_human`。

## 2026-07-01 P4.2/P4.3/P5 POC 实施审计

### 已落地能力

- `POST /missions/{mission_id}/review-gate/override` 支持人工 `approve` / `deny`。
- approve override 会写入 `review_gate_override.json`、记录 `review.gate_override_recorded`，并恢复未启动下游 task。
- 新增 `release-gate` profile，使用 `release_gate.json` 和 `merge_deploy.gate_*` 事件做 merge/deploy 前置 gate。
- qwen adapter 会从 gate task 的最终文本 fenced JSON 中抽取 gate artifact。
- `/acp` 提供 JSON-RPC-over-HTTP POC：`initialize`、`run.create`、`run.status`、`run.input`、`run.cancel`。
- `/.well-known/agent-card.json`、`/a2a/tasks` 提供 A2A Gateway POC，把外部 task 映射成内部 mission。
- `/temporal/workflows/.../plan` 导出 `AgentRunWorkflow` / `MissionWorkflow` plan，明确 Temporal 只管理粗粒度编排引用。

### 审计结论

- 正向审计：blocked reviewer gate 经人工 approve 后可以恢复下游 task，mission 最终完成。
- 反向审计：release gate 的 critical/block 会阻塞 deploy/report 下游 task。
- qwen 接入审计：qwen final text 中的 fenced JSON 能落为 `review_gate.json`，再由 supervisor 统一评估。
- 协议边界审计：ACP/A2A/Temporal 当前都是 POC wrapper，不替代内部 SAEU contract，也不承诺完整协议兼容。
- 恢复审计：override、gate、A2A task、Temporal plan 都通过 mission/run DB snapshot 和 artifact 引用恢复上下文。

### 剩余风险

- ACP endpoint 只是 JSON-RPC-over-HTTP POC，还不是官方 Streamable HTTP/WebSocket 完整实现。
- A2A Gateway 尚未实现完整 JSON-RPC task lifecycle、streaming、push notification 和 auth federation。
- Temporal POC 只导出 workflow plan，没有启动 Temporal Service、Worker 或 Python SDK workflow。
- qwen gate extraction 依赖模型按提示输出 fenced JSON；不从任意自然语言推断风险。
- 人工 override 尚未实现审批超时、多人审批、策略引擎或审计签名。

## 2026-07-02 P5/P6 Beta Ready 与产品化管理台审计

### 已落地能力

- `/metrics.json` 提供 run/mission/queue/permission/failure/latency 指标。
- `/ops/status`、`/ops/drills`、`/ops/backups` 提供 beta 运维面：状态、演练、DB + artifact 备份和下载。
- ACP POC 扩展到 run/mission events/artifacts；A2A POC 扩展到 task events/artifacts。
- React + Tailwind + TanStack Router/Form/Query 管理台替换静态页面，使用 hash routing 适配 `/cloud-agents/` 公网前缀。
- 管理台覆盖 Overview、Runs、Run Detail、Missions、Profiles、Operations；支持创建 run/mission、取消 run、处理 permission request、查看事件、下载 artifact/audit/backup、触发 drill。
- 管理台支持黑白主题、桌面侧边导航和移动端抽屉导航。
- CI 增加 Node 22、web lint、90%+ web coverage、production build、Playwright desktop/mobile E2E。
- Deploy workflow 增加 web gate，并将 `PUBLIC_DOMAIN`、stale worker、backup retention 配置传入部署脚本。

### 审计结论

- 产品可用性审计：首屏直接进入管理控制台，而不是说明页；功能按运行、任务、Profile、Ops 分区，核心操作均有明确按钮和下载入口。
- 路由审计：使用 hash routing 避免 `/cloud-agents/runs` 刷新时被后端 API `/runs` 截获。
- 恢复审计：备份包包含 `runtime.db` 和 artifact 目录，排除 workspace 和 backup 自身，能支撑单节点 beta 恢复。
- 安全审计：公网仍由 Nginx Basic Auth + bearer 注入保护；Run Manager token 不暴露给浏览器。
- 测试审计：前端单元/集成覆盖率超过 90%，E2E 覆盖桌面和移动端主流程；后端 runtime coverage gate 继续保持。

### 剩余风险

- 成本预算仍缺 model proxy/provider billing 接入，当前只能通过 timeout、profile limits、resource policy 间接治理。
- 备份是单节点 tar.gz 形态；多控制面或高并发阶段仍需要 Postgres + 对象存储策略。
- P5 ACP/A2A 仍是 POC facade，不等同官方完整协议实现。
- Web 管理台当前是单租户控制台；团队权限、审计签名、多人审批属于后续 P7/P8。

## 2026-07-02 Runner 实时进展与产品可用性补充

### 设计结论

- Run Detail 不应只展示 raw event table；用户需要一个能直接理解“runner 正在做什么”的实时视图。
- 产品层采用双层结构：`Live Runner Chat` 面向操作员阅读，`Event Stream` 面向审计和排障保留原始 canonical event。
- 实时视图直接订阅 `GET /runs/{run_id}/events` SSE；浏览器或测试环境不支持 EventSource 时回落到现有 `events.json` 轮询数据。
- `message.delta` 按 prompt 聚合为连续 Agent 输出；`step.*`、`permission.*`、`stream.warning`、`run.completed/failed/cancelled` 转译为 timeline/chat bubble。
- 权限请求仍保持独立 `Permission Requests` 操作区，避免审批按钮埋在日志流里。

### 已落地能力

- Run Detail 新增 `Live Runner Chat` 卡片，展示连接状态、run status、last event、sequence。
- SSE 事件进入前端后按 sequence 去重合并，可承接 Last-Event-ID 重连后的补偿事件。
- 桌面和移动端 E2E 增加 Live Runner Chat 断言。
- 前端 coverage gate 保持 90%+。

### 后续优化

- 增加“只看 Agent 输出 / 只看权限 / 只看 warning”的过滤器。
- 增加 runner 内部工具调用的结构化展示，例如 command、cwd、exit code、stdout/stderr 摘要。
- 支持从 Live Runner Chat 一键下载当前 run 的可读执行报告。

## 2026-07-02 P7 产品化控制台补充

### 设计结论

- Mission 必须有独立详情页；否则多 Agent 编排只停留在列表和报告下载，无法解释“任务是如何拆分、依赖和推进的”。
- Profile 是版本化执行模板，不是 Agent instance；系统内置 profile 应可复制，用户 profile 应可编辑并形成新版本。
- Runner Chat 面向操作员，Raw Event Stream 面向审计；两者都保留，且 Chat 需要过滤、stalled signal 和可下载可读报告。
- Access/RBAC 在当前 beta 先做 foundation：明确当前 principal、role matrix、scope 和审计边界，后续可替换为企业 IAM。
- 多 SAEU 隔离不应再绑定单一 qwen endpoint；后续升级为 Executor Registry，支持 shared daemon、per-run daemon、container worker 和 remote worker。

### 已落地能力

- Mission Detail：状态、Task DAG、依赖、子 run 跳转、mission events、mission artifacts、review gate override。
- Runner Chat v2：Agent/permission/warning/error 过滤、一键下载执行报告、stalled signal、adapter/tool event 摘要。
- Profile Editor：复制系统 profile、新增/编辑用户 profile JSON policy、保存为版本化 profile。
- Access 页面与 `/access/policy`：展示单租户到企业 RBAC 的迁移基线。
- qwen mission 验收脚本：`scripts/validate_qwen_mission.py`。
- Executor Registry：`/executors`、`/runs/<run_id>/executor`、SQLite
  `executor_leases`、per-run qwen process、executor lifecycle events、stdout/stderr
  logs 和 `executor.json` 已落地。
- 公网可用性监控：`scripts/monitor_runtime.py` 检测 Basic Auth、console HTML/assets、
  `/health`、`/capabilities`、`/queue`、`/executors`、`/access/policy`；`Runtime Monitor`
  workflow 在部署完成后自动运行，并每 15 分钟定时巡检。

### 剩余风险

- Access/RBAC foundation 还不是完整用户系统；没有 org/project、role assignment、session login 和多人审批。
- Executor isolation 已从设计进入实现：per-run qwen process 可用，但 Docker/container
  worker、cgroup 资源限制、网络策略和远程 worker registry 仍需实机验收。
- Mission DAG 当前是产品可视化，不是图数据库或专业 workflow engine；复杂 DAG 后续仍可接 Temporal/LangGraph/Airflow 外层调度。
- 默认巡检不创建真实任务，避免监控污染任务队列；需要完整 runner 验证时通过
  workflow_dispatch 的 `deep_run` 或本地 `--deep-run` 手动触发。

## 2026-07-02 Qwen Per-run + Lightweight Mission 实机验收

### 验收配置

- GitHub Actions run：`Deploy Runtime` workflow_dispatch `28590700911`。
- 部署 revision：`8ab6b2a28050d25d13de5b52aa17cd66f35a95c8`。
- executor strategy：`per_run_process`。
- qwen acceptance：`validate_qwen=true`、`qwen_validate_mission=true`、`qwen_mission_task_count=1`、timeout `1200s`。
- 部署后 Runtime Monitor：workflow_run `28591480294` 成功。

### 验收结果

- `per_run_process` 单 run 成功完成，executor lifecycle 包含 `executor.starting`、`executor.acquired`、`executor.released`。
- 单 run artifact 包含 `events.jsonl`、`raw_events.jsonl`、`diagnostics.json`、`cost.json`、`executor.json`、stdout/stderr、permission request/resolution、`workspace.json`。
- 单 run executor 使用 `qwen serve --hostname 127.0.0.1 --port 4210`，状态 `released`，exit code `0`，workspace 为 per-run isolated workspace。
- 轻量 qwen mission `mission_a2625eb5f8e542a9817b546d73ae2adc` 完成 `1/1` task。
- Mission events 包含 `mission.created`、`task.created`、`mission.started`、`task.queued`、`task.run_created`、`task.running`、`task.completed`、`mission.completed`。
- Mission artifact 包含 `events.jsonl`、`final_report.md`、`mission_manifest.json`、`mission_spec.json`、`task_inspect.json`。

### 审计结论

- 单 SAEU 的 per-run qwen process 隔离已经具备真实可用性：可启动、可审计、可释放、可下载 executor artifacts。
- 轻量 mission 证明 “Supervisor -> task -> SAEU run -> artifact -> mission completion” 的真实 qwen 链路可跑通。
- 真实 qwen acceptance 仍然是分钟级深验收，不应放入默认 push 路径；当前只通过 workflow_dispatch 开启是正确边界。
- `mission_task_count=1` 适合小 VPS 的 smoke；`mission_task_count=2` 可验证 dependency handoff；更高任务数需要单独预算时间、内存和 qwen 配额。
- 下一项硬风险仍是 `container` executor：需要用 `qwen_container_build=true` 或真实 image 验证 Docker/cgroup/network、credential mount 和 cleanup。

## 2026-07-02 Container Executor 第一轮实机验收与诊断加固

### 验收配置

- GitHub Actions run：`Deploy Runtime` workflow_dispatch `28592133076`。
- executor strategy：`container`。
- container image：`qwen_container_build=true`，base image `node:22-bookworm-slim`，local tag `cloud-agents-qwen:local`。
- resource limit：`cpus=1`、`memory_mb=1024`、`pids=256`。
- qwen acceptance：`validate_qwen=true`、`qwen_validate_mission=false`、timeout `1200s`。

### 验收结果

- CI、Docker image build、VPS deploy、systemd service reload、runtime health 和 fake smoke 均通过。
- Fake smoke run `run_1661fea6bff443d0a5f6ff45f243775c` 完成，证明 container strategy 下控制面和队列基础链路可工作。
- qwen single-run `run_f324bcca642d4898ac5aae60ebc0df25` 在 container executor 启动阶段失败。
- executor lease 最后状态为 `failed`，`last_error` 为 `[Errno 104] Connection reset by peer`；当时 workflow 没有输出 `executor.stderr.log`，无法定位 qwen 容器内退出原因。
- Docker 命令曾把 `QWEN_SERVER_TOKEN=<value>` 放入 argv，存在进入 executor artifact / CI debug 输出的风险。

### 已修复

- `default_container_command()` 不再把 token 值写入 Docker argv，改为 `-e QWEN_SERVER_TOKEN -e QWEN_SERVE_TOKEN`，由父进程环境传入容器。
- `scripts/validate_qwen_mission.py` 在 single-run 失败时会列出 run artifacts，并拉取 `executor.stderr.log`、`executor.stdout.log`、`executor.json`、`diagnostics.json` 的尾部。
- 验收脚本输出现在会遮罩 `QWEN_*_TOKEN`、`Authorization: Bearer ...` 和 JSON/text token 字段，避免把运行时密钥带进 CI 日志。

### 本地验证

- `python3 scripts/check_style.py`
- `python3 -m compileall -q runtime scripts`
- `PYTHONPATH=runtime/tests python3 -m unittest discover -s runtime/tests`：71 tests passed。
- `/tmp/agent-research-coverage-venv/bin/python scripts/check_runtime_coverage.py`：71 tests passed，runtime coverage `91.35%`。
- `git diff --check`

### 审计结论

- Container executor 的控制面基础已可部署，但 qwen 容器内启动仍未验收通过，不能标记为 production-ready。
- 第一轮失败不是架构否定：部署、build、systemd、fake run 和 resource metadata 已通过，剩余风险集中在 qwen container runtime 环境。
- 下一轮必须基于新增 artifact 诊断重跑 container workflow，并按 stderr 决定是否调整容器用户、`HOME`/`.qwen` 挂载、settings 可写性、`QWEN_*` env 或 image 依赖。
- 在 container qwen acceptance 通过前，默认 push 部署继续保持 shared/per-run 稳定路径，不把 container strategy 作为公网默认运行形态。

## 2026-07-02 Container Executor 第二轮实机验收与 readiness 修复

### 验收配置

- GitHub Actions run：`Deploy Runtime` workflow_dispatch `28593133445`。
- executor strategy：`container`。
- container image：`qwen_container_build=true`，base image `node:22-bookworm-slim`，local tag `cloud-agents-qwen:local`。
- resource limit：`cpus=1`、`memory_mb=1024`、`pids=512`。
- qwen acceptance：`validate_qwen=true`、`qwen_validate_mission=false`、timeout `1200s`。

### 验收结果

- CI、Docker image build、VPS deploy、systemd service reload、runtime health 和 fake smoke 均通过。
- qwen single-run `run_bbcffecd56af40848302f32e35e032fa` 失败，最终错误仍为 `[Errno 104] Connection reset by peer`。
- 新增诊断证明容器内 `qwen serve` 已经启动并监听：
  - `executor.stdout.log` 输出 `qwen serve listening on http://0.0.0.0:4211`。
  - `executor.stderr.log` 显示 workspace 绑定到 per-run workspace，`processToListenMs=1488`、`runQwenServeToListenMs=114`，并启用 `/acp WebSocket transport`。
  - `executor.json` 中 Docker argv 仅包含 `-e QWEN_SERVER_TOKEN -e QWEN_SERVE_TOKEN`，未暴露 token 值。

### 根因判断

- 失败点已经从“qwen 容器是否能启动”收敛到 runtime readiness probe。
- `_wait_until_ready()` 过去直接对字符串 URL 请求 `/health`，没有带 bearer token。
- 启动瞬间的 raw `ConnectionResetError` / `OSError` 没有被视为可重试错误，因此 qwen 已监听后仍可能被 runtime 误判为 executor failed。

### 已修复

- readiness probe 改为构造 `urllib.request.Request`，在 lease token 存在时带 `Authorization: Bearer ...`。
- readiness probe 将 `OSError` 纳入启动窗口内的可重试错误，避免瞬时 TCP reset 直接终止 executor。
- 新增 `test_executor_readiness_retries_reset_with_auth`，覆盖首次 reset、二次成功和 health auth header。

### 审计结论

- 第二轮失败不是容器镜像启动失败；qwen 已在容器内完成监听，当前修复聚焦在 runtime 对健康探测的误判。
- Container executor 仍需第三轮 workflow_dispatch 实机验收，验收标准是 qwen single-run 完成且 executor lease 进入 released。
- 在第三轮通过前，container strategy 仍保持手动验收路径，不作为默认公网部署策略。

## 2026-07-02 Container Executor 第三轮验收阻塞：SSH keepalive

### 验收配置

- GitHub Actions run：`Deploy Runtime` workflow_dispatch `28593787794`。
- executor strategy：`container`。
- container image：`qwen_container_build=true`，base image `node:22-bookworm-slim`。
- resource limit：`cpus=1`、`memory_mb=1024`、`pids=512`。
- qwen acceptance：`validate_qwen=true`、`qwen_validate_mission=false`、timeout `1200s`。

### 验收结果

- 本地 CI gates、web E2E 和 deploy credential 写入均通过。
- 失败发生在 `Deploy runtime to VPS`，尚未进入 deployed runtime smoke 或 qwen deep acceptance。
- 日志显示 SSH 在远端 deploy 静默约 5 分 30 秒后断开：`client_loop: send disconnect: Broken pipe`。
- 该失败不能作为 qwen container acceptance 的负面结论；它暴露的是长时间远端 Docker build / deploy 命令缺少 SSH keepalive。

### 已修复

- `scripts/deploy_runtime_vps.sh` 增加统一 `SSH_OPTIONS`，用于 deploy SSH 和 qwen settings scp。
- 默认启用 `ServerAliveInterval=30`、`ServerAliveCountMax=60`、`TCPKeepAlive=yes`，允许慢 VPS 上的静默构建保持连接。
- keepalive 参数可通过 `DEPLOY_SSH_SERVER_ALIVE_INTERVAL` 和 `DEPLOY_SSH_SERVER_ALIVE_COUNT_MAX` 覆盖。
- 远端 deploy 脚本增加 `[deploy] ...` 阶段日志，并为 apt/npm/git/docker build/pull 增加命令级 timeout。
- 普通命令 timeout 默认 `900s`，Docker build/pull 默认 `1800s`，可通过 `DEPLOY_COMMAND_TIMEOUT_SECONDS` 和 `DEPLOY_DOCKER_BUILD_TIMEOUT_SECONDS` 覆盖。
- run `28595774630` 进一步定位到尚未进入远端脚本前，qwen settings `scp` 阶段发生 `kex_exchange_identification: read: Connection reset by peer`；已为 scp 增加 `ConnectTimeout=30`、单次连接尝试、多轮重试和 workflow deploy step 外层 timeout。

### 审计结论

- 第三轮没有触达 qwen runtime 层，当前下一步应先重跑默认 stable deploy 确认 VPS 状态恢复，再重跑 container workflow。
- Container executor 的 qwen single-run 验收状态仍为 `pending`，不是 failed by qwen。
- 当前默认 stable deploy 的最新失败点是 SSH/scp 传输层，不是 runtime service、qwen adapter 或 worker registry 功能失败。
- 后续若 Docker build 仍超过命令 timeout，应改为远端 `systemd-run`/后台 build job + poll 日志，或预构建/发布 executor image，减少小 VPS 在线构建压力。

## 2026-07-02 Remote Worker Registry Foundation 审计

### 目标

- 支持未来多 VPS worker：控制面保存 run/job/lease/audit，远程 worker 通过中心 API 注册、抢占任务、执行本地 SAEU，并回传事件与 artifact。
- 保持当前单 VPS 本地 worker 兼容：默认 push 部署仍可使用同进程 worker，不强制引入第二台机器。

### 已实现

- `WorkerState` 增加 `metadata`，持久化到 SQLite `workers.metadata_json`，并对既有 DB 自动补列。
- 远程 worker metadata 支持 `kind`、`endpoint`、`hostname`、`version`、`region`、`zone`、`labels`、`capabilities`、`resources`、`executor`、`sandbox`。
- 新增控制面 API：
  - `POST /workers/{worker_id}/heartbeat`
  - `POST /workers/{worker_id}/claim`
  - `POST /workers/{worker_id}/runs/{run_id}/events`
  - `POST /workers/{worker_id}/runs/{run_id}/artifacts`
  - `GET /workers/{worker_id}`
- `claim` 复用现有 `run_jobs` lease；中心 `worker_capacity=0` 时不会本地抢占任务，适合纯 control-plane 模式。
- 远程 worker 回传 event/artifact 前会校验 run lease 属于该 worker，防止跨 worker 写入。
- 远程 worker claim 会按 run `metadata.worker_requirements` 或 `metadata.required_worker` 过滤：
  - `adapters` / `features` 匹配 worker `capabilities`。
  - `labels` 匹配 worker `labels`，并兼容 top-level `region`、`zone`。
  - `resources` 中数值型要求按 `worker >= required` 判断。
  - `executor` / `sandbox` 按 key-value 精确匹配。
- 远程 artifact 上传支持 text `mode=append`、`chunk_index`、`final`；JSON artifact 保持 write-only。
- API token registry 已接入 runtime 鉴权，master `RUN_MANAGER_TOKEN` 保持全权限；scoped token 需要匹配路由 scope 才能访问。
- 远程 worker HTTP API 要求 `workers:write` 或 `workers:*`；worker token revoke 后立即失效。
- Nginx 增加 `/cloud-agents-worker/` 专用入口，透传 worker 的 Bearer token；浏览器 `/cloud-agents/` 仍保持 Basic Auth 并由 Nginx 注入内部 master token。
- 新增 `python -m cloud_agents_runtime.worker` 远程 worker daemon foundation：
  - 使用 stdlib HTTP client 连接 control-plane。
  - 周期 heartbeat，`/claim` 成功后在本机执行 fake/qwen adapter。
  - 通过 HTTP-backed store 把 canonical events、raw event artifact 和 JSON artifact 回传中心。
  - `raw_events.jsonl` 使用 append chunk 上传，worker 本地 artifact mirror 也写入同名 JSONL。
  - 支持 `--once` 验收模式和长期 poll 模式。
- 新增 `deploy/systemd/cloud-agents-worker.service`、`cloud-agents-worker.env.example` 和 `scripts/deploy_worker_vps.sh`，用于第二台 VPS 上安装依赖、同步 repo、写入 worker env、启动 worker daemon。

### 测试覆盖

- `test_remote_worker_registry_claims_events_and_artifacts` 覆盖 manager 级 heartbeat、claim、metadata 合并、event 回传、artifact 写入/append、跨 worker 越权拒绝和 terminal 后 capacity 释放。
- `test_remote_worker_claim_respects_requirements` 覆盖 adapter、feature、label、resource 能力匹配调度。
- `test_remote_worker_http_registry_claims_and_reports_run` 覆盖 HTTP API 级注册、worker 查询、claim、event、artifact append 和 run completion。
- `test_auth_protects_run_routes_and_allows_health` 覆盖 scoped token forbidden 和 revoked token unauthorized。
- `test_remote_worker_http_registry_claims_and_reports_run` 同时覆盖 `workers:*` scoped token 可执行 worker 闭环、但不能访问 access 管理接口。
- `test_remote_worker_daemon_once_executes_fake_run` 覆盖真实 HTTP control-plane 下远程 worker daemon claim、heartbeat、执行 fake adapter、分片上传 raw events、中心 run completion 和 worker metadata。

### 审计结论

- 当前实现已经具备多 VPS worker registry + worker daemon 的基础闭环；单台控制面 + 第二台 worker VPS 的最小拓扑可以从代码层跑通。
- qwen worker 目前要求 worker 本机已有 `QWEN_SERVE_URL` 或等价 qwen serve endpoint；per-run/container qwen executor 与 remote daemon 的深度结合仍是后续项。
- 能力匹配已覆盖 adapters/features/labels/resources/executor/sandbox 的 foundation；更复杂的优先级、亲和性、反亲和性和队列公平性仍未实现。
- Artifact API 当前支持轻量 JSON/text 和 text append；二进制产物、大文件压缩、断点续传和对象存储 multipart 仍未实现。
- 多 VPS deploy 已有手动脚本和 systemd worker unit；尚未接 GitHub Actions workflow_dispatch 自动部署 worker VPS。
- Worker route 当前依赖 TLS + scoped bearer token；mTLS、Nginx IP allowlist、WireGuard/Tailscale 仍属于外部部署配置。
- 远程取消和 permission resolution 仍需补 worker pull-loop 对 control-plane state 的反向订阅/轮询。

## Go / No-Go 决策

### Go

- 单 SAEU + qwen serve adapter。
- ACP-compatible runtime adapter contract。
- SubAgent/SAEU 调度边界。
- 外部 Event Store。
- Permission Service。
- Artifact Collector。
- 多 Agent 通过 Supervisor + artifact 协作。
- A2A Gateway 放在系统边界。

### No-Go

- 直接把 qwen serve 暴露公网。
- 多 Agent 共享同一个可写 workspace。
- 把 qwen event ring 当审计日志。
- 把 A2A 当内部 worker 全量控制协议。
- 把所有 SubAgent 都强制拆成独立 daemon。
- 从头实现 coding agent core。

## 最终可实施判断

方案可行，且适合 1-2 台 VPS 起步。推荐第一阶段只实现一个 qwen serve SAEU adapter，把审计、权限、事件、artifact 和恢复做扎实；第二阶段再抽象 ACP-compatible runtime adapter；第三阶段再做常驻 Supervisor + SubAgent + SAEU 的多 Agent 编排。

只要不跳过单 Agent 单元稳定性，多 Agent 编排不会建立在松散 CLI 进程上，而是建立在可管理、可恢复、可审计的执行单元上。
