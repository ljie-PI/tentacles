# 网页源示例到底演示什么

`web-list`、`web-llm` 是任意取的 **source ID**，不是服务商、网站、信息主题或系统预置订阅。实际能力由 `type: web` 和 `mode` 决定。

它们演示：**当某个网站没有 RSS，但普通 HTTP 请求能拿到包含新闻的 HTML 时，如何把网页里的内容变成 Tentacles 的条目。**

```text
具体网站 URL
    ↓ HTTP GET
HTML
    ├─ mode: list → CSS 选择器提取每条新闻
    ├─ mode: llm  → 模型识别每条新闻
    └─ mode: page → 监控一段文本的变化
    ↓
条目入库 → pipeline 选材、筛选、生成报告
```

## `web-list`：列表页，用规则抽取

假设一个新闻网站的 `/news` 页面返回这种 HTML（以下内容仅用于演示，不是真实新闻）：

```html
<article>
  <h2><a href="/news/tool-release">工具项目发布新版本</a></h2>
  <p class="summary">新版本的功能说明。</p>
</article>
<article>
  <h2><a href="/news/security-fix">软件项目修复安全问题</a></h2>
  <p class="summary">修复内容的说明。</p>
</article>
```

配置中的规则：

```yaml
- id: some-site-news
  type: web
  mode: list
  schedule: 1h
  url: https://YOUR-SITE/news
  selectors:
    item: article
    title: h2
    link: a@href
    summary: .summary
```

含义是：每个 `article` 变成一条新闻，`h2` 是标题，`a` 的 `href` 是详情链接，`.summary` 是摘要。这个页面会得到两个条目。可另加 `fetch_content: true` 请求详情页正文。列表抽取本身不调用模型，适合结构稳定的网站。

## `web-llm`：同样是列表抽取，但让模型辨认结构

当页面的 HTML 很杂乱、不想维护 CSS 选择器时：

```yaml
- id: another-site-news
  type: web
  mode: llm
  schedule: 1h
  url: https://YOUR-SITE/updates
```

系统把页面中取得的文本和链接交给 `defaults.models.extract`，让模型识别新闻条目、标题、链接、摘要和日期，再入库。会消耗模型额度；它不是上网搜索工具，也不会任意爬网站或执行网页 JavaScript。

## `web-page`：监控变化，不是抽取新闻列表

例如监控产品的状态页或公告区域。系统比较两次文本快照，发生变化时生成差异条目；和“从页面中拆出多条新闻”的 `list` / `llm` 不同。

## 为什么是 `example.com`

示例不指定真实订阅，所以用占位域名。它不是值得关注的信息源，不能靠打开示例自动得到真实新闻。使用时需要换成你想关注的网站；`list` 还需适配该站的 CSS 结构。

## X、Reddit 和需要登录的网页

- Reddit 有可用 RSS 时，用 RSS；不需要套 `web-list` / `web-llm`。
- X 等需要 JavaScript 或登录态的页面，使用 `type: browser`：在已经登录的可见 Chrome 中抓取。
- 没有 RSS 并不自动意味着要用浏览器：关键是静态 HTTP 能不能取得你要的内容。

[`sources.yaml`](sources.yaml) 中所有条目都是独立源的配置片段，默认关闭且不自动加载。复制需要的条目到主配置 `sources`，替换占位项、开启，并把 ID 加入目标 `pipeline.sources`。
