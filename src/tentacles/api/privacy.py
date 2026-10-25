"""Prevent framework rethrows and lifespan messages from leaking to server logs."""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

log = logging.getLogger(__name__)


class LogSafeAPI(FastAPI):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = finished = failed_lifespan = False

        async def filtered_send(message: Message) -> None:
            nonlocal started, finished, failed_lifespan
            if message['type'] in {'lifespan.startup.failed', 'lifespan.shutdown.failed'}:
                failed_lifespan = True
                message = {**message, 'message': 'application lifecycle failed'}
            elif message['type'] == 'http.response.start':
                started = True
            elif message['type'] == 'http.response.body' and not message.get('more_body', False):
                finished = True
            await send(message)

        try:
            await super().__call__(scope, receive, filtered_send)
        except asyncio.CancelledError:
            # Preserve cancellation, not arbitrary cancellation-message content.
            raise asyncio.CancelledError() from None
        except Exception:
            log.error('application operation failed')
            if scope['type'] == 'http':
                if not started:
                    await JSONResponse(status_code=500, content={
                        'error': {'code': 'error', 'message': 'internal server error'}})(scope, receive, send)
                elif not finished:
                    # A partial response cannot change its status or length.
                    # Let the server close it, but never attach the original
                    # exception, traceback, request data or provider content.
                    raise RuntimeError('HTTP response was interrupted') from None
                # Starlette already sent a complete sanitized 500, then raised
                # again for server logging. Do not let that rethrow escape.
            elif scope['type'] != 'lifespan' or not failed_lifespan:
                raise RuntimeError('application operation failed') from None
