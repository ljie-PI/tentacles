# 读取与消费采集结果（Items & Reports）

本章详细说明下游 Agent 通过 `items list`、`items show` 和 `reports list/show` 能拿到哪些字段、如何筛选与关联兴趣匹配结果，以及如何定位原文。

---

## 1. 分页检索采集条目：`tentacles --json items list`

默认按发布时间 `published`（缺失时回退首次抓取时间 `fetched_at`）倒序排列。
**不传 `--pipeline` / `--persona` 时，也会自动关联该条目最新一次有效评估**，因此一次列表调用即可拿到正文、来源链接与兴趣匹配结果。

### 筛选与分页参数

| 参数 | 说明 |
| :--- | :--- |
| `--pipeline ID` | 关联指定 Pipeline 在**当前最新 Persona 版本**下的评估结果；只返回该 Pipeline 已评估的条目 |
| `--persona NAME` | 关联使用该 Persona 的所有 Pipeline 中最新的有效评估（与 `--pipeline` 互斥） |
| `--min-score N` | 只看关联评估的 `match_score` ≥ N（`0..1`）的条目；未评估条目被排除 |
| `--topic TAG` | 只看关联评估的 `topics` 中包含该主题标签的条目；未评估条目被排除 |
| `--passed` / `--rejected` | 仅筛选通过或被淘汰的条目（二者互斥，**必须同时指定 `--pipeline`**） |
| `--source, -s ID` | 按信息源 ID 过滤，可多次传入（如 `-s hacker-news -s 36kr`） |
| `--since T` / `--until T` | 时间窗口边界。支持相对时长（`90s`、`30m`、`24h`、`7d`、`2w`）或 ISO-8601 时间（无时区后缀时按配置的 `app.timezone` 解析） |
| `--search, -q TEXT` | 在 `title`、`summary`、`author`、`content` 中执行不区分大小写的字面子串搜索（`%` 与 `_` 按普通字符处理，非通配符） |
| `--limit N` | 每页返回条数，范围 `1..500`，默认 `50` |
| `--offset N` | 分页偏移量，默认 `0` |

### 返回 JSON 结构（`Page[ItemView]`）

单次列表即可拿到条目正文、原文与入口链接以及兴趣匹配结果：

```json
{
  "items": [
    {
      "id": "6912c034fd032f1e",
      "source_id": "hacker-news",
      "kind": "rss",
      "title": "Show HN: Fast local inference engine in Rust",
      "url": "https://example.com/rust-infer",
      "page_url": "https://hnrss.org/frontpage",
      "author": "alice",
      "summary": "开源本地推理引擎，支持 GGUF 量化与 Metal 加速……",
      "content": "项目作者发布了……（入库正文，最多 1000 字/词）",
      "content_truncated": true,
      "published": "2026-10-25T15:20:00Z",
      "fetched_at": "2026-10-25T15:30:00.123456Z",
      "updated_at": null,
      "is_updated": false,
      "pipeline_id": "daily-tech",
      "persona_version": 2,
      "passed": true,
      "topics": ["人工智能与开源"],
      "match_score": 0.91,
      "match_level": "high",
      "match_points": ["发布了实质性的开源本地推理引擎代码"],
      "score": 0.91,
      "tags": ["人工智能与开源"],
      "reason": "发布了实质性的开源本地推理引擎代码",
      "stage": "llm",
      "stage_scores": [
        {
          "index": 0,
          "type": "keyword",
          "score": 0.5,
          "tags": [],
          "reason": "no keyword match",
          "error": null,
          "details": {}
        }
      ]
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

### 字段语义详解

- **基础内容与来源定位**：
  - `id`：16 位十六进制稳定主键（由 `source_id + external_id` 派生）。
  - `source_id` / `kind`：来源配置 ID 与种类（`rss` | `email` | `web` | `browser`）。`kind: "browser"` 表示该条来自有界面 Chrome 重放的固化 Playwright 脚本，正文即脚本抓到的页面文字（同样入库截断到 1000 units），不含图片/视频二进制内容。
  - `url`：规范化后的**单条原文链接**（已剥离追踪参数与 `#fragment`；纯文本邮件或无链接 GUID 条目可能为空字符串 `""`）。Agent 需要阅读未截断原文时直接请求该 `url`。
  - `page_url`：发现该条目的**入口页链接**（RSS/Atom Feed 地址、Web 列表或监控页地址、`type: browser` 脚本重放的入口页地址）；邮件为空字符串。`url` 与 `page_url` 至少有一个是合法的 `http(s)` 地址，聚合外链帖（如 Hacker News）的讨论链接保留在 `items show` 的 `meta.comments_url`。
  - `summary`：清洗后的短摘要（≤ 300 字/词；网页 `mode: page` 版本监控源中，此处存放增删行 `unified_diff`）。
  - `content`：**入库即截断的纯文本正文**，CJK 字符 + 非 CJK 词元合计 ≤ 1000 units。
  - `content_truncated`：`true` 表示正文超过 1000 units 已被截断，需要完整原文时请回访 `url`。
  - `updated_at` / `is_updated`：当信息源开启 `update_existing: true` 且正文后续发生更新时，`is_updated` 为 `true`，`updated_at` 为最近一次内容变更的 UTC 时间，而 `fetched_at` 始终保留首次入库时间。
- **兴趣匹配度与主题**（未传 `--pipeline` / `--persona` 时关联最新一次有效评估；从未评估或内容已变更时为空）：
  - `topics`：命中的 Persona 兴趣标签列表，即条目的主题归类（`tags` 为兼容别名，二者取值一致）。
  - `match_score`：`0.0 ~ 1.0` 的最终归一化兴趣匹配分数（`score` 为兼容别名）。
  - `match_level`：可读分档，由 `match_score` 与 `passed` 派生：`>= 0.8` 为 `high`、`>= 0.5` 为 `medium`、其余已评估为 `low`；`passed` 为 `false` 时一律 `rejected`；未评估为 `unscored`。
  - `match_points`：与兴趣匹配的具体点（字符串列表）。`keyword` 阶段作为决定阶段时会写入实际命中的具体词项（如 `触发硬排除特征：限时特惠`、`命中排除词「广告」，受正向兴趣「开源、算法」豁免转交语义精筛`）；其他阶段未提供结构化匹配点时等于 `[reason]`（`reason` 为兼容别名）。
  - `pipeline_id` / `persona_version` / `passed` / `stage`：提供该评估的 Pipeline、Persona 版本、通过与否及最终决定阶段。
  - `reason` / `stage_scores`：最终阶段的简短判定理由与级联中走过的每个阶段的完整打分轨迹与模型原始答案（例如 JEV `decision` 阶段在 `details.answers` 中包含 `noul` 概率、`raw_score`、`confidence` 或各标签 `probabilities`）。
- **评估的有效性**：只有 `input_revision == content_revision`（评估针对当前正文）的评估才会被关联；正文更新后旧评估自动失效，条目回到 `match_level: "unscored"`，直到重新过滤。

---

## 2. 读取单条正文与跨流水线评估：`tentacles --json items show <ITEM_ID>`

当列表摘要不足以回答问题时，调用 `items show` 获取单条正文、来源元数据与全部评估：

```bash
tentacles --json items show 6912c034fd032f1e
```

返回 `ItemDetail`（包含 `ItemView` 的全部字段，其中评估相关字段为最新一次有效评估，并新增以下 4 个字段）：

| 新增字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| **`external_id`** | `string` | 源内稳定外部身份（如 RSS GUID、邮件 `Message-ID` 或 `文件夹:UIDVALIDITY:UID`、网页与浏览器源条目的规范化 URL、页面监控 `URL#hash`） |
| **`fingerprint`** | `string` | 标题 + 摘要的规范化 SHA-1 指纹 |
| **`meta`** | `object` | 来源特定扩展元数据。例如邮件包含 `folder`、`uidvalidity`、`uid`、`message_id`、`to`、`cc`、`attachments`（附件文件名数组）；`update_existing` 源包含截断前全文哈希 `full_content_sha1`；聚合外链帖与 `type: browser` 条目包含 `comments_url`；`type: browser` 条目还带 `browser_script`（重放的脚本文件名）与 `browser_entry_url`（脚本报告的入口页） |
| **`evaluations`** | `Evaluation[]` | 该条目在**所有已评估过的 Pipeline** 下的最新评分记录，每条额外包含 `match_points` 与 `reported_at`（若已被某 Pipeline 消费写入报告则为 UTC 时间戳，未消费则为 `null`） |

`content` 与 `content_truncated` 与列表一致：正文最多 1000 字/词，完整原文请打开 `url`。

---

## 3. 读取加工后的报告：`reports list` 与 `reports show`

如果用户已经配置了 `digest`（日报）、`deep`（重大事件深度报道）或 `summarize`（邮件简报与待办清单），Agent 可以直接消费成稿：

```bash
# 分页列出报告（按创建时间倒序，可选按 --pipeline 过滤）
tentacles --json reports list --pipeline daily-tech --limit 10 --offset 0

# 读取单份报告完整 Markdown 与引用的原始条目 ID 列表
tentacles --json reports show <REPORT_ID>
```

`reports show` 返回的 `ReportView` 结构：

```json
{
  "id": "20261025-daily-tech-a1b2c3d4",
  "pipeline_id": "daily-tech",
  "persona_version": 2,
  "kind": "digest",
  "title": "每日科技日报（2026-10-25）",
  "markdown": "# 每日科技日报...\n\n- 开源推理引擎进展 [1]\n...",
  "item_ids": ["6912c034fd032f1e", "8f31a902bc114e09"],
  "model": "claude-sonnet-5-5",
  "usage": {"input_tokens": 3200, "output_tokens": 850, "total_tokens": 4050},
  "cost": 0.0223,
  "path": "/abs/reports/daily-tech/2026-10-25_digest_....md",
  "created_at": "2026-10-25T16:00:12Z"
}
```

- **溯源核查**：`item_ids` 列出了生成该篇报告所依据的全部原始条目 ID。Agent 在阅读报告时如需核实某处细节，可直接对 `item_ids` 中的 ID 执行 `tentacles --json items show <ID>`，或取出其 `url` 回访源站。
