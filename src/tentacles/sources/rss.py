"""RSS / Atom source with conditional GET."""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar, Type, cast

import feedparser
import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tentacles.errors import UpstreamError
from tentacles.models import Item
from tentacles.sources.base import FetchContext, FetchResult, Source, register
from tentacles.text import html_to_text, truncate


class RssOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    max_age_days: int | None = Field(default=None, ge=0)
    headers: dict[str, str] = Field(default_factory=dict)
    proxy: str | None = None
    timeout: float = 30

    @field_validator("url")
    @classmethod
    def _http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("must be an http(s) URL")
        return v


def _entry_published(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:  # feedparser 给出的是 UTC struct_time
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
    raw = entry.get("published")
    if raw:
        try:
            return parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            pass
    return None


def entry_to_item(entry, source_id: str) -> Item | None:
    link = (entry.get("link") or "").strip()
    title = (entry.get("title") or "").strip()
    if not link or not title:
        return None
    content = entry.content[0].value if entry.get("content") else ""
    summary = entry.get("summary") or entry.get("description") or content
    return Item(
        source_id=source_id,
        kind="rss",
        external_id=(entry.get("id") or "").strip(),  # 为空时 Item 用规范化后的 url
        url=link,
        title=title,
        author=entry.get("author") or "",
        summary=truncate(html_to_text(summary), 2000),
        content=html_to_text(content),
        published=_entry_published(entry),
    )


@register
class RssSource(Source):
    type: ClassVar[str] = "rss"
    Options: ClassVar[Type[BaseModel]] = RssOptions

    async def fetch(self, cursor: dict[str, Any], ctx: FetchContext) -> FetchResult:
        opts = cast(RssOptions, self.options)
        headers = dict(opts.headers)
        if cursor.get("url") != opts.url:  # 换了地址：旧的条件请求游标作废
            cursor = {}
        if cursor.get("etag"):
            headers["If-None-Match"] = cursor["etag"]
        if cursor.get("last_modified"):
            headers["If-Modified-Since"] = cursor["last_modified"]
        try:
            resp = await ctx.http.client(opts.proxy).get(opts.url, headers=headers, timeout=opts.timeout)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"GET {opts.url} failed: {exc}") from exc
        if resp.status_code == 304:
            return FetchResult([], cursor, {"not_modified": True})
        if not resp.is_success:
            raise UpstreamError(f"GET {opts.url} returned HTTP {resp.status_code}")

        # content-location 让相对链接按 feed 地址补全；content-type 让只在 HTTP 头里声明的字符集（如 gbk）生效
        response_headers = {"content-location": str(resp.url)}
        if resp.headers.get("content-type"):
            response_headers["content-type"] = resp.headers["content-type"]
        parsed = feedparser.parse(resp.content, response_headers=response_headers)
        # bozo 也会因内容类型不规范等无害原因置位；只有根本没识别出 feed 才算失败
        if not parsed.get("version") and not parsed.entries:
            raise UpstreamError(f"cannot parse feed {opts.url}: {parsed.get("bozo_exception", "not a feed")}")

        cutoff = None if opts.max_age_days is None else ctx.now - timedelta(days=opts.max_age_days)
        items, too_old = [], 0
        for entry in parsed.entries:
            item = entry_to_item(entry, self.spec.id)
            if item is None:
                continue
            if cutoff and item.published and item.published < cutoff:
                too_old += 1
                continue
            items.append(item)

        new_cursor = {"url": opts.url, **{k: v for k, v in (
            ("etag", resp.headers.get("etag")), ("last_modified", resp.headers.get("last-modified"))) if v}}
        return FetchResult(items, new_cursor, {"entries": len(parsed.entries), "too_old": too_old})
