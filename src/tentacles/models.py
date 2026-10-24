"""Core data models and normalization helpers."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import BaseModel, Field, field_validator

# 常见追踪参数，抓取内容时无意义
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "spm", "from", "ref", "ref_src", "share_token", "share_id",
    "scene", "nsukey", "fbclid", "gclid", "igshid",
}


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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Article(BaseModel):
    """一条从 feed 抓到的原始文章。"""

    id: str = ""
    url: str = ""
    source: str = ""
    title: str = ""
    summary: str = ""
    published: datetime | None = None
    fetched_at: datetime = Field(default_factory=utcnow)
    fingerprint: str = ""
    cluster_id: str | None = None

    @field_validator("title", "summary")
    @classmethod
    def _clean(cls, v: str) -> str:
        return re.sub(r"\s+", " ", v).strip()

    def model_post_init(self, __context) -> None:
        self.url = normalize_url(self.url)
        self.fingerprint = content_fingerprint(self.title, self.summary)
        if not self.id:
            self.id = self.fingerprint[:16]
