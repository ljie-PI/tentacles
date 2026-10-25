# tentacles

个人信息与新闻管理系统：从多种信息源抓取内容，按个人 persona 过滤，再用 LLM 加工成日报、深度报道或邮件简报。整体设计见 [#18](https://github.com/ljie-PI/tentacles/issues/18)，实施进度见它的子 issue。

> 目前已完成骨架：配置驱动的 RSS 抓取、去重入库、条目查询、运行记录、持久化调度、命名模型注册表/连通性测试、版本化 Persona 管理，以及 keyword/decision/llm 级联过滤和 digest 日报。pipeline 自动调度已接入；其他过滤/处理、邮件和网页源、Web 端将陆续加入。

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
pytest                 # 测试，不联网：HTTP 通过可注入的 transport 伪造
mypy src/tentacles     # 类型检查
```

测试都通过 Service 门面（`TentaclesService`）驱动。外部依赖只在边界处替换：HTTP 用 `tests/conftest.py` 里的 `FakeWeb`。
