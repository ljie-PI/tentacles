"""ASGI application owns one Service for its entire lifespan."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Callable
from hmac import compare_digest

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from tentacles.api.models import Health, SchedulerLoopView
from tentacles.api.errors import ERROR_RESPONSES, install_error_handlers
from tentacles.api.routes import router
from tentacles.config import AppConfig
from tentacles.service import TentaclesService
from tentacles.server_runtime import poll_scheduler
from tentacles.sources.imap_client import ImapFactory, connect_imap
from tentacles.store import Store
from tentacles.timeutil import utcnow


def create_app(config: AppConfig, *, transport: httpx.AsyncBaseTransport | None = None,
               clock: Callable[[], datetime] = utcnow, imap_factory: ImapFactory = connect_imap) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = TentaclesService(config, Store(config.db_path), transport=transport, clock=clock,
                               imap_factory=imap_factory)
        app.state.service = svc
        stop = asyncio.Event()
        app.state.scheduler_loop = SchedulerLoopView(enabled=config.server.scheduler,
                                                       poll_interval=config.server.poll_interval)
        worker = (asyncio.create_task(poll_scheduler(svc, stop, app.state.scheduler_loop, clock),
                                      name='tentacles-scheduler') if config.server.scheduler else None)
        try:
            yield
        finally:
            stop.set()
            try:
                if worker is not None:
                    await worker
            finally:
                await svc.aclose()

    app = FastAPI(title='Tentacles', version='0.2.0', lifespan=lifespan,
                  openapi_url=None, docs_url=None, redoc_url=None, responses=ERROR_RESPONSES)
    install_error_handlers(app, config)
    token = config.server.token.get_secret_value().encode('utf-8')
    bearer = HTTPBearer(auto_error=False)

    def authorize(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        supplied = credentials.credentials.encode('utf-8') if credentials is not None else b''
        if token and not compare_digest(supplied, token):
            raise HTTPException(401, 'invalid or missing bearer token', headers={'WWW-Authenticate': 'Bearer'})

    @app.get('/api/openapi.json', response_model=dict[str, Any], include_in_schema=False,
             dependencies=[Depends(authorize)])
    def openapi():
        return app.openapi()

    @app.get('/api/health', response_model=Health)
    def health():
        return Health()

    app.include_router(router, prefix='/api', dependencies=[Depends(authorize)])
    app.add_middleware(CORSMiddleware, allow_origins=config.server.cors_origins,
                       allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'],
                       allow_headers=['Authorization', 'Content-Type'])
    static_root = config.resolve(config.server.static_dir).resolve()

    @app.get('/{path:path}', include_in_schema=False, response_class=FileResponse)
    def static(path: str):
        if path == 'api' or path.startswith('api/'):
            raise HTTPException(404, 'API route not found')
        index = (static_root / 'index.html').resolve()
        if not index.is_relative_to(static_root) or not index.is_file():
            raise HTTPException(404, 'frontend is not built; see README')
        candidate = (static_root / path).resolve()
        if candidate.is_relative_to(static_root) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)

    for path in app.openapi()['paths'].values():
        for operation in path.values():
            operation.get('responses', {}).pop('422', None)
    return app


def warn_if_exposed(config: AppConfig) -> None:
    import ipaddress
    import logging
    host = config.server.host
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host.casefold() == 'localhost'
    if not local and not config.server.token.get_secret_value():
        logging.getLogger(__name__).warning('server is listening beyond loopback without a token; configure server.token')
