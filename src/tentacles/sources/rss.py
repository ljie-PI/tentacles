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
from tentacles.models import Item, ItemObservation
from tentacles.sources.base import FetchContext, FetchResult, Source, register
from tentacles.text import html_to_text, truncate


class RssOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    max_age_days: int | None = Field(default=None, ge=0)
    headers: dict[str, str] = Field(default_factory=dict)
    proxy: str | None = None
    timeout: float = 30

    @field_validator("proxy")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        return v or None  # ${HTTP_PROXY:-} 未设置时插值为空串

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


def entry_to_item(entry, source_id: str, *, allow_partial: bool = False) -> Item | None:
    link = (entry.get("link") or "").strip()
    title = (entry.get("title") or "").strip()
    # Opted-in stable identities may carry updates without a usable new-item
    # title. Store resolves them under its write lock and rejects unknown ones.
    partial = allow_partial and bool((entry.get("id") or "").strip())
    if (not title and not partial) or not (link or entry.get("id")):
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
        content=html_to_text(content or summary),
        published=_entry_published(entry),
    )


def entry_observation(entry, item: Item, *, feed_version: str = "") -> ItemObservation:
    """Presence comes from the parsed entry, never Item's default empty strings."""
    fields: dict[str, Any] = {"stable_external_id": bool((entry.get("id") or "").strip())}
    for name, key in (("title", "title"), ("author", "author")):
        if key in entry:
            fields[name] = getattr(item, name) if entry[key] is not None else None
    # feedparser can synthesize entry.link from Atom id / RSS guid. Only
    # actual alternate links are URL observations (including explicit empty).
    alternates = [link for link in entry.get("links", []) or []
                  if link.get("rel", "alternate") == "alternate"]
    if alternates:
        alternate = next((link for link in alternates if link.get("href", "") == entry.get("link")), alternates[0])
        # RSS <link/> has an alternate record but no href. No alternate record
        # means missing; a record with no href is an explicitly empty URL.
        fields["url"] = alternate.get("href", "")
    # Atom summaries are excerpts, whereas RSS descriptions supply the full
    # description-backed body when no explicit content field was delivered.
    summary_key = next((key for key in ("summary", "description") if key in entry), None)
    if isinstance(entry, feedparser.FeedParserDict) and "summary_detail" not in entry:
        summary_key = None  # feedparser also synthesizes summary from content
    if summary_key is not None:
        summary = entry[summary_key]
        fields["summary"] = None if summary is None else truncate(html_to_text(summary), 2000)
        if feed_version.startswith("rss") and "content" not in entry:
            fields["content"] = None if summary is None else html_to_text(summary)
    if "content" in entry:
        body = entry["content"]
        value = body[0].get("value") if body else None
        fields["content"] = None if value is None else html_to_text(value)
    if any(key in entry for key in ("published", "updated", "published_parsed", "updated_parsed")):
        # Malformed nonempty dates are not successful observations.
        if item.published is not None or not (entry.get("published") or entry.get("updated")):
            fields["published"] = item.published
    return ItemObservation(**fields)


@register
class RssSource(Source):
    type: ClassVar[str] = "rss"
    Options: ClassVar[Type[BaseModel]] = RssOptions
    supports_updates: ClassVar[bool] = True

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
            raise UpstreamError(f"RSS request failed ({type(exc).__name__})") from None
        if resp.status_code == 304:
            return FetchResult([], cursor, {"not_modified": True})
        if not resp.is_success:
            raise UpstreamError(f"RSS request returned HTTP {resp.status_code}")

        # content-location 让相对链接按 feed 地址补全；content-type 让只在 HTTP 头里声明的字符集（如 gbk）生效
        response_headers = {"content-location": str(resp.url)}
        if resp.headers.get("content-type"):
            response_headers["content-type"] = resp.headers["content-type"]
        parsed = feedparser.parse(resp.content, response_headers=response_headers)
        # bozo 也会因内容类型不规范等无害原因置位；只有根本没识别出 feed 才算失败
        if not parsed.get("version") and not parsed.entries:
            reason = parsed.get("bozo_exception")
            category = type(reason).__name__ if reason is not None else "unrecognized format"
            raise UpstreamError(f"cannot parse feed ({category})")

        cutoff = None if opts.max_age_days is None else ctx.now - timedelta(days=opts.max_age_days)
        items, too_old = [], 0
        observations: list[ItemObservation] = []
        for entry in parsed.entries:
            item = entry_to_item(entry, self.spec.id, allow_partial=self.spec.update_existing)
            if item is None:
                continue
            if cutoff and item.published and item.published < cutoff:
                too_old += 1
                continue
            items.append(item)
            if self.spec.update_existing:
                observations.append(entry_observation(entry, item, feed_version=parsed.get("version", "")))

        new_cursor = {"url": opts.url, **{k: v for k, v in (
            ("etag", resp.headers.get("etag")), ("last_modified", resp.headers.get("last-modified"))) if v}}
        return FetchResult(items, new_cursor, {"entries": len(parsed.entries), "too_old": too_old}, observations)
