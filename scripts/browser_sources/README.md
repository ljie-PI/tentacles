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

## 静态门禁

脚本在 `exec` 之前先经 `ast.parse` 静态扫描，规则只有一条不变量：**脚本里每个可读的名字都必须是脚本自己绑定的（参数、赋值、循环/推导目标），或者是门禁列出的内建函数；导入只能拿到两个契约模型和一个页面类型，且导入名不得充当属性链的根。** 因此脚本里没有任何表达式能触到模块对象、`__builtins__` 或别的对象的内部。

- 导入只允许两种写法：`from <白名单模块> import <白名单名字>`，和 `import <白名单模块> as <别名>`；整条白名单只有
  `playwright.async_api` 的 `Page` 与 `tentacles.sources.browser_contract` 的
  `BrowserExtractOutput` / `RawBrowserItem`。没有任何纯数据模块：`re` / `json` / `math` /
  `datetime` / `urllib.parse` / `dataclasses` / `typing` / `__future__` 都不在其中（`typing`
  能执行注解字符串，其余现有脚本用不上）；真的需要时按评审加回具体名字。
- `import a.b`（会绑定顶层包 `a`）、相对导入、`import *`、`import os` / `sys` / `importlib` /
  `typing` 等一律拒绝；导入名不得覆盖内建名（`from x import y as compile` 会被拦）。
- 名字：每个 `ast.Name` 必须是脚本自己绑定的名字或上面列出的内建；`__builtins__`、`__import__`、
  `eval`、`exec`、`compile`、`open`、`globals`、`locals`、`vars`、`getattr`、`setattr`、`delattr`、
  `dir`、`type`、`object`、`super`、`input`、`breakpoint`、`help`、`memoryview` 以及任何 `__dunder__`
  一律拒绝（赋值、参数名、函数名同样拒绝）。
- 属性/下标：属性名不得以 `_` 开头（`__class__`、`__globals__`、`f_globals` 这些都在此列），
  不得调用 `launch` / `launch_persistent_context` / `executable_path` / `connect_over_cdp` /
  `install` / `open` / `read_text` / `import_module` / `system` / `popen` 这些起进程、连远端或读写文件的入口；
  属性链的根不得是导入名（`RawBrowserItem.model_rebuild`、`p.chromium` 都被拦，把导入名先赋值给
  局部变量也不行——导入名只能出现在注解里或作为直接调用目标）。
- 语句：只允许函数定义、赋值、`for`/`while`/`if`/`with`/`try`/`raise`/`assert` 与 `return` 等；
  `class`（`__init_subclass__` 钩子在定义时就执行）、`global`、`nonlocal`、`del` 与 `match` 一律拒绝。

错误信息会指出真实原因（dangerous import / dangerous name / dangerous attribute / dangerous statement）、
文件名与行号。被拦截的脚本不会执行，以 `script_assertion_failed` 记账，并让该源整次抓取直接失败（`on_detail_error`
不改变这一点）：坏脚本配置不能降级成每轮空跑。

属于**故意保留**的能力：`page.evaluate` 与页面跳转本身（脚本可以在浏览器里跑任意 JS、打开任意地址）、以及 f-string 与 `%` 格式化。它们都不产生主机侧代码执行。

另外拒绝：解释器帧与格式化属性（`gi_frame` / `gi_code` / `f_back` / `f_code` / `f_trace` / `format` / `format_map`）、
宿主路径参数（任何调用的 `path=` 关键字，阻断 `route.fulfill(path=…)` / `tracing.stop(path=…)` /
`storage_state(path=…)`）、`file:` 本地导航、`tracing` / `storage_state` / `new_browser_cdp_session`。
脚本仍是需要代码评审的仓库代码：浏览器自身代表它访问网络与登录态，模块级死循环会阻塞该源的工作线程。

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
