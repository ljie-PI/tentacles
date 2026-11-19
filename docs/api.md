# Web 管理界面与 HTTP REST API 参考

Tentacles 通过 `tentacles serve` 在同一服务进程内托管已构建的前端静态页面（`server.static_dir`）与 FastAPI REST API，并与 CLI `--json` 共用同一套 Pydantic 响应模型。

- OpenAPI 规范：`GET /api/openapi.json`
- 生成/校验前端类型：在 `frontend/` 下运行 `pnpm generate:api` 或 `pnpm check:api`（底层调用 `python -m tentacles.api_schema`）

---

## 1. Web 管理界面功能概览

- **Feed**：按来源、时间、搜索词（SQLite FTS5 全文索引）、Pipeline 及通过/淘汰状态筛选资讯；抽屉展示正文（截断至 1000 字/词时提示）、原文与入口页 `page_url`、各阶段评分轨迹，并支持按 Persona 点赞/点踩。
- **Sources**：查看各源最近状态、累计条目数、下次运行时间，支持单源或全量立即抓取。
- **Pipelines**：查看待报告条目数（`pending`），手动触发「仅过滤」或「立即生成报告」。
- **Reports**：渲染 GFM Markdown 报告与引用条目（禁用原始 HTML、不自动加载外部图片，图片转为无引荐来源链接），点击引用可跳回 Feed 原文。
- **Personas**：编辑画像与关键词、输入演化指令预览行级 Diff 后应用、查看不可变版本历史并一键回滚。
- **Runs & Settings**：每 5 秒刷新运行记录、Token 用量与模型费用；在「故障详情 → 查看排障现场」展开已脱敏的 `debug_context`。在 **Settings** 页保存浏览器本地 Bearer Token、在线测试各模型连通性并查看调度循环状态。

---

## 2. 鉴权与统一错误响应

- **Bearer 鉴权**：当 `server.token` 非空时，除 `GET /api/health` 与静态前端资源外，所有 `/api/*`（含 `/api/openapi.json`）均要求请求头 `Authorization: Bearer <TOKEN>`。
- **错误响应格式**：`{"error": {"code": "...", "message": "..."}}`
- **状态码映射**：`invalid -> 400`、`unauthorized -> 401`、`forbidden -> 403`、`not_found -> 404`、`conflict / busy -> 409`、`upstream -> 502`、`error -> 500`。

---

## 3. REST API 路由表

| 方法与路径 | 返回模型与行为说明 |
| :--- | :--- |
| `GET /api/health` | `{"ok": true}`（免 Token） |
| `GET /api/settings` | `{"timezone": "Asia/Shanghai"}`（仅公开元数据，不含密钥或本地路径） |
| `GET /api/sources` | `SourceView[]` |
| `POST /api/sources/fetch` | 可选请求体 `{"ids": ["id"]}`，返回 `FetchOutcome[]`（省略时抓取全部启用源） |
| `POST /api/sources/{source_id}/fetch` | 单个 `FetchOutcome`；未知 ID `404`，运行中 `409`，抓取失败 `502` |
| `POST /api/sources/{source_id}/distill` | 显式重提炼 `type: browser` 源的固化脚本，返回 `FetchOutcome` |
| `GET /api/items` | `Page[ItemView]`；支持重复 `sources`/`kinds` 及 `since`、`until`、`search`、`pipeline`、`persona`、`passed`、`min_score`、`topic`、`sort`（`time`\|`relevance`）、`limit`(1–500)、`offset`、`include_archive`（默认 `false`）。`sort=relevance` 需同时提供 `search`（否则 `400`）；带 `search` 时额外返回高亮 `snippet`；开启 `include_archive=true` 扩展至年度冷库 |
| `GET /api/items/{item_id}` | `ItemDetail`（含入库正文、原文/入口链接、`meta` 与 `evaluations`）；支持 `include_archive=true`，热库优先、冷库结果带 `archive_year` |
| `GET /api/items/{item_id}/feedback` | 查询指定或默认 `persona` 的最新投票 `FeedbackView \| null` |
| `POST /api/items/{item_id}/feedback` | 提交投票：`rating` 为 `+1` 或 `-1`，可选 `persona`、`note` |
| `GET /api/personas` | 当前所有画像最新版本 `PersonaView[]` |
| `GET /api/personas/current` | 查询参数 `name`（必填）、`version`（可选） |
| `PATCH /api/personas` | 编辑或新建画像（必填 `name`、`expected_version`，`0` 表示新建；并发冲突返回 `409`） |
| `GET /api/personas/history` | 查询参数 `name` 的全部历史版本列表 |
| `POST /api/personas/evolve` | 生成只读演化预览提案 `PersonaProposal`（不写库，会调用模型） |
| `POST /api/personas/apply` | 原子校验 `base_version` 并应用提案，返回新 `PersonaView` |
| `POST /api/personas/rollback` | 复制历史版本 `version` 追加为新版本（需传 `expected_version`） |
| `GET /api/pipelines` | `PipelineView[]`（含 `pending` 数与调度状态） |
| `POST /api/pipelines/{pipeline_id}/filter` | `PipelineOutcome`；可选 `{"reevaluate": true, "since": "7d"}` |
| `POST /api/pipelines/{pipeline_id}/run` | 运行过滤与处理器生成报告；成功/`no_new` 返回 `200`，忙 `409`，失败 `502` |
| `GET /api/reports` / `GET /api/reports/{report_id}` | 分页报告列表 `Page[ReportView]` 与单篇报告全文 |
| `GET /api/models` / `POST /api/models/{name}/test` | 命名模型列表（仅含 `key_configured` 布尔值）与连通性探测 `ModelTest` |
| `GET /api/runs` | `RunView[]`（可选筛选 `kind`、`target`、`limit`） |
| `GET /api/runs/{run_id}` | `RunDetail`（`RunView` + 本次关联 `errors: ErrorEventView[]`）；未知 ID `404` |
| `GET /api/errors` | `Page[ErrorEventView]`；支持 `unresolved`、`source`、`pipeline`、`phase`、`error_code`、`run_id`、`since`、`until`、`limit`(1–500)、`offset`（列表不含 `debug_context`） |
| `GET /api/errors/{error_id}` | `ErrorEventDetail`（含已脱敏 `debug_context`，序列化后 <= 16 KB）；未知 ID `404` |
| `GET /api/scheduler` / `GET /api/scheduler/loop` | 持久化任务列表 `TaskView[]` 与内置轮询循环统计 |
| `POST /api/scheduler/tick` | 手动触发一轮到期任务 `TickOutcome[]` |
| `GET /api/storage/status` | `StorageStatus`（热库与冷库体积、行数与 FTS 统计） |
| `POST /api/storage/reindex` | 查询参数 `optimize`、`include_archive`，返回 `StorageReindex` |
| `POST /api/storage/archive` | 请求体 `{"before": "365d", "dry_run": false, "vacuum": false}`，返回 `StorageArchive` |
