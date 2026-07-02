# AgentFlow

AgentFlow 是一个面向长周期云端执行、多 Agent 编排、运行时治理、审计与恢复的 Agent 编排系统。

## 文档入口

- [可云端长期运行的多 Agent 系统落地方案](implementation/index.md)
- [稳定单 Agent 执行单元](implementation/stable-agent-execution-unit.md)
- [SubAgent 与独立执行单元边界](implementation/subagent-vs-execution-unit.md)
- [基于 qwen-code serve 的云端单 Agent 单元方案](implementation/qwen-serve-single-agent-cloud-unit.md)
- [沙箱与隔离方案](implementation/sandbox-isolation.md)
- [ACP、A2A 与 MCP 协议选型](implementation/protocol-acp-a2a.md)
- [Temporal 调研与适配方案](implementation/temporal-evaluation.md)
- [事件溯源、JSONL 与回放](implementation/event-sourcing-and-replay.md)
- [单 Agent 基座选型](implementation/single-agent-strategy.md)
- [从单 Agent 执行单元到多 Agent 编排](implementation/single-to-multi-agent-implementation-plan.md)
- [外部方案对比与多方向审计](implementation/alternative-solutions-comparative-audit.md)
- [方案审计与 Review 记录](implementation/review-and-audit-record.md)
- [实施 Roadmap](implementation/implementation-roadmap.md)
- [AgentFlow 与企业级 Cloud Agents 技术调研报告](cloud-agents/index.md)
- [生产级多 Agent 系统编排与运行屏障工程](multi-agent/research-on-multi-agent-orchestration-frameworks.md)
- [Qwen Code Core 与 SDK Agent 架构分析](other-agents/qwen-code-core-sdk-agent-architecture.md)

## 本地预览

```bash
python -m pip install -r requirements.txt
mkdocs serve
```

## 部署

本站使用 MkDocs + Material 构建，并通过 GitHub Pages 发布。
