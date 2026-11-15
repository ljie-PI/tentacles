"""Output contract for the fixed `type: browser` replay scripts.

A script imports this module for `BrowserExtractOutput` and `RawBrowserItem`,
so it must stay pure data: it holds no filesystem, process or import machinery,
because everything reachable from an allowlisted module is reachable from an
untrusted script. The static gate and the loader that own those capabilities
live in `browser_script`, which scripts cannot import.
"""

from __future__ import annotations

from typing import Annotated, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from tentacles.errors import ConfigError


class RawBrowserItem(BaseModel):
    """One entry a replay script extracted; `Item` truncation happens on ingest."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    url: str | None = Field(default=None, max_length=2000)
    author: str = Field(default="", max_length=200)
    summary: str = Field(default="", max_length=4000)
    content: str = Field(default="", max_length=200000)
    published: str | None = Field(default=None, max_length=200)
    comments_url: str | None = Field(default=None, max_length=2000)


class BrowserExtractOutput(BaseModel):
    """What `extract()` must return: the entry address and its entries."""

    model_config = ConfigDict(extra="forbid")

    page_url: str = Field(max_length=2000)
    items: list[RawBrowserItem] = Field(default_factory=list, max_length=500)


# `extract(page, *, page_url, max_items, timeout_ms=30000) -> BrowserExtractOutput`
ExtractScript = Callable[..., Awaitable[BrowserExtractOutput]]


class ScriptGateError(ConfigError):
    """A replay script failed the static import/whitelist check."""
