# tentacles

个人信息与新闻管理系统：从多种信息源抓取内容，按个人 persona 过滤，再用 LLM 加工成日报、深度报道或邮件简报。整体设计见 [#18](https://github.com/ljie-PI/tentacles/issues/18)，实施进度见它的子 issue。

> 已支持配置驱动的 RSS、IMAP 和网页抓取、去重入库、持久化调度、命名模型、版本化 Persona 与反馈演化，以及 keyword/decision/llm 级联过滤和 digest/deep/summarize/none 处理器。Web 端已提供 Feed、Sources、Pipelines、Reports、Runs、Settings 和 Persona 管理；实现与验证范围见 [规格核对](docs/spec-18-audit.md)。

## 网页与 REST API

```bash
# 安装后端依赖后，启动 API 与调度器；省略轮询可加 --no-scheduler
tentacles serve --no-scheduler
# 另一个终端：构建网页后，后端会直接托管静态产物
cd frontend
pnpm install --frozen-lockfile
pnpm build
# 浏览 http://127.0.0.1:8000
```

已提供 Feed（来源／时间／关键词／pipeline 与通过判定筛选、加载更多、正文抽屉）、Sources（状态、立即抓取）、
Pipelines（过滤／运行）、Reports（Markdown 表格与引用条目）、Runs（自动刷新与成本）和 Settings（token、模型测试、调度状态）。
支持暗色模式和移动端；Persona 页面可编辑画像与关键词、预览演化差异后应用、查看历史并回滚；Feed 支持按画像点赞／点踩。
开发模式运行 `pnpm dev`，通过 Vite 代理访问后端。要求 Node >=22.12、pnpm 10.33；
`pnpm generate:api` 重新生成提交的后端类型，`pnpm check:api` 检查一致性。

默认仅监听本机；对外监听前设置 `.env` 的 `TENTACLES_SERVER_TOKEN`，并配置 HTTPS 代理。
除了健康检查，API 均需 Bearer token（若已配置），静态网页无需鉴权。
详细配置、路由、错误及关闭行为见 [HTTP API](docs/http-api.md) 和 [前端开发说明](frontend/README.md)。

## 快速开始

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env              # 可选：存放配置中 ${VAR} 引用的密钥
tentacles config check            # 校验 config/tentacles.yaml
tentacles sources fetch           # 抓取全部启用的信息源
tentacles items list --since 24h  # 查看最近 24 小时的条目
```

## 配置

所有配置都在一个 YAML 文件中，默认为 `config/tentacles.yaml`，也可以用 `--config` 或 `$TENTACLES_CONFIG` 指定：

- 值中可以写 `${VAR}` 或 `${VAR:-默认值}`，从环境变量读取。系统也会读取项目根目录或配置所在目录的 `.env`，但不会覆盖已有的环境变量。
- 相对路径都相对于配置文件所在的目录解析。
- 每个信息源需要声明 `id`、`type`、`schedule`；不同类型还有各自的选项，示例配置中有完整说明。

## 命令

| 命令 | 作用 |
| ---- | ---- |
| `tentacles config check` | 校验配置 |
| `tentacles models list` | 列出命名模型，不泄露密钥 |
| `tentacles models test NAME` | 实际调用模型，返回可用性、延迟、样例和用量 |
| `tentacles persona list/show/history/export` | 查看 Persona、版本历史或导出完整 YAML 快照 |
| `tentacles persona set NAME` | 从文本/文件/stdin 修改 profile 和关键词，可跳过标签抽取 |
| `tentacles persona evolve [NAME] --instruction TEXT [--dry-run]` | 用指令和/或反馈演化 Persona，预览模式不写库 |
| `tentacles persona apply --proposal FILE\|-` | 应用 JSON 提案，过期时返回 conflict |
| `tentacles feedback ITEM_ID --like/--dislike` | 点赞/点踩，可指定 `--persona` 和 `--note` |
| `tentacles persona rollback NAME VERSION` | 复制旧内容为新版本，不删除历史 |
| `tentacles sources list` | 列出信息源及其抓取状态 |
| `tentacles sources fetch [ID...]` | 立即抓取信息源，不写 ID 时抓取全部启用的源 |
| `tentacles items list` | 列出条目，可按来源、时间、关键词筛选，支持分页 |
| `tentacles items show ID` | 查看条目详情 |
| `tentacles tick` | 先并发抓取，再依次过滤、依次运行到期 pipeline |
| `tentacles scheduler status` | 查看任务频率、到期时间、运行状态 |
| `tentacles pipelines list/filter/run ID` | 查看 pipeline、只过滤或生成报告 |
| `tentacles reports list/show ID` | 查看报告列表和全文 |
| `tentacles runs list` | 查看运行记录 |

所有命令都支持 `--json`：stdout 只输出一个 JSON 值，日志写到 stderr，退出码按错误类型区分。给 agent 用的完整说明见 [docs/agent-cli.md](docs/agent-cli.md)。

## IMAP 邮箱

示例配置的 `inbox` 默认 `enabled: false`，不会因升级自动连接邮箱。填好 `.env` 中的
`IMAP_HOST`、`IMAP_USERNAME`、`IMAP_PASSWORD` 后，可手动抓取；设置 `enabled: true`
后由 `tick` 按该源的 `schedule` 自动抓取。密码只通过环境变量／`.env` 注入，不写入游标或日志。

```sh
tentacles sources fetch inbox
tentacles items list --source inbox
tentacles items show ITEM_ID
```

- **Gmail**：本客户端不使用“通过 Google 登录”，需在账户允许时启用两步验证并创建
  **应用专用密码**，不要填写日常账户密码。工作／学校账户、Advanced Protection 等策略可能禁止此功能。
- **Outlook.com / Hotmail**：微软已从 **2024-09-16** 起关闭 Basic Authentication，当前 IMAP
  需要 OAuth2；应用专用密码不能绕过该限制。本期仅实现 IMAP LOGIN，OAuth2/XOAUTH2 不在范围内，
  因此暂不支持这些账户。其他允许密码或应用专用密码的 IMAP 服务器可按其实际策略配置。
- 服务商依据：[Google 应用专用密码](https://support.google.com/accounts/answer/185833)、
  [Microsoft Outlook 现代认证要求](https://support.microsoft.com/en-us/support/known-issues/modern-authentication-methods-now-needed-to-continue-syncing-outlook-email-in-non-microsoft-email-ap)。

`host`、`username`、`password` 配置连接；`ssl` 默认 true，启用时校验证书和主机名，
`port` 默认随 SSL 取 993／143（显式端口优先），`timeout` 默认 30 秒。
`ssl: false` 不会自动升级 STARTTLS，用户名和密码可能明文发送，**只应在可信本地连接使用**。

`folders` 默认 `[INBOX]`，支持含空格、引号和非 ASCII 字符的文件夹名。每个文件夹分别保存
UIDVALIDITY 和最后处理 UID；首次、UIDVALIDITY 或账户身份变化时按 `initial_days`（默认 30）搜索，
之后仅处理更大的 UID。按 UID 升序处理，所有文件夹合计最多 `max_fetch`（默认 100）个 UID，
剩余邮件留到下一轮。文件夹按配置顺序处理；排除的发件人和期间被删除的邮件也消耗处理名额并推进游标。
附加的 `search`（默认 `ALL`）直接与日期／UID 条件组合，例如 `UNSEEN` 或 `FROM "name@example.com"`。
`exclude_senders` 按 From 中完整邮箱地址不区分大小写精确匹配，不按显示名或子串误排除。

文件夹以只读方式选择，正文使用 `BODY.PEEK[]`；不标已读、不移动、不删除、不 expunge。
主题缺失用 `(no subject)`，发件人与 MIME 标题会解码，日期统一为 UTC；外部 ID 优先使用 Message-ID，
缺失时采用“原始文件夹名:UIDVALIDITY:UID”。正文优先纯文本，否则 HTML 转文本；附件及附带邮件的正文
不进入信息流，只记录附件名。清理常见 `>` 引用、回复头及 HTML quote 容器后，正文和摘要分别截断到
`max_body_chars`（默认 20000）、`max_summary_chars`（默认 2000）。邮件不按内容指纹或近似标题去重，
因此内容相同、Message-ID 不同的两封邮件都能保留。认证／连接等失败按 upstream 处理，错误不回显密码、
服务端响应或邮件内容；像其他来源一样，单个失败以抓取结果的 `status=error` 返回，不阻断其他源。

## 网页 list / page / llm 信息源

没有 RSS 的站点可以配置 `type: web`：list 用 CSS 选择器提取文章，可选并发抓新详情；page 保存内容版本，
内容变化时生成带增删行差异的条目。同 URL 的页面版本可独立入库，不会被普通文章判重吞掉。
示例 `web-list` / `web-page` 默认关闭，不会因升级新增网络请求。

```sh
tentacles sources fetch web-list web-page
tentacles items list --source web-page
tentacles items show ITEM_ID
```

使用前替换 URL 与选择器；属性写法、500-id 缓存、Cookie 隔离和页面版本身份见 [Web 信息源协议](docs/web-sources.md)。
仅支持静态页面；非 2xx（含重定向）会失败并保留旧游标。
杂乱页面还可用 `mode: llm`，由 extract 阶段模型从有界“文本 + 链接”中抽取；编造链接被丢弃。
`web-llm` 示例默认关闭，启用后会发送页面内容并可能产生费用；严格 JSON、缓存与计费规则同见协议。

## 模型配置

示例提供 `fast`（DeepSeek）、`smart`（Claude Sonnet）、`max`（Claude Opus）和 `decision`（JEV）。
在 `.env` 中按需填写 key，然后运行 `tentacles models test fast`；这会真实调用模型并可能产生费用。
注册表懒加载，不会在启动、检查配置或列出模型时请求服务商。

纯 RSS 配置可以完全省略 `models` 和 `defaults`。一旦配置模型，就必须在 `defaults.models`
为 `extract`、`filter`、`decision`、`persona`、`process` 五个阶段指定已存在、种类匹配的默认模型。
`api_key` 可以为空；只有真正调用模型时才报错。`models list` 仅显示 key 是否配置。
本地 Ollama/vLLM 等无鉴权服务也请填写非空占位 key，例如 `local`。

OpenAI 兼容接口使用 `provider: openai` 和服务的 API 根路径 `base_url`（如 `http://localhost:11434/v1`）；
Anthropic 使用 `provider: anthropic`，OpenRouter 用 `provider: openrouter`。
详细字段、重试规则和 JSON 输出见 [agent 命令说明](docs/agent-cli.md)。

## Persona 与兴趣标签

配置里的 `personas` 是初始种子，每项的 `profile`（内联文本）和 `profile_file`（UTF-8 文件）二选一。
同名 persona 已存在时，启动会忽略种子内容及文件；数据库是唯一来源。启动导入不调用模型。

```sh
tentacles persona list
tentacles persona show default
tentacles persona set default --file ./my-interests.md --note "调整优先级"
tentacles persona set default --include 机器人 --remove-exclude 科学 --note "更新关键词"
cat ./my-interests.md | tentacles persona set work --stdin --skip-tags
tentacles persona history default
tentacles persona rollback default 1 --note "恢复初始方向"
tentacles persona export default > default-snapshot.yaml
```

`set` 修改 profile 时使用 `persona` 阶段的模型抽取 5–20 个按优先级排序的标签；
已有标签会提供给模型，语义相同应保留旧名。模型失败或标签无效时，profile 修改仍会保存，
旧标签保持不变，版本和运行记录中标记降级。`--skip-tags` 明确禁止本次模型调用；
仅修改关键词/备注或 profile 未变化时也不会调用模型。

关键词去重/删除不区分大小写（Unicode casefold），保留最早的拼写和顺序。
每次编辑写成新版本；回滚同样追加版本。并发的纯关键词编辑会合并。提供 profile 且未跳过标签时，
若提交前 persona 已被修改（包括内容相同的快捷路径），旧请求返回 `conflict`，请刷新后重试。导出的是完整版本快照，不是只有 profile 的文本，也不会直接覆盖任何文件。

## 点踩 → 提案 → 应用

```sh
tentacles feedback ITEM_ID --dislike --note "减少无依据的炒作"
tentacles --json persona evolve default --instruction "多关注机器人" --dry-run > proposal.json
cat proposal.json                            # 检查 summary、diff、标签与关键词
tentacles --json persona apply --proposal proposal.json
# 已确认可以直接应用时，也可以通过 JSON 管道：
tentacles --json persona evolve default --instruction "多关注机器人" --dry-run | tentacles --json persona apply --proposal -
tentacles pipelines filter daily-tech --reevaluate --since 7d
```

`feedback` 未指定 persona 时，只有一个就使用它，否则使用 `default`；未知条目或 persona 返回 `not_found`。
`evolve` 的名称默认 `default`，至少需要非空指令或有效反馈之一；不加 `--dry-run` **直接应用**。
模型使用 `defaults.models.persona`（示例设为 `max`），尊重已有配置，不会擅自升级模型。
预览仍会调用模型并可能产生费用，但不创建版本、运行记录或任何数据库写入。
CLI 预览在内存中的数据库快照运行，迁移、种子导入和调度初始化的改动仅保存在内存，
不更新原库的 schema 或数据。读取既有 WAL 数据库时，SQLite 可能创建或更新 `-wal` / `-shm`
协调文件；这些文件可能正被其他进程使用，预览不会主动删除它们。没有原库时不会创建数据目录。
应用不再次调用模型；基准版本已被其他修改替换时返回 `conflict`，请刷新并重新生成提案。
成功应用追加 `source=llm` 的新版本，备注是变更摘要；无效模型输出不会修改 persona。

`persona_update` 可配置 `feedback_window`（默认 `30d`）、`max_feedback`（默认 100，范围 1–1000）
和 `max_profile_chars`（默认 12000，上限 50000）。仅选窗口内、不在未来、严格晚于最近 LLM
版本创建时间的反馈，按时间与 id 倒序取上限条数；手动编辑或回滚不会重置这个界线。
反馈条目标题、摘要与备注作为有界的证据传入，不视为指令。演化保留结构、优先级与旧标签名，
严格执行指令，不因单条反馈过度泛化；返回的新 profile、5–20 个标签、关键词和摘要均会校验。

`--since` 只可用于 `--reevaluate`，接受时长或 ISO 时间，覆盖 pipeline 的默认窗口起点
（按条目**抓取时间**选取，截止当前时间）。仅清除该 pipeline 当前 persona 版本在窗口内的评估；
不会清除旧版本评估或消费账本，因此重新打分或 persona 改版也不会重复报告已消费的条目。

## Keyword 过滤与 digest 日报

示例配置声明了 `daily-tech` pipeline：使用 default Persona、最近 24h **抓取**的条目，
先用 keyword 级联筛选，再选择分数最高的最多 60 条生成日报。

```sh
tentacles pipelines filter daily-tech
tentacles items list --pipeline daily-tech --passed
tentacles pipelines run daily-tech       # 显式调用 process 模型，可能产生费用
tentacles reports list --pipeline daily-tech
tentacles reports show REPORT_ID
```

`tick` 按顺序执行全部到期任务：先并发抓取，再逐条只过滤，最后逐条运行 pipeline。
重叠调用中遇到来源 busy 时，其依赖的 pipeline 会延期而不推进时间，避免提前空跑漏报；无关 pipeline 仍继续。
每条启用的 pipeline 按 `schedule` 处理；可选 `filter_schedule` 提供更频繁的只过滤轮次。
`pipelines list` 和 `scheduler status` 显示下次执行时间与当前待报告数量。
**注意：`tick` 现在可能调用配置中的模型并产生费用。** 关闭自动运行可设置 pipeline `enabled: false`；
只想抓取可用 `sources fetch`。
过滤和运行各有独立的持久化锁；运行遇到过滤锁被占用时，会直接处理之前已通过的条目。
消费记录独立于 Persona 版本，同一 pipeline 中的条目只消费一次。报告失败时保留未报告状态，修复问题后可以重试。
报告存入 SQLite，同时按 pipeline 写入 `reports_dir` 下的 Markdown 文件（新文件默认私有权限）。

关键词在标题和摘要中不区分大小写匹配，exclude 优先（0 分）、include 为 1 分，否则 0.5。
可启用有执行时限的正则匹配。级联保留每阶段分数、理由及统计；最后阶段出错的条目不缓存，之后可以重试。
`items list --pipeline ... --rejected` 查看淘汰结果；`items show ID` 包含各 pipeline 的最新评估。

`pipelines filter ID --reevaluate` 会删除窗口内当前 Persona 版本的已有评估并重算，
消费账本不会被清除，不会让已报告条目重新进入报告。
`processor.type: none` 不调用模型、不生成文件，但将选中的条目标记为已消费。
完整参数和自定义提示词格式见 [agent 命令说明](docs/agent-cli.md)。

## deep 重大事件深度报道

```bash
tentacles pipelines run weekly-deep
tentacles --json pipelines run weekly-deep
tentacles reports list --pipeline weekly-deep
```

示例 `weekly-deep` 每周运行，窗口 7 天，使用 `max` 模型，默认关闭。
手动运行仍可能产生费用。`min_cluster_size`（默认 3）是事件条目数阈值，
`max_reports`（默认 3）限制每次报道数量；均可配置为 1–500。
沿用既有标题聚类算法，按事件规模、来源数排序，每个达标事件生成一份
“深度报道：事件标题”，保留背景、各方说法、数据与疑点、影响与后续及待确认问题。

一次运行可能生成多份报告：`report_id` 是第一份，`stats.report_ids` 列出全部 ID。
通过过滤、按分数选出的前 `max_items` 条全部标记为已消费，包括未达阈值或超出报道上限的事件。
整个批次成功才发布报告和消费条目；生成或发布失败则整批保留待重试，已知模型费用仍记入运行记录。
`none` 处理器完全不生成报告，也按同样的条目上限消费。详见 [处理器协议](docs/agent-cli.md#deep-处理器)。

## summarize 邮件简报与待办

示例 `mail-summary` 使用 `inbox` 邮件源，二者默认关闭。配置好邮箱和 process 模型后，可以手动运行：

```sh
tentacles sources fetch inbox
tentacles pipelines run mail-summary       # 会调用模型，可能产生费用
tentacles reports list --pipeline mail-summary
tentacles reports show REPORT_ID
```

处理器每 10 条一批生成结构化简报，再汇总成“需要处理”“仅供参考”“未能解析”三部分。
待办用勾选框列出，条目保留发件人、时间及原文／邮件定位。格式错误的一批整体进入“未能解析”，
仍视为已报告；传输或模型调用失败则不消费本次条目，可以重试。设置 `enabled: true` 后会按
pipeline 的频率自动运行；手动命名调用不受开关限制。确认隐私和费用后再开启。

批次关联规则、截止日期排序、提示词格式和费用语义见 [summarize 协议](docs/agent-cli.md#summarize-处理器)。

## Decision（JEV）过滤

可以在 pipeline.filters 中加入 OpenRouter Decisions 阶段：

```yaml
- type: decision
  model: decision          # 可省略，使用 defaults.models.decision
  relevance_type: noul     # 或 score：四级有序量表
  reject_below: 0.3
  accept_above: 0.8
```

每条候选内容分别判断“用户是否想读”；Persona 有兴趣标签时同时选择一个标签（或 other）。
score 的原始分数按量表最大值归一化，保存原分数、概率/置信度及实际返回的模型名。
模型的 `concurrency` 控制固定 worker 池；结果保持输入对应关系，429 对同一批所有 worker 共享暂停点。
401/403/非瞬时 402 会停止新派发，等待在途请求完成，再将本批记为失败；400/413 只影响该条目。
已完成的最终评估仍缓存，重试不会为它们重复请求；取消也会保留已返回的已知用量和成本。

默认要求服务方不收集数据，额外 provider 路由偏好不会移除这个默认值；只有显式 `data_collection: allow` 才放宽。
示例配置中的阶段保持注释，启用后会实际调用模型并产生费用。`models test decision` 可先用固定 noul 题探测。

## LLM 批量过滤

对前面阶段拿不准的条目，可追加一层聊天模型批量分类：

```yaml
- type: llm
  model: fast             # 可省略，默认 defaults.models.filter
  batch_size: 20          # 1–100
  reject_below: 0.5
```

请求包含有序兴趣标签、画像和编号标题列表；每条最多一个标签和一句短理由。
未返回的编号按 0 分处理，未知编号忽略。脏 JSON 会先由共享提取器恢复；无法可靠解析的批次仅拆成两半各重试一次，
失败半批不会被误存成“无关”，而是交给下一阶段或留待重试。重试仍计入已知用量/成本。
无兴趣标签时，使用 profile 中非空条目行作为有序兴趣单元；空画像不盲目生成负面评估。
默认示例仍不自动启用此付费阶段，确认模型和费用后可取消对应配置注释。

## 自动抓取

`schedule` 支持正整数间隔（`30s`、`30m`、`2h`、`1d`、`1w`）、
`@hourly` / `@daily` / `@weekly`，或五段 cron（分、时、日、月、星期）。
cron 支持 `*`、范围、步长、列表（如 `0,30 9-17/2 * * 1-5`），星期 0 和 7 都是周日。
日和星期同时限定时取或（Vixie 语义：任一字段以 `*` 开头时，包括 `*/N`，两者取且），
按 `app.timezone` 计算；夏令时不存在的时刻跳过，
回拨产生的两个真实时刻分别触发。非法频率在加载配置时拒绝。

间隔任务首次立即到期，cron 首次等待下一个触发点。状态保存在 SQLite 中，重启不会重置；
修改频率后以持久化的上次开始时间（尚未执行则用首次出现时间）重新计算。
成功和失败均推进到期时间。配置变更在下次打开 Service/CLI 进程后生效，常驻服务需重启。
从未执行过的 pipeline cron 在首次启用（或首次添加只过滤频率）时，等到下一个触发点；
已有手动运行历史则保留上次开始时间，不重置进度。`sources fetch` 忽略频率立即执行（也推进调度时间），
但与 `tick` 共用原子任务锁；锁被占用时返回 `busy`，一小时后过期可抢占。

### launchd 示例（macOS）

保存为 `~/Library/LaunchAgents/local.tentacles.tick.plist`，把下面所有路径替换成你的绝对路径。
launchd 每 30 秒启动一次短进程，不需要另行运行常驻调度器；SQLite 锁防止重复执行同一任务。
到期的 pipeline 也会运行，因此启用 digest 等处理器前请确认模型配置和费用。
日志目录需要预先存在。密钥继续通过配置目录的 `.env` 加载，不要写进 plist。

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

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.tentacles.tick.plist
# 卸载：launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/local.tentacles.tick.plist
```

## 开发

```bash
mypy src/tentacles     # 类型检查
```
