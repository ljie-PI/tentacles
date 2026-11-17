# 读取与消费采集结果（Items & Reports）

本章详细说明下游 Agent 通过 `items list`、`items search`、`items show` 和 `reports list/show` 能拿到哪些字段、如何筛选与关联兴趣匹配结果，以及如何按相关性检索与定位原文。

---

## 1. 分页检索采集条目：`tentacles --json items list`

默认按发布时间 `published`（缺失时回退首次抓取时间 `fetched_at`）倒序排列，同一时间按 Item `id` 升序决胜。
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
| `--search, -q TEXT` | 全文检索标题、摘要、作者、正文：按空白切分词项并取交集（引号包住的部分保持为一个短语），3 个字符及以上的词项走 SQLite FTS5 倒排索引，1–2 字短词（如 `广告`、`AI`）逐个逐列匹配，因此短词也不会漏召回；`C++`、`foo-bar`、`OR`、`%`、`_` 均按字面处理，不会变成检索语法 |
| `--sort time\|relevance` | 排序方式，默认 `time`（发布时间倒序）；`relevance` 需配合 `--search`，有长词时按 BM25 相关性排序（标题权重最高），纯短词时标题命中优先；同分再按时间倒序、`id` 升序 |
| `--limit N` | 每页返回条数，范围 `1..500`，默认 `50` |
| `--offset N` | 分页偏移量，默认 `0` |
| `--include-archive` | 连同各年度冷归档库（`data/archive/tentacles-YYYY.db`）一起查询，结果用 `archive_year` 标注年份；`items list`、`items search`、`items show` 都支持 |

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
      "topics": ["人工智能与开源", "推荐与广告算法"],
      "match_score": 0.91,
      "match_level": "high",
      "match_points": [
        "命中包含关键词：开源、算法",
        "开源了万亿参数 CTR 广告预估模型的权重与训练代码",
        "给出公开数据集上的离线 AUC 与线上推理延迟实测对比"
      ],
      "score": 0.91,
      "tags": ["人工智能与开源", "推荐与广告算法"],
      "reason": "命中包含关键词：开源、算法",
      "stage": "semantic",
      "archive_year": null,
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
  - `topics`：命中的 Persona 兴趣标签列表，即条目的主题归类（`tags` 为兼容别名，二者取值一致）。多阶段级联下按阶段顺序去重汇总，`keyword` 命中的兴趣词与 `semantic` 模型选中的标签会同时出现。
  - `match_score`：`0.0 ~ 1.0` 的最终归一化兴趣匹配分数（`score` 为兼容别名）。
  - `match_level`：可读分档，由 `match_score` 与 `passed` 派生：`>= 0.8` 为 `high`、`>= 0.5` 为 `medium`、其余已评估为 `low`；`passed` 为 `false` 时一律 `rejected`；未评估为 `unscored`。
  - `match_points`：与兴趣匹配的具体点（字符串列表，跨阶段去重后最多 5 条）。`keyword` 阶段写入实际命中的具体词项（如 `触发硬排除特征：限时特惠`、`命中排除词「广告」，受正向兴趣「开源、算法」豁免转交语义精筛`）；`semantic` 的 Chat 链路写入模型给出的具体事实点，JEV 链路写入可读的相关性/量表评级（如 `JEV 相关性 0.91，命中优先兴趣「AI与广告算法」（推荐系统、CTR预估与机器学习论文）`）。完全未提供结构化匹配点的阶段才退回等于 `[reason]`（`reason` 为兼容别名，取首条匹配点）。
  - `pipeline_id` / `persona_version` / `passed` / `stage`：提供该评估的 Pipeline、Persona 版本、通过与否及做出最终决定的阶段类型（`keyword` / `semantic`，旧写法仍可能是 `llm` / `decision`）。
  - `reason` / `stage_scores`：最终阶段的简短判定理由（即首条匹配点）与级联中走过的每个阶段的完整打分轨迹与模型原始答案（例如 JEV `semantic` 阶段在 `details.answers` 中包含 `noul` 概率、`raw_score`、`confidence` 或各标签 `probabilities`；`details.executor` 标明本次走的是 `chat` 还是 `decision` 链路）。
- **评估的有效性**：只有 `input_revision == content_revision`（评估针对当前正文）的评估才会被关联；正文更新后旧评估自动失效，条目回到 `match_level: "unscored"`，直到重新过滤。
- **`snippet`**：仅在带 `--search` 时返回命中高亮摘录（命中处用 `[...]` 包裹），不检索时为 `null`；需要 `bm25_rank` 时请用 `items search`。
- **`archive_year`**：条目来自热库时为 `null`；加 `--include-archive` 时，来自 `tentacles-2025.db` 的条目为
  `2025`。跨年结果是**只读**的：冷库里的条目不会再被更新、过滤或反馈，需要原文时顺着 `url` 回访。

---

## 2. 按相关性检索：`tentacles --json items search <QUERY>`

`items list --search` 保持发布时间倒序，适合“翻最近的内容”；需要**按相关度排序并看命中上下文**时用 `items search`：

```bash
tentacles --json items search '广告推荐 AI' --limit 20
tentacles --json items search Playwright --persona default --min-score 0.8
```

- **默认排序**：`relevance`（BM25 相关性，标题权重 10 > 摘要 3 > 正文 2 > 作者 1.5）。只含 1–2 字短词、没有倒排索引分数的查询改为「标题命中优先 + 发布时间倒序」，此时 `bm25_rank` 为 `null`。`--sort time` 可切回发布时间倒序。
- **跨年检索**：加 `--include-archive` 时热库与各年冷库分别检索后在内存归并分页（每个库内部都有自己的
  `items_fts`），不受 SQLite 单连接最多 `ATTACH 10` 个库的限制，命中条目用 `archive_year` 标注年份。
  `--include-archive` 只扩展读取范围，不切换排序语义：纯 1–2 字短词也保持**标题命中优先**，之后按发布时间
  （缺失时为抓取时间）倒序、`id` 升序决胜；BM25 同分也使用这两个决胜条件。BM25 是**库内**统计量，
  跨库按各库返回的分值归并，不声称不同年份的分值已成为全局可比统计。长短词混合仍按 AND 筛选。
- **筛选与分页**：与 `items list` 完全一致——`--source/-s`、`--pipeline`、`--persona`、`--passed/--rejected`、`--min-score`、`--topic`、`--since/--until`、`--limit 1..500`、`--offset`，关联的同样是“当前正文对应的最新一次有效评估”。
- **返回 `Page[ItemSearchView]`**：在 `ItemView` 的全部字段之外新增两个字段：

| 新增字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| **`bm25_rank`** | `number \| null` | FTS5 BM25 排名分，**越小越相关**（通常是负数）；短词回退路径为 `null` |
| **`snippet`** | `string` | 命中高亮摘录，命中处用 `[...]` 包裹，长文本首尾用 `...` 截断；短词回退路径截取命中处前后约 60 字 |

```json
{
  "items": [
    {
      "id": "9a8b7c6d5e4f3210",
      "source_id": "hacker-news",
      "page_url": "https://hnrss.org/frontpage",
      "url": "https://example.com/ctr-ad-ranking",
      "title": "Scaling CTR Prediction Models for Real-Time Ad Ranking",
      "bm25_rank": -8.42,
      "snippet": "...proposes a sparse MoE architecture for [CTR] prediction in [ad] ranking...",
      "topics": ["推荐与广告算法"],
      "match_score": 0.94
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

> 检索词为空时返回 `invalid` 错误；`items list` 与 `GET /api/items` 也支持 `--sort relevance` / `sort=relevance`，但只有带检索词时才有相关性顺序。

---

## 3. 读取单条正文与跨流水线评估：`tentacles --json items show <ITEM_ID>`

当列表摘要不足以回答问题时，调用 `items show` 获取单条正文、来源元数据与全部评估：

```bash
tentacles --json items show 6912c034fd032f1e
# 热库里找不到的旧条目：到各年冷库依次查找
tentacles --json items show 6912c034fd032f1e --include-archive
```

返回 `ItemDetail`（包含 `ItemView` 的全部字段，其中评估相关字段为最新一次有效评估，并新增以下 4 个字段）：

| 新增字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| **`external_id`** | `string` | 源内稳定外部身份（如 RSS GUID、邮件 `Message-ID` 或 `文件夹:UIDVALIDITY:UID`、网页与浏览器源条目的规范化 URL、页面监控 `URL#hash`） |
| **`fingerprint`** | `string` | 标题 + 摘要的规范化 SHA-1 指纹 |
| **`meta`** | `object` | 来源特定扩展元数据。例如邮件包含 `folder`、`uidvalidity`、`uid`、`message_id`、`to`、`cc`、`attachments`（附件文件名数组）；`update_existing` 源包含截断前全文哈希 `full_content_sha1`；聚合外链帖与 `type: browser` 条目包含 `comments_url`；`type: browser` 条目还带 `browser_script`（重放的脚本文件名）与 `browser_entry_url`（脚本报告的入口页） |
| **`evaluations`** | `Evaluation[]` | 该条目在**所有已评估过的 Pipeline** 下的最新评分记录，每条额外包含 `match_points` 与 `reported_at`（若已被某 Pipeline 消费写入报告则为 UTC 时间戳，未消费则为 `null`） |

`content` 与 `content_truncated` 与列表一致：正文最多 1000 字/词，完整原文请打开 `url`。

不带 `--include-archive` 时，已归档条目返回 `not_found`（退出码 `3`）；带上后 `archive_year` 给出所在冷库的
自然年。详情始终优先返回热库条目，仅热库没有时才按年份从新到旧查冷库。
热库与冷库的顶层评估字段都只关联当前 `content_revision` 的最新有效评估；较新的失效评估不会遮蔽另一条较早的有效评估。
`evaluations` 则保留各 Pipeline 最新 Persona 版本的评分记录供回溯，可能包含旧 `input_revision`，不等同于顶层的有效关联。
冷库只读，这些记录不会写回热库。

---

## 4. 读取加工后的报告：`reports list` 与 `reports show`

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
