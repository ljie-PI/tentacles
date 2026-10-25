# Web 信息源：list / page / llm

配置 `type: web`，先运行 `tentacles --json config check`，再手动 `sources fetch ID`。
检查每项 `status`；失败项返回 `status=error`，批量命令仍退出 0。用 `items list --source ID`
和 `items show ITEM_ID` 查看正文、差异及定位。示例两个源均默认关闭；确认 URL 和选择器后再启用。

## list：文章列表

```yaml
- id: web-list
  type: web
  mode: list
  url: https://example.com/news
  schedule: 1h
  enabled: false
  selectors:
    item: article
    title: h2
    link: a@href
    summary: .summary
    date: time@datetime
  fetch_content: true
  concurrency: 4
```

`item`、`title`、`link` 必填；`summary`、`date` 可省略。选择器相对于每个 item：

- 普通选择器取清洗后的文本；`选择器@属性` 取属性；`@href` / `@data-title` 直接取 item 自身属性。
  link 没有显式属性时默认取 href。属性名遵循 HTML 的不区分大小写规则。
- CSS 中引号／属性值里的 `@` 不作为后缀分隔，例如 `a[data-owner="team@example.com"]`。
- 相对链接按有效 HTML 基址补全（首个可用 base href，相对 base 再按响应 URL 解析；缺省为响应 URL）；
  仅接受无 URL 凭据的 http(s)，规范化后作为 external_id。
  缺标题、缺链接、非法链接直接跳过，同次出现的相同链接只处理一次。
- 日期用来源的 app 时区及注入时钟模糊解析，支持带标签和相对时间，统一存 UTC；无法解析或过长则为空，不丢弃条目。
- `fetch_content` 默认 false。启用后，新链接依次尝试非空 article、main、body，最后回退文档文本；
  去除导航、header/footer、script/style 等无关内容，HTTP 头或 HTML meta 中声明的字符集都会保留。
  并发固定 worker 默认 4，上限 16；任一详情失败，本次整体失败，旧游标不推进。
- 只交付新链接。游标缓存最近 500 个 id（`stats.seen_cached`）；数据库另记观察身份，
  即使条目被文章判重丢弃、缓存淘汰或进程重启，也不重复抓详情。已存其他来源的标准文章 URL 也不再抓取。
  原已见链接不会因之后开启 fetch_content 自动补正文；监控同一 URL 的内容更新请使用 page。

## page：内容版本

```yaml
- id: web-page
  type: web
  mode: page
  url: https://example.com/status
  selector: main             # 可省略，监控整个文档文本；多个匹配区域合并
  emit_baseline: true        # 默认 true；false 时只建游标，后续变化才产出
  schedule: 30m
  enabled: false
```

以清洗后的区域文本做完整内容哈希，保持不变时不产出。变化版本保留全文，标题含页面标题与 app 时区日期，
无 HTML 标题时回退来源名／主机名。summary 为截断到 2000 字符的增删行差异，保留换行。
指定区域不存在时返回 upstream，保留上次快照，避免将登录页／布局变化误记为整页清空。

页面版本保留原 URL，external_id 为规范化 URL + `#` + 内容哈希。它跳过文章级 URL、指纹和近似标题判重；
普通文章和邮件规则保持不变，页面版本也不会污染文章的判重候选。source + external_id 仍稳定：
恢复已保存内容会复用既有版本，不生成同身份重复行。`stats.changed`、前后哈希和本次 diff 会记录恢复事件；
已有版本的消费账本不重置。浏览历史版本请使用条目 id，而不是仅访问当前页面 URL。

## llm：从杂乱页面抽取

```yaml
- id: web-llm
  type: web
  mode: llm
  url: https://example.com/updates
  schedule: 2h
  enabled: false
  # model: fast            # 显式覆盖优先，否则 defaults.models.extract
  max_input_chars: 20000
  fetch_content: false    # 可选：对新抽取链接继续抓正文
```

此模式会将页面内容发送给配置的 extract 模型，可能产生费用；确认隐私与费用后再启用。
启动时检查引用必须为已有 chat 模型，仍允许 key 为空，仅在真正调用时检查凭据。

先去除 script/style、导航、页眉页脚、表单等无关元素，转换为文本及规范化绝对链接，再把 text
截断到 `max_input_chars`（默认 20000，1–200000）。请求另含当前时间与 app 时区；页面内容作为数据，
不作为模型指令。页面会话、headers/cookies/proxy 不会转交模型 provider，使用独立模型 HTTP 客户端。

严格输出协议如下；summary 可以为空，published 缺失或未知为 null，无法解析的日期保存为空：

```json
{"items":[{"title":"标题","url":"页面中存在的链接","summary":"摘要","published":null}]}
```

页面转换与模型结果校验共用 HTML 有效基址；相对／带追踪参数的结果也会规范化。
链接不在清洗后实际页面链接集合中则丢弃，并记 dropped_links。
未知链接在校验其余字段前丢弃，不让无关幻觉行拖垮正确结果。同一链接本批或历史只交付一次，
标题／摘要按已配置模型密钥脱敏，保留原始 URL 身份。仍可使用详情抓取及 500-id／持久观察记录规则。

与宽容的摘要处理器不同，此来源遵照严格 JSON 协议：围栏、解释文字、尾逗号、非法 schema、
超过 100 万字符的响应或模型调用失败都会令**本来源本次抓取失败**，旧游标保留，可重试。
不发布局部结果，不回显模型响应正文。已返回的 usage/cost 保留在失败／取消运行中；
HTTP200 但正文无效时也保留已知计费。迟到的旧租约结果只更新自己的费用，不覆盖新游标。

模型输入文本哈希和模型定义缓存避免无效重复调用：输入不变、无有效链接或所有链接已知时跳过付费请求。
更新模型定义／源覆盖／输入上限会重新判断；API key 轮换不会使相同输入重新计费。
fetch stats 及 runs.stats 的 usage、cost、models 记录实际返回信息，runs.cost 保存本次已知费用。
无模型调用时 fetch 输出不附加计费字段，Run 则统一记录零 usage、null cost 和空 models，不估算费用；
不要把同一 fetch 的 stats.cost 与 run.cost 再累加一次。

## HTTP 与运行边界

每源可设 `headers`、`cookies`（字典）、`timeout`（默认 30 秒）和 `proxy`；可用 `${VAR}` 注入敏感值。
会话按源隔离。自定义 headers/cookies 只发往配置 URL 的同源请求；跨域详情移除 Cookie，使用默认请求头，
避免将凭据转交外域。GET 响应必须是 2xx，**不跟随重定向**；遇到 3xx 请配置最终 URL。
错误不回显请求头、Cookie、代理凭据、URL 查询密钥或响应正文。失败／取消等待已启动的 IO 清理并保留旧游标；
若取消到达时数据库 IO 已在提交，已提交条目／观察身份会保留，重试按稳定身份判重。其他来源仍可运行。

只处理静态 HTTP 内容，不执行 JS。配置与选择器来自可信本地设置；详情 URL 由页面提供，启用前确认来源可信。
新存储版本添加标准／revision 身份及观察记录，旧数据按标准文章规则迁移，已有 persona／报告／消费记录保持不变。
