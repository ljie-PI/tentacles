"""Shared test infrastructure: a scriptable fake web and temp configs.

Tests drive the system through the Service facade (or the CLI adapter on top
of it). The only doubles live at external boundaries — here, all outbound HTTP
goes through a FakeWeb mounted as an httpx transport.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
import yaml

from tentacles.service import TentaclesService

Handler = Callable[[httpx.Request], httpx.Response]


@dataclass
class FakeWeb:
    """Routes URL -> response (or handler). Unknown URLs return 404."""

    routes: dict[str, Handler | httpx.Response] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def serve(self, url: str, body: str | bytes = "", *, status: int = 200,
              headers: dict[str, str] | None = None) -> None:
        content = body.encode() if isinstance(body, str) else body
        self.routes[url] = httpx.Response(status, content=content, headers=headers or {})

    def handle(self, url: str, handler: Handler) -> None:
        self.routes[url] = handler

    def requests_to(self, url: str) -> list[httpx.Request]:
        return [r for r in self.requests if str(r.url) == url]

    def transport(self) -> httpx.MockTransport:
        def dispatch(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            route = self.routes.get(str(request.url))
            if route is None:
                return httpx.Response(404, content=b"not found")
            if callable(route):
                return route(request)
            return httpx.Response(route.status_code, content=route.content,
                                  headers=route.headers)
        return httpx.MockTransport(dispatch)


def rss_feed(*entries: dict[str, str], title: str = "Feed") -> str:
    """Build a minimal RSS 2.0 document. Entry keys: title, link, guid, pubDate, description."""
    items = []
    for e in entries:
        parts = "".join(f"<{k}>{v}</{k}>" for k, v in e.items())
        items.append(f"<item>{parts}</item>")
    return (f'<?xml version="1.0"?><rss version="2.0"><channel><title>{title}</title>'
            f"<link>https://example.com</link>{''.join(items)}</channel></rss>")


@pytest.fixture
def web() -> FakeWeb:
    return FakeWeb()


@pytest.fixture
def write_config(tmp_path: Path) -> Callable[..., Path]:
    """Write a config file into a temp project; returns its path."""

    def _write(data: dict[str, Any] | str, name: str = "tentacles.yaml") -> Path:
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(exist_ok=True)
        path = cfg_dir / name
        text = data if isinstance(data, str) else yaml.safe_dump(data, allow_unicode=True)
        path.write_text(text, encoding="utf-8")
        return path

    return _write


@pytest.fixture
def open_service(web: FakeWeb):
    """Open a service on a config path with the fake web as its only network."""
    opened: list[TentaclesService] = []

    def _open(config_path: Path, *, env: dict[str, str] | None = None) -> TentaclesService:
        svc = TentaclesService.open(config_path, transport=web.transport(), env=env or {})
        opened.append(svc)
        return svc

    yield _open
    for svc in opened:
        asyncio.run(svc.aclose())
