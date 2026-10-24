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
from tentacles.models import Item, normalize_url
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
        external_id=(entry.get("id") or "").strip() or normalize_url(link),
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

        parsed = feedparser.parse(resp.content)
        if parsed.bozo and not parsed.entries:
            raise UpstreamError(f"cannot parse feed {opts.url}: {parsed.bozo_exception}")

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

        new_cursor = {k: v for k, v in (("etag", resp.headers.get("etag")),
                                        ("last_modified", resp.headers.get("last-modified"))) if v}
        return FetchResult(items, new_cursor, {"entries": len(parsed.entries), "too_old": too_old})
