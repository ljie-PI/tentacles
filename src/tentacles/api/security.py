"""Bearer authorization and browser-origin checks before any API operation."""
from __future__ import annotations

import ipaddress
from hmac import compare_digest
from urllib.parse import urlsplit
from typing import Callable

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from tentacles.config import AppConfig


def authorization(config: AppConfig) -> Callable[..., None]:
    token = config.server.token.get_secret_value().encode('utf-8')
    bearer = HTTPBearer(auto_error=False)
    allowed_origins = set(config.server.cors_origins)
    known_hosts = {'localhost', '127.0.0.1', '::1', config.server.host.casefold()}
    known_hosts.update(host for origin in allowed_origins if (host := urlsplit(origin).hostname) is not None)
    wildcard_bind = config.server.host in {'0.0.0.0', '::'}

    def authorize(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        supplied = credentials.credentials.encode('utf-8') if credentials is not None else b''
        if token and not compare_digest(supplied, token):
            raise HTTPException(401, 'invalid or missing bearer token', headers={'WWW-Authenticate': 'Bearer'})
        # Without a token, Host must not turn a rebinding domain into a trusted
        # same-origin site. Explicit CORS origins also support reverse proxies.
        host = (request.url.hostname or '').casefold()
        try:
            literal_ip = ipaddress.ip_address(host)
        except ValueError:
            literal_ip = None
        if not token and host not in known_hosts and not (wildcard_bind and literal_ip is not None):
            raise HTTPException(403, 'untrusted API host')
        origin = request.headers.get('origin')
        same_origin = f'{request.url.scheme}://{request.url.netloc}'
        if origin is not None and origin != same_origin and origin not in allowed_origins:
            raise HTTPException(403, 'untrusted request origin')
        if origin is None and request.headers.get('sec-fetch-site') == 'cross-site':
            raise HTTPException(403, 'cross-site request requires an allowed origin')

    return authorize
