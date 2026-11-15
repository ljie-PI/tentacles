# 触发采集、流水线与运维状态速查

除读取已入库数据与管理 Persona 外，拥有运维权限的 Agent 还可以通过以下 `--json` 命令触发抓取、运行过滤/报告流水线，并审计运行状态与模型账单。

---

## 1. 信息源列表与手动抓取

```bash
# 列出所有配置的信息源、上次抓取状态、累计条数与下次调度时间
tentacles --json sources list

# 立即并发抓取所有已启用（enabled: true）的信息源
tentacles --json sources fetch

# 显式指定一个或多个源 ID 立即抓取（即使配置中 enabled: false 也会执行）
tentacles --json sources fetch hacker-news inbox
```

### 返回结果检查要点（`FetchOutcome[]`）

```json
[
  {
    "source_id": "hacker-news",
    "run_id": 7,
    "status": "ok",
    "new": 5,
    "updated": 2,
    "dup": 13,
    "error": null,
    "stats": {"entries": 20, "too_old": 0, "fetched": 20, "new": 5, "updated": 2, "dup": 13},
    "error_count": 0,
    "failed_targets": []
  },
  {
    "source_id": "broken-feed",
    "run_id": 8,
    "status": "error",
    "new": 0,
    "updated": 0,
    "dup": 0,
    "error": "RSS request failed (ConnectError)",
    "stats": {},
    "error_count": 1,
    "failed_targets": [
      {"error_id": 21, "target_url": null, "external_id": null, "error_code": "connection_failed"}
    ]
  }
]
```

- **必须逐项检查 `status`**：批量抓取中即使部分源报错（`status: "error"`）或正被其他进程占用锁（`status: "busy"`，`run_id: 0`），**进程退出码仍为 `0`**；仅当传入了不存在的 Source ID 时才返回退出码 `3`（`not_found`）。
- 成功批次满足 `stats.fetched = new + updated + dup`（未开启 `update_existing: true` 的源 `updated` 恒为 `0`）。
- **`error_count` / `failed_targets`**：本次运行写入故障账本的条数与指针（`error_id` 用于接续 `tentacles --json errors show <error_id>`）。未获取成功的目标即使整批业务数据回滚也一定会出现在这里，详见 **[第 5 节 故障账本](#5-故障账本errors-list--errors-show--runs-show)**。

---

## 2. 流水线查询、仅过滤与生成报告

```bash
# 查看所有 Pipeline 配置、待报告候选数（pending）与下次运行时间
tentacles --json pipelines list

# 仅对窗口内尚未被当前 Persona 版本评估的条目执行过滤打分（不生成报告）
tentacles --json pipelines filter daily-tech

# 清除最近 7 天当前 Persona 版本的评估缓存并强制重评
tentacles --json pipelines filter daily-tech --reevaluate --since 7d

# 先执行过滤，再选取高分未消费条目调用处理器生成报告（可能产生模型费用）
tentacles --json pipelines run daily-tech
```

### `pipelines run` 返回解读（`PipelineOutcome`）

```json
{
  "pipeline_id": "daily-tech",
  "run_id": 12,
  "status": "ok",
  "report_id": "20261025-daily-tech-a1b2c3d4",
  "stats": {
    "candidates": 18,
    "evaluated": 18,
    "passed": 6,
    "rejected": 12,
    "errors": 0,
    "filter_status": "ok",
    "filter_run_id": 11,
    "processor_candidates": 6,
    "consumed": 6,
    "report_ids": ["20261025-daily-tech-a1b2c3d4"],
    "usage": {"input_tokens": 3500, "output_tokens": 900, "total_tokens": 4400},
    "cost": 0.024,
    "models": ["deepseek-flash", "claude-sonnet-5-5"]
  },
  "error": null,
  "error_count": 0,
  "failed_targets": []
}
```

- `status` 取值：`ok`、`no_new`（窗口内没有新的待消费通过条目，`report_id` 为 `null`）、`busy`（锁被占用）、`error`（单次运行失败，**退出码仍为 `0`**）。
- 对于一次可能产出多篇报告的 `deep` 处理器，`report_id` 为第一篇报告 ID，`stats.report_ids` 包含本批生成的全部报告 ID。

---

## 3. 周期调度器与单轮触发（`scheduler status` & `tick`）

```bash
# 查看全部 fetch / filter / pipeline 任务的启用状态、锁状态、下次触发时间与 pending 数
tentacles --json scheduler status

# 按严格顺序执行所有已启用且已到期的任务（1.并发抓源 -> 2.过滤 -> 3.跑报告）
tentacles --json tick
```

- 无任何到期任务时，`tick` 返回空数组 `[]`。
- 若某个到期源在阶段 1 返回 `status: "busy"`，依赖该源的所有下游 `filter` 和 `pipeline` 任务本轮会自动跳过且不推进调度时间，防止漏抓漏报。

---

## 4. 配置校验、模型探测与计费运行记录

```bash
# 无副作用校验 YAML 配置、环境变量引用、模板语法与 Persona 引用（失败时退出码为 2）
tentacles --json config check

# 列出命名模型及密钥是否已配置（不联网、不泄露密钥）
tentacles --json models list

# 实际发起一次探测调用验证模型可用性与延迟（退出码为 0，检查返回中的 ok 布尔值）
tentacles --json models test fast

# 查询最近的运行与计费记录（可选按 --kind fetch|filter|pipeline|persona 和 --target 过滤）
tentacles --json runs list --limit 20
tentacles --json runs list --kind pipeline --target daily-tech
```

### 费用汇总防重提示（重要）
每次执行 `pipelines run` 时，内部触发的过滤子步骤会单独产生一条 `kind: "filter"` 的子运行记录（其 ID 保存在父 `pipeline` 运行记录的 `stats.filter_run_id` 中），而父 `pipeline` 记录的 `usage` 与 `cost` **已经合并包含了该子过滤步骤的开销**。因此 Agent 在统计总账单时，**请勿将父 `pipeline` Run 与它关联的 `stats.filter_run_id` 子 Run 重复相加**；同样，单次 `sources fetch` 返回结果中的 `stats.cost` 就是其对应 `run_id` 记录的同笔费用。

---

## 5. 故障账本（`errors list` / `errors show` / `runs show`）

业务写入可能整批回滚，但逐条失败事件**必定落库**：`run_errors` 行在正常提交路径上跟随 `items` / `reports` 的业务写入在**同一个事务**内提交，因此该事务回滚时它们也一并消失；回滚安全来自**失败/取消后的清理路径**——`sources fetch`、`tick`、`pipelines filter/run` 在异常或取消后用独立事务把执行中收集到的每一条结构化故障（含具体哪个 `target_url` / `external_id` 没拿到）再次写入。

```bash
# 只看尚未消警（后续重试仍未成功）的故障，按时间倒序（默认 limit 50）
tentacles --json errors list --unresolved

# 定位某个源最近 7 天抓不到的条目
tentacles --json errors list --source hacker-news --since 7d --limit 100

# 展开一条故障的完整描述与已脱敏的排障现场
tentacles --json errors show 21

# 查看单次运行的全部统计与本次运行记录的故障
tentacles --json runs show 8
```

### `errors list` 筛选参数

| 参数 | 说明 |
| :--- | :--- |
| `--unresolved` | 仅返回 `resolved_at IS NULL`（当前仍未消警）的记录 |
| `--source ID` | 按信息源筛选（定位某个源漏抓了哪些条目） |
| `--pipeline ID` | 按 Pipeline 筛选（过滤/成稿阶段故障） |
| `--phase P` | 按阶段筛选：`fetch_page`、`parse_entry`、`fetch_detail`、`imap_fetch`、`browser_replay`、`browser_heal`、`browser_distill`、`llm_extract`、`filter_stage`、`report_process`、`script_pr` |
| `--error-code CODE` | 按机器错误码筛选，见下方速查表 |
| `--run-id N` | 只看某一次运行（与 `failed_targets[].error_id` 配合可精确定位） |
| `--since T` / `--until T` | 时间窗，接受时长（`30m`、`7d`）或 UTC ISO 时间 |
| `--limit 1..500` / `--offset N` | 分页；越界返回退出码 `2`（`invalid`） |

返回 `Page[ErrorEventView]`：**列表故意不包含体积较大的 `debug_context`**，便于高密度扫描；需要现场时再单条 `errors show`。

```json
{
  "items": [
    {
      "id": 21,
      "run_id": 8,
      "occurred_at": "2026-10-25T03:15:00Z",
      "phase": "fetch_page",
      "severity": "error",
      "source_id": "hacker-news",
      "pipeline_id": null,
      "page_url": "https://news.ycombinator.com/",
      "target_url": null,
      "item_id": null,
      "external_id": null,
      "error_code": "connection_failed",
      "http_status": null,
      "exception_type": "UpstreamError",
      "message": "RSS request failed (ConnectError)",
      "retryable": true,
      "resolved_at": null
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

### `errors show <ERROR_ID>`

在 `ErrorEventView` 全部字段之外追加 `debug_context`（已剥离凭据的结构化现场，序列化后严格 `<= 16 KB`）：

```json
{
  "id": 21,
  "run_id": 8,
  "phase": "fetch_page",
  "error_code": "connection_failed",
  "message": "RSS request failed (ConnectError)",
  "page_url": "https://[REDACTED]@feed.example.test/rss?token=[REDACTED]&limit=5",
  "debug_context": {
    "exception": "Traceback (most recent call last):\n  File \".../sources/runner.py\", line 55, in fetch_source\n    result = await source.fetch(state.cursor, ctx)\ntentacles.errors.UpstreamError: RSS request failed (ConnectError)"
  }
}
```

- ID 不存在时退出码 `3`（`not_found`）。
- `debug_context` 中模型 `api_key`、`Authorization` / `Cookie` / `Set-Cookie` / `Proxy-Authorization`、IMAP 与代理密码、URL userinfo 及 `token` / `key` / `sig` / `password` / `secret` 等敏感参数一律替换为 `[REDACTED]`；保留的是**请求地址、HTTP 状态与白名单特征头**（`content-type`、`location`、`retry-after`、`server`、`cf-mitigated`、`x-ratelimit-*`）、HTML 骨架裁剪与栈帧摘要。超限时以 `{"truncated": true, ...}` 标记，不会静默丢弃。

### `browser_replay` 阶段的排障现场（`type: browser` 有界面浏览器源）

有界面 Chrome 重放固化脚本失败时（脚本抛错、选择器/整体超时、返回值不合契约、静态导入门禁拦截，
或本轮返回 0 条），账本里会出现 `phase: "browser_replay"` 的记录。它是**唯一**带实时页面现场的阶段：

| `debug_context` 字段 | 说明 |
| :--- | :--- |
| `current_url` | 失败瞬间工作 Tab 的地址；`target_url` 优先取它，取不到时回退源级 `page_url` |
| `page_title` | 失败瞬间的页面标题（判断是否被登录墙/风控页替换） |
| `a11y_snapshot` | 精简的 A11y 树摘录（≤ 4096 字符），比 HTML 更小、更能反映真实可见结构与角色名 |
| `traceback` | 脚本内栈帧（不含局部变量），定位到具体选择器/断言行 |
| `page_url` | 源配置的入口页地址 |

```bash
# 看某个浏览器源最近仍失败的记录，再展开现场
tentacles --json errors list --source reddit-localllama --phase browser_replay --unresolved --since 7d
tentacles --json errors show <ERROR_ID>
```

- `playwright_selector_timeout`：等待选择器/整体重放超时，通常是站点改版 → 修改
  `scripts/browser_sources/<id>.py`；`a11y_snapshot` 里能看到新结构。
- `script_assertion_failed`：断言失败、返回 0 条（`severity: "warning"`）、返回值不合契约或脚本未通过导入白名单门禁。
- `retryable` 恒为 `false`：页面结构问题与脚本缺陷不会因重试自愈；修好脚本后同一 `(source_id, target_url)`
  成功入库会自动回填 `resolved_at`。
- 若同时看到 `phase: "fetch_page"` 的 `unexpected_exception`，那是整源失败的外层记录（如 Chrome 未开调试端口、
  Profile 不匹配）；`message` 里带可直接执行的启动命令。

### `browser_distill` 阶段的排障现场（`type: browser` 冷启动）

`scripts/browser_sources/<id>.py` 还不存在时，本轮走“受控 `pi` 探索 → 录制轨迹 → 提炼脚本 → 静态门禁 + 真实试跑”
路径。它在 `sources fetch` 的 `stats` 中报告 `browser_mode: "distill"`，成功时额外带 `trajectory_path`（本地轨迹
JSONL）与 `script_path`（刚写入的固化脚本）；失败时带 `browser_distill_failed: true`，不写脚本、不动旧游标。

```bash
# 冷启动成功后确认脚本落地与轨迹位置
tentacles --json sources fetch reddit-localllama | jq '.[0].stats | {browser_mode, trajectory_path, script_path, new}'
# 冷启动失败：拿现场
tentacles --json errors list --source reddit-localllama --phase browser_distill --unresolved
tentacles --json errors show <ERROR_ID>
```

| `error_code` | 含义与处理建议 |
| :--- | :--- |
| `pi_rpc_failed` | `pi` 子进程没能完成探索：二进制/模型/凭据/`browser.pi.timeout` 问题，源没有 `task` 文字，或 Agent 一个动作都没做。`debug_context.trajectory_path` 指向本地轨迹；`message` 会带上子进程的错误原因，必要时先手工跑一次 `pi --model <model> --thinking <level> "hello"` 验证凭据 |
| `pi_script_rejected` | 提炼出的脚本未通过 AST 门禁或真实试跑（报错 / 超时 / 0 条 / 条目缺有效链接 / `page_url` 不是入口页），已用完 1 次修正机会。`debug_context.last_failure` 是最后一次拒绝原因，`trajectory_path` 指向本轮轨迹；看完页面结构与轨迹后调整 `task` 措辞或人工写脚本 |

- 两类记录都同时把 Token 与费用留在当轮 Run 上（`tentacles --json runs show <run_id>` 的 `stats.usage` / `stats.cost`），
  失败不代表免费：`pi` 已经完成的回合照常计费。
- `retryable` 恒为 `false`：同一配置下一轮会重试冷启动（每轮都重新计费），需要先修正配置或 `task`。

### `runs show <RUN_ID>`

在 `RunView` 全部字段之外追加 `errors: ErrorEventView[]`（同样是轻量列表，不含 `debug_context`）；运行 ID 不存在时退出码 `3`。

### `error_code` 速查表（Agent 排障指向）

| `phase` | `error_code` | 含义与处理建议 |
| :--- | :--- | :--- |
| `fetch_page` / `fetch_detail` | `http_status_error` | 目标返回 401/403/404/429/5xx：检查登录态与 Cookie，或把源切换为 `type: browser`；`http_status` 字段给出具体状态码 |
| `fetch_page` / `fetch_detail` | `http_redirect_blocked` | 静态 `web` 源遇到 301/302；`debug_context` 中的跳转目标即新地址，改用 `https://` 或修正 `url` |
| `fetch_page` / `fetch_detail` | `network_timeout` | 连接/读取超时（可重试）：多为瞬态网络抖动、代理不通或 TLS 握手失败 |
| `fetch_page` / `fetch_detail` / `imap_fetch` | `connection_failed` | DNS 解析失败、连接被拒绝、TLS 失败、IMAP 拉取中连接中断（可重试）：先确认 `url` 可达与代理配置，IMAP 则确认服务器在线 |
| `fetch_page` | `feed_parse_error` | RSS/Atom 返回了无法识别的正文（常见于登录页/HTML 错误页）：查看 `debug_context.body_preview` 确认 |
| `parse_entry` | `selector_no_match` | CSS 选择器匹配数为 0，或列表项缺少 `title` / `link`：需改 `list_selector` / 字段选择器 |
| `parse_entry` | `entry_unusable` | RSS/Atom 条目既无标题也无 `guid`/`link`，无法定位；`debug_context` 给出原始 `guid`/`link` |
| `fetch_detail` | `empty_extracted_body` | 详情页 HTTP 200 但正文为空（SPA 空壳或反爬挑战页）：需要浏览器源 |
| `browser_replay` | `playwright_selector_timeout` | 固化 Playwright 脚本等待选择器超时（页面结构已变） |
| `browser_replay` | `script_assertion_failed` | 脚本试跑抓到 0 条或断言不通过 |
| `browser_heal` | `pi_heal_exhausted` | `pi` 自愈后试跑仍未通过，需要人工介入 |
| `browser_distill` | `pi_rpc_failed` | 冷启动时受控 `pi` 子进程未完成探索（二进制/模型/超时/无 `task`） |
| `browser_distill` | `pi_script_rejected` | 提炼脚本未通过静态门禁或真实试跑（已用过 1 次修正） |
| `script_pr` | `git_or_pr_failed` | 脚本已生成，但推送分支或 `gh pr create` 失败 |
| `llm_extract` | `hallucinated_link_dropped` | 模型返回了页面内不存在的链接，该条已被丢弃 |
| `llm_extract` | `invalid_json_schema` | 模型返回违反严格 JSON Schema，本轮降级 |
| `imap_fetch` | `imap_auth_failed` | IMAP 认证失败：检查 `username` / `password`（确认不是应用专用密码过期） |
| `imap_fetch` | `imap_message_parse_error` | 指定 `folder + uid` 的邮件解析异常（单条跳过） |
| `filter_stage` | `decision_item_error` | 过滤阶段单条错误（该条本轮未判定，下一轮会重试） |
| `filter_stage` | `llm_batch_parse_error` | 拆批后仍无法解析模型输出 |
| `filter_stage` | `regex_timeout` | 关键词正则超时，需简化表达式 |
| `filter_stage` | `filter_stage_aborted` | 整批过滤被上游错误中断；`debug_context` 中带阶段进度 |
| `report_process` | `summarize_batch_unparsed` | 简报批次 JSON 违约，已降级处理 |
| `report_process` | `citation_out_of_range` | 报告引用编号越界，已丢弃该引用 |
| 任意阶段 | `unexpected_exception` | 未被更具体分类的失败（`retryable` 为 `false`，需查 `exception_type` 与栈帧） |

> 当前版本会在抓取阶段产出 `http_status_error` / `http_redirect_blocked` / `network_timeout` / `connection_failed` / `selector_no_match` / `empty_extracted_body` / `hallucinated_link_dropped` / `invalid_json_schema` / `feed_parse_error` / `entry_unusable` / `imap_auth_failed` / `imap_message_parse_error` / `unexpected_exception`，有界面浏览器源产出 `playwright_selector_timeout` / `script_assertion_failed`（重放）与 `pi_rpc_failed` / `pi_script_rejected`（冷启动提炼），在过滤阶段产出 `decision_item_error` / `llm_batch_parse_error` / `regex_timeout` / `filter_stage_aborted`，在报告阶段产出 `summarize_batch_unparsed` / `citation_out_of_range`；`script_pr` / `pi_heal_exhausted` 等机器码是账本已预留的共享词表，由 `type: browser` 自愈与提 PR 相关实现按同一契约写入，未出现的码不会作为「未知」返回。

### 各模块的 `debug_context` 现场（实战对照）

| 场景 | `error_code` | `debug_context` 关键字段与处理建议 |
| :--- | :--- | :--- |
| Web 列表选择器整页失效 | `selector_no_match` | `configured_selectors`（当前生效的全部选择器）、`html_title`、`html_skeleton`（`main`/`body` 的脱敏标签骨架，只保留标签名与 `class`/`id`/`slot`/`href` 等布局属性）：把骨架里的实际标签与 `configured_selectors` 对比即可定位改写点 |
| 列表部分节点缺 `title`/`link` | `selector_no_match`（`warning`） | `matched_nodes` / `skipped_nodes` 是命中与跳过计数；`node_samples` 最多 10 条，每条含 `node_index`、`title_found`、`link_found` 与该节点的 `node_html_snippet`。`skipped_nodes` 远大于样本数说明是全站式改版 |
| Web 详情页 3xx | `http_redirect_blocked` | `redirect_location` + `redirect_url`（已解析成绝对地址）：直接把 YAML 的 `url` 换成 `redirect_url` |
| Cloudflare / 反爬挑战页 | `http_status_error` 或 `empty_extracted_body` | `html_title` 出现 `Just a moment...` / `Attention Required`，`response_headers.cf-mitigated` 为 `challenge`，`retry-after` 给出等待秒数：静态 `web` 源无法绕过，需换 `type: browser` |
| 详情页 4xx/5xx | `http_status_error` | `http_status`、`known_title`（列表已知标题，便于对照是哪篇）、`elapsed_ms`、`response_headers`（白名单）、`html_title`、`body_preview`（前 1000 字）、`dom_skeleton` |
| 模型幻觉链接 | `hallucinated_link_dropped`（`warning`） | `dropped_urls`（最多 50 条）与 `dropped_count`：模型编造的链接已被丢弃，不需人工处理 |
| 抽取输出违反 Schema | `invalid_json_schema` | `validation_errors`（字段路径 + 错误类型，如 `title: string_too_short`）与 `output_preview`（800 字脱敏） |
| IMAP 单封坏信 | `imap_message_parse_error` | `external_id` 即 `folder:UIDVALIDITY:UID`（可直接用它在客户端里搜到该信），`debug_context.folder` / `uidvalidity` / `uid` / `exception` |
| `summarize` 批次未解析 | `summarize_batch_unparsed`（`warning`） | `item_ids` 为该批全部条目（≤ 10 条），`target_urls` 便于逐篇核对，`validation_errors` 与 `output_preview` 说明违约点；报告已降级到「未能解析」栏目 |
| 报告引用越界 | `citation_out_of_range` | `out_of_range` 即模型引用的非法编号列表，`max_number` 是本轮提供的最大合法编号，`item_ids` 是本次选题 |
| 过滤单条未判定 | `regex_timeout` / `llm_batch_parse_error` / `decision_item_error` | `item_id` + `target_url` 指向具体条目，`stage` / `stage_error` / `stage_message` 说明是哪一级、什么错误；下一轮会自动重试，成功后自动消警 |

### Web 详情页局部失败（`on_detail_error: record_and_skip`）

默认 `fail_batch` 保留原子语义：本轮任一详情失败就整批回滚、游标不前进，但**回滚前所有失败详情已逐条入库**。若改为 `record_and_skip`，失败的那篇只进账本，其余文章正常入库，且失败 `target_url` 不会被写进游标 `seen` 与 `source_seen_ids`：

```bash
# 哪些漏网链接会在下一轮自动重试（target_url 就是它们）
tentacles --json errors list --unresolved --source web-list --error-code http_status_error
# 单篇现场：是不是 Cloudflare 挑战页？
tentacles --json errors show 21
```

修复上游后重跑 `sources fetch web-list`，这些目标会自动重试，成功后 `resolved_at` 被回填、从 `--unresolved` 列表消失。

### 自动消警（`resolved_at`）

- 同一 `(source_id, target_url)` 或 `(source_id, external_id)` **后续成功入库**时，该源此前未解决的记录自动回填 `resolved_at`；
- 同一 `(pipeline_id, item_id)` **后续成功写入评估**时，`filter_stage` 的未决记录自动消警；
- 因此 `errors list --unresolved` 就是「当前仍然漏抓 / 未成功判定」的准确清单，不需要 Agent 自己做去重与对账。

---

## 6. 失败诊断与错误排查工作流

推荐的排障顺序（每一步都能拿到可直接执行的线索）：

1. **执行结果先看指针**：`sources fetch` / `tick` / `pipelines run` 返回的 `error_count` 与 `failed_targets`。`error_count > 0` 时不要只看 `status`，那只是整轮结论。
2. **按失败目标定位**：用 `failed_targets[].error_id` 直接 `errors show <error_id>`；或用 `errors list --run-id <RUN_ID>` 看本次运行的全部故障。
3. **按源/时间扫描漏网内容**：`errors list --unresolved --source <SOURCE_ID> --since 7d`，`target_url` 就是没拿到的具体内容地址。
4. **按错误码决定动作**：查上面速查表 —— `retryable: true` 的（`network_timeout`、`connection_failed`）等下一轮或立即重跑；`retryable: false` 或 `http_status_error` / `selector_no_match` / `empty_extracted_body` 属于确定性故障，必须改配置或换源类型，重试不会好转。
5. **交叉核对运行汇总**：`runs show <RUN_ID>` 给出该轮 `stats` 与故障列表；`sources list` / `pipelines list` / `scheduler status` 中的 `last_status`、`last_error` 只反映**最近一次**运行结论。
6. **单条已入库条目的过滤异常**：`items show <ITEM_ID>` 查看 `evaluations[].stage_scores[].error`（已入库数据仍保留在此，不在账本里）。

注意：账本只记录执行期的失败现场，不含任何业务数据；修好配置后可以用 `errors list --unresolved` 的条数下降来验证修复是否生效。

---

## 7. 存储状态、索引重建与冷归档（`storage`）

热库 `data/tentacles.db` 始终是唯一的在线数据库；超过 `app.storage.hot_retention_days`（默认 365 天）的历史
条目可以按**发布时间的自然年**搬进 `data/archive/tentacles-YYYY.db`（各自带 `items_fts` 索引）。三条命令都支持
`--json`，另有只读的 `--include-archive` 用于跨年检索（见 [读取与消费采集结果](consuming-items-and-reports.md)）。

```bash
# 体积、WAL 体积、各表行数、FTS 行数、条目时间范围与全部年度冷库清单
tentacles --json storage status

# 在线重建 FTS5 索引（--optimize 只合并索引段；--include-archive 连各年冷库一起处理）
tentacles --json storage reindex --optimize --include-archive

# 先空跑：只统计会迁出多少条、哪些条目因活跃关联被豁免，不创建也不修改任何文件
tentacles --json storage archive --dry-run

# 正式归档（默认截止时间 = 今天 - hot_retention_days），成功后回收热库空间
tentacles --json storage archive --before 365d --vacuum

# 指定截止日期：按配置的 app.timezone 解析
tentacles --json storage archive --before 2025-01-01
```

### `storage status` 返回（`StorageStatus`）

```json
{
  "path": "/srv/tentacles/data/tentacles.db",
  "db_bytes": 495616,
  "wal_bytes": 0,
  "schema_version": 18,
  "tables": {"items": 50, "evaluations": 420, "reports": 18, "runs": 900, "run_errors": 12, "archived_identities": 137},
  "fts_indexed_rows": 50,
  "oldest_item_at": "2026-03-15T08:00:00Z",
  "newest_item_at": "2026-11-01T09:10:00Z",
  "hot_retention_days": 365,
  "archive_dir": "/srv/tentacles/data/archive",
  "archives": [
    {"year": 2025, "path": "/srv/tentacles/data/archive/tentacles-2025.db", "bytes": 458752,
     "items": 8120, "evaluations": 24000, "fts_indexed_rows": 8120}
  ]
}
```

- `tables.archived_identities` 是热库里永久保留的去重墓碑行数，不是条目数；`fts_indexed_rows` 应恒等于
  `tables.items`（不等就说明索引需要 `storage reindex`）。
- `wal_bytes`：热库 WAL 体积。检查点之后为 `0`；持续增长说明有长事务或副本进程一直读着旧快照。

### `storage reindex` 返回（`StorageReindex`）

```json
{
  "optimize": true,
  "rows": 8170,
  "seconds": 0.42,
  "databases": [
    {"path": "/srv/tentacles/data/tentacles.db", "year": null, "rows": 50, "seconds": 0.01},
    {"path": "/srv/tentacles/data/archive/tentacles-2025.db", "year": 2025, "rows": 8120, "seconds": 0.41}
  ]
}
```

- `--optimize` 只做 FTS5 段合并（不改内容）；不带它则 `rebuild` 全量重建，耗时更长但能修好索引与行数不一致。
- 各年冷库若不存在，`--include-archive` 不会凭空创建。

### `storage archive` 返回（`StorageArchive`）

```json
{
  "dry_run": false,
  "cutoff": "2025-11-15T19:35:49.264145Z",
  "vacuumed": true,
  "items": 88,
  "evaluations": 6,
  "deferred": 0,
  "years": [
    {"year": 2024, "path": "/srv/tentacles/data/archive/tentacles-2024.db", "items": 38, "evaluations": 3, "bytes": 409600},
    {"year": 2025, "path": "/srv/tentacles/data/archive/tentacles-2025.db", "items": 50, "evaluations": 3, "bytes": 458752}
  ],
  "exempt": {"recent_activity": 0, "active_feedback": 1, "unresolved_errors": 1},
  "seconds": 0.044
}
```

| 字段 | 说明 |
| :--- | :--- |
| `cutoff` | 实际使用的截止时间（UTC）；早于它的条目才可能被迁出 |
| `items` / `evaluations` | 本次真正搬走的条目数与评估数（`dry-run` 时是计划搬走量） |
| `years[]` | 按自然年分组的结果；`items` 是该年冷库里本次新增的条数，`bytes` 是归档后的库体积（`dry-run` 为 `0`） |
| `deferred` | 归档期间被更新（或新近产生未决错误/反馈）而临时留在热库的条数；下次再跑会自动重试 |
| `exempt` | 早于截止时间却豁免的条目数，三个原因互不重叠：`recent_activity`（最近 30 天有更新）、`active_feedback`（`persona_update.feedback_window` 内的反馈）、`unresolved_errors`（仍有未决错误） |

### 语义与安全边界

- **去重墓碑让旧文不复活**：迁出的条目在热库留下 `archived_identities`（含源内外部身份、原文链接、内容指纹，
  以及原条目的 `kind` 与 `identity_policy`），并且 `source_seen_ids` 永不清理。归档后再抓同一个源，旧文章既不会
  变成 `new`，也不会重新生成条目。墓碑按与热行相同的三条规则判重：外部身份总是判重，原文链接只在原条目为
  `standard` 策略时判重，内容指纹只在原条目为 `standard` 且非 email 时判重。需要正文时用
  `items show <ID> --include-archive`。
- **两阶段、可重跑**：先写冷库并校验其 `items_fts`，再在单个热库写事务里写墓碑并删除冷行。中途被打断时热库
  要么全有要么全无；重跑幂等（不会重复计数），已完成的部分直接跳过。
- **不动的数据**：`reports`（自带完整 markdown）永远留在热库；`pipeline_consumption` 与 `feedback` 行保留，
  因此报告与已消费标记不会失效。`run_errors` 账本本身不迁移。
- **`--dry-run` 纯只读**：不创建 `archive_dir`、不写墓碑、不改任何文件，可安全地放进定时巡检。
  `--vacuum` 不能与 `--dry-run` 同时使用（退出码 `2`）。
- **`--before` 解析**：接受相对时长（`365d`、`2w`、`24h`）或 ISO 日期/时间（无时区后缀按 `app.timezone`）。
  格式非法返回退出码 `2`（`invalid`），不会退化成“归档全部”。
