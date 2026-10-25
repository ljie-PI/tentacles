"""Read-only, UID-based mail ingestion. All blocking mail IO stays in one worker."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from email.message import MIMEPart
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any, ClassVar, Type, cast

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from tentacles.asyncutil import run_blocking
from tentacles.errors import UpstreamError
from tentacles.models import Item
from tentacles.text import html_to_text, truncate
from tentacles.sources.base import FetchContext, FetchResult, Source, register


def _has_controls(value: str) -> bool:
    return any(ord(c) < 32 or ord(c) == 127 for c in value)


class ImapOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    host: str
    port: int | None = Field(default=None, ge=1, le=65535, strict=True)
    ssl: bool = True
    username: str
    password: SecretStr = SecretStr('')
    folders: list[str] = Field(default_factory=lambda: ['INBOX'], min_length=1)
    initial_days: int = Field(default=30, ge=1, le=36500, strict=True)
    max_fetch: int = Field(default=100, ge=1, le=10000, strict=True)
    search: str = 'ALL'
    exclude_senders: list[str] = Field(default_factory=list)
    max_body_chars: int = Field(default=20000, ge=1, le=1000000, strict=True)
    max_summary_chars: int = Field(default=2000, ge=1, le=20000, strict=True)
    timeout: float = Field(default=30, gt=0, allow_inf_nan=False)

    @field_validator('host', 'search')
    @classmethod
    def _argument(cls, value: str) -> str:
        value = value.strip()
        if not value or _has_controls(value):
            raise ValueError('must be nonempty without control characters')
        return value

    @field_validator('username')
    @classmethod
    def _username(cls, value: str) -> str:
        if _has_controls(value):
            raise ValueError('username must not contain control characters')
        return value.strip()

    @field_validator('password')
    @classmethod
    def _password(cls, value: SecretStr) -> SecretStr:
        if _has_controls(value.get_secret_value()):
            raise ValueError('password must not contain control characters')
        return value

    @field_validator('folders')
    @classmethod
    def _folders(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or _has_controls(value) for value in values):
            raise ValueError('folders must be nonempty without control characters')
        return list(dict.fromkeys(values))

    @field_validator('exclude_senders')
    @classmethod
    def _excluded(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or _has_controls(value) for value in values):
            raise ValueError('excluded sender addresses must not be blank or contain controls')
        return list(dict.fromkeys(value.strip().casefold() for value in values))

    @property
    def server_port(self) -> int:
        return self.port if self.port is not None else 993 if self.ssl else 143


def _mailbox_name(folder: str) -> str:
    def encode(match: re.Match[str]) -> str:
        text = match[0]
        if text == '&':
            return '&-'
        return '&' + base64.b64encode(text.encode('utf-16-be')).decode().rstrip('=').replace('/', ',') + '-'
    encoded = re.sub(r'[^\x20-\x7e]+|&', encode, folder)
    return '"' + encoded.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _ok(response: Any) -> list[Any]:
    status, data = response
    if status != 'OK' or not isinstance(data, list):
        raise ValueError('IMAP operation failed')
    return data


def _uid(value: Any) -> int:
    number = int(value)
    if not 1 <= number <= 4294967295:
        raise ValueError('invalid IMAP UID')
    return number


_REPLY_BOUNDARY = re.compile(
    r'^(?:On .+\bwrote:|[在于].+写道[：:]|[-_]{2,}\s*(?:Original Message|原始邮件|Forwarded message|转发邮件)'
    r'\s*[-_]{2,}|Begin forwarded message:)\s*$', re.I)


_WEEKDAY = r'(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)'
_MONTH = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
_MAIL_DATE = (rf'(?:\d{{1,2}}\s+{_MONTH}\s+\d{{4}}|{_MONTH}\s+\d{{1,2}},?\s+\d{{4}}|'
              r'\d{4}[-/]\d{1,2}[-/]\d{1,2})')
_ATTRIBUTION_DATE = re.compile(
    rf'^(?:On\s+(?:{_WEEKDAY},?\s+)?{_MAIL_DATE}\b|'
    r'[在于]\s*(?:\d{4}年\d{1,2}月\d{1,2}日|\d{4}[-/]\d{1,2}[-/]\d{1,2}))', re.I)

_SHORT_ATTRIBUTION = re.compile(rf'^On\s+{_WEEKDAY},\s+(?P<name>.+?)\s+wrote:$', re.I)


def _body_parts(msg: MIMEPart) -> list[tuple[str, str]]:
    if msg.get_content_disposition() == 'attachment' or msg.get_filename() or msg.get_content_maintype() == 'message':
        return []
    if msg.is_multipart():
        return [part for child in msg.iter_parts() for part in _body_parts(child)]
    kind = msg.get_content_type()
    if kind not in {'text/plain', 'text/html'}:
        return []
    payload = msg.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return []
    try:
        text = payload.decode(msg.get_content_charset() or 'utf-8', errors='replace')
    except LookupError:
        text = payload.decode('utf-8', errors='replace')
    return [(kind, text)]


def _attribution_name(value: str, *, capitalized: bool) -> bool:
    words = value.split()
    if not 1 <= len(words) <= 8 or words[0].casefold() in {
            'i', 'we', 'you', 'they', 'this', 'that', 'here', 'what', '我', '我们', '你', '你们', '他们', '她们'}:
        return False
    for word in words:
        letters = word.replace('-', '').replace("'", '').replace('.', '').replace('&', '')
        if not letters or not (letters.isalpha() if capitalized else letters.isalnum()):
            return False
        if capitalized and word[0].islower():
            return False
    return True


def _attribution_writer(value: str, *, allow_name: bool) -> bool:
    address = re.fullmatch(r'(?P<name>.*?)\s*(?:<[^@\s<>]+@[^@\s<>]+>|[^@\s<>]+@[^@\s<>]+)', value)
    if address:
        return not address['name'].strip() or _attribution_name(address['name'].strip(), capitalized=False)
    return allow_name and _attribution_name(value, capitalized=True)


def _reply_attribution(header: str) -> bool:
    if not _REPLY_BOUNDARY.fullmatch(header):
        return False
    if not re.match(r'^(?:On\s|[在于])', header, re.I):
        return True  # explicit "Original Message" / forwarding separators
    ending = re.compile(r'(?:\bwrote:|写道[：:])\s*$', re.I)
    date = _ATTRIBUTION_DATE.match(header)
    if date:
        # Date evidence is not sufficient: the rest must be an optional clock
        # followed by a writer, not an arbitrary sentence containing "wrote".
        writer = ending.sub('', header[date.end():]).strip(' ,，')
        writer = re.sub(
            r'^(?:at\s*|T)?\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?'
            r'(?:\s*(?:UTC|GMT)(?:[+-]\d{1,2}(?::?\d{2})?)?)?\s*[,，]?\s*',
            '', writer, flags=re.I)
        return _attribution_writer(writer, allow_name=True)
    short = _SHORT_ATTRIBUTION.fullmatch(header)
    if short:
        return _attribution_writer(short['name'], allow_name=True)
    # An undated attribution needs an actual mailbox, not just arbitrary words.
    writer = ending.sub('', re.sub(r'^(?:On\s+|[在于]\s*)', '', header, flags=re.I)).strip()
    return _attribution_writer(writer, allow_name=False)


def _sentence_end(line: str, *, in_attribution: bool = False) -> bool:
    if in_attribution and re.search(r'\b(?:(?i:Dr|Mr|Mrs|Ms|Prof|Sr|Jr)|[A-Z])\.$', line.strip()):
        return False
    return line.rstrip(" \t\"'”’»)]").endswith(('.', '!', '?', '。', '！', '？'))


def _reply_header(lines: list[str], start: int) -> bool:
    header = lines[start].strip()
    if _reply_attribution(header):
        return True
    date_context = _ATTRIBUTION_DATE.match(header) is not None
    if (_REPLY_BOUNDARY.fullmatch(header) or _sentence_end(header, in_attribution=date_context)
            or not re.match(r'^(?:On\s|[在于])', header, re.I)):
        return False
    # A continuation cannot borrow a later sentence or independent attribution.
    for line in lines[start + 1:start + 5]:
        continuation = line.strip()
        if (not continuation or _sentence_end(continuation, in_attribution=date_context)
                or _REPLY_BOUNDARY.fullmatch(continuation)):
            break
        header += ' ' + continuation
        if len(header) > 1000:
            break
        if _reply_attribution(header):
            return True
    return False


def _new_text(text: str) -> str:
    result = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _reply_header(lines, i):
            break
        if re.match(r'^(?:From|发件人)[：:]', line.strip(), re.I) and any(
                re.match(r'^(?:Sent|Date|Subject|发送时间|主题)[：:]', following.strip(), re.I)
                for following in lines[i + 1:i + 6]):
            break
        if not line.lstrip().startswith('>'):
            result.append(line.rstrip())
    return '\n'.join(result).strip()


def _mail_item(raw: bytes, source: str, folder: str, validity: int, uid: int,
               opts: ImapOptions) -> Item | None:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    author = str(msg.get('From', ''))
    if any(address.casefold() in opts.exclude_senders for _, address in getaddresses([author])):
        return None
    parts = _body_parts(msg)
    plain = [text for kind, text in parts if kind == 'text/plain' and text.strip()]
    if plain:
        text = '\n\n'.join(plain)
    else:
        html = '\n'.join(text for kind, text in parts if kind == 'text/html')
        soup = BeautifulSoup(html, 'html.parser')
        for quote in soup.select('blockquote, .gmail_quote, .yahoo_quoted'):
            quote.decompose()
        text = html_to_text(str(soup))
    text = _new_text(text)
    try:
        published = parsedate_to_datetime(str(msg['Date'])) if msg['Date'] else None
    except (ValueError, TypeError):
        published = None
    message_id = str(msg.get('Message-ID', '')).strip()
    return Item(source_id=source, kind='email', external_id=message_id or f'{folder}:{validity}:{uid}',
                title=str(msg.get('Subject', '')).strip() or '(no subject)', author=author,
                content=truncate(text, opts.max_body_chars),
                summary=truncate(re.sub(r'\s+', ' ', text).strip(), opts.max_summary_chars), published=published,
                meta={'folder': folder, 'uidvalidity': validity, 'uid': uid, 'message_id': message_id,
                      'to': str(msg.get('To', '')), 'cc': str(msg.get('Cc', '')),
                      'attachments': [part.get_filename() for part in msg.walk() if part.get_filename()]})


def _message(data: list[Any], uid: int) -> bytes | None:
    if not data or all(part is None for part in data):
        return None  # expunged between SEARCH and FETCH
    for part in data:
        if isinstance(part, tuple) and len(part) == 2 and isinstance(part[0], bytes) and isinstance(part[1], bytes):
            match = re.search(rb'\bUID\s+(\d+)\b', part[0], re.I)
            if match is None or _uid(match[1]) != uid:
                raise ValueError('IMAP FETCH UID mismatch')
            return part[1]
    raise ValueError('missing IMAP message literal')


@register
class ImapSource(Source):
    type: ClassVar[str] = 'imap'
    Options: ClassVar[Type[BaseModel]] = ImapOptions
    dedup_titles: ClassVar[bool] = False

    async def fetch(self, cursor: dict[str, Any], ctx: FetchContext) -> FetchResult:
        return await run_blocking(self._fetch, cursor, ctx)

    def _fetch(self, cursor: dict[str, Any], ctx: FetchContext) -> FetchResult:
        opts = cast(ImapOptions, self.options)
        if not opts.username or not opts.password.get_secret_value():
            raise UpstreamError('IMAP username or password is not configured')
        connection = None
        try:
            identity = hashlib.sha256(json.dumps([opts.host, opts.server_port, opts.ssl, opts.username]).encode()).hexdigest()
            folders = dict(cursor.get('folders', {})) if cursor.get('account') == identity else {}
            connection = ctx.imap_factory(opts.host, opts.server_port, ssl=opts.ssl, timeout=opts.timeout)
            _ok(connection.login(opts.username, opts.password.get_secret_value()))
            items, processed, excluded = [], 0, 0
            for folder in opts.folders:
                if processed >= opts.max_fetch:
                    break
                _ok(connection.select(_mailbox_name(folder), readonly=True))
                _, response = connection.response('UIDVALIDITY')
                validity = _uid(response[0])
                previous = folders.get(folder, {})
                last = previous.get('last_uid', 0) if previous.get('uidvalidity') == validity else 0
                if last == 4294967295:
                    continue
                if last:
                    query = ('UID', f'{last + 1}:*')
                else:
                    date = ctx.now - timedelta(days=opts.initial_days)
                    month = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')[date.month - 1]
                    query = ('SINCE', f'{date.day:02d}-{month}-{date.year}')
                data = _ok(connection.uid('SEARCH', None, *query, opts.search))
                uids = sorted({_uid(token) for line in data for token in line.split() if _uid(token) > last})
                for uid in uids[:opts.max_fetch - processed]:
                    raw = _message(_ok(connection.uid('FETCH', str(uid), '(UID BODY.PEEK[])')), uid)
                    if raw is not None:
                        item = _mail_item(raw, self.spec.id, folder, validity, uid, opts)
                        if item is None:
                            excluded += 1
                        else:
                            items.append(item)
                    last, processed = uid, processed + 1
                folders[folder] = {'uidvalidity': validity, 'last_uid': last}
            return FetchResult(items, {'account': identity, 'folders': folders}, {'processed': processed, 'excluded': excluded})
        except Exception as exc:
            # Servers and socket errors may echo LOGIN credentials or message bodies.
            raise UpstreamError(f'IMAP fetch failed ({type(exc).__name__})') from None
        finally:
            if connection is not None:
                try:
                    connection.logout()
                except Exception:
                    try:
                        connection.shutdown()
                    except Exception:
                        pass
