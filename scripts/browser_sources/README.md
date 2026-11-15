# 固化 Playwright 脚本（`scripts/browser_sources/`）

`type: browser` 信息源不调用模型，只**确定性重放**这里的脚本。脚本由仓库托管、随代码评审，
默认路径为 `<browser.scripts_dir>/<source_id>.py`（也可用源级 `script_path` 显式指定，
但必须落在 `browser.scripts_dir` 目录内并返回配置错误之外的结果）。

## 唯一契约

```python
from playwright.async_api import Page
from tentacles.sources.browser_contract import BrowserExtractOutput, RawBrowserItem


async def extract(page: Page, *, page_url: str, max_items: int,
                  timeout_ms: int = 30_000) -> BrowserExtractOutput:
    await page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_ms)
    ...
    return BrowserExtractOutput(page_url=page_url, items=[RawBrowserItem(...)])
```

- `page` 是运行时为本次重放**新建的隔离工作 Tab**，已经携带你在目标 `chrome_profile`
  中的登录态（Cookie / 会话），并且已单独关闭 HTTP 缓存（总是读取实时页面）。运行时在成功、异常、超时与取消时都会关掉这个 Tab，
  也绝不关闭你的 Chrome 主窗口；脚本**不要**自己 `page.close()`。
- 脚本如需额外 Tab（例如逐条打开详情），必须自己关闭：
  `extra = await page.context.new_page()` … `await extra.close()`。
- 运行时用 `page_url` 的源级 `timeout`（秒）整体限时；`timeout_ms` 传给脚本作为单步
  `goto`/`wait_for_selector` 的上限。超时会记为 `playwright_selector_timeout`。
- 返回 `0` 条、抛出异常或返回值不合契约时记为 `script_assertion_failed`，
  `debug_context` 中带失败瞬间的 `current_url`、`page_title`、精简 A11y 快照与栈帧。
- `on_detail_error: fail_batch` 时该源整批失败；`record_and_skip`（默认）只记账并返回空批次。

## 静态导入白名单门禁

脚本在 `exec` 之前先经 `ast.parse` 静态扫描，只允许：

- 导入 `__future__`、`re`、`json`、`math`、`datetime`、`urllib.parse`、`typing`、`dataclasses`、
  `playwright.async_api`、`tentacles.sources.browser_contract`；
- 不含 `subprocess`、`os`、`socket`、`shutil` 等任何其他模块（相对导入同样拦截）；
- 不调用 `__import__`、`eval`、`exec`、`compile`、`open`、`getattr`、`setattr`、`input`、
  `breakpoint`、`globals`/`locals`/`vars`、`exit`/`quit` 等（含 `obj.open(...)` 形式的同名方法调用）；
- 不访问 `__dunder__` 属性。

被拦截的脚本不会执行，直接以 `script_assertion_failed` 记账。

## 输出字段

| 字段 | 说明 |
| :--- | :--- |
| `title` | 必填，条目标题（≤ 1000 字） |
| `url` | 条目**原文/帖子详情**链接，可写相对路径（按 `page_url` 解析）；非 `http(s)` 会被丢弃 |
| `author` | 作者（可选） |
| `summary` | 摘要（可选，入库截断到 300 units） |
| `content` | 正文（可选，入库截断到 1000 units；缺省时用 `summary`） |
| `published` | **ISO 8601** 发布时间（如 `2026-10-26T03:10:00Z`）；相对时间请在脚本里换算成绝对时间 |
| `comments_url` | 讨论页链接（可选，写入 `meta.comments_url`） |

`url` 与 `page_url` 至少有一个合法 `http(s)` 地址：只给出 `url` 缺失的条目时，条目仍以
入口页 `page_url` 作为原文可达地址入库。

## 示例

- `reddit-localllama.py`：`config/tentacles.yaml` 中默认关闭的 `reddit-localllama` 示例，
  通过 CDP 附着已登录的 Chrome 后抓取 r/LocalLLaMA 的 New 列表。
- 本地验证：先手动启动带调试端口的 Chrome
  `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222 --profile-directory="Default"`，
  再 `tentacles sources fetch reddit-localllama`。
