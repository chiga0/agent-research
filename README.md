# AgentFlow

AgentFlow is an Agent orchestration system for long-running cloud execution,
multi-agent task management, runtime governance, auditability, and recovery.

Online site: https://chiga0.github.io/agent-research/

## Key documents

- [可云端长期运行的多 Agent 系统落地方案](docs/implementation/index.md)
- [稳定单 Agent 执行单元](docs/implementation/stable-agent-execution-unit.md)
- [SubAgent 与独立执行单元边界](docs/implementation/subagent-vs-execution-unit.md)
- [基于 qwen-code serve 的云端单 Agent 单元方案](docs/implementation/qwen-serve-single-agent-cloud-unit.md)
- [沙箱与隔离方案](docs/implementation/sandbox-isolation.md)
- [ACP、A2A 与 MCP 协议选型](docs/implementation/protocol-acp-a2a.md)
- [Temporal 调研与适配方案](docs/implementation/temporal-evaluation.md)
- [事件溯源、JSONL 与回放](docs/implementation/event-sourcing-and-replay.md)
- [单 Agent 基座选型](docs/implementation/single-agent-strategy.md)
- [从单 Agent 执行单元到多 Agent 编排](docs/implementation/single-to-multi-agent-implementation-plan.md)
- [外部方案对比与多方向审计](docs/implementation/alternative-solutions-comparative-audit.md)
- [方案审计与 Review 记录](docs/implementation/review-and-audit-record.md)
- [实施 Roadmap](docs/implementation/implementation-roadmap.md)

## Local preview

```bash
python3 -m pip install -r requirements.txt
mkdocs serve
```

## AgentFlow Runtime

The runtime lives in [runtime](runtime/). It provides a stdlib Run Manager with
`/runs`, `/runs/{id}/input`, `/runs/{id}/events`, and `/runs/{id}/cancel` over a
pluggable SAEU adapter boundary.

```bash
python3 -m runtime.cloud_agents_runtime --host 127.0.0.1 --port 8765
python3 -m unittest discover -s runtime/tests
```
