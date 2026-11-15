# 基础配置与命名模型

所有配置集中在一个 YAML 文件中，默认为 `config/tentacles.yaml`，也可通过全局参数 `--config/-c PATH` 或环境变量 `$TENTACLES_CONFIG` 指定。

## 1. 环境变量与路径解析

- **环境变量插值**：配置值中可写 `${VAR}` 或 `${VAR:-默认值}`。系统启动时会依次尝试读取项目根目录或配置文件所在目录的 `.env`，但**不会覆盖进程已存在的环境变量**。请始终将 API Key、邮箱密码、Server Token 存放在 `.env` 中。
- **相对路径基准**：`app.data_dir`、`app.reports_dir`、`personas.*.profile_file`、`processor.prompt_file`、`server.static_dir` 等所有相对路径，一律**相对于该 YAML 配置文件所在的目录**解析。

```yaml
app:
  timezone: Asia/Shanghai     # Web 展示与 5 段 cron 计算使用的 IANA 时区；SQLite 内一律存 UTC
  data_dir: ../data           # SQLite 数据库目录
  reports_dir: ../reports     # Markdown 报告落盘目录
  storage:                    # 冷热分层（详见第 5 节）
    hot_retention_days: 365   # 热库保留天数
    archive_dir: ../data/archive  # 年度冷库目录 tentacles-YYYY.db
```

## 2. 配置校验（`tentacles config check`）

修改 `tentacles.yaml` 后，建议先运行无副作用校验：

```bash
tentacles config check
```

- 一次性收集 YAML 结构、`${VAR}` 插值、Source/Pipeline ID 唯一性、模型阶段种类匹配、Jinja 提示词模板语法及 Persona 引用的全部错误，不会输出密钥或文件内容。
- 对已存在的数据库，会将其连同 WAL/journal 复制到私有临时目录做只读 Persona 名称核验，**绝不修改原库、不创建数据目录、不执行 schema 迁移或注册调度任务**。

---

## 3. 命名模型（`models`）与五阶段路由（`defaults.models`）

纯 RSS 抓取场景可以完全省略 `models` 与 `defaults`。一旦配置了 `models`，就必须在 `defaults.models` 中为以下 **5 个阶段**显式指定已存在且种类（`kind`）匹配的默认模型：

| 阶段 (`stage`) | 要求模型种类 (`kind`) | 用途与触发时机 |
| :--- | :--- | :--- |
| **`extract`** | `chat` | `type: web` + `mode: llm` 网页智能抽取（可在具体 source 上用 `model:` 覆盖） |
| **`filter`** | `chat` | `- type: semantic`（或旧写法 `type: llm`）在模型为 chat 时使用的默认模型（可在该 filter 上用 `model:` 覆盖） |
| **`decision`** | `decision` | `- type: semantic`（或旧写法 `type: decision`）在模型为 decision 时使用的默认模型；只有 `type: decision` 会默认取这一项（仅支持 OpenRouter Decisions API，如 JEV） |
| **`persona`** | `chat` | `persona set` 修改画像文本时自动抽取 5–20 个兴趣标签，以及 `persona evolve` 画像演化 |
| **`process`** | `chat` | Pipeline 处理器（`digest` / `deep` / `summarize`）生成最终报告（可在 processor 上用 `model:` 覆盖） |

### 配置示例

```yaml
models:
  fast:
    provider: openai
    model: deepseek-flash
    base_url: https://api.deepseek.com
    api_key: ${DEEPSEEK_API_KEY:-}
    max_tokens: 4096
    timeout: 60
    retries: 2
  smart:
    provider: anthropic
    model: claude-sonnet-5-5
    api_key: ${ANTHROPIC_API_KEY:-}
    max_tokens: 4096
  max:
    provider: anthropic
    model: claude-opus-5-5
    api_key: ${ANTHROPIC_API_KEY:-}
    max_tokens: 8192
  decision:
    provider: openrouter
    kind: decision
    model: typesafe/jev-1.13
    api_key: ${OPENROUTER_API_KEY:-}
    concurrency: 8
    extra_body:
      provider: {data_collection: deny}

defaults:
  models:
    extract: fast
    filter: fast
    decision: decision
    persona: max
    process: smart
```

### 模型字段参考

| 字段 | 含义与默认值 |
| :--- | :--- |
| `provider` | `openai`（默认，适用于所有 OpenAI 兼容接口如 DeepSeek、Moonshot、Ollama、vLLM）、`anthropic`、`openrouter` |
| `kind` | `chat`（默认）或 `decision`（仅限 `provider: openrouter`） |
| `model` | 服务商侧真实模型 ID（必填） |
| `base_url` | API 根路径（如本地 Ollama `http://localhost:11434/v1`）；缺省使用对应服务商官方地址；禁止包含凭据、query 或 fragment |
| `api_key` | 支持为空字符串；启动、`config check` 和 `models list` 时不联网且不报错，**只有实际发起模型调用时才检查非空**。本地无鉴权服务（Ollama/vLLM）请填任意非空占位值（如 `local`） |
| `temperature` | 可选 `0~2`，未配置则不在请求体中发送 |
| `max_tokens` | 正整数，默认 `4096`（Anthropic 始终发送；OpenAI 兼容接口若在 `extra_body` 指定了 `max_completion_tokens`，则自动省略旧 `max_tokens`） |
| `timeout` | 单次 HTTP 请求超时秒数，默认 `60` |
| `retries` | 首次请求失败后的最大重试次数，默认 `2` |
| `concurrency` | `decision` 模型每批并发固定 worker 上限，默认 `8` |
| `extra_body` | 原样合并进请求 JSON 的扩展参数；禁止覆盖 `model / messages / system / stream / state / questions` 等协议保留键 |

### 连通性测试、重试与安全边界

- **探测命令**：`tentacles models list` 查看密钥是否已配置（绝不回显明文）；`tentacles models test <NAME>`（或在 Web **Settings** 页点击测试）会发起一次真实轻量请求（**可能产生微量费用**），返回延迟、响应样例、Token 用量与服务商返回的 `cost`。
- **重试策略**：对 HTTP `408 / 409 / 425 / 429`、所有 `5xx`（含 `524 / 529`）、网络错误和超时自动重试。优先遵守响应头 `Retry-After`（支持秒数和 HTTP 日期，上限 60 秒），否则采用带抖动的指数退避（上限 30 秒）。不跟随 HTTP 重定向，防止 `Authorization` 头泄露到第三方域名。
- **密钥脱敏**：所有模型 `api_key` 注册进全局脱敏器，任何日志、异常诊断、报告正文或提取标题中若意外出现已配置的密钥文本，均会被强制替换为 `[REDACTED]`。

---

## 4. 有界面浏览器运行时（`browser`）

`type: browser` 信息源共用一个**严格禁止无头模式**的 Chrome 运行时：它复用你日常 Chrome 的登录态，
在真实可见窗口里新建一个隔离工作 Tab 重放固化脚本，抓完即关，不抢占你的浏览窗口。

```yaml
browser:
  cdp_url: http://127.0.0.1:9222          # Chrome Remote Debugging 端点
  webbridge_url: http://127.0.0.1:10086   # 探索期本地工具桥接地址（仅回环）
  auto_launch: false                      # CDP 无人监听时是否自动拉起有界面 Chrome
  chrome_executable: null                 # 可选：显式 Chrome 可执行文件路径
  chrome_user_data_dir: null              # 可选：Chrome User Data 根目录（auto_launch 时必填）
  default_profile: Default                # 默认 Chrome Profile 目录名（如 Default、Profile 1）
  scripts_dir: ../scripts/browser_sources # 固化 Playwright 脚本目录，相对本文件解析
  pi:                                     # 首次探索与脚本提炼所用的 Agent 模型
    binary: pi
    model: anthropic/claude-sonnet-4-6
    reasoning_level: high                 # off | minimal | low | medium | high | xhigh | max
    timeout: 300
  pr:                                     # 预留：#83 脚本自愈后自动提 PR
    enabled: true
    base_branch: master
    branch_prefix: auto/browser-source-
    labels: [ready-for-human]
```

| 字段 | 含义与默认值 |
| :--- | :--- |
| `cdp_url` | 用 `connect_over_cdp` 附着的有界面 Chrome 调试端点，**必须带显式端口**，默认 `http://127.0.0.1:9222` |
| `webbridge_url` | 首次探索时的本地工具桥接端点，**只接受回环地址与显式端口**（默认 `http://127.0.0.1:10086`）。抓取进程在该地址启动一次性 JSON 端点，受控 `pi` 子进程里的扩展只能通过它操作工作 Tab；端口被占用会直接报配置错误 |
| `auto_launch` | 默认 `false`。为 `true` 且 `cdp_url` 无人监听时，按目标 Profile 以有界面模式拉起 Chrome（**绝不传 `--headless`**）；抓取结束后只关闭这次拉起的进程 |
| `chrome_executable` | 可选 Chrome 可执行文件；缺省在 macOS/Windows 常见路径与 `PATH` 的 `google-chrome*` / `chromium*` 中查找 |
| `chrome_user_data_dir` | 可选 User Data 根目录；附着时若配置了它，会与实际命令行 `--user-data-dir` 比对 |
| `default_profile` | 默认 Chrome Profile 目录名，源级 `chrome_profile` 可覆盖；只接受目录名（禁止 `/`、前导 `-` 等可注入参数的值） |
| `scripts_dir` | 固化脚本目录，相对 YAML 解析；源级 `script_path` 必须解析到该目录内的 `.py` 文件 |
| `pi.binary` | `pi` 可执行文件，默认走 `PATH` 上的 `pi`；源级 `pi.binary` 可覆盖 |
| `pi.model` | 首次探索与脚本提炼使用的模型（`provider/id` 形式），源级 `pi.model` 可覆盖 |
| `pi.reasoning_level` | 映射为 `pi --thinking`，取值 `off`/`minimal`/`low`/`medium`/`high`/`xhigh`/`max` |
| `pi.timeout` | 单轮探索或提炼回合的超时秒数（默认 300）；超时会中止该回合并杀掉子进程 |
| `pr` | #83 的预留配置块，当前**只做 schema 校验**，不影响抓取行为 |

`pi` 只在**尚无固化脚本的冷启动**里使用：探索、提炼与试跑都消耗真实模型 Token 与费用，
完整流程与计费说明见 [信息源 → 有界面浏览器源](sources.md#5-有界面浏览器源type-browser)。

**硬性安全边界**

- **绝不无头**：`headless` 只接受 `false`（或缺省）。`browser.headless: true` 与源级 `headless: true` 都会直接报配置错误，
  运行时代码里也不存在任何无头分支；附着到的 Chrome 若带 `--headless`，同样立即拒绝。
- **Profile 核验**：附着成功后读取 `chrome://version` 的真实命令行，比对 `--profile-directory`（以及配置了
  `chrome_user_data_dir` 时的 `--user-data-dir`）。不匹配时 `ConfigError` 会直接给出可复制执行的启动命令。
- **脚本路径防越界**：源级 `script_path` 必须是以 `.py` 结尾、真实路径落在 `browser.scripts_dir` 内的文件；
  `../../` 或符号链接逃逸都会在 `tentacles config check` 阶段报错。
- **只关自己的 Tab**：每次重放新建一个隔离工作 Tab，无论成功、超时、异常还是 Ctrl-C 取消都只关闭该 Tab；
  运行时不调用 `browser.close()`，绝不关闭你的 Chrome 主窗口，也不写你的 Profile。

---

## 5. 存储冷热分层与年度冷归档（`app.storage`）

热库始终是**唯一的在线数据库** `data/tentacles.db`：7 天深度聚类、30 天反馈回溯、跨月原位更新与 SQLite
外键完整性全部留在原地，不会因为跨月被切成多个库。运行数年后，超出保留期的历史条目可以按**发布时间的自然年**
搬进独立冷库：

```text
data/
├── tentacles.db                # 热库：默认保留最近 hot_retention_days 天
└── archive/
    ├── tentacles-2024.db       # 2024 自然年冷库（自带独立 items_fts 索引）
    └── tentacles-2025.db
```

```yaml
app:
  storage:
    hot_retention_days: 365     # storage archive 的默认截止时间（天）
    archive_dir: ../data/archive  # 相对配置文件所在目录解析
```

| 配置项 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `hot_retention_days` | `365` | `tentacles storage archive` 不传 `--before` 时的默认截止天数：更早的条目会被搬进冷库 |
| `archive_dir` | `../data/archive` | 年度冷库目录，相对 YAML 文件解析；`tentacles-<YYYY>.db` 由工具生成，`tentacles config check` 会拒绝把它设成 `app.data_dir` 或其父目录 |

**归档不变量**

1. **去重墓碑永久留在热库**：条目迁出时，热库只保留极窄的 `archived_identities`
   （`source_id`、`external_id`、`url`、`fingerprint`、归档年份），`source_seen_ids` 一并保留，因此归档之后
   任何信息源再抓到旧文章都不会被当成 `new`：按外部身份、原文链接与内容指纹三条规则都会判为重复。
2. **活跃关联自动豁免**：仍有未决错误（`errors list --unresolved`）、处于 `persona_update.feedback_window`
   活跃反馈窗口内、或最近 30 天还有更新活动的条目一律留在热库，不会产生悬空引用。
3. **报告自洽**：`reports` 自带完整 `markdown`，其 `item_ids` 指向已归档条目时仍可通过
   `items show --include-archive` 查回原文。

**运维命令**（完整 JSON 契约见 [Agent CLI 手册](../agent-cli/operations-reference.md#7-存储状态索引重建与冷归档storage)）：

```bash
# 先看体积、各表行数与已有冷库清单（只读）
tentacles --json storage status

# 只统计会迁出/豁免多少条，不创建也不修改任何文件
tentacles --json storage archive --dry-run

# 正式归档：先写冷库并校验，再在一个热库事务里写墓碑、删冷行；--vacuum 回收空间
tentacles --json storage archive --before 365d --vacuum

# 重建（或 --optimize 合并）FTS5 索引，可带上全部冷库
tentacles --json storage reindex --include-archive

# 跨年检索：热库 + 全部冷库归并分页，结果用 archive_year 标注年份
tentacles --json items search 'iPhone' --include-archive
```

归档是**两阶段**的：先把条目与对应 `evaluations` 写进 `tentacles-<YYYY>.db` 并校验该库的 `items_fts`，
再在一个热库写事务里写入墓碑并删除冷行。中途被打断时热库要么全有要么全无，重跑 `storage archive`
是幂等的；期间被更新过的条目会留在热库（返回里计入 `deferred`），冷库里的过期副本会被撤掉。
