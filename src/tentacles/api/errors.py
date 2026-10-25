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
from tentacles.errors import TentaclesError

log = logging.getLogger(__name__)
STATUS_CODES = {'invalid': 400, 'not_found': 404, 'conflict': 409, 'busy': 409, 'upstream': 502}
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {status: {'model': ErrorResponse} for status in (400, 401, 404, 409, 500, 502)}


def install_error_handlers(app: FastAPI, config: AppConfig) -> None:
    secrets = [model.api_key.get_secret_value() for model in config.models.values()]
    secrets.append(config.server.token.get_secret_value())

    def response(code: str, message: str, status: int, headers=None) -> JSONResponse:
        for secret in secrets:
            if secret:
                message = message.replace(secret, '[redacted]')
        return JSONResponse(status_code=status, headers=headers,
                            content=ErrorResponse(error=ErrorDetail(code=code, message=message)).model_dump())

    @app.exception_handler(TentaclesError)
    async def domain_error(request: Request, exc: TentaclesError):
        return response(exc.code, exc.message, STATUS_CODES.get(exc.code, 500))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return response('invalid', 'invalid request parameters', 400)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code = {401: 'unauthorized', 404: 'not_found', 405: 'invalid'}.get(exc.status_code, 'error')
        return response(code, str(exc.detail), 400 if exc.status_code == 405 else exc.status_code, exc.headers)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        log.error('HTTP operation failed (%s)', type(exc).__name__)
        origin = request.headers.get('origin')
        headers = ({'Access-Control-Allow-Origin': origin, 'Vary': 'Origin'}
                   if origin in config.server.cors_origins else None)
        return response('error', 'internal server error', 500, headers)
