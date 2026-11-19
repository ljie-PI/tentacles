"""One sanitized error envelope for domain, HTTP and validation failures."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from tentacles.api.models import ErrorDetail, ErrorResponse
from tentacles.config import AppConfig
from tentacles.diagnostics import configured_secrets
from tentacles.errors import TentaclesError

log = logging.getLogger(__name__)
STATUS_CODES = {'invalid': 400, 'not_found': 404, 'conflict': 409, 'busy': 409, 'upstream': 502}
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {status: {'model': ErrorResponse} for status in (400, 401, 403, 404, 409, 500, 502)}


def install_error_handlers(app: FastAPI, config: AppConfig) -> None:
    def active_secrets(request: Request) -> list[str]:
        svc = getattr(request.app.state, 'service', None)
        current = svc.config if svc is not None else config
        raw = configured_secrets(current)
        token = current.server.token.get_secret_value()
        if token:
            raw.append(token)
        return sorted(set(raw), key=len, reverse=True)

    def response(request: Request, code: str, message: str, status: int, headers=None) -> JSONResponse:
        for secret in active_secrets(request):
            if secret:
                message = message.replace(secret, '[redacted]')
        return JSONResponse(status_code=status, headers=headers,
                            content=ErrorResponse(error=ErrorDetail(code=code, message=message)).model_dump())

    @app.exception_handler(TentaclesError)
    async def domain_error(request: Request, exc: TentaclesError):
        return response(request, exc.code, exc.message, STATUS_CODES.get(exc.code, 500))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return response(request, 'invalid', 'invalid request parameters', 400)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code = {401: 'unauthorized', 403: 'forbidden', 404: 'not_found', 405: 'invalid'}.get(exc.status_code, 'error')
        return response(request, code, str(exc.detail), 400 if exc.status_code == 405 else exc.status_code, exc.headers)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        log.error('HTTP operation failed (%s)', type(exc).__name__)
        origin = request.headers.get('origin')
        headers = ({'Access-Control-Allow-Origin': origin, 'Vary': 'Origin'}
                   if origin in config.server.cors_origins else None)
        return response(request, 'error', 'internal server error', 500, headers)
