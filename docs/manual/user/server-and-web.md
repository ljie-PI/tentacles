# Web 管理界面、常驻服务与自动调度

Tentacles 支持两种日常运行方式：**常驻守护服务（`tentacles serve`）** 或 **系统级无状态定时触发（`tentacles tick`）**。两者共用 SQLite 中的持久化任务进度与 1 小时过期原子租约锁，即使混用也不会重复并发跑同一任务。

---

## 1. 启动 Web 管理界面与 REST 服务

### 构建前端与启动后端

```bash
# 1. 构建前端静态资源（要求 Node >= 22.12, pnpm 10.33）
cd frontend
pnpm install --frozen-lockfile
pnpm build
cd ..

# 2. 启动 HTTP API、静态网页托管与内置轮询调度器
tentacles serve
```

- CLI 参数 `--host`、`--port`、`--scheduler / --no-scheduler` 可临时覆盖 YAML 中的 `server:` 配置。
- 前端开发调试可在一个终端运行 `tentacles serve --no-scheduler`，另一个终端在 `frontend/` 下运行 `pnpm dev`（Vite 代理默认保留同源 `Host` 头）。

### Web 页面功能一览
- **Feed**：按来源、时间、搜索词、Pipeline 及通过/淘汰状态筛选资讯（搜索走 SQLite FTS5 全文索引，毫秒级返回），展开抽屉阅读全文与各阶段评分轨迹，直接按画像点赞/点踩；抽屉同时给出原文链接与来源入口页 `page_url`，正文被截断到 1000 字/词时显示提示。
- **Sources**：查看各源上次抓取状态、累计条目数、下次运行时间，支持单源或全量立即抓取。
- **Pipelines**：查看待报告条目数（`pending`），手动触发“仅过滤”或“立即生成报告”。
- **Reports**：渲染 GFM Markdown 报告表格与引用，点击引用可跳回 Feed 原文（为保护隐私，报告渲染禁用原始 HTML 且不自动加载外部图片，图片转为安全无引荐来源链接）。
- **Personas**：编辑画像、包含/软排除/硬排除关键词，输入演化指令预览行级 Diff 后应用，查看不可变版本历史并一键回滚。
- **Runs & Settings**：每 5 秒自动刷新运行记录、Token 用量与已知模型费用；点开单次运行可查看本次运行的故障账本与逐条排障现场。在浏览器本地保存 Bearer Token、在线测试各模型连通性并查看调度循环状态。

### 在 Runs 页排查漏抓内容

1. 在 **Runs** 表里点某次运行的「故障详情」，抽屉会列出本次运行写入的所有故障事件（阶段、错误码、目标地址、是否可重试、是否已消警）；
2. 点某条事件的「查看排障现场」才会请求 `GET /api/errors/{id}`，展开已脱敏的 `debug_context`（请求地址、HTTP 状态与特征响应头、HTML 骨架、栈帧）；
3. 业务数据即使整批回滚也不会丢这些记录；目标在后续重试中成功获取或评估后，该条会自动标上「已消警」和消警时间；
4. 在命令行侧，`tentacles --json errors list --unresolved` 给出当前仍然漏抓的清单，详见 **[Agent CLI 操作速查](../agent-cli/operations-reference.md)**。

---

## 2. `server` 安全配置与远程访问规范

```yaml
server:
  host: 127.0.0.1
  port: 8000
  token: ${TENTACLES_SERVER_TOKEN:-}
  cors_origins: []               # 仅允许显式列出的无尾斜杠 origin，如 ["https://news.example.com"]
  scheduler: true
  poll_interval: 30              # 内置调度器轮询间隔（秒）
  static_dir: ../frontend/dist
```

- **鉴权规则**：当 `server.token` 非空时（要求无空白可打印 ASCII 字符），除 `GET /api/health` 与前端静态页面资源外，所有 `/api/*`（含 `/api/openapi.json`）均强制要求请求头 `Authorization: Bearer <TOKEN>`。用户首次打开网页可在 **Settings** 页填入 Token。
- **防跨站与防 DNS 重绑定**：禁止野生浏览器 `Origin`（含无预检的简单 `POST`）；当未设置 `token` 时，服务端还会严格校验 `Host` 头（仅信任本机回环、配置的 `host` 及 `cors_origins` 中的主机名），阻断 DNS Rebinding 攻击。
- **远程暴露要求**：Tentacles 是单用户系统。如需在局域网或公网访问，**必须在 `.env` 中设置高强度 `TENTACLES_SERVER_TOKEN` 并前置 HTTPS 反向代理**，切勿直接裸奔无 Token 实例。
- **优雅关闭与隐私日志**：收到停止信号后，停止新轮询并等待当前正在执行的 `tick` 收尾后再释放连接池；Uvicorn 访问日志默认关闭，防止请求查询词或敏感路径写入标准输出。

---

## 3. 自动调度语义与 macOS `launchd` 部署

### 调度规则（`schedule`）
- 支持正整数间隔（`30s`、`30m`、`2h`、`1d`、`1w`）、`@hourly / @daily / @weekly`，以及五段 Cron（`分 时 日 月 星期`，按 `app.timezone` 计算，星期 `0` 和 `7` 均为周日）。
- **首次到期行为**：间隔任务首次注册立即到期；从未执行过的 Cron 任务在首次启用（或禁用后重新启用）时等待下一个触发整点。状态持久化在 SQLite 中，进程重启不丢失进度。
- **`tick` 三阶段屏障**：每轮严格按 **① 并发抓所有到期 Source（上限 4 并发） → ② 依次跑到期 `filter_schedule` → ③ 依次跑到期 Pipeline `schedule`** 执行。若某信息源正处于 `busy` 锁定中，依赖它的下游 Pipeline 本轮自动延期，不会提前空跑漏报。

### 不常驻进程的 `launchd` 定时方案（macOS）

如果你不想一直挂着 `tentacles serve`，可以用 macOS `launchd` 每 30 秒调用一次短进程 `tentacles tick`（保存为 `~/Library/LaunchAgents/local.tentacles.tick.plist`，将 `/absolute/path/tentacles` 替换为你的实际绝对路径，并确保 `data/` 目录存在）：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.tentacles.tick</string>
  <key>ProgramArguments</key><array>
    <string>/absolute/path/tentacles/.venv/bin/tentacles</string>
    <string>--config</string><string>/absolute/path/tentacles/config/tentacles.yaml</string>
    <string>--json</string><string>tick</string>
  </array>
  <key>StartInterval</key><integer>30</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/absolute/path/tentacles/data/tick.log</string>
  <key>StandardErrorPath</key><string>/absolute/path/tentacles/data/tick-error.log</string>
</dict></plist>
```

```bash
# 加载启用
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.tentacles.tick.plist
# 停止卸载
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/local.tentacles.tick.plist
```

---

## 4. HTTP REST API 路由速查

OpenAPI 规范位于 `GET /api/openapi.json`（前端也可离线运行 `pnpm generate:api` 从 `python -m tentacles.api_schema` 生成类型）。所有响应模型与 CLI `--json` 完全共用同一套 Schema：

| 方法与路径 | 返回模型与行为说明 |
| :--- | :--- |
| `GET /api/health` | `{ "ok": true }`（免 Token） |
| `GET /api/settings` | `{ "timezone": "Asia/Shanghai" }`（仅公开展示元数据，不含密钥或本地路径） |
| `GET /api/sources` | `SourceView[]` |
| `POST /api/sources/fetch` | 可选请求体 `{ "ids": ["id"] }`，返回 `FetchOutcome[]`（省略时抓取全部启用源） |
| `POST /api/sources/{source_id}/fetch` | 单个 `FetchOutcome`；未知 ID `404`，运行中 `409`，抓取失败 `502` |
| `GET /api/items` | `Page[ItemView]`；支持重复 `sources`/`kinds` 及 `since`、`until`、`search`、`pipeline`、`persona`、`passed`、`min_score`、`topic`、`sort`（`time`\|`relevance`，默认 `time`）、`limit`(1–500)、`offset`。`search` 走 SQLite FTS5 全文索引（3 字以上词项走倒排索引，1–2 字短词逐列匹配），带 `search` 时每条额外返回命中高亮 `snippet`；`sort=relevance` 需同时提供 `search`（否则 `400`），并按 BM25 相关性排序 |
| `GET /api/items/{item_id}` | `ItemDetail`（含完整正文 `content`、`meta` 与各 Pipeline 的 `evaluations`） |
| `GET /api/items/{item_id}/feedback` | 查询指定或默认 `persona` 的最新投票 `FeedbackView \| null` |
| `POST /api/items/{item_id}/feedback` | 提交投票：`rating` 为 `+1` 或 `-1`，可选 `persona`、`note` |
| `GET /api/personas` | 当前所有画像最新版本 `PersonaView[]` |
| `GET /api/personas/current` | 查询参数 `name`（必填）、`version`（可选） |
| `PATCH /api/personas` | 编辑或新建画像（必填 `name`、`expected_version`，`0` 表示新建；并发冲突返回 `409`） |
| `GET /api/personas/history` | 查询参数 `name` 的全部历史版本列表 |
| `POST /api/personas/evolve` | 生成演化预览提案 `PersonaProposal`（始终只读预览不落库，但会调用模型） |
| `POST /api/personas/apply` | 原子校验 `base_version` 并应用提案，返回新 `PersonaView` |
| `POST /api/personas/rollback` | 复制历史版本 `version` 追加为新版本（需传当前 `expected_version`） |
| `GET /api/pipelines` | `PipelineView[]`（含待报告数 `pending` 与调度状态） |
| `POST /api/pipelines/{pipeline_id}/filter` | `PipelineOutcome`；可选 `{ "reevaluate": true, "since": "7d" }` |
| `POST /api/pipelines/{pipeline_id}/run` | 运行过滤与处理器生成报告；成功/无新内容 `200`，忙 `409`，失败 `502` |
| `GET /api/reports` / `GET /api/reports/{id}` | 分页报告列表 `Page[ReportView]` 与单篇报告全文 |
| `GET /api/models` / `POST /api/models/{name}/test` | 命名模型列表（仅含 `key_configured` 布尔值）与连通性探测 `ModelTest` |
| `GET /api/runs` | `RunView[]`（可选筛选 `kind`、`target`、`limit`） |
| `GET /api/runs/{run_id}` | `RunDetail`：`RunView` 字段 + 本次运行关联的 `errors: ErrorEventView[]`；未知 ID `404` |
| `GET /api/errors` | `Page[ErrorEventView]`；支持 `unresolved`、`source`、`pipeline`、`phase`、`error_code`、`run_id`、`since`、`until`、`limit`(1–500)、`offset`。列表**不含** `debug_context`，以保证高密度扫描 |
| `GET /api/errors/{error_id}` | `ErrorEventDetail`（`ErrorEventView` 加已脱敏的 `debug_context`，序列化后 ≤ 16 KB）；未知 ID `404` |
| `GET /api/scheduler` / `GET /api/scheduler/loop` | 持久化任务列表 `TaskView[]` 与内置轮询器循环统计 |
| `POST /api/scheduler/tick` | 手动触发一轮到期任务 `TickOutcome[]` |

错误响应统一格式为 `{"error": {"code": "...", "message": "..."}}`，状态码映射：`invalid -> 400`、`unauthorized -> 401`、`forbidden -> 403`、`not_found -> 404`、`conflict / busy -> 409`、`upstream -> 502`、`error -> 500`。

`ItemView` 直接返回 `page_url`、截断后的 `content`、`content_truncated`、命中高亮摘录 `snippet`（仅带 `search` 时非 `null`）以及最新一次有效评估的 `topics`、`match_score`、`match_level`、`match_points`（`tags` / `score` / `reason` 为兼容别名）；`/api/items` 与 CLI `items list` 共用同一套字段与筛选语义，需要 `bm25_rank` 与必带 `snippet` 时用 `tentacles --json items search`（见 [Agent CLI 手册](../agent-cli/consuming-items-and-reports.md)）。
