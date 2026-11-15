# 信息源配置与原位更新

每个信息源在 `sources:` 列表下声明，通用字段包括：
- `id`（必填，全局唯一）
- `type`（`rss` | `imap` | `web` | `browser`）
- `schedule`（抓取频率，支持 `30s/30m/2h/1d/1w`、`@hourly/@daily/@weekly` 或 5 段 Cron）
- `name`（可选显示名称）
- `enabled`（默认 `true`；设为 `false` 后定时 `tick` 跳过，但手动显式指定 `tentacles sources fetch <id>` 仍可强制抓取）

---

## 0. 入库统一规则（所有信息源）

- **正文截断**：`content` 在入库时统一截断到 **1000 units**（1 个 CJK 字符 = 1 unit，1 个连续非 CJK 字母/数字词元 = 1 unit，标点与空白不计入）；超过时 `content_truncated` 为 `true`。
- **摘要上限**：`summary` 不超过 **300 units**（网页 `mode: page` 的版本 diff 保留换行）。
- **原文可达性**：非邮件条目（`rss` / `web` / `browser`）必须保留合法的 `http(s)` 单条原文 `url` 或入口 `page_url`；两者都缺失的条目按无效条目跳过（计入运行统计 `invalid_url`，不入库）。
- **入口页与原文**：RSS 的 `page_url` 是 Feed 地址，Web 是列表或监控页地址；聚合外链帖（如 Hacker News）的讨论页写入 `meta.comments_url`，`url` 仍保留外链原文，需要完整原文时顺着链接回访即可。

---

## 1. RSS / Atom 订阅（`type: rss`）

```yaml
sources:
  - id: hacker-news
    type: rss
    name: Hacker News
    url: https://hnrss.org/frontpage
    schedule: 30m
    max_age_days: 2          # 可选：丢弃发布时间早于 N 天的旧条目
    update_existing: false   # 可选：是否允许同源条目内容原位更新（默认 false）
    headers: {}              # 可选额外请求头
    proxy: ${HTTP_PROXY:-}   # 可选单源代理
    timeout: 30              # 请求超时秒数（默认 30）
```

- 自动处理条件 GET（`ETag` / `Last-Modified`，返回 `304 Not Modified` 时零解析开销）。
- 新增入库时执行多级去重：同源同 `external_id`、规范化 URL（剥离 `utm_*`、`spm`、`fbclid` 等追踪参数与 `#fragment`）、标题+摘要哈希指纹，以及 48 小时内跨源近似标题去重。
- 每条条目保留入口 `page_url`（即该 Feed 地址）；条目带 `<comments>` 等讨论链接且与本条 `url` 不同时，将讨论页写入 `meta.comments_url`。
- **逐条失败现场**：Feed 请求失败记 `http_status_error` / `network_timeout` / `connection_failed`，无法识别的 Feed 记 `feed_parse_error`（均附响应头与前 500 字脱敏预览）；因既无标题也无 `guid`/`link` 而无法定位的条目记 `entry_unusable`（`severity="warning"`，附原始 `guid`/`link`），计入 `stats.unusable`，不影响其余条目入库。

---

## 2. IMAP 邮箱（`type: imap`）

示例配置中的 `inbox` 默认 `enabled: false`。在 `.env` 中填好 `IMAP_HOST`、`IMAP_USERNAME`、`IMAP_PASSWORD` 后即可手动验证：

```yaml
  - id: inbox
    type: imap
    name: 邮箱
    enabled: false
    schedule: 30m
    host: ${IMAP_HOST:-imap.gmail.com}
    port: 993
    ssl: true
    username: ${IMAP_USERNAME:-}
    password: ${IMAP_PASSWORD:-}
    folders: [INBOX]
    initial_days: 30        # 首次或 UIDVALIDITY 变化时回溯最近 N 天
    max_fetch: 100          # 单轮所有文件夹合计最多处理的 UID 数（被排除或已删邮件也计入）
    search: ALL             # 附加 IMAP 条件，如 UNSEEN 或 FROM "newsletter@example.com"
    exclude_senders: []     # 按完整邮箱地址（不区分大小写）精确排除发件人
    max_body_chars: 20000
    max_summary_chars: 2000
    timeout: 30
```

### 邮箱服务商认证要求
- **Gmail**：需开启两步验证并生成**应用专用密码（App Password）**填入 `IMAP_PASSWORD`，请勿填写日常登录密码。
- **Outlook.com / Hotmail**：微软已关闭 IMAP Basic Auth 并强制要求 OAuth2，当前仅实现 `IMAP LOGIN`，暂不支持个人微软账户；其他支持标准密码或应用专用密码的企业/自建 IMAP 均可正常使用。
- **安全警告**：`ssl` 默认 `true` 并严格校验证书与主机名；`ssl: false` 不会升级 STARTTLS，凭据可能明文传输，仅限可信本地桥接（如 Proton Mail Bridge / DavMail）使用。

### 只读与解析行为
- 文件夹始终以**只读模式**打开，使用 `BODY.PEEK[]` 读取：**绝不标记已读、不移动、不删除、不 expunge**。
- 每个文件夹独立维护 `UIDVALIDITY + 最后处理 UID` 游标；按 UID 升序拉取。
- 优先取 `text/plain`，无纯文本时将 HTML 转为文本；自动清理常见 `>` 回复引用头和 HTML quote 容器；不提取附件正文，仅记录附件文件名列表。邮件不做内容指纹或近似标题判重（相同内容但 `Message-ID` 不同的邮件均会保留）。
- **单封坏信不会拖垮整个邮箱**：登录/连接失败记 `imap_auth_failed`（**不回显密码与服务器 banner**）；登录后在拉取过程中连接中断记 `connection_failed`（可重试），其余未被分类的异常记 `unexpected_exception`（不可重试）；某封邮件 MIME 解析或 UID 校验异常时记 `imap_message_parse_error`，`external_id` 为 `folder:UIDVALIDITY:UID`，该封被跳过并计入 `stats.unparsed`，游标仍向前推进，因此坏信不会毎轮堵住整个文件夹。

---

## 3. Web 网页信息源（`type: web`）

针对没有提供 RSS 的静态网页，提供三种工作模式（只处理 HTTP `2xx` 静态响应，不执行前端 JS，不自动跟随 `3xx` 重定向；请填写最终 URL）。每个源拥有隔离的 HTTP 会话，自定义 `headers` 和 `cookies` 仅发往同源地址，跨域抓详情页时自动剥离凭据。

### 模式 A：`mode: list`（CSS 选择器文章列表）

```yaml
  - id: web-list
    type: web
    mode: list
    enabled: false
    schedule: 1h
    url: https://example.com/news
    selectors:
      item: article          # 必填：每条内容的容器选择器
      title: h2              # 必填：相对 item 的标题选择器
      link: a@href           # 必填：相对 item 的链接（缺 @属性 时默认取 href）
      summary: .summary      # 可选：列表摘要
      date: time@datetime    # 可选：时间文本或属性（按 app.timezone 解析为 UTC）
    fetch_content: true      # 是否并发请求详情页提取正文（默认 false）
    on_detail_error: fail_batch   # fail_batch（默认）| record_and_skip，见下
    update_existing: false   # 可选原位更新（默认 false）
    concurrency: 4           # 详情抓取固定并发数（默认 4，上限 16）
    headers: {}
    cookies: {}
    proxy: ${HTTP_PROXY:-}
    timeout: 30
```

- **选择器属性语法**：普通选择器提取清洗后文本；`选择器@属性` 提取指定属性；`@href` 直接读取当前 `item` 节点自身属性（引号或方括号内的 `@` 不会被误判为属性分隔符）。
- **详情与去重**：启用 `fetch_content: true` 后，详情页依次尝试提取非空 `article` → `main` → `body`；若本轮任一详情请求失败，按 `on_detail_error` 决定整批回滚（见下）。游标缓存最近 500 个已见 ID，数据库同时持久化观察身份，避免重启后重复抓详情。
- **`on_detail_error`（详情页局部容错）**：
  - `fail_batch`（默认，兼容旧行为）：任一详情页请求失败或正文提取为空（与 `update_existing` 无关，两种模式一致）整批业务数据回滚、游标不前进；但**回滚前会先把本轮所有失败详情逐条写入故障账本**（`errors list --unresolved` 可查，含具体 `target_url`、列表已知标题、HTTP 状态、`html_title`、`body_preview`）。
  - `record_and_skip`：失败的单篇详情只写入故障账本（`severity="error"`，`retryable` 按状态码判定），**本轮其余成功抓到的文章正常入库**。关键不变量：被跳过的 `target_url` **不写入游标 `seen` 缓存，也不写入 `source_seen_ids`**，所以下一轮它仍会被判定为 `new` 自动重试；重试成功入库后，账本里对应的未决记录自动回填 `resolved_at`（自动消警）。
  - 运行统计里 `details_fetched` 是本轮详情总数，`details_failed` / 跳过导致的 `skipped_nodes` 只在有失败或选择器异常时出现。

### 模式 B：`mode: page`（单页内容版本监控）

```yaml
  - id: web-page
    type: web
    mode: page
    enabled: false
    schedule: 30m
    url: https://example.com/status
    selector: main           # 可省略（省略则监控整页文本）；匹配不到时报错保留旧快照，防误判为空页
    emit_baseline: true      # 默认 true（首次抓取即生成基线条目）；false 则仅建快照，后续变化才生成条目
```

- 对区域清洗文本计算完整哈希；内容变化时生成一条 `identity_policy="revision"` 的新版本条目，`summary` 为增删行 `unified_diff`（最多 2000 字符），`external_id` 为 `规范化URL#<内容哈希>`，不会被普通文章 URL 去重吞掉。
- 版本条目的 `page_url` 为监控页地址；`content` 同样受 1000 units 入库截断，完整快照保留在源游标中用于下次 diff。
- `selector` 匹配数为 0 时报错并保留旧快照（防误判为空页），同时把 `configured_selectors`、`html_title` 与页面 `main`/`body` 的脱敏标签骨架写入故障账本（`selector_no_match`）。

### 模式 C：`mode: llm`（杂乱页面模型结构化抽取）

```yaml
  - id: web-llm
    type: web
    mode: llm
    enabled: false
    schedule: 2h
    url: https://example.com/updates
    # model: fast            # 可选覆盖，默认使用 defaults.models.extract（必须是 chat 模型）
    max_input_chars: 20000   # 清洗后发给模型的文本上限（1~200000）
    fetch_content: false
    update_existing: false
```

- **防幻觉机制**：先剔除导航/脚本/页脚，提取“有界文本 + 页面真实存在的规范化链接集合”发给模型；模型返回的 JSON 中凡是不在真实页面链接集合内的编造链接一律丢弃（计入 `stats.dropped_links`，并在故障账本记 `hallucinated_link_dropped` 附具体丢弃的 URL）。
- **输出违约可见**：模型输出违反严格 JSON Schema 时不再只是“本轮降级”，而是记录 `invalid_json_schema` 与违约字段路径、800 字脱敏输出预览。
- **付费缓存**：页面有效文本、链接集合及模型配置未发生变化时，后续轮次直接命中持久游标缓存，**零模型调用**（单纯轮换 API Key 不会使缓存失效）。
- 抽取出的每条条目都保留入口 `page_url`（即该列表页地址）与自己的原文 `url`。

---

## 4. 原位更新（`update_existing: true`）

默认所有源 `update_existing: false`（遇到已知条目直接跳过计入 `dup`）。**RSS / Atom**、**Web `list` / `llm`** 与 **`type: browser`** 支持在源级开启 `update_existing: true`（IMAP 和 Web `page` 设为 `true` 会报配置错误）：

1. **只刷新本轮再次出现的本源标准条目**：系统绝不后台遍历历史旧链接，仅当源列表/Feed 本轮再次返回该条目时才检查更新。
2. **稳定身份定位**：RSS 先按同源稳定 `external_id`（含已记录的同源别名）匹配，未命中再回退到本轮实际观察到的规范化 URL 唯一定位；Web `list`/`llm` 按规范化 URL 定位本源条目。若身份与 URL 指向冲突，整批事务回滚报 `Conflict`。
3. **Web 详情与缓存配合**：
   - `fetch_content: true` 时，每轮会对列表里再次出现的本源条目重新抓取详情页并比对完整正文；
   - `fetch_content: false` 时，仅更新列表实际提供的标题/摘要/日期，绝不用短摘要覆盖已存的详情正文；
   - `mode: llm` 开启更新后会缓存已校验的条目描述，页面未变时零抽取模型费用，仅在 `fetch_content: true` 时重抓详情页。
   - `type: browser` 每轮重放的条目按规范化 URL 定位本源条目，正文/摘要/作者/发布时间取脚本本轮真实返回的值。
4. **对下游过滤与报告的影响**：
   - 正文或核心字段发生实质变化后，条目保留原 `id` 与首次 `fetched_at`，设置 `updated_at`，递增修订号并**自动使旧评分失效**；
   - 正文入库时已截断到 1000 units，为感知截断范围之外的实质更新，RSS 与 Web 详情在 `meta.full_content_sha1` 记录**截断前全文哈希**；哈希变化同样触发原位更新；
   - 该条目会按 `updated_at` 重新进入 Pipeline 时间窗口参与重评（**若开启了付费过滤器可能产生重评模型费用**）；
   - **消费账本保持不变**：已被某条 Pipeline 写入过报告的条目，即使更新重评后也不会在同一 Pipeline 中重复生成报告。
   - 抓取结果中满足 `stats.fetched = new + updated + dup`。

---

## 5. 有界面浏览器源（`type: browser`）

静态 `type: web` 抓不到的动态流、需登录态页面（如新版 Reddit `shreddit-post`、X/Twitter），用 `type: browser`：
它在**你已登录的可见 Chrome** 里新建一个隔离工作 Tab，确定性重放仓库内的固化 Playwright 脚本，
把文字条目转成 `kind: "browser"` 入库。无头模式被彻底禁止（见[基础配置手册](configuration-and-models.md)第 4 节）。

第一次抓取某个源时还没有固化脚本，运行时会走一次**冷启动**：受控 `pi --mode rpc` 探索 → 录制动作轨迹 →
从轨迹提炼脚本 → 静态门禁 + 真实试跑通过后才写入 `scripts/browser_sources/`。之后每次抓取都是零 Token 的重放。

### 前置：让 Chrome 打开调试端口

```bash
# macOS 示例：用日常 Profile 启动一个带调试端口的有界面 Chrome，然后正常登录目标站点
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 --profile-directory="Profile 1"
```

- 默认 `browser.cdp_url: http://127.0.0.1:9222` 会用 `connect_over_cdp` 附着这个实例，
  **完整复用该 Profile 的 Cookie 与登录态**；抓取期间只会新建并关闭自己的 Tab。
- 若该端口无人监听：`auto_launch: false`（默认）直接报错并打印上面这条命令；
  `auto_launch: true` 则在 `chrome_user_data_dir` 指定的 User Data 目录下以**有界面**模式拉起目标 Profile，
  抓完只关闭这次拉起的进程。
- 附着后会读取 `chrome://version` 的真实命令行核验 Profile：`chrome_profile` 与实际 `--profile-directory` 不一致、
  或该实例带 `--headless`，都会立即报 `ConfigError` 并说明如何修正。

### 源配置

```yaml
sources:
  - id: reddit-localllama
    type: browser
    name: r/LocalLLaMA 最新讨论
    enabled: false
    schedule: 1h
    page_url: https://www.reddit.com/r/LocalLLaMA/new/
    task: >
      打开 r/LocalLLaMA 的 New 列表页，抓取前 20 条帖子的标题、帖子详情链接、作者、
      发布时间和正文摘要，跳过置顶广告（promoted）。
    chrome_profile: Profile 1             # 可选：覆盖 browser.default_profile
    update_existing: false                # 可选：同源规范化 URL 原位更新
    on_detail_error: record_and_skip      # fail_batch | record_and_skip
    script_path: ../scripts/browser_sources/reddit-localllama.py  # 可省略，缺省 <scripts_dir>/<id>.py
    max_items: 25
    timeout: 60                           # 单次重放整体超时秒数
    pi:                                   # 可选：源级覆盖 browser.pi 的探索模型
      model: anthropic/claude-opus-4-7
      reasoning_level: high
```

| 字段 | 含义 |
| :--- | :--- |
| `page_url` | 入口页 `http(s)` 地址，必填；成为该源条目的 `page_url` |
| `task` | 人类可读的采集任务描述：冷启动时交给 `pi` 探索，已有脚本时不影响重放 |
| `chrome_profile` | 源级 Chrome Profile 目录名（如 `Profile 1`），缺省用 `browser.default_profile` |
| `script_path` | 固化脚本路径，相对 YAML 解析；**必须**落在 `browser.scripts_dir` 内且以 `.py` 结尾 |
| `update_existing` | 开启后本轮再次出现的本源条目按规范化 URL 原位更新（与 Web `list` 一致） |
| `on_detail_error` | `record_and_skip`（默认）只记账并返回空批次；`fail_batch` 让该源整批失败 |
| `max_items` | 单轮最多入库条目数，`1..200`，默认 `25` |
| `timeout` | 单次重放的整体超时秒数（默认 `60`），同时作为脚本单步 `goto`/`wait_for_selector` 的上限 |
| `headless` | 只允许 `false` 或缺省；`true` 直接报配置错误 |

### 抓取行为

- **串行焦点保护**：同一 `(cdp_url, chrome_profile)` 同时只有一个重放在跑，避免多个源抢你的窗口焦点。
- **工作 Tab 必回收**：成功、脚本异常、超时、Ctrl-C 取消都以 `finally` 关闭本次新建的 Tab；
  脚本自己开的额外 Tab 由脚本负责关闭（运行时不关闭你的其它 Tab，也绝不调用 `browser.close()`）。
- **总是读取实时页面**：工作 Tab 通过 CDP 单独关闭 HTTP 缓存（只作用于该 Tab，不改你的浏览器设置），
  避免重放命中 Chrome 缓存拿到旧内容导致漏更新。
- **去重与更新**：同一轮内按条目标题指纹 / 规范化 URL 去重；跨轮按本源已存规范化 URL 跳过（计入 `stats.skipped_known`），
  开启 `update_existing: true` 时改为原位更新（`stats.refresh_links`）。`stats.entries` 为脚本本轮返回的条目数，`stats.duplicates` 为同轮内重复、`stats.skipped_known` 为本源已存的条目数。
- **失败账本**：脚本抛错、超时、返回 0 条或未通过静态导入门禁时，会写入 `run_errors`（`phase: browser_replay`）：
  - `playwright_selector_timeout`：选择器/整体重放超时（页面结构已变，需要改脚本）；
  - `script_assertion_failed`：断言失败、返回值不合契约、脚本被门禁拦截或返回 0 条（0 条记为 `warning`）；
  - `debug_context` 带 `current_url`、`page_title`、精简 A11y 快照与脚本栈帧；`target_url` 指向失败瞬间的页面地址。
  排障命令见 [Agent CLI 运维速查](../agent-cli/operations-reference.md) 第 5 节。

### 冷启动：首次探索、轨迹录制与脚本提炼

当 `scripts/browser_sources/<id>.py` 还不存在（且未配 `script_path`）时，本轮不直接重放，而是：

1. **受控探索**：按 `pi.model` / `pi.reasoning_level` 拉起 `pi --mode rpc --no-session`，
   关闭所有内建工具、MCP、技能与扩展发现（`--no-builtin-tools --no-extensions --no-mcp --no-skills`），
   只加载随包发布的桥接扩展，因此 Agent 能用的工具只有 `browser_navigate` / `browser_snapshot` /
   `browser_click` / `browser_fill` / `browser_scroll` / `browser_evaluate` / `browser_submit_result`；
   这些工具通过 `browser.webbridge_url` 上的**一次性回环端点**（每次运行一个随机 Token）操作本轮工作 Tab，
   拿不到 Shell、文件系统或其它进程。
2. **轨迹录制**：每个动作都先捕获决策页面上下文（URL、标题、精简 A11y 快照、候选选择器），
   再记录动作与观察结果，逐行追加到 `data/trajectories/<id>/<run_id>.jsonl`（`data/` 已在 `.gitignore` 中，**不入 Git**）。
   已配置的密钥、URL 里敏感查询参数与 `Cookie` 等字段在写入前即被脱敏。
3. **提炼与双重门禁**：`pi` 在同一会话里把 `task` + 轨迹 + 探索样本条目提炼成固定 Playwright 脚本；
   脚本必须先通过与重放同一套 AST 静态门禁，再在一个**全新的真实工作 Tab** 上独立试跑（超时、报错、
   返回 0 条、条目缺 `http(s)` 链接或 `page_url` 不是入口页都算失败）。失败会把报错与当时的页面现场
   反馈给同一会话修正**最多 1 次**。
4. **落盘边界**：两门禁都通过才写入 `scripts/browser_sources/<id>.py`，并在本地
   `data/browser_sources/<id>.meta.json` 写下轨迹路径、脚本选用的选择器、试跑耗时与样本条数
   （**只写本地 `data/`，绝不入库、绝不含 Cookie / 账号名 / 正文**）；本轮条目正常入库，
   `stats.browser_mode = "distill"` 并带 `stats.trajectory_path`、`stats.script_path`。
   未通过则**不写脚本**，失败栈与 `trajectory_path` 记入 `run_errors`（`phase: browser_distill`），
   下一轮仍从冷启动重试（旧游标保持不变）。

**计费提醒**：冷启动的探索 + 提炼 + 修正回合都发真实模型请求，Token 与费用实时累加到当轮 `Run`
（`tentacles runs show <run_id>` 的 `stats.usage` / `stats.cost`，`tentacles runs list` 汇总可见）；
脚本落盘后每轮都是零 Token 重放。子进程继承当前进程的环境（`pi` 自己的服务商凭据仍按 `pi` 的配置/环境生效），
但扩展发现、MCP 服务器、技能与 prompt 模板均被关闭，只多拿到两个桥接变量。

### 固化脚本规范

脚本位于 `scripts/browser_sources/<source_id>.py`，唯一入口是
`async def extract(page, *, page_url, max_items, timeout_ms=30_000) -> BrowserExtractOutput`，
输出 `RawBrowserItem(title=..., url=..., author=..., content=..., summary=..., published=<ISO 8601>, comments_url=None)`。
脚本在 `exec` 之前会先经过 `ast.parse` 静态门禁：每个可读的名字必须是脚本自己绑定的或门禁列出的内建，导入只允许 `playwright.async_api.Page` 与 `tentacles.sources.browser_contract` 的两个契约模型（`os` / `subprocess` / `importlib` / `typing` / `__builtins__` / `eval` / `open` / 任何 `__dunder__` 一律拒绝），另拒绝解释器帧与格式化属性（`gi_frame` / `f_back` / `f_code` / `format` / `format_map`）、宿主路径参数（`fulfill(path=…)` / `tracing.stop(path=…)` / `storage_state(path=…)`）、`file:` 本地导航与原始 CDP 会话。

**信任边界（诚实说明）**：门禁保证脚本无法执行任意 Python、无法直接读写宿主文件，但 **Playwright 自身仍代表脚本访问网络与页面**，所以脚本依旧是需要代码评审的仓库代码（它可按登录态访问站点、发起请求）；模块级死循环或超大内存分配会阻塞该源的工作线程（`MAX_SCRIPT_BYTES` 只限制脚本源码大小）。
完整契约、字段表与示例见 [`scripts/browser_sources/README.md`](../../../scripts/browser_sources/README.md)。

```bash
# 首次验证：先手动启动带调试端口的 Chrome 并登录，再显式抓取（enabled: false 也会执行）
tentacles sources fetch reddit-localllama
# 冷启动：本轮会花 Token 探索并提炼脚本，之后脚本已存在则零 Token 重放
tentacles --json sources fetch reddit-localllama | jq '.[0].stats | {browser_mode, trajectory_path, script_path}'
tentacles items list --source reddit-localllama
```
