# 信息源配置与原位更新

每个信息源在 `sources:` 列表下声明，通用字段包括：
- `id`（必填，全局唯一）
- `type`（`rss` | `imap` | `web`）
- `schedule`（抓取频率，支持 `30s/30m/2h/1d/1w`、`@hourly/@daily/@weekly` 或 5 段 Cron）
- `name`（可选显示名称）
- `enabled`（默认 `true`；设为 `false` 后定时 `tick` 跳过，但手动显式指定 `tentacles sources fetch <id>` 仍可强制抓取）

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
    update_existing: false   # 可选原位更新（默认 false）
    concurrency: 4           # 详情抓取固定并发数（默认 4，上限 16）
    headers: {}
    cookies: {}
    proxy: ${HTTP_PROXY:-}
    timeout: 30
```

- **选择器属性语法**：普通选择器提取清洗后文本；`选择器@属性` 提取指定属性；`@href` 直接读取当前 `item` 节点自身属性（引号或方括号内的 `@` 不会被误判为属性分隔符）。
- **详情与去重**：启用 `fetch_content: true` 后，详情页依次尝试提取非空 `article` → `main` → `body`；若本轮任一详情请求失败，整轮回滚且不推进游标，方便下次重试。游标缓存最近 500 个已见 ID，数据库同时持久化观察身份，避免重启后重复抓详情。

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

- **防幻觉机制**：先剔除导航/脚本/页脚，提取“有界文本 + 页面真实存在的规范化链接集合”发给模型；模型返回的 JSON 中凡是不在真实页面链接集合内的编造链接一律丢弃（计入 `stats.dropped_links`）。
- **付费缓存**：页面有效文本、链接集合及模型配置未发生变化时，后续轮次直接命中持久游标缓存，**零模型调用**（单纯轮换 API Key 不会使缓存失效）。

---

## 4. 原位更新（`update_existing: true`）

默认所有源 `update_existing: false`（遇到已知条目直接跳过计入 `dup`）。**RSS / Atom** 与 **Web `list` / `llm`** 支持在源级开启 `update_existing: true`（IMAP 和 Web `page` 设为 `true` 会报配置错误）：

1. **只刷新本轮再次出现的本源标准条目**：系统绝不后台遍历历史旧链接，仅当源列表/Feed 本轮再次返回该条目时才检查更新。
2. **稳定身份定位**：RSS 先按同源稳定 `external_id`（含已记录的同源别名）匹配，未命中再回退到本轮实际观察到的规范化 URL 唯一定位；Web `list`/`llm` 按规范化 URL 定位本源条目。若身份与 URL 指向冲突，整批事务回滚报 `Conflict`。
3. **Web 详情与缓存配合**：
   - `fetch_content: true` 时，每轮会对列表里再次出现的本源条目重新抓取详情页并比对完整正文；
   - `fetch_content: false` 时，仅更新列表实际提供的标题/摘要/日期，绝不用短摘要覆盖已存的详情正文；
   - `mode: llm` 开启更新后会缓存已校验的条目描述，页面未变时零抽取模型费用，仅在 `fetch_content: true` 时重抓详情页。
4. **对下游过滤与报告的影响**：
   - 正文或核心字段发生实质变化后，条目保留原 `id` 与首次 `fetched_at`，设置 `updated_at`，递增修订号并**自动使旧评分失效**；
   - 该条目会按 `updated_at` 重新进入 Pipeline 时间窗口参与重评（**若开启了付费过滤器可能产生重评模型费用**）；
   - **消费账本保持不变**：已被某条 Pipeline 写入过报告的条目，即使更新重评后也不会在同一 Pipeline 中重复生成报告。
   - 抓取结果中满足 `stats.fetched = new + updated + dup`。
