# 读取与消费采集结果（Items & Reports）

本章详细说明下游 Agent 通过 `items list`、`items show` 和 `reports list/show` 能拿到哪些字段、如何筛选与关联兴趣匹配结果，以及如何定位原文。

---

## 1. 分页检索采集条目：`tentacles --json items list`

默认按发布时间 `published`（缺失时回退首次抓取时间 `fetched_at`）倒序排列。

### 筛选与分页参数

| 参数 | 说明 |
| :--- | :--- |
| `--pipeline ID` | 关联指定 Pipeline 在**当前最新 Persona 版本**下的评估结果（开启后每条额外返回分数、标签、理由和阶段轨迹） |
| `--passed` / `--rejected` | 仅筛选通过或被淘汰的条目（二者互斥，**必须同时指定 `--pipeline`**） |
| `--source, -s ID` | 按信息源 ID 过滤，可多次传入（如 `-s hacker-news -s 36kr`） |
| `--since T` / `--until T` | 时间窗口边界。支持相对时长（`90s`、`30m`、`24h`、`7d`、`2w`）或 ISO-8601 时间（无时区后缀时按配置的 `app.timezone` 解析） |
| `--search, -q TEXT` | 在 `title`、`summary`、`author` 中执行不区分大小写的字面子串搜索（`%` 与 `_` 按普通字符处理，非通配符） |
| `--limit N` | 每页返回条数，范围 `1..500`，默认 `50` |
| `--offset N` | 分页偏移量，默认 `0` |

### 返回 JSON 结构（`Page[ItemView]`）

当附加 `--pipeline <ID>` 查询时，单次列表即可拿到条目摘要与兴趣匹配结果：

```json
{
  "items": [
    {
      "id": "6912c034fd032f1e",
      "source_id": "hacker-news",
      "kind": "rss",
      "title": "Show HN: Fast local inference engine in Rust",
      "url": "https://example.com/rust-infer",
      "author": "alice",
      "summary": "开源本地推理引擎，支持 GGUF 量化与 Metal 加速……",
      "published": "2026-10-25T15:20:00Z",
      "fetched_at": "2026-10-25T15:30:00.123456Z",
      "updated_at": null,
      "is_updated": false,
      "pipeline_id": "daily-tech",
      "persona_version": 2,
      "passed": true,
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
        },
        {
          "index": 1,
          "type": "decision",
          "score": 0.76,
          "tags": ["人工智能与开源"],
          "reason": "decision relevance",
          "error": null,
          "details": {
            "model": "typesafe/jev-1.13",
            "answers": {
              "relevance": {"type": "noul", "noul": 0.76},
              "tag": {"type": "choice", "choice": "t0"}
            },
            "labels": {"t0": "人工智能与开源"}
          }
        },
        {
          "index": 2,
          "type": "llm",
          "score": 0.91,
          "tags": ["人工智能与开源"],
          "reason": "发布了实质性的开源本地推理引擎代码",
          "error": null,
          "details": {"model": "deepseek-flash", "id": 1}
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
  - `source_id` / `kind`：来源配置 ID 与种类（`rss` | `email` | `web`）。
  - `url`：规范化后的单条原文链接（已剥离追踪参数与 `#fragment`；纯文本邮件或无链接 GUID 条目可能为空字符串 `""`）。Agent 需要阅读网页最新未裁剪原文或评论区时，可直接请求该 `url`。
  - `summary`：清洗后的纯文本摘要（网页 `mode: page` 版本监控源中，此处存放增删行 `unified_diff`）。
  - `updated_at` / `is_updated`：当信息源开启 `update_existing: true` 且正文后续发生更新时，`is_updated` 为 `true`，`updated_at` 为最近一次内容变更的 UTC 时间，而 `fetched_at` 始终保留首次入库时间。
- **兴趣匹配度与主题（传 `--pipeline` 时填充，未传时为 `null` / `[]`）**：
  - `passed`：是否通过该 Pipeline 的级联过滤（`true` / `false`）。
  - `score`：`0.0 ~ 1.0` 的最终归一化兴趣匹配分数。
  - `tags`：命中的 Persona 兴趣标签列表（即条目的主题归类）。
  - `reason` / `stage`：做出最终决定的阶段名称（`keyword` / `decision` / `llm` / `none`）及其简短判定理由。
  - `stage_scores`：级联中走过的每一个阶段的完整打分轨迹与模型原始答案（例如 JEV `decision` 阶段在 `details.answers` 中包含 `noul` 概率、`raw_score`、`confidence` 或各标签 `probabilities`）。

---

## 2. 读取单条正文与跨流水线评估：`tentacles --json items show <ITEM_ID>`

当列表摘要不足以回答问题时，调用 `items show` 获取完整入库正文与全部元数据：

```bash
tentacles --json items show 6912c034fd032f1e
```

返回 `ItemDetail`（包含 `ItemView` 的全部字段，并新增以下 5 个字段）：

| 新增字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| **`content`** | `string` | 入库的清洗后纯文本正文（如开启了详情页抓取的网页全文、RSS 全文、去除了回复引用的邮件正文；若源仅提供短摘要则可能与摘要相同或为空） |
| **`external_id`** | `string` | 源内稳定外部身份（如 RSS GUID、邮件 `Message-ID` 或 `文件夹:UIDVALIDITY:UID`、网页规范化 URL、页面监控 `URL#hash`） |
| **`fingerprint`** | `string` | 标题 + 摘要的规范化 SHA-1 指纹 |
| **`meta`** | `object` | 来源特定扩展元数据。例如邮件包含 `folder`、`uidvalidity`、`uid`、`message_id`、`to`、`cc`、`attachments`（附件文件名数组） |
| **`evaluations`** | `Evaluation[]` | 该条目在**所有已评估过的 Pipeline** 下的最新评分记录，每条额外包含 `reported_at`（若已被某 Pipeline 消费写入报告则为 UTC 时间戳，未消费则为 `null`） |

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
