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
