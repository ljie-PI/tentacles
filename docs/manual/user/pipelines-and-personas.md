# 个人画像（Persona）、级联过滤与报告流水线

Tentacles 的核心价值在于用版本化的个人兴趣画像（**Persona**）驱动多级过滤器筛选噪音，再交由处理器（**Processor**）生成结构化报告。

---

## 1. 版本化个人画像（Persona）

### 初始种子配置

在 `config/tentacles.yaml` 中声明初始种子（`profile` 内联文本与 `profile_file` 外部 UTF-8 文件二选一）：

```yaml
persona_update:
  feedback_window: 30d       # 演化时采纳最近多长时间内的点赞/点踩反馈
  max_feedback: 100          # 单次演化最多附带的反馈证据条数（1~1000）
  max_profile_chars: 12000   # 画像文本最大长度上限（最高 50000）

personas:
  default:
    profile: |
      优先关注人工智能与开源软件的实质进展。
      其次关注科技产业、机器人、科学和能源。
      不关注重复营销、广告和缺乏事实依据的炒作。
    include: [人工智能, AI, 开源]
    exclude: [广告, 促销]                      # 软排除：同时命中正向兴趣时会被豁免
    hard_exclude: ['限时特惠', '扫码加群']       # 硬排除：命中即淘汰的确定性垃圾特征
```

- **数据库是唯一权威来源**：`personas` 配置仅在数据库中尚不存在同名画像时导入为 `v1 (source=seed)`（首次种子导入**不调用**标签模型）。一旦入库，后续修改 YAML 不会覆盖已有版本。
- **日常管理方式**：可通过 Web 界面 **Personas** 页在线编辑、预览演化差异与回滚，也可使用 `tentacles persona ...` 命令行工具（所有修改、演化和回滚均为不可变追加新版本，永不覆盖历史）。
- **自动兴趣标签（Tags）**：手动保存修改了 `profile` 正文时，系统会调用 `defaults.models.persona` 模型抽取 5–20 个按优先级排序的细分标签（若模型失败则自动降级保留旧标签，编辑本身仍成功保存）。仅修改 `include/exclude/hard_exclude` 关键词不会调用模型。

---

## 2. 流水线与级联过滤器（`pipelines.filters`）

每条 Pipeline 绑定一个 Persona、一组信息源（`sources`）、回溯时间窗口（`window`，按 `COALESCE(updated_at, fetched_at)` 计算）以及运行计划（`schedule` 与可选的高频预过滤 `filter_schedule`）。

```yaml
pipelines:
  - id: daily-tech
    name: 每日科技日报
    persona: default
    sources: [solidot, 36kr, hacker-news, techcrunch]
    window: 24h
    schedule: '@daily'
    filter_schedule: 30m
    max_items: 500           # 单次过滤最多评估的候选条数（1~5000）
    filters:
      - type: keyword
        regex: true
        hard_exclude: ['sponsored\s+by']
        guard_ambiguous_exclude: true   # 默认 true：软排除与正向词同时命中时豁免转交下一关
        reject_below: 0.5
      - type: decision
        model: decision
        relevance_type: noul
        reject_below: 0.3
        accept_above: 0.8
      - type: llm
        model: fast
        batch_size: 20
        reject_below: 0.5
    processor:
      type: digest
      language: zh-CN
      max_items: 60
```

### 级联流转规则
1. 得分 `< reject_below`（默认 `0.5`）：**立即淘汰**，不再进入后续付费阶段。
2. 得分 `>= accept_above`：**立即通过**，跳过后续阶段直接入选（节省大模型开销）。
3. 介于两者之间：**转交下一阶段**继续判断；若已是最后一个阶段且未被淘汰，则视为通过；若未配置任何 `filters: []`，所有候选条目默认 `1.0` 分通过。
4. 某阶段单条请求出错时不会误判为 `0` 分淘汰：有下一阶段则转交下一阶段，若是最后阶段出错则暂不落库，留待下一轮自动重试。

### 三种过滤器详解

#### ① `keyword`（零成本本地匹配）
- 合并 Persona 的 `include/exclude/hard_exclude` 与当前阶段配置的同名列表，对“标题 + 摘要”做不区分大小写匹配（`regex: true` 时按 Unicode 正则匹配，单条单阶段限时 50ms 防回溯卡死）。
- 软排除（`exclude`）与硬排除（`hard_exclude`）语义不同：**硬排除**只放无歧义的垃圾特征（`限时特惠`、`扫码加群`、`[AD]`），命中即淘汰；**软排除**放跨领域双关词（`广告`、`营销`），同时命中正向兴趣时会被豁免。
- 正向特征 = 命中的 `include` 词 ∪ 命中的 Persona 兴趣标签。

| 优先级 | 命中条件 | 得分 | 级联流向 |
| :--- | :--- | :--- | :--- |
| 1 | 命中任意 `hard_exclude` | `0.0` | 当场淘汰 |
| 2 | 命中软 `exclude`，且未开启豁免或没有正向特征 | `0.0` | 当场淘汰 |
| 3 | 命中软 `exclude`，`guard_ambiguous_exclude: true`（默认）且同时命中正向特征 | `0.55` | **豁免**：不淘汰也不满分短路，转交下一关结合完整画像裁决（若 `keyword` 已是最后一关则保守通过） |
| 4 | 仅命中正向特征 | `1.0` 或 `0.85` | 见下 |
| 5 | 均未命中 | `0.5` | 转交下一关 |

- 纯正向命中：`accept_above: 1.0` 时仍给 `1.0` 分短路通过（100% 兼容旧的省钱用法）；未配置 `accept_above` 时给 `0.85` 分，让条目继续进入语义关获取基于完整画像的打分与理由。
- 每条评估都会记录实际命中的全部具体词项（`match_points`，例如 `命中排除词「广告」，受正向兴趣「开源、算法」豁免转交语义精筛`），可通过 `items list/show` 与 REST `ItemView.match_points` 直接读取；`StageScore.details` 另含 `matched_include` / `matched_exclude` / `matched_hard_exclude` 原始命中列表。

**为什么需要豁免？** 若只想屏蔽带货促销，`exclude: [广告]` 会把《字节开源万亿参数**广告** CTR **算法**模型》一起误杀。把双关词留在 `exclude`、把确定垃圾词放进 `hard_exclude`，《…广告…算法…》会得 `0.55` 分送到语义关，由模型结合完整 Persona 判定；纯促销稿（只命中“广告”）仍得 `0.0` 分淘汰。关闭豁免（`guard_ambiguous_exclude: false`）即恢复旧的无条件淘汰行为。

#### ② `decision`（OpenRouter Decisions / JEV 高并发判别打分）
- 使用 `kind: decision` 模型（如 `typesafe/jev-1.13`）对每条候选资讯独立判断：**“根据该 Persona 及其优先级，用户是否想读这条内容？”**
- `relevance_type` 支持：
  - `noul`（默认）：直接返回 `0.0 ~ 1.0` 的“想读”概率；
  - `score`：按四级有序量表（`Not interested` / `Slight interest` / `Relevant` / `Essential reading`，`0~3`）打分并归一化到 `0.0 ~ 1.0`。
- 当 Persona 拥有兴趣标签时，同一次请求会附带一道 `choice` 题，为该条目选出最匹配的一个优先标签（或 `other`）。
- 隐私保护：默认强制附加 `provider.data_collection = "deny"`（拒绝服务商收集数据），除非你在模型的 `extra_body.provider` 中显式写出 `data_collection: allow`。

#### ③ `llm`（聊天模型批量分类与短理由）
- 使用 `chat` 模型（默认 `defaults.models.filter`）按 `batch_size`（默认 20，范围 1–100）将编号标题与 Persona 打包分类。
- 每条最多选一个标签、打 `0~1` 分并附一句简短匹配理由（低于 `0.3` 分的无关项模型直接省略，按 `0` 分处理）。
- 自带脏 JSON 提取恢复；若某批返回格式损坏，会自动将该批对半拆成两半各重试一次，避免单条脏数据拖垮整批。

---

## 3. 四种报告处理器（`pipelines.processor`）

Pipeline 运行时，从窗口内**已通过过滤且尚未被本 Pipeline 消费**的条目中按分数倒序选出前 `processor.max_items` 条交由处理器处理。生成的报告同时存入 SQLite 并写入 `reports_dir/<pipeline_id>/` 下的私有 Markdown 文件（权限 `0600`）。**同一条目在同一 Pipeline 中终身只消费一次**（修改或回滚 Persona 也不会导致旧新闻重复上报）。

| `processor.type` | 适用场景 | 行为说明 |
| :--- | :--- | :--- |
| **`digest`** | 每日科技/行业资讯日报 | 将选中条目一次交给 `process` 模型，按兴趣标签分组编排摘要并强制校验 `[编号]` 引用合法性，文末自动追加确定性来源目录。 |
| **`deep`** | 每周重大事件多源深度报道 | 先对选中条目做标题贪心聚类，筛选条目数 `>= min_cluster_size`（默认 3）的前 `max_reports`（默认 3）个大事件，每个事件独立生成一份《深度报道：事件标题》（含导语、背景、各方说法、数据与疑点、待确认问题）。未达标或超上限的选中条目也会一并标记为已消费。 |
| **`summarize`** | 邮件简报与待办事项提取 | 每 10 封一批调用模型抽取 JSON（`summary`、`action_required`、`deadline`、`todos`），最终汇总渲染为**“需要处理”**（按截止时间升序并带 `[ ]` 待办勾选框）、**“仅供参考”**、**“未能解析”**三栏简报，保留发件人与原文/邮件定位信息。 |
| **`none`** | 纯筛选信息流（不生成报告） | 不调用模型、不写 Markdown 文件，仅将选中的前 `max_items` 条标记为已消费（适合只通过 Web Feed 或外部 Agent CLI 消费筛后条目的场景）。 |

### 自定义处理器提示词（`prompt_file`）

所有生成报告的处理器均支持 `language`（默认 `zh-CN`）、`instructions`（追加系统要求）以及 `prompt_file`（相对于配置文件目录的 UTF-8 模板文件）：

```text
[system]
你是按用户画像取舍、仅依据材料事实的编辑，必须使用 [编号] 引用，输出 Markdown。
[user]
用户画像：{{ persona }}
当前时间：{{ now }}
pipeline：{{ pipeline.id }}
{% for item in items %}
[{{ item.number }}] {{ item.source }} / {{ item.title }}
{{ item.summary }}
{% endfor %}
```

`[system]` 段为纯文本；`[user]` 段使用受限沙箱 Jinja2 模板（可用变量：`persona`、`items`、`groups`、`tags`、`now`、`pipeline`），未知变量或非法语法会在 `tentacles config check` 时直接拦截报错。
