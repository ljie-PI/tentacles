# tentacles CLI — agent 使用说明

所有功能都可以通过非交互命令完成。agent 调用时请始终加 `--json`，或者设置 `TENTACLES_JSON=1`。

## 输出约定

- **stdout**：只输出一个 JSON 值。成功时是结果数据本身，出错时是错误对象：

  ```json
  {"error": {"code": "not_found", "message": "unknown item 'abc'"}}
  ```

- **stderr**：所有日志都写到这里。默认只输出 warning 及以上级别，加 `-v` 可以看到 info。
- **时间格式**：JSON 中的时间一律是 UTC ISO8601。
- **参数错误也是 JSON**：未知命令、缺少参数、参数类型错误同样输出错误对象（`invalid`，退出码 2），不会输出 usage 文本。

| 退出码 | error.code | 含义 |
|---|---|---|
| 0 | — | 成功 |
| 2 | `invalid` | 配置或参数错误，例如非法时间、limit 越界、配置校验失败 |
| 3 | `not_found` | 信息源或条目不存在 |
| 4 | `conflict` / `busy` | 版本冲突，或任务正在运行 |
| 5 | `upstream` | 外部服务失败，包括模型、邮箱、网站 |
| 1 | `error` | 未预期的内部错误 |
| 130 | `aborted` | 被中断（Ctrl-C） |

## 全局选项

| 选项 | 说明 |
|---|---|
| `--config, -c PATH` | 配置文件路径。不指定时依次使用 `$TENTACLES_CONFIG`、`./config/tentacles.yaml` |
| `--json` | stdout 只输出一个 JSON 值 |
| `-v, --verbose` | 在 stderr 输出 info 日志 |

全局选项必须写在子命令之前，例如 `tentacles --json items list`。

## 命令

### `config check`

校验配置文件，包括 `${VAR}` 插值、id 是否唯一，以及各信息源的选项。

```json
{"ok": true, "path": "/abs/config/tentacles.yaml", "sources": 11}
```

校验失败时退出码为 2，`message` 会指出出错的位置，例如 `sources[3].url: must be an http(s) URL`。

### `models list`

列出命名模型及可安全公开的配置，不调用服务商，不输出密钥及额外请求体。

```json
[{"name": "fast", "provider": "openai", "kind": "chat", "model": "deepseek-flash",
  "base_url": "https://api.deepseek.com", "key_configured": false}]
```

### `models test NAME`

实际调用一次模型（遇到临时错误时按配置重试），可能产生费用。
对话模型发送简短连通性提问；decision 模型通过 Decisions API 执行一个 noul 问题，默认拒绝数据收集。
返回可用性、总耗时（含重试等待）、最多 500 字符的样例、实际模型、用量和提供方返回的 cost（缺省为 null）。

```json
{"name": "fast", "ok": true, "latency_ms": 120.0, "sample": "OK", "model": "deepseek-flash",
 "usage": {"input_tokens": 12, "output_tokens": 1, "total_tokens": 13}, "cost": null, "error": null}
```

提供方失败或 key 为空时，返回 `ok: false` 和包含命名模型名的 `error`；**退出码仍为 0**，
调用方须检查 `ok`，与 `sources fetch` 的逐项状态约定一致。未知名称返回 `not_found`（退出码 3）；
配置引用/种类错误返回 `invalid`（退出码 2）。诊断会脱敏所有已配置模型 key；不记录请求正文或完整响应。

模型规格：

| 字段 | 含义/默认值 |
|---|---|
| `provider` | `openai`（默认，OpenAI 兼容）、`anthropic`、`openrouter` |
| `kind` | `chat`（默认）或 `decision`（仅 OpenRouter） |
| `model` | 服务商模型 ID，必填 |
| `base_url` | API 根路径，可选；缺省使用对应服务商地址；不允许凭证、query、fragment |
| `api_key` | 可为空，推荐 `${VAR:-}`；本地免鉴权服务可填非空占位值 |
| `temperature` | 可选 0–2，未配置则不发送 |
| `max_tokens` | 正整数，默认 4096；Anthropic 始终发送 |
| `timeout` | 每次 HTTP 请求超时秒数，默认 60 |
| `retries` | 首次请求之后最多重试次数，默认 2 |
| `concurrency` | decision 并发配置，默认 8（由后续 decision 过滤阶段使用） |
| `extra_body` | 原样合并的 JSON 额外参数；不允许覆盖 `model/messages/system/stream/state/questions` 协议字段 |

显式调用的 token/温度参数优先于配置。OpenAI 兼容模型可以在 `extra_body` 设置
`max_completion_tokens`，此时不再发送旧 `max_tokens`。decision 的 OpenRouter 根路径
使用 `/api`（若填写 `/api/v1` 则自动去掉 `/v1`），调用 `/alpha/decisions`。

重试范围：408、409、425、429、所有 5xx（含 524、529）、网络错误和超时。
`Retry-After` 支持秒数和 HTTP 日期，上限 60 秒；否则指数退避加抖动，上限 30 秒。
其余 4xx 不重试，诊断包含脱敏后的响应体前 500 字符。不跟随模型请求重定向，避免将认证头发往另一地址。

纯 RSS 配置可以省略整个模型注册表；配置模型时必须显式为五个阶段提供种类匹配的默认引用。
`decision` 阶段只用 decision 模型，其余阶段用 chat；单次操作显式指定的名称优先于阶段默认值。
模型客户端及 JSON 提取是后续过滤、persona 和处理器的共享能力，并通过同一个可注入 HTTP 边界测试。

### `persona list` / `persona show NAME [--version N]`

`list` 返回每个名称的最新版本数组；`show` 返回单个完整版本，默认最新。
支持多个 persona；名称可以是中文，不能是空白或包含控制字符。

```json
{"name":"default","version":2,"profile":"优先关注 AI 与开源","tags":[],
 "include":["AI"],"exclude":["广告"],"source":"manual","note":"更新方向",
 "created_at":"2026-10-25T00:00:00Z","tag_status":"skipped","tag_error":null}
```

`tags` 元素为 `{"tag":"名称","description":"含义"}`，按优先级排序。
`source` 是 `seed` / `manual` / `llm` / `rollback`；手动编辑即使辅助调用标签模型也记为 manual，
llm 来源留给后续演化操作。`tag_status` 为 `ok` / `error` / `skipped` / `unchanged`。
不存在的名称或版本返回 `not_found`，版本号非正数返回 `invalid`。

### `persona set NAME`

名称不存在则创建第一版，否则追加一个新版本。

| 选项 | 作用 |
|---|---|
| `--text TEXT` | 用参数中的文本替换 profile |
| `--file PATH` | 从 UTF-8 文件替换 profile；`--file -` 从标准输入读 |
| `--stdin` | 从标准输入读完整 profile |
| `--include KEY` / `--exclude KEY` | 增加关键词，可重复 |
| `--remove-include KEY` / `--remove-exclude KEY` | 删除关键词，可重复 |
| `--note TEXT` | 本版本备注 |
| `--skip-tags` | 保留当前标签，不调用模型 |

三个 profile 输入选项互斥；可以只修改关键词或备注，不提供任何修改则返回 `invalid`。
关键词 trim 后不允许空白，不区分大小写去重/删除，保留首次拼写和顺序；同次操作先删除再增加。
不提供 profile 就保留当前内容。仅改关键词/备注、profile 内容相同或指定 `--skip-tags` 均不触发抽取。
新建时如果只有关键词，profile 为空。

profile 变化后，通过阶段 `persona` 的模型生成 5–20 个标签；旧标签提供给模型，要求语义相同保留原名。
初次提取参考 TrendRadar 的细分、不重叠、具体描述和优先级规则；更新使用 keep/add/remove 和最终 order。
验证名称非空、数量 5–20、无大小写重复、旧标签都有明确去留、order 无缺漏。
抽取失败时仍保存 profile 和关键词、保留旧标签，返回版本中 `tag_status: "error"` 与脱敏诊断。
调用方应检查此字段；降级是成功保存，不是整次修改失败。`runs list --kind persona` 可查看用量、成本和降级状态。

仅修改关键词时，在同一 SQLite 事务中基于最新版本合并，版本号不会重复。
提供 profile 且未指定 `--skip-tags` 时，会保护读取到的版本，包括 profile 内容相同而不调用模型的快捷路径。
提交前若版本变化，返回 `conflict`（退出码 4），不会覆盖新版本或混用新标签；刷新后可重试。
取消时会等待数据库写入结束，已提交的新版本与成功运行记录会保留。

### `persona history NAME`

返回全部历史版本数组，按版本号倒序。每版包括来源、备注、UTC 创建时间、标签抽取状态。
旧版本永不原地修改。

### `persona rollback NAME VERSION [--note TEXT]`

把指定旧版的 profile、标签、全部 include/exclude 复制为一个新版本，来源记为 rollback。
不删除任何历史，不重新调用标签模型。返回新的完整版本。

### `persona export NAME [--version N]`

导出完整 YAML 版本快照，包含 profile、标签、关键词和版本元数据。
人类模式 stdout 是原始 YAML，适合重定向；JSON 模式 stdout 是 **一个 JSON 字符串**，解析该字符串即可得到 YAML。
导出不修改数据库，不自动写文件；快照不是纯 profile，不能直接作为 `set --file` 的输入。

```sh
tentacles persona export default > persona-snapshot.yaml
tentacles --json persona export default | jq -r . > persona-snapshot.yaml
```

配置中 `personas` 是名称到初始种子的映射，profile/profile_file 二选一，另可指定 include/exclude/note。
只在数据库不存在该名称时读取并导入；首次导入为 seed v1，跳过标签模型。
已有名称会忽略后续种子内容和文件，即使原文件已移除也能启动。所有新种子同一事务导入，失败不会留下半批种子。

### `sources list`

列出信息源及其抓取状态，包含 `next_run_at`（禁用源为 null）。

```json
[{"id": "hacker-news", "type": "rss", "name": null, "schedule": "30m", "enabled": true,
  "last_fetch_at": "2026-10-24T15:30:00.000000Z", "last_status": "ok", "last_error": null,
  "total_items": 120, "next_run_at": "2026-10-24T16:00:00Z"}]
```

### `sources fetch [ID...]`

立即抓取指定的信息源。不写 ID 时抓取全部启用的源；显式写出 ID 时，即使该源已禁用也会抓取。

- 只有 guid、没有链接的 RSS 条目也会保留；正文只在 description 中时仍完整保存到详情。
- 单个源失败不会影响其他源。失败的源在结果中 `status` 为 `"error"`，并保留上次的游标，下次抓取时会重试。
- 手动抓取与 `tick` 共用锁；已经运行的源返回 `status: "busy"`、`run_id: 0`，不产生新运行记录。手动抓取也会推进下次调度时间。
- 即使部分源失败或 busy，**退出码仍为 0**，需要逐条检查 `status`。
- 只有 ID 不存在时才会返回错误对象，退出码为 3。
- 中断会等待在途数据库操作结束。若抓取已经完整提交，保留成功记录；否则记录失败并保留旧游标。

```json
[{"source_id": "hacker-news", "run_id": 7, "status": "ok", "new": 5, "dup": 15, "error": null,
  "stats": {"entries": 20, "too_old": 0, "fetched": 20, "new": 5, "dup": 15}},
 {"source_id": "broken", "run_id": 8, "status": "error", "new": 0, "dup": 0,
  "error": "GET https://broken.example.com/rss returned HTTP 500", "stats": {}}]
```

`dup` 是本次抓到但没有入库的条目数，原因有两类：

1. **与已入库条目重复**：同源同外部 id、同 url，或同内容指纹（邮件不按指纹去重）。
2. **近似标题**：同一批次内标题高度相似时保留第一条；也会跳过其他来源最近 48 小时内已入库过的近似标题。这些被跳过条目的外部 id 会持久化保存，重启或超过 48 小时也不会重新入库。同一来源不同批次的近似标题不会仅因此被合并。

### `tick`

并发抓取所有启用且已到期的信息源（最多四个同时执行），返回与 `sources fetch` 相同的数组。
未到期/禁用的源不出现在结果中；没有到期任务时返回 `[]`。
每项 `status` 为 `ok`、`error` 或 `busy`。部分失败或锁被占用时仍退出 0，应逐项检查。

```json
[{"source_id": "news", "run_id": 9, "status": "ok", "new": 0, "dup": 0,
  "error": null, "stats": {"entries": 0, "too_old": 0, "fetched": 0, "new": 0, "dup": 0}},
 {"source_id": "locked", "run_id": 0, "status": "busy", "new": 0, "dup": 0,
  "error": null, "stats": {}}]
```

任务首次出现时间、上次开始时间和最近状态保存在 SQLite。间隔任务首次立即到期，
cron 首次等到下一个触发点；成功/失败都按开始时间推进频率。锁默认一小时过期，
过期后即使原间隔未到也可重试，旧持锁者不能覆盖新任务的条目、游标或状态。
可以每 30 秒通过系统 cron/launchd 执行此命令（README 有示例）。

### `scheduler status`

列出配置中全部源的任务状态。`last_run_at` 为最近完成任务的开始时间，
`running` 表示锁尚未到期；`last_status` 为 `ok` / `error` 或尚未运行时的 null。
`next_run_at` 用当前配置的频率计算，禁用任务为 null；修改频率后下次打开 Service 即生效。

```json
[{"kind": "fetch", "target": "news", "schedule": "30m", "enabled": true,
  "last_run_at": "2026-10-25T00:10:00Z", "next_run_at": "2026-10-25T00:40:00Z",
  "running": false, "last_status": "ok", "last_error": null}]
```

### `items list`

按发布时间倒序列出条目。没有发布时间的条目按抓取时间排序。

| 选项 | 说明 |
|---|---|
| `--source, -s ID` | 按信息源筛选，可以重复使用 |
| `--since T` / `--until T` | 时间范围。`T` 可以写时长（`90s`、`30m`、`24h`、`7d`、`2w`，表示从现在往前推），也可以写 ISO 时间；不带时区的 ISO 时间按配置的 `app.timezone` 解释 |
| `--search, -q TEXT` | 在标题、摘要、作者中按字面子串搜索（不区分大小写，`%`、`_` 不是通配符） |
| `--limit N` | 每页条数，范围 1–500，默认 50 |
| `--offset N` | 分页偏移量，默认 0 |

```json
{"items": [{"id": "6912c034fd032f1e", "source_id": "hacker-news", "kind": "rss", "title": "FreeDNS",
            "url": "https://example.com/freedns", "author": "", "summary": "…",
            "published": "2026-10-24T15:20:00Z", "fetched_at": "2026-10-24T15:30:00.123456Z"}],
 "total": 25, "limit": 50, "offset": 0}
```

`kind` 的取值为 `rss`、`email` 或 `web`。

### `items show ID`

查看条目详情。返回 `items list` 中的全部字段，另外还有 `external_id`、`content`（正文，可能为空）、`fingerprint` 和 `meta`。

### `runs list [--kind K] [--target T] [--limit N]`

按时间倒序列出运行记录。每次抓取都会生成一条记录，其中 `kind` 为 `fetch`，`target` 为信息源 id。

```json
[{"id": 8, "kind": "fetch", "target": "broken", "status": "failed",
  "started_at": "…", "finished_at": "…", "stats": {}, "cost": null, "error": "…HTTP 500"}]
```

`status` 的取值为 `running`、`ok` 或 `failed`。

## 典型工作流

### 新增一个信息源

1. 编辑配置，在 `sources` 下添加一项。
2. 运行 `tentacles --json config check`，确认退出码为 0。
3. 运行 `tentacles --json sources fetch NEW_ID`，检查返回结果中的 `status` 和 `new`。
4. 运行 `tentacles --json items list --source NEW_ID --limit 5`，查看抓到的内容。

### 回答"今天有什么新内容"

运行 `tentacles --json items list --since 24h --limit 100`，然后对感兴趣的条目运行 `items show ID` 读取正文。
