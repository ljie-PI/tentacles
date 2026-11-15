"""r/LocalLLaMA 最新讨论：`type: browser` 固化脚本示例。

契约与安全边界见同目录 README.md；本脚本只读取页面文字，不下载任何二进制内容。
脚本运行在运行时新建的隔离工作 Tab 中（已带目标 Chrome Profile 的登录态），
不要关闭该 Tab，也不要打开后不关自己的额外 Tab。
"""

from playwright.async_api import Page

from tentacles.sources.browser_contract import BrowserExtractOutput, RawBrowserItem

LIST_READY = "shreddit-post"
BODY_SLOT = '[slot="text-body"]'


async def extract(page: Page, *, page_url: str, max_items: int,
                  timeout_ms: int = 30_000) -> BrowserExtractOutput:
    await page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_ms)
    # 结构不匹配时这里超时，运行时记 playwright_selector_timeout 并保留 A11y 快照。
    await page.wait_for_selector(LIST_READY, timeout=timeout_ms)
    posts = page.locator(LIST_READY)
    items: list[RawBrowserItem] = []
    for index in range(min(await posts.count(), max_items)):
        post = posts.nth(index)
        title = (await post.get_attribute("post-title") or "").strip()
        # 广告位与骨架节点没有标题属性，直接跳过。
        if not title:
            continue
        body = ""
        excerpt = post.locator(BODY_SLOT)
        if await excerpt.count():
            body = (await excerpt.first.inner_text()).strip()
        items.append(RawBrowserItem(
            title=title,
            url=await post.get_attribute("permalink") or "",
            author=await post.get_attribute("author") or "",
            content=body,
            published=await post.get_attribute("created-timestamp"),
            comments_url=await post.get_attribute("permalink") or "",
        ))
    return BrowserExtractOutput(page_url=page.url, items=items)
