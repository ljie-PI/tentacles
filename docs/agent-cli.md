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
