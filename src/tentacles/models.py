"""Core data model (Item) and normalization helpers."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from tentacles.timeutil import to_utc, utcnow

# 常见追踪参数，抓取内容时无意义
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "spm", "from", "ref", "ref_src", "share_token", "share_id",
    "scene", "nsukey", "fbclid", "gclid", "igshid",
}

ItemKind = Literal["rss", "email", "web"]
LinkDisposition = Literal["new", "refresh", "skip"]


def normalize_url(url: str) -> str:
    """去掉 fragment、追踪参数和多余斜杠，用于 URL 级去重。"""
    parsed = urlparse(url.strip())
    query = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    path = parsed.path or "/"
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), path,
                       "", urlencode(query), ""))


def content_fingerprint(title: str, summary: str = "") -> str:
    """标题 + 摘要的规范化指纹，近似内容的哈希去重。"""
    text = re.sub(r"\s+", " ", f"{title} {summary}".casefold()).strip()
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def item_id(source_id: str, external_id: str) -> str:
    return hashlib.sha1(f"{source_id}\0{external_id}".encode("utf-8")).hexdigest()[:16]


class Item(BaseModel):
    """一条从任意信息源抓到的内容（文章、邮件、网页变更）。"""

    id: str = ""
    source_id: str
    kind: ItemKind
    identity_policy: Literal["standard", "revision"] = "standard"
    external_id: str = ""
    url: str = ""
    title: str
    author: str = ""
    summary: str = ""
    content: str = ""
    published: datetime | None = None
    fetched_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime | None = None
    content_revision: int = Field(default=1, ge=1)
    fingerprint: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "summary", "author")
    @classmethod
    def _clean(cls, v: str, info: ValidationInfo) -> str:
        if info.field_name == "summary" and info.data.get("identity_policy") == "revision":
            return v.strip()
        return re.sub(r"\s+", " ", v).strip()

    @field_validator("published", "fetched_at", "updated_at")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return None if v is None else to_utc(v)

    def model_post_init(self, __context) -> None:
        if self.url:
            self.url = normalize_url(self.url)
        if not self.fingerprint:
            self.fingerprint = content_fingerprint(self.title, self.summary)
        if not self.external_id:
            self.external_id = self.url or self.fingerprint
        if not self.id:
            self.id = item_id(self.source_id, self.external_id)


class ItemObservation(BaseModel):
    """Successfully observed mutable fields, not a replacement Item.

    Unset fields preserve stored values; explicit None clears them (empty text
    for non-nullable Item strings). Identity is never inferred from these fields.
    Metadata is merged by key and does not itself revise downstream content.
    """

    model_config = ConfigDict(extra="forbid")

    stable_external_id: bool = False
    title: str | None = None
    url: str | None = None
    author: str | None = None
    summary: str | None = None
    content: str | None = None
    published: datetime | None = None
    meta: dict[str, Any] | None = None

    def apply(self, item: Item) -> Item:
        data = item.model_dump()
        changes = self.model_dump(exclude_unset=True, exclude={"stable_external_id"})
        for name, value in changes.items():
            if name == "meta":
                data[name] = {} if value is None else {**item.meta, **value}
            else:
                data[name] = value if name == "published" else value or ""
        data["fingerprint"] = ""
        return Item.model_validate(data)
