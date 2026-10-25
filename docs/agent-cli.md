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

一次收集配置结构、`${VAR}` 插值、id 唯一性、Source 选项/引用、模型默认值、
Pipeline Stage/Processor 模型种类、prompt 文件和 Persona 引用的所有错误。
已有数据库只读查询 Persona 名（含 `persona set` 创建的名称）；不存在的数据库或尚无
Persona 表的旧 schema 视为空。不创建数据目录、数据库，不迁移、seed 或注册/同步任务。
为避免 SQLite 只读连接仍创建 WAL/SHM，已有数据库及其 WAL/journal 会复制到私有临时目录，
仅对副本执行 SQLite 恢复和查询，完成后删除。不修改原库或其 sidecar；包含尚未 checkpoint 的
已提交 WAL 数据。复制期间数据库若变化，最多重试三次，仍变化则以配置错误退出并提示重试。

```json
{"ok": true, "path": "/abs/config/tentacles.yaml", "sources": 11}
```

校验失败时退出码为 2，保留 `error.code` / `error.message`，并在 `error.errors` 列出
每条错误的配置路径和信息，不输出原始配置值、密钥或文件内容：

```json
{"error":{"code":"invalid","message":"sources[0].url: Field required\npipelines[0].persona: unknown Persona","errors":[{"path":"sources[0].url","message":"Field required"},{"path":"pipelines[0].persona","message":"unknown Persona"}]}}
```

其他命令启动 Service 时使用相同校验；通过后仍执行原有迁移、Persona seed 和调度/任务引导。
数据库已有的 Persona 不会重新读取或覆盖配置中的 seed profile 文件。

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
| `concurrency` | decision 每批固定 worker 上限，默认 8 |
| `extra_body` | 原样合并的 JSON 额外参数；不允许覆盖 `model/messages/system/stream/state/questions` 协议字段 |

显式调用的 token/温度参数优先于配置。OpenAI 兼容模型可以在 `extra_body` 设置
`max_completion_tokens`，此时不再发送旧 `max_tokens`。decision 的 OpenRouter 根路径
使用 `/api`（若填写 `/api/v1` 则自动去掉 `/v1`），调用 `/alpha/decisions`。

重试范围：408、409、425、429、所有 5xx（含 524、529）、网络错误和超时。
`Retry-After` 支持秒数和 HTTP 日期，上限 60 秒；否则指数退避加抖动，上限 30 秒。
其余 4xx 不重试（decision 的明确瞬时 402 见下文），诊断包含脱敏后的响应体前 500 字符。不跟随模型请求重定向，避免将认证头发往另一地址。

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

### `pipelines list`

返回配置中的 pipeline 数组，包括 id/name/persona/sources/schedule/enabled/processor、
当前 Persona 版本和窗口内的待报告数量 `pending`，以及上次运行时间、最近状态/错误。
`next_run_at` 是下次处理时间；`filter_schedule` / `filter_next_run_at` 是可选的只过滤频率/下次时间。
禁用 pipeline 的下次时间为 null，未配置 filter_schedule 时不创建独立的过滤调度任务。
`tick` 只自动执行启用的任务；手动命名调用仍可执行禁用的 pipeline。

### `pipelines filter ID [--reevaluate]`

仅执行过滤，不调用报告处理器。候选条目必须来自指定来源，`fetched_at` 在窗口内，
且还没有该 pipeline / 条目 / 当前 Persona 版本的评估。单次数量由 pipeline `max_items` 控制（默认 500，1–5000）。

配置示例：

```yaml
pipelines:
  - id: daily
    persona: default
    sources: [news]
    window: 24h
    schedule: '@daily'
    filter_schedule: 30m
    filters:
      - type: keyword
        include: [robotics]
        exclude: [sponsored]
        regex: false
        reject_below: 0.5
        accept_above: 1
    processor:
      type: digest
      language: zh-CN
      max_items: 60
```

keyword 合并 Persona 和阶段的 include/exclude；对标题+摘要做不区分大小写的子串匹配。
exclude 优先得 0，include 得 1，否则 0.5。`regex: true` 使用 Unicode 正则；每条/阶段总匹配预算 50ms，
超时或非法运行时模式记为阶段错误，不会阻塞整批；配置自身的非法正则在加载时拒绝。

级联规则：

1. 低于 `reject_below` 淘汰；没设置时取 `min_score`，再没设置时取 0.5。显式 0 有效。
2. 不低于 `accept_above` 时直接通过，跳过后续阶段。
3. 最后一个阶段未被淘汰就通过；无任何阶段时全部通过（分数 1、阶段 none）。
4. 阶段出错交给下一阶段；最后出错不落库，下次可重试。每阶段保留分数或 error、标签和理由。

`--reevaluate` 先删除窗口内当前 Persona 版本的评估再重算；独立的 pipeline/条目消费账本不会删除。
变更过滤配置不会自动清空既有缓存。Persona 版本变化后按新版本独立评估，
但消费记录不受版本或重算影响：已报告或已被 none 消费的条目不会再次处理。

### Decision 过滤阶段

```yaml
filters:
  - type: decision
    model: decision
    relevance_type: noul
    reject_below: 0.3
    accept_above: 0.8
  - type: keyword
```

- `model` 可省略，解析 defaults.models.decision；显式覆盖必须存在且 kind=decision，加载配置时校验。
- `relevance_type` 是 noul（默认，返回“想读”概率）或 score（四级量表 0–3，按最大值 3 归一化）。
  通用 Decisions 客户端按实际量表长度减一归一化，并保留 raw_score。
- 每个条目单独请求，state 包含截断到 12000 字的 Persona（profile+优先标签）、source/title/author 和前 1000 字摘要。
- 有标签时加 choice 题：选项使用稳定的位置 ID 对应真实标签，另有保留选项 other（不返回任何标签）。
  这样即使用户标签名叫 other 也不会与保留选项冲突。模型看到每个标签的名称/描述。
- 分数、标签、理由和各阶段轨迹在 items list/show 中可见；stage_scores.details 包含实际 model、归一化 answers、标签 ID 映射。
  choice 保留 probabilities/confidence，score 保留 raw_score/归一化 score，模型名的日期后缀不修改。
- 无效概率、量表越界、未知选项或响应元数据缺失只让该条目出错。错误不当作 0 分；后续阶段继续，最后阶段出错不缓存。

固定 worker 池大小为模型 concurrency 与本批条目数的较小值，不为每个条目创建一个任务。
输出槽位按输入索引存放，即使 HTTP 响应乱序也不会把分数/标签配给别的条目。
429 对同批所有 worker 共享一个最大暂停截止点；新的 429 可以延后它，重试耗尽的条目也会触发这次公共暂停。
Retry-After 支持非负数字秒数和 HTTP 日期、最多 60 秒；负数数值无效，合法过去日期按 0 等待。
无合法提示时指数退避加抖动、最多 30 秒。

401/403/非瞬时 402 立即停止本批新派发（含重试），**不取消已发出的请求**，等待它们返回后上报批失败。
402 仅在有合法 Retry-After 时视为明确瞬时错误，否则保守地按终止性错误处理。其它非重试 4xx（含 400/413）只影响本条。
等待公共暂停/本地退避的 worker 会被终止信号立即唤醒，不需再等完 60 秒。
已获得最终通过/淘汰结论的条目即使本批随后终止也会缓存；需要后续阶段才能判定的条目保持未评估，下一次重试。
终止批返回 status=error、运行记为 failed，stage 的 aborted/abort_status 标明原因，不保存请求正文/原始响应。

默认 provider.data_collection=deny；合并额外 provider 偏好时保留默认，null 也按 deny。
只有显式配置 allow 才放宽。其它 provider 路由参数仍原样传递。

计费记录：每阶段 models/usage/cost 汇总已返回的有效元数据，包含答案校验失败或外部取消之前已返回的用量。
未提供的费用不估算。pipeline 运行的总量包含其过滤子运行与处理器，stats.filter_run_id 指向子记录；
**汇总全部 runs 时不要把父 pipeline 与这个子 filter 再加一遍**。单个报告的 usage/cost 仅表示报告生成。
即使 status=no_new，也可能已经进行了付费过滤。外部取消会取消并等待所有 worker 清理，再关闭共享 HTTP/数据库资源。

### LLM 批量过滤阶段

```yaml
filters:
  - type: decision
    reject_below: 0.3
    accept_above: 0.8
  - type: llm
    model: fast
    batch_size: 20
    reject_below: 0.5
```

- type 为 llm，model 可省略，使用 defaults.models.filter；必须是已存在的 chat 模型，否则加载时报 invalid。
- batch_size 默认 20，范围 1–100。按顺序分批发送，只处理前一阶段转交的条目。
- 每个阶段的候选项有稳定编号 1…N；不同批次不复用编号，拆半后仍保留原编号。
  请求含 persona、tags（id/name/description，有序）和 items（id/source/title）。画像渲染上限 12000 字。
- 系统提示要求：每条最多一个标签、忽略标题党和广告、尊重用户额外排除条件、相近相关度优先前排标签、
  只返回至少 0.3 分的匹配项，并给一句短理由。不臆造材料外事实。
- 有兴趣标签时使用其优先级；没有时使用 profile 的非空条目行（去 Markdown 列表前缀、大小写去重）作为兴趣单元。
  profile 也为空时本阶段出错，不调用模型、不缓存无依据的零分。

模型应返回数组：

```json
[{"id":1,"tag_id":2,"score":0.9,"reason":"与用户优先方向一致"}]
```

识别到的 id 只接受请求中的整数或其规范数字字符串；对象行中的未知 id/缺失 id 直接忽略，不因其其它字段有误而影响整批；非对象数组行属于结构错误，不当成漏返回。
已知 id 的分数须是 0–1 有限数（不能是布尔值），tag_id 必须对应一个标签，reason 必须是非空文本。
重复已知编号、无效标签、分数或理由均视为不可靠的批次输出；不会随意挑最后一项或缓存为负面评估。
返回有效数组却漏掉某编号时，该条记 0 分、无标签；这是“未匹配”，不同于解析错误。
评估保存标签原名和最多 300 字的脱敏理由，stage_scores.details 包含实际模型名与本阶段编号。

先使用共享 JSON 提取器处理代码块、前后解释、括号配平及尾随逗号（不改字符串内容）。
若还不能可靠解析，就把本批按原顺序拆为两半，**各请求一次**，不递归继续拆。
单条批次只做一次非空重试。有效半批可保留，失败半批的全部条目都是阶段错误，转交下一阶段或最后阶段不落库。
HTTP/网络失败不当作输出 JSON 错误来拆批，使用模型既定 HTTP 重试策略，然后将该请求覆盖的条目标为阶段错误。

用量/费用在收到模型结果后、解析分类前即累计；初次失败输出、两个重试、外部取消之前已返回的结果都计入运行记录。
父 pipeline 总量仍包含 filter 子运行，stats.filter_run_id 用于避免重复汇总；schema6 消费账本不受重算影响，不会重发已报告内容。
示例阶段保持注释，不会仅因升级自动增加付费调用。

### `pipelines run ID`

先过滤，再按分数倒序选取已通过且尚未消费的前 N 条（processor `max_items`，默认 60，1–500）。
消费账本以 pipeline/条目为主键，独立于评估版本；新评估会继承历史消费时间。
运行锁和过滤锁独立、原子获取，默认一小时租约；第二个并发运行返回 busy。
内部过滤拿不到锁时，跳过过滤、直接处理已有通过项。过期持锁者不能写入新的评估或报告、不能消费替代任务的条目。

```json
{"pipeline_id":"daily","run_id":12,"status":"ok","report_id":"report-uuid",
 "stats":{"candidates":3,"evaluated":3,"passed":2,"rejected":1,"errors":0,
          "filter_status":"ok","processor_candidates":2,"consumed":2,
          "usage":{"input_tokens":100,"output_tokens":20,"total_tokens":120}},"error":null}
```

`status` 为 ok / error / busy / no_new。单次操作失败时返回 error，**退出码仍为 0**，调用方检查 status；
未知 ID 是 not_found（退出码 3），非法配置/参数是 invalid（退出码 2）。无新内容不生成报告，report_id 为 null。
`stats.stages` 包含每阶段的 passed/rejected/forwarded/errors 和 usage；模型已返回的用量/cost 即使报告校验或落盘失败也会记录。

Digest 使用 process 默认模型或 processor `model` 覆盖，按 Persona 取舍和排序、按标签分组，保留稳定的 [编号] 引用。
只允许提供材料的编号，空白报告或越界引用会失败。模型正文保留后追加确定性的来源目录。
数据库记录、消费标记和本次成功记录在同一事务提交；写文件或 SQL 失败时不消费，
尽力清理本次新建文件（清理失败会告警），之后可重试。硬中断可能留下孤立文件，但不会把未提交的条目标为已消费。
文件在 `reports_dir/PIPELINE/`，文件名含时间、digest、标题及唯一 ID；独占创建、不覆盖已有文件，POSIX 下默认 0600 权限。
取消时等待数据库/文件 IO 完成，已提交的成功报告和消费状态保留。

`processor.type: none` 不生成报告、不调用模型，但选中的前 N 条仍会被标记为已消费（evaluations.reported_at）。

### deep 处理器

`processor.type: deep` 使用既有贪心标题聚类（算法不变），按事件条目数、来源数降序处理。
`min_cluster_size` 默认为 3，`max_reports` 默认为 3，范围都是 1–500。
规模是条目数，不是独立来源数；阈值含等号，达标事件每个一份，超出上限的事件本次不生成报告。
共用的 `max_items` 仍是按评估分数取出的消费条目数上限（不是每个事件的上限）。

每份报告 kind 为 `deep`，标题为 `深度报道：事件标题`，仅关联其事件成员。
默认提示词沿用原有深度报道要求：导语、背景、各方说法、数据与疑点、影响与后续、尚待确认的问题；
模型不得使用材料之外的知识。输入保留每条至多 1200 字符摘要，按事件从 1 编号。
输出需含合法编号引用（`来源1`、`[来源1]` 或 `[1]`），并附确定的编号来源清单。
模型选择为显式 `processor.model` 优先，否则使用 `defaults.models.process`。
自定义提示词格式同 digest，`items` 仅含当前事件，`groups.event` 同 `items`；
还可使用 `persona`、`tags`、`now`、`pipeline`。语言和追加指令同样生效。

`pipelines run ID` 的 `report_id` 保持单值，为本批第一份报告或 null；`stats.report_ids` 返回本批全部 ID。
即使全部事件低于阈值，也返回 `status=ok`，并消费所有选中条目；没有待处理条目则为 `no_new`。
超出 `max_reports` 的条目同样消费，不会在下次补写。`none` 不调用模型、不生成报告，仍消费选中条目。
消费账本独立于 persona 版本或重新评估，因此不会因修改兴趣而重复报道。

所有事件生成完成后，全部文件、报告记录与消费标记在同一受租约保护的事务内发布。
任一模型、引用、文件或数据库错误都会使本批失败，不留下半批报告，选中条目留待重试。
取消和租约到期同样不能提前消费；已知 usage/cost 保存在本次运行记录，每份报告只记录其自身费用。
已提交批次即使遇到取消也保留完整结果。文件独占创建、默认 0600，不覆盖已有文件。
验证全程用 HTTP 替身，不需要付费模型。

### summarize 处理器

将 pipeline 的 `processor.type` 设为 `summarize`，然后调用 `pipelines run ID`。先检查返回的 `status`；
单个 pipeline 失败返回 `status=error`，CLI 仍退出 0。成功后用 `report_id` 查询报告。

- 使用 `process` 默认模型；`processor.model` 可覆盖。`language` 控制摘要／待办文本语言，`instructions` 追加要求。
- 选中的条目每批至多 10 条，正文每条至多 8000 字符。请求包含稳定 `id`、标题、发件人、时间、收件人／抄送、
  来源摘要、评分、标签和安全 URL；不把 URL 凭据交给模型。空正文使用来源摘要。
- 模型必须返回以下 JSON；顺序可变，但每个输入 `id` 恰好一次：

  ```json
  {"items":[{"id":"输入条目 id","summary":"摘要","action_required":true,
             "deadline":"2026-11-01","todos":["联系团队"]}]}
  ```

- `summary` 为 1–4000 字符的非空文本，`action_required` 为 JSON 布尔值，`deadline` 为 ISO 日期／日期时间或 null，
  `todos` 为每项 1–1000 字符的非空文本列表（没有待办时 `[]`），不另限条数。日期未知用 null，避免猜测。可恢复围栏／解释文字中的 JSON。
- 缺项、未知／重复 id、非法字段或超过 100 万字符的响应，均使**整批**进入“未能解析”；不保留可信度不明的局部结果，
  不回显模型原始输出。其他批次继续。报告保留三个固定栏目：需要处理、仅供参考、未能解析。
- “需要处理”按截止时间排序，未知截止日期放最后。日期时间按明确偏移量排序；没有偏移量时使用 app 时区；
  仅日期视作该时区当天结束。摘要、待办和来源元数据按纯文本转义，不执行 HTML／Markdown 控制内容。
- 每项附来源时间（发布时间，缺省为抓取时间；越过本地 datetime 范围时保留 UTC）、发件人及安全 http(s) 原文链接；邮件无链接时提供
  source、文件夹、UIDVALIDITY、UID、Message-ID、条目 id，供 `items show ID` 定位。
- 成功发布时，**所有交给处理器的条目**原子标记已报告，包括“未能解析”。重复运行、Persona 改版或 `--reevaluate`
  均不清消费账本。检查“未能解析”后可人工处理原文；其条目不会自动产生下一次模型调用。
- HTTP／模型调用、模板或报告发布失败时，整次运行失败、没有报告／消费标记；可重试。
  已返回的用量／费用仍保留；重复取消会等待 HTTP 清理。Report 的 usage/cost 只含处理器各批（包括解析失败批次），
  pipeline 的总量另含过滤子运行；汇总时继续按 `filter_run_id` 避免重复。

### 自定义处理器提示词

处理器共用字段：`type`（digest/deep/summarize/none）、`model`、`language`（默认 zh-CN）、`instructions`（追加指令）、
`max_items` 和 `prompt_file`。prompt_file 相对配置目录，UTF-8；以下是 digest 示例：

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

system 是纯文本；user 使用沙箱 Jinja 模板，字段有 persona、items、groups、tags、now、pipeline。
Summarize 按批渲染模板，groups 按每项首个标签分组（无标签为 other）；系统仍追加固定 JSON 输出协议。
Digest 的 items 含 number/id/source/title/summary/url/score/tags，来源摘要最多 600 字。
Summarize 的 items 如上文协议，另有 kind/author/time/to/cc/content，来源摘要最多 2000 字。persona 共用 12000 字渲染上限。
模板文件和模板渲染结果最多 100 万字符，未知变量、语法错误、外部模板引用在配置检查时拒绝；渲染时缺失字段则本次运行失败。
只使用可信的本地模板；沙箱不等于独立进程的 CPU/内存隔离。language 和 instructions 会追加到 system。

### `reports list [--pipeline ID] [--limit N] [--offset N]` / `reports show ID`

list 返回 Page（items/total/limit/offset），按创建时间倒序；limit 1–500，offset 非负。
show 返回完整报告：id/pipeline_id/persona_version/kind/title/markdown/item_ids/model/usage/cost/path/created_at。
人类模式渲染 Markdown。即使 pipeline 从配置中移除，仍可查询已存报告与评估历史。

### `sources list`

列出信息源及其抓取状态，包含 `next_run_at`（禁用源为 null）。

```json
[{"id": "hacker-news", "type": "rss", "name": null, "schedule": "30m", "enabled": true,
  "last_fetch_at": "2026-10-24T15:30:00.000000Z", "last_status": "ok", "last_error": null,
  "total_items": 120, "next_run_at": "2026-10-24T16:00:00Z"}]
```

### `sources fetch [ID...]`

立即抓取指定的信息源。不写 ID 时抓取全部启用的源；显式写出 ID 时，即使该源已禁用也会抓取。

- 源可设置 `update_existing: true`（默认 false，支持 RSS/Atom 与 Web list/llm，IMAP/page 仍报错）。优先同源稳定外部 ID/别名，再按实际观察到的规范化 URL 回退至同源唯一标准 Item；不按指纹/标题定位，不覆盖跨源赢家或复活抑制记录。
- 外部身份与 URL 不一致、同源 URL 多义或更新 URL 与另一标准 Item 冲突会使整个源批次失败回滚（别名、内容、评估、计数、游标均不部分提交）。同批别名只采用每个目标的首条有效观察；只改身份不触发内容更新。详见 README 的原位更新说明。
- Web list/llm 只刷新本轮再发现的本源标准 Item，fetch_content 控制是否重新抓详情。llm 首次可能付费建描述缓存；有效输入/完整链接集合/模型与抽取配置不变时不再抽取，但开启详情仍有请求，内容变化仍可能付费重评。无详情的缓存命中不写内容；移除的链接不重访，详见 Web 信息源协议。
- JSON 和文本结果区分 `new`、默认 0 的 `updated`、`dup`；成功交付存储的条目满足 `stats.fetched = new + updated + dup`。更新不再计为重复。
- 内容变化会清除旧评估，并按内容活动时间重新进入 pipeline 窗口（包括已拒绝/已消费条目）；可能再次支付评分费用，但同 pipeline 不重复消费/报告。Feed 的发布时间筛选不变。
- `items list/show` 及 HTTP 列表/详情返回可空 `updated_at` 和 `is_updated`，首次 `fetched_at` 不变。只存最新原文，旧报告 Markdown 不变；关闭开关不回滚内容。
- 只有 guid、没有链接的 RSS 条目也会保留；正文只在 description 中时仍完整保存到详情。
- 单个源失败不会影响其他源。失败的源在结果中 `status` 为 `"error"`，并保留上次的游标，下次抓取时会重试。
- 手动抓取与 `tick` 共用锁；已经运行的源返回 `status: "busy"`、`run_id: 0`，不产生新运行记录。手动抓取也会推进下次调度时间。
- 即使部分源失败或 busy，**退出码仍为 0**，需要逐条检查 `status`。
- 只有 ID 不存在时才会返回错误对象，退出码为 3。
- 中断会等待在途数据库操作结束。若抓取已经完整提交，保留成功记录；否则记录失败并保留旧游标。

```json
[{"source_id": "hacker-news", "run_id": 7, "status": "ok", "new": 5, "updated": 2, "dup": 13, "error": null,
  "stats": {"entries": 20, "too_old": 0, "fetched": 20, "new": 5, "updated": 2, "dup": 13}},
 {"source_id": "broken", "run_id": 8, "status": "error", "new": 0, "updated": 0, "dup": 0,
  "error": "RSS request returned HTTP 500", "stats": {}}]
```

`dup` 是本次交付存储但既非新增也无内容更新的条目数（也包括仅元数据变化），主要原因有两类：

1. **与已入库条目重复**：同源同外部 id、同 url，或同内容指纹（邮件不按指纹去重）。
2. **近似标题**：同一批次内标题高度相似时保留第一条；也会跳过其他来源最近 48 小时内已入库过的近似标题。这些被跳过条目的外部 id 会持久化保存，重启或超过 48 小时也不会重新入库。同一来源不同批次的近似标题不会仅因此被合并。

### Web 来源（list / page / llm）

配置 `type: web` 后，仍用 `sources fetch ID`、`items list/show` 和 `runs list`。
模式、CSS 属性选择器、详情抓取、500-id 缓存及页面版本身份见 [Web 信息源协议](web-sources.md)。
list 的 `stats.entries` 计解析项，`new_links` 计新身份，`details_fetched` 计详情请求，`seen_cached` 最多 500。
page 的 `stats` 包含 baseline、changed、content_hash、previous_hash、diff；恢复旧版本可 changed=true 而 new=0。
三个样例默认关闭；显式命名抓取仍执行。检查逐项 status，错误正文会脱敏，游标不推进。
llm 来源使用 source.model 覆盖或 extract 默认 chat 模型，严格 JSON 失败保留旧游标；usage/cost/models 进入 fetch 与运行记录，
已知费用在失败／取消／旧租约迟到时也保留。详细协议和付费缓存规则见上方链接。

### `tick`

一次执行启用且已到期的任务，严格分三个阶段：

1. 并发抓取到期来源（最多四个同时），等全部结束。
2. 按配置顺序依次执行到期的 `filter_schedule` 任务。
3. 按配置顺序依次执行到期的 pipeline `schedule` 任务。

无到期任务返回 `[]`；未到期/禁用的任务不出现在数组中。
抓取返回 busy 表示其他调用仍在更新输入：依赖该来源的 pipeline 本轮延期，不创建运行记录、不推进下游频率；
不依赖它的 pipeline 照常执行。部分普通失败不会中断其他任务；
用户取消则停止本轮并等待 IO 清理。status 是 ok / error / busy，pipeline 无新内容为 no_new。
仍退出 0，调用方应逐项检查 status。模型支持的 pipeline 会实际调用服务商，可能产生费用。

每项现在包含 **kind**（fetch/filter/pipeline）和 **target**（来源或 pipeline ID）。
fetch 项保留 source_id/new/dup；filter/pipeline 项含 pipeline_id/report_id/stats。手动 sources fetch 的输出不变。

```json
[{"kind":"fetch","target":"news","status":"ok","run_id":9,
  "source_id":"news","pipeline_id":null,"new":3,"dup":0,"report_id":null,"stats":{},"error":null},
 {"kind":"filter","target":"daily","status":"ok","run_id":10,
  "source_id":null,"pipeline_id":"daily","new":0,"dup":0,"report_id":null,"stats":{"evaluated":3},"error":null},
 {"kind":"pipeline","target":"daily","status":"no_new","run_id":11,
  "source_id":null,"pipeline_id":"daily","new":0,"dup":0,"report_id":null,"stats":{"no_new":true},"error":null}]
```

每条启用的 pipeline 都有一个运行任务；只有 filter_schedule 非 null 才有独立过滤任务。
二者频率都支持正整数间隔、三个别名和五段 cron，按 app.timezone 计算。
间隔首次立即到期；从未执行过的 pipeline cron 从首次启用/加入该调度频率时等待下个触发点。
首次启用前已有手动执行记录时，沿用真实的上次开始时间；重启、频率修改不会丢进度。
配置修改在下次打开 Service/CLI 时生效；常驻服务需要重启以读取新配置。

任务锁默认一小时过期；过期后即使原间隔未到也可重试。手动与调度入口复用一次原子获取，不会重复锁住自己。
运行内部仍会过滤一次（已有评估走缓存）；过滤锁忙时跳过该步，直接处理已通过项。
内部过滤会更新其真实开始时间，之后只过滤频率也据此计算。成功、失败、无新内容都推进任务时间，避免轮询风暴。
旧持锁者不能覆盖替代任务的数据，消费账本保证跨版本、频繁过滤也不会重复报告。

可以每 30 秒通过 cron/launchd 执行本命令（README 有示例）。

### `scheduler status`

列出全部配置的来源任务（fetch），以及每条 pipeline 的运行（pipeline）和过滤（filter）任务，
包括禁用的 pipeline 和没有 filter_schedule 的过滤角色。
last_run_at 是最近已完成任务的**开始时间**，running 表示锁未到期。
last_status 为 ok/error；没有已完成 Task 的旧来源沿用 source_state 的最近状态和错误，无历史结果时为 null。
已有 Task 结果时只使用该结果，不回退到旧来源错误。
next_run_at 使用当前频率计算；禁用任务（包括未配置 filter_schedule 的过滤任务）为 null。
状态与 sources list / pipelines list 共用同一处计算。filter/pipeline 的 pending 是当前窗口、当前 Persona 版本的待报告数量，
fetch 的 pending 为 null。同一 pipeline 的两个角色显示相同 pending。

```json
[{"kind":"pipeline","target":"daily","schedule":"@daily","enabled":true,
  "last_run_at":null,"next_run_at":"2026-10-25T16:00:00Z","running":false,
  "last_status":null,"last_error":null,"pending":2}]
```

### `items list`

按发布时间倒序列出条目。没有发布时间的条目按抓取时间排序。

| 选项 | 说明 |
|---|---|
| `--pipeline ID` | 只列出该 pipeline 已评估的条目，每个条目只看最新 Persona 版本那条评估 |
| `--passed` / `--rejected` | 按通过/淘汰筛选，二者互斥且需提供 --pipeline |
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

`kind` 的取值为 `rss`、`email` 或 `web`。指定 pipeline 时额外显示 pipeline_id/persona_version/passed/score/tags/reason/stage/stage_scores。
`items show` 包含 evaluations，列出每个 pipeline 的最新评估及其消费时间 reported_at。

### `items show ID`

查看条目详情。返回 `items list` 中的全部字段，另外还有 `external_id`、`content`（正文，可能为空）、`fingerprint` 和 `meta`。

### `runs list [--kind K] [--target T] [--limit N]`

按时间倒序列出运行记录。每次抓取都会生成一条记录，其中 `kind` 为 `fetch`，`target` 为信息源 id。

```json
[{"id": 8, "kind": "fetch", "target": "broken", "status": "failed",
  "started_at": "…", "finished_at": "…",
  "stats": {"usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "cost": null, "models": []},
  "cost": null, "error": "…HTTP 500"}]
```

`status` 的取值为 `running`、`ok` 或 `failed`。
新完成的 fetch、filter、pipeline、persona Run 均在 `stats` 中使用同一组 `usage` / `cost` / `models`：
用量累计所有已返回调用，模型列表按首次实际返回顺序去重，未知费用为 null；没有模型调用时为零用量和空模型列表。
Persona 标签抽取降级仍是成功编辑，Run 为 `ok`，`error` / `stats.tag_error` 保留诊断。
来源失败不推进 cursor；过期 Lease 的迟到用量只回填原 Run，不改变接替抓取的 cursor 或状态。
历史 Run 不重写；`sources fetch` 的无模型输出保持原有简洁形式，省略这三个累计字段。

## 典型工作流

### 新增一个信息源

1. 编辑配置，在 `sources` 下添加一项。
2. 运行 `tentacles --json config check`，确认退出码为 0。
3. 运行 `tentacles --json sources fetch NEW_ID`，检查返回结果中的 `status` 和 `new`。
4. 运行 `tentacles --json items list --source NEW_ID --limit 5`，查看抓到的内容。

### 回答"今天有什么新内容"

运行 `tentacles --json items list --since 24h --limit 100`，然后对感兴趣的条目运行 `items show ID` 读取正文。

### Browser/agent option parity

- `persona set NAME [--expected-version N]`: optional compare-and-swap; `0` expects a new name.
- `persona evolve NAME [--feedback | --no-feedback] [--expected-version N] [--dry-run]`:
  feedback stays enabled by default. `--no-feedback` uses only the instruction; an instruction is then required.
- `persona rollback NAME VERSION [--expected-version N]`: optionally fence the current version before copying history.
- `items feedback ITEM_ID [--persona NAME]`: read the latest persisted feedback; JSON is `null` if none.

Omitting the new flags preserves existing CLI behavior. Version conflicts use exit 4 and code `conflict`.
