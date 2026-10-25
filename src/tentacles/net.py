"""Outbound HTTP. Every request in the system goes through an HttpPool, whose
transport can be injected — the single network boundary tests replace."""

from __future__ import annotations

import httpx

USER_AGENT = "tentacles/0.2 (+personal feed reader)"


class HttpPool:
    """Hands out AsyncClients per proxy/scope, sharing one transport.

    Optional scopes isolate source sessions; existing unscoped callers retain
    their shared clients.

    With an injected transport, proxies are ignored so the transport sees
    every request.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, *, timeout: float = 30):
        self._transport = transport
        self._timeout = timeout
        self._clients: dict[tuple[str | None, str | None], httpx.AsyncClient] = {}

    def client(self, proxy: str | None = None, *, scope: str | None = None) -> httpx.AsyncClient:
        key = (None if self._transport else proxy, scope)
        if key not in self._clients:
            self._clients[key] = httpx.AsyncClient(
                transport=self._transport, proxy=key[0], timeout=self._timeout,
                follow_redirects=True, headers={"User-Agent": USER_AGENT},
            )
        return self._clients[key]

    async def aclose(self) -> None:
        for c in self._clients.values():
            await c.aclose()
        self._clients.clear()

    async def __aenter__(self) -> "HttpPool":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()
