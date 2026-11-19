# Tentacles 文档总览

`docs/` 按目标受众划分为以下区域（另含本地 `.gitignore` 忽略的 `docs/plans/` 计划草稿区）：

## 1. 配置、部署与使用入口（根目录）

- **[项目配置与部署手册](../README.md)**：安装、`.env` 与 `config/tentacles.yaml` 全部配置项、冷热存储、命名模型、信息源、流水线、安全边界与 `launchd` 调度。
- **[Agent Skills 目录](../skills/)**：面向 AI Agent 与日常 CLI 操作的技能集合：
  - [`tentacles-feed`](../skills/tentacles-feed/SKILL.md)：检索条目（`items list/search/show`）与阅读报告（`reports list/show`）
  - [`tentacles-persona`](../skills/tentacles-persona/SKILL.md)：查看、反馈、编辑、演化与回滚 Persona
  - [`tentacles-ops`](../skills/tentacles-ops/SKILL.md)：抓取、过滤、成稿、`tick` 调度、浏览器源运维、故障账本排障与冷热存储维护

## 2. Web 与接口文档

- **[Web 管理界面与 HTTP REST API 参考](api.md)**：Web 六大页面导览、Bearer 鉴权、错误码映射与完整 REST API 路由表。
- **[前端开发说明](../frontend/README.md)**：Vite 开发代理、OpenAPI 类型生成与生产构建。
- **[有界面浏览器脚本契约](../scripts/browser_sources/README.md)**：`extract()` 契约、AST 静态门禁与自愈/PR 流程。

## 3. 代码研发 Agent 规范与内部参考（Dev Agents）

位于 **`agents/`**（由仓库根 [`AGENTS.md`](../AGENTS.md) 索引）：

- [Issue Tracker 约定](agents/issue-tracker.md)
- [Triage 标签映射](agents/triage-labels.md)
- [领域文档约定](agents/domain.md)
- [实施计划存放规范（本地 `docs/plans/` -> GitHub Issues）](agents/plans.md)
- [流水线与存储事务内部接缝（Processing Seams）](agents/processing-seams.md)
- [#18 规格核对历史记录](agents/spec-18-audit.md)
