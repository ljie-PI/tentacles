# tentacles

个人信息与新闻管理系统：从 RSS、IMAP 邮箱、静态网页与有界面浏览器抓取内容，按版本化个人画像（Persona）级联过滤，再用大模型加工为日报、深度报道或邮件简报。

- **AI Agent 使用技能**：见 [`skills/`](skills/)（`tentacles-feed`、`tentacles-persona`、`tentacles-ops`）
- **Web 与 REST API 参考**：见 [`docs/api.md`](docs/api.md) 与 [`frontend/README.md`](frontend/README.md)
- **浏览器脚本契约与开发文档**：见 [`scripts/browser_sources/README.md`](scripts/browser_sources/README.md) 与 [`docs/README.md`](docs/README.md)

---

## 快速开始

### 方式一：从 GitHub Release 直接安装（无需克隆仓库或安装 Node.js）

要求 Python >= 3.10（推荐使用 `uv` 或 `pipx`）：

```bash
uv tool install https://github.com/ljie-PI/tentacles/releases/download/v0.2.0/tentacles-0.2.0-py3-none-any.whl
tentacles init                    # 初始化 ~/.tentacles（配置、提示词、.env、data/、reports/）
tentacles skills install          # 可选：安装 Agent 技能至 ~/.agents/skills/
tentacles serve                   # 启动内置 Web 界面（http://127.0.0.1:8000）与后台调度器
```

按需编辑 `~/.tentacles/.env` 填入模型 API Key 或 IMAP 凭据后即可使用。

### 方式二：源码开发安装

#### 1. 安装后端并校验配置

要求 Python >= 3.10：

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env              # 按需填入模型 API Key、IMAP 密码或 Server Token
tentacles config check            # 无副作用校验 config/tentacles.yaml
```

默认仓库配置开启了中英文科技与新闻 RSS、GitHub Trending 网页源、`mail-tech` / `mail-finance` 邮箱源以及有界面浏览器滚动源（`x-home` 与 Reddit 自定义流；通过 `tentacles init` 初始化新机器工作目录时会自动将 `type: browser` 源置为 `enabled: false`，未配置模型 Key、邮箱凭据或登录态 Chrome 时可先指定单个 RSS 源测试抓取）：

```bash
tentacles sources fetch solidot   # 单源测试抓取（配置好邮箱后可直接运行 tentacles sources fetch）
tentacles items list --since 24h  # 查看最近 24 小时入库的条目
```

#### 2. 构建前端与启动 Web 服务（可选）

要求 Node >= 22.12、pnpm 10.33：

```bash
cd frontend && pnpm install --frozen-lockfile && pnpm build && cd ..
tentacles serve                   # 托管前端、REST API 与内置调度器（--no-scheduler 关闭后台轮询）
```

访问 `http://127.0.0.1:8000`；前端开发调试可运行 `tentacles serve --no-scheduler` 并在 `frontend/` 下启动 `pnpm dev`。

#### 3. 安装 AI Agent 技能（可选）

将 `skills/` 下的 3 个技能软链至 `~/.agents/skills/`，并在 `~/.local/bin/tentacles` 生成带默认配置路径的 CLI 入口（支持 `--target`、`--bin-dir`、`--copy`、`--uninstall`）：

```bash
./scripts/install-skills.sh
```

---

## 全局配置与存储（`config/tentacles.yaml`）

默认按 `--config/-c PATH` → `$TENTACLES_CONFIG` → `./config/tentacles.yaml` → `~/.tentacles/config/tentacles.yaml` 顺序解析配置文件：

- **环境变量插值**：配置值支持 `${VAR}` 或 `${VAR:-默认值}`。启动时依次读取项目根目录与配置文件所在目录的 `.env`，**不会覆盖进程已有环境变量**。密钥一律放在 `.env` 中，勿写入 YAML（注意 `TENTACLES_CONFIG` 与 `TENTACLES_JSON=1` 须作为真实环境变量设置，写在 `.env` 内不生效）。
- **相对路径基准**：`app.data_dir`、`app.reports_dir`、`app.storage.archive_dir`、`personas.*.profile_file`、`processor.prompt_file`、`server.static_dir`、`browser.chrome_user_data_dir`、`browser.scripts_dir` 均相对于该 YAML 所在目录解析。
- **配置校验**：`tentacles config check` 只读核验 YAML 结构、插值、ID 唯一性、模型种类匹配、Jinja 模板与已有数据库中的 Persona 名称，不修改原库、不联网、不创建目录或调度任务。

```yaml
app:
  timezone: Asia/Shanghai        # 展示与 5 段 cron 计算使用的 IANA 时区；SQLite 内一律存 UTC
  data_dir: ../data              # SQLite 热库目录（tentacles.db）
  reports_dir: ../reports        # Markdown 报告落盘目录（文件权限 0600）
  storage:
    hot_retention_days: 365      # storage archive 默认截止天数
    archive_dir: ../data/archive # 按自然年归档目录 tentacles-YYYY.db（不可设为 data_dir 或其父目录）
```

### 冷热存储不变量

- `data/tentacles.db` 是唯一的在线热库；超过保留期的条目按发布时间（缺失时按抓取时间）自然年迁入 `data/archive/tentacles-YYYY.db`（各自带 `items_fts` 索引）。
- 迁出时热库保留窄表 `archived_identities` 去重墓碑与 `source_seen_ids`，与热行遵循同一套去重规则（外部身份总是判重；URL 仅 `standard` 判重；指纹仅 `standard` 且非 email 判重），旧文不会复活。
- 仍有未决错误、处于 `persona_update.feedback_window` 活跃反馈窗口内、或最近 30 天有更新的条目自动豁免留在热库；归档采用两阶段提交，可幂等重跑。

---

## 命名模型与五阶段路由（`models` / `defaults.models`）

纯 RSS + `keyword` 过滤场景可完全省略 `models` 与 `defaults`。一旦配置 `models`，必须在 `defaults.models` 中为以下 **5 个阶段**指定已存在且 `kind` 匹配的默认模型：

| 阶段 (`stage`) | 要求 `kind` | 用途与触发时机 |
| :--- | :--- | :--- |
| `extract` | `chat` | `type: web` + `mode: llm` 网页智能抽取（可在 source 上用 `model:` 覆盖） |
| `filter` | `chat` | `type: semantic`（或旧写法 `llm`）使用 `chat` 模型时的默认模型（可覆盖） |
| `decision` | `decision` | `type: semantic`（或旧写法 `decision`）使用 `decision` 模型时的默认模型（仅 OpenRouter JEV） |
| `persona` | `chat` | `persona set` 修改画像文本自动抽取 5–20 个标签，以及 `persona evolve` 演化 |
| `process` | `chat` | Pipeline 处理器（`digest` / `deep` / `summarize`）生成报告（可覆盖） |

### 模型字段

| 字段 | 说明与默认值 |
| :--- | :--- |
| `provider` | `openai`（默认）、`openai-compatible`（兼容 DeepSeek / Moonshot / Ollama / vLLM 等，须填 `base_url`）、`anthropic`、`openrouter`、`opencode-go`（自动附带 `x-opencode-session`） |
| `kind` | `chat`（默认）或 `decision`（仅限 `provider: openrouter`） |
| `model` | 服务商侧真实模型 ID（必填） |
| `base_url` | API 根路径（如本地 Ollama `http://localhost:11434/v1`）；禁止携带凭据、query 或 fragment |
| `api_key` | 支持为空；仅实际发起调用时检查非空。本地无鉴权服务填任意非空占位值（如 `local`） |
| `temperature` / `max_tokens` | `temperature` 可选 `0~2`；`max_tokens` 默认 `4096`（OpenAI 接口若在 `extra_body` 设了 `max_completion_tokens` 则省略 `max_tokens`） |
| `timeout` / `retries` / `concurrency` | 默认超时 `60`s、重试 `2` 次；`decision` 并发 worker 上限默认 `8` |
| `headers` | 可选自定义 HTTP 请求头字典；禁止覆盖 `authorization / x-api-key / host / content-length / content-type`，敏感头自动脱敏 |
| `extra_body` | 合并进请求 JSON 的扩展参数；禁止覆盖 `model / messages / system / stream / state / questions` |

- **重试与脱敏**：对 `408 / 409 / 425 / 429`、所有 `5xx`、网络错误和超时自动重试（优先遵守 `Retry-After`，上限 60s，否则指数退避上限 30s），不跟随 HTTP 重定向。所有模型 `api_key` 均全局脱敏为 `[REDACTED]`。
- **探测**：`tentacles models list` 仅查看密钥是否配置；`tentacles models test <NAME>` 发起真实轻量请求（可能产生微量费用）。

---

## 信息源配置（`sources` & `browser`）

通用必填字段：`id`（全局唯一，`1..128` 位字母/数字/`_` /`.`/`-`，首字符须为字母或数字，禁止保留字 `fetch`、`..` 及末尾 `.` / `.lock`）、`type`（`rss` \| `imap` \| `web` \| `browser`）、`schedule`；可选 `name`、`enabled`（默认 `true`；`false` 跳过定时 `tick`，显式 `sources fetch <ID>` 仍可执行）、`update_existing`（默认 `false`）。

- **统一入库规则**：正文 `content` 截断至 **1000 units**（1 个 CJK 字符或 1 个连续非 CJK 词元计 1 unit，`content_truncated` 标记截断），`summary` 不超过 **300 units**；非邮件条目必须保留合法 `http(s)` 的 `url` 或入口 `page_url`；讨论页链接写入 `meta.comments_url`。

### 1. RSS / Atom（`type: rss`）
- 字段：`url`（必填）、`max_age_days`、`headers`、`proxy`、`timeout`（默认 30s）。
- 自动处理条件 GET（`ETag` / `Last-Modified` -> `304`）；执行多级去重（同源 `external_id`、规范化 URL 剥离追踪参数、标题+摘要指纹及 48h 跨源近似标题去重）。

### 2. IMAP 邮箱（`type: imap`）
- 字段：`host`、`port`（默认 SSL `993` / 非 SSL `143`）、`ssl`（默认 `true` 严格校验证书；`false` 不升级 STARTTLS 仅限可信本地桥接）、`username`、`password`、`folders`（默认 `[INBOX]`）、`include_subfolders`（默认 `false`，为 `true` 时通过 IMAP `LIST` 递归展开子文件夹）、`initial_days`（默认 30）、`max_fetch`（默认 100，所有文件夹合计）、`search`（默认 `ALL`）、`exclude_senders`、`max_body_chars`（20000）、`max_summary_chars`（2000）、`timeout`（30）。
- **认证要求**：Gmail 须启用两步验证并使用**应用专用密码**；Outlook.com / Hotmail 已停用 Basic Auth 强制 OAuth2，当前仅支持 `IMAP LOGIN` 暂不适用。
- **只读语义**：始终以只读模式和 `BODY.PEEK[]` 读取，绝不标记已读、移动或删除；按文件夹独立维护 `UIDVALIDITY + 最后 UID` 游标；优先取 `text/plain`，清理引用块并仅记录附件名；不做指纹/近似标题去重；单封坏信跳过并推进游标。

### 3. Web 网页源（`type: web`）
仅处理静态 HTTP `2xx`（不执行前端 JS、不跟随 `3xx` 重定向）；自定义 `headers` / `cookies` 仅发往同源，跨域抓取详情时自动剥离凭据：
- **`mode: list`**：必填 `url` 与 `selectors`（`item`、`title`、`link`，可选 `summary`、`date`；支持 `选择器@属性` 与 `@属性`）；可选 `fetch_content`（提取 `article -> main -> body`）、`concurrency`（1–16，默认 4）、`on_detail_error`（`fail_batch` 整批回滚并落账本 \| `record_and_skip` 单篇落账本下轮重试）。
- **`mode: page`**：`url` + 可选 `selector`（匹配 0 个节点时报错保留旧快照防误判）、`emit_baseline`（默认 `true`）；区域文本变化时生成 `identity_policy="revision"` 的 `unified_diff` 版本条目。
- **`mode: llm`**：使用 `defaults.models.extract`（可用 `model:` 覆盖）和 `max_input_chars`（1–200000）；丢弃页面内不存在的幻觉链接并记账；页面有效文本、链接集合及抽取模型配置未变时命中持久游标缓存（零模型调用）。

### 4. 有界面浏览器源（`type: browser`）
适用于需登录态或动态渲染站点。在真实可见 Chrome 中开隔离工作 Tab 执行采集，支持 `mode: script`（默认，重放固化脚本 `scripts/browser_sources/<id>.py`）与 `mode: list`（无脚本，基于 CSS 选择器自动下翻滚动与互动量预筛）；**严格禁止无头模式**（`headless: true` 直接报错）。

```yaml
browser:
  cdp_url: http://127.0.0.1:9222          # Chrome Remote Debugging 端点（须带显式端口）
  webbridge_url: http://127.0.0.1:10086   # 冷启动探索的一次性本地回环桥接地址
  auto_launch: false                      # true 且端口无监听时以有界面模式拉起目标 Profile
  chrome_executable: null                 # 可选 Chrome 可执行文件路径
  chrome_user_data_dir: null              # User Data 根目录（auto_launch 时必填）
  default_profile: Default                # 默认 Chrome Profile 目录名（禁止路径穿越或前导 '-'）
  scripts_dir: ../scripts/browser_sources # 固化脚本目录，源级 script_path 须位于其内
  pi:
    binary: pi
    model: anthropic/claude-sonnet-4-6
    reasoning_level: high                 # off | minimal | low | medium | high | xhigh | max
    timeout: 300
  pr:
    enabled: true                         # false 时仍提交到本机评审分支，只跳过 push 与 gh
    base_branch: master                   # PR 目标分支（永不直接推送）
    branch_prefix: auto/browser-source-
    labels: [ready-for-human]
```

- **通用源级字段**：`page_url`、`mode`（`script` \| `list`，默认 `script`）、`chrome_profile`、`max_items`（1–500，默认 25）、`timeout`（`script` 默认 `60`s，`list` 默认 `180`s）。
- **`mode: script`（默认）**：可选 `task`（冷启动探索提示）、`script_path`、`on_detail_error`（仅控制冷启动失败：`record_and_skip` \| `fail_batch`）、`pi`（源级覆盖）。缺少脚本时走受控 `pi --mode rpc` 探索与提炼（轨迹写入 gitignored 的 `data/trajectories/`），重放异常/0 条时触发一次 `pi` 自愈（最多 2 候选）；脚本须通过 AST 静态门禁与真实试跑门禁才落盘，并按 `browser.pr` 在隔离 `git worktree` 中单文件提交并开/更新评审 PR（软降级不阻断采集；命中凭据扫描则中止推送）。
- **`mode: list`（滚动列表与互动量预筛）**：零 LLM/脚本开销，适用于 X / Reddit 等虚拟列表无限滚动流（与 `script_path` / `task` / `pi` / `on_detail_error` / `sources distill` 互斥）：
  - `preset`（可选）：内置站点模板 `reddit`（`shreddit-post` 选择器 + 作者与票数/评论前缀）或 `x-following`（自动切到「正在关注 / Following」Tab + 推文/引用推文/卡片与互动指标抽取）；显式配置的 `selectors` / `actions` / `format` 会叠加覆盖在 `preset` 之上。
  - `selectors`（未设 `preset` 时必填）：纯 DOM 选择器配置——`item`（卡片节点 CSS）、`link`（必填，支持多候选如 `a@data-url, a@href` 或 `css@attr` / `@attr`），以及 `title`、`content`、`summary`（三者至少配其一；省略 `title` 时自动从正文首行派生；卡片内命中多个节点如引用转推时自动合并）、`author`、`date`、`comments_url`、`external_url`（存入 `meta.external_url`）、`media`（图片/视频/卡片媒体节点 CSS 列表）、`skip_if`（命中即跳过的广告/置顶 CSS 列表）、`require`（卡片须匹配的 CSS 列表，如 `["time"]`）、`metrics`（`votes` / `likes` / `comments` / `reposts` / `views` 选择器，自动解析 `K/M/B`、`万/亿` 与 `aria-label` 非负数字写入 `meta.metrics`）。
  - `actions`（可选）：扫描前的前置页面点击动作列表（如切换时间流 Tab：`{selector, has_text, nth, wait_ms}`；显式设 `[]` 可关闭预设动作）。
  - `format`（可选）：存储于 `meta.display_format`，在展示视图（CLI / Web API）与下游语义过滤/报告 Prompt 渲染边界动态拼装 `prefix_author_in_title`、`prefix_metrics_in_title`（`作者 | 互动数字 | 真实标题`）与 `prepend_metadata_in_content`（`[Author: ... | Published: ... | Metrics: ...]`），而热库 `items` 表始终保存纯净语义标题与正文，不污染跨源去重、FTS5 索引与 `update_existing` 修订号。
  - `scroll`：`max_scrolls`（0–200，默认 `20`）、`wait_min_ms`（默认 `2000`）、`wait_max_ms`（默认 `10000`）、`idle_rounds`（连续无新卡片轮数即停，默认 `2`）；跨轮按规范化 URL 累积去重，已入库旧链接自动跳过且不占用 `max_items` 配额。
  - `prefilter`：`match`（`any` \| `all`，默认 `any`）、`min_votes` / `min_likes` / `min_comments` / `min_reposts` / `min_views`、`min_text_chars`（默认 `0`）与 `min_media_text_chars`（命中 `selectors.media` 的图文/视频帖单独适用的字数门槛）；页面有卡片但全部被 `prefilter` 或已读去重过滤时正常返回空批，不报错且不触发 `pi`。
- **运行时隔离**：按 `(cdp_url, profile)` 串行获取焦点；通过 `chrome://version` 核验真实 Profile 且禁用该 Tab 的 HTTP 缓存；退出时仅关闭工作 Tab。

### 5. 原位更新（`update_existing: true`）
支持 **RSS / Atom**、**Web `list` / `llm`** 与 **`type: browser`**（IMAP 与 Web `page` 设为 `true` 报错）：
- 仅当本轮源列表再次出现该本源标准条目时才核验更新（RSS 先按同源稳定外部 ID/别名定位再回退规范化 URL；Web/Browser 按规范化 URL 定位；身份冲突整批回滚）。
- `fetch_content: true` 会重新抓取详情并与 `meta.full_content_sha1` 全文哈希比对；发生实质变更时保留 `id` 与首次 `fetched_at`，更新 `updated_at`、递增 `content_revision` 并使旧评分失效，按 `updated_at` 重新进入 Pipeline 窗口重评，但**同一 Pipeline 已消费过的条目不会重复生成报告**。

---

## Persona 种子与流水线（`personas` / `pipelines`）

```yaml
persona_update:
  feedback_window: 30d       # 演化采纳最近多久的反馈
  max_feedback: 100          # 单次演化最多附带反馈条数（1~1000）
  max_profile_chars: 12000   # 画像文本上限（最高 50000）

personas:
  default:
    profile: |               # 与 profile_file 二选一
      优先关注人工智能与开源软件的实质进展。
    include: [人工智能, AI, 开源]
    exclude: [广告, 促销]                # 软排除：同时命中正向词时豁免转交语义关
    hard_exclude: ['限时特惠', '扫码加群'] # 硬排除：命中即 0 分淘汰
```

- **数据库是唯一权威来源**：`personas` 仅在数据库尚无同名 Persona 时作为 `v1 (source=seed)` 导入（不调用模型），后续改动请通过 Web **Personas** 页或 `tentacles persona` CLI 进行。

### 级联过滤（`pipelines[].filters`）
Pipeline 按 `window`（`COALESCE(updated_at, fetched_at)`）与 `max_items`（1–5000）拉取候选：
1. 得分 `< reject_below`（默认 `0.5`）立即淘汰；`>= accept_above` 立即通过；介于两者之间转交下一阶段；最后阶段未淘汰即通过（`filters: []` 全部按 `1.0` 通过）；末阶段出错暂不落库并写入故障账本等待重试。
2. **`keyword`**：合并 Persona 与阶段词表，不区分大小写匹配标题+摘要（`regex: true` 时单条限时 50ms）。命中 `hard_exclude` 得 `0.0`；只命中软 `exclude` 得 `0.0`；软 `exclude` 与正向词同时命中且 `guard_ambiguous_exclude: true`（默认）得 `0.55` 转交下一关；仅命中正向词在 `accept_above: 1.0` 时给 `1.0` 短路，否则 `0.85`；均未命中给 `0.5`。
3. **`semantic`**（推荐写法；兼容旧 `llm` / `decision`）：根据所选模型自身的 `kind` 自动分派 **Chat 批量链路**（`batch_size` 1–100 默认 20，`summary_chars` 0–1000 默认 160，脏 JSON 自动恢复/折半重试）或 **JEV 链路**（`relevance_type`: `noul` \| `score`，`jev_batch_mode`: `packed_questions` \| `windowed_pool`，`400/413` 自动折半并回退逐条并发，默认 `provider.data_collection = "deny"`）。每完成一个 `batch_size` 分块即在租约保护下增量落库。可选 `instructions`（不超过 4000 字符）附加流水线级筛选约束，与 Persona 一并参与判断。

### 报告处理器（`pipelines[].processor`）
按分数倒序选出窗口内已通过且未被本 Pipeline 消费的前 `max_items` 条，**同一条目在同一 Pipeline 中终身只消费一次**：
- `digest`：日报，强制校验 `[编号]` 引用并追加来源目录。
- `deep`：周报/事件深度报道，按标题聚类选条目数 `>= min_cluster_size`（默认 3）的前 `max_reports`（默认 3）个事件分别成稿，选中条目一并标记已消费。
- `summarize`：邮件简报，每 10 封抽结构化待办，输出「需要处理」「仅供参考」「未能解析」三栏。
- `none`：只标记已消费，不调用模型也不写报告文件。
- 生成类处理器均支持 `language`（默认 `zh-CN`）、`instructions` 与 `prompt_file`（`[system]` 纯文本 + `[user]` 沙箱 Jinja2，可用变量 `persona` / `items` / `groups` / `tags` / `now` / `pipeline`）。

---

## Web 服务、安全边界与自动调度（`server` / `schedule`）

```yaml
server:
  host: 127.0.0.1
  port: 8000
  token: ${TENTACLES_SERVER_TOKEN:-}
  cors_origins: []           # 仅允许显式列出的无尾斜杠 origin
  scheduler: true
  poll_interval: 30          # 内置调度轮询秒数
  static_dir: ../frontend/dist
```

- **安全边界**：`server.token` 非空时（无空白可打印 ASCII），除 `/api/health` 与静态页面外全部 `/api/*` 须带 `Authorization: Bearer <TOKEN>`；未配置 Token 时服务端严格校验 `Host` 与 `Origin` / `Sec-Fetch-Site` 阻断跨站与 DNS Rebinding；公网或局域网访问**必须设置高强度 Token 并前置 HTTPS 反向代理**。访问日志默认关闭，收到退出信号时等待当前 `tick` 收尾后优雅关闭。
- **调度规则（`schedule`）**：支持间隔（`30s`、`30m`、`2h`、`1d`、`1w`）、`@hourly/@daily/@weekly` 及五段 Cron（按 `app.timezone` 计算）。间隔任务首次立即到期，Cron 等待下一个触发点，进度与 1 小时原子租约锁持久化在 SQLite 中。每轮 `tick` 严格按 **① 并发抓源（上限 4） → ② 过滤（`filter_schedule`） → ③ Pipeline 成稿** 执行，上游源 `busy` 时下游自动延期。

### macOS `launchd` 无常驻调度示例

保存为 `~/Library/LaunchAgents/local.tentacles.tick.plist`（替换为实际绝对路径，并确保 `data/` 目录存在）：

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

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.tentacles.tick.plist
# 卸载：launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/local.tentacles.tick.plist
```

---

## 开发与校验

```bash
mypy src/tentacles                        # 后端类型检查
tentacles config check                    # 配置文件无副作用校验
cd frontend && pnpm check:api && pnpm build  # 校验前后端 OpenAPI 类型一致性并构建
./scripts/build-wheel.sh                  # 构建内置前端与配置模板的单文件 Wheel（推送 v* tag 自动发 Release）
```
