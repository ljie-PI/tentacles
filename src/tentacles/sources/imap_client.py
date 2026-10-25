"""Injectable IMAP socket boundary; never disables TLS certificate verification."""
from __future__ import annotations

import imaplib
import ssl as tls
from typing import Any, Protocol


class ImapConnection(Protocol):
    def login(self, user: str, password: str) -> Any: ...
    def select(self, mailbox: str = 'INBOX', readonly: bool = False) -> Any: ...
    def response(self, code: str) -> Any: ...
    def uid(self, command: str, *args: Any) -> Any: ...
    def logout(self) -> Any: ...
    def shutdown(self) -> None: ...


class ImapFactory(Protocol):
    def __call__(self, host: str, port: int, *, ssl: bool, timeout: float) -> ImapConnection: ...


def connect_imap(host: str, port: int, *, ssl: bool, timeout: float) -> ImapConnection:
    if ssl:
        return imaplib.IMAP4_SSL(host, port, ssl_context=tls.create_default_context(), timeout=timeout)
    return imaplib.IMAP4(host, port, timeout=timeout)
