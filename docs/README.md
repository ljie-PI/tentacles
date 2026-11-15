# Tentacles 文档总览

`docs/` 按目标受众划分为三个已追踪区域（另含本地 `.gitignore` 忽略的 `docs/plans/` 计划草稿区）：

## 1. 用户手册：配置与运行项目（面向人类使用者）

位于 **[`manual/user/`](manual/user/README.md)**：

- [快速开始与手册导览](manual/user/README.md)
- [基础配置与命名模型](manual/user/configuration-and-models.md)
- [信息源配置（RSS / IMAP / Web）与原位更新](manual/user/sources.md)
- [个人画像（Persona）、级联过滤与报告流水线](manual/user/pipelines-and-personas.md)
- [Web 管理界面、常驻服务、安全鉴权与定时调度](manual/user/server-and-web.md)

## 2. Agent CLI 手册：使用采集信息与操控画像（面向消费侧 AI Agent）

位于 **[`manual/agent-cli/`](manual/agent-cli/README.md)**：

- [全局 JSON 调用契约与错误退出码](manual/agent-cli/README.md)
- [读取与消费采集结果（Items & Reports）](manual/agent-cli/consuming-items-and-reports.md)
- [通过 Agent CLI 反馈、修改与演化 Persona](manual/agent-cli/persona-and-feedback.md)
- [触发抓取、运行流水线与状态/计费审计速查](manual/agent-cli/operations-reference.md)

## 3. 代码研发 Agent 规范与内部参考（Dev Agents）

位于 **`agents/`**（由仓库根 [`AGENTS.md`](../AGENTS.md) 索引）：

- [Issue Tracker 约定](agents/issue-tracker.md)
- [Triage 标签映射](agents/triage-labels.md)
- [领域文档约定](agents/domain.md)
- [实施计划存放规范（本地 `docs/plans/` -> GitHub Issues）](agents/plans.md)
- [流水线与存储事务内部接缝（Processing Seams）](agents/processing-seams.md)
- [#18 规格核对历史记录](agents/spec-18-audit.md)
