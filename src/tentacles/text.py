"""Text helpers shared by sources."""

from __future__ import annotations

import hashlib
import re

from bs4 import BeautifulSoup

_BLOCK_TAGS = ["p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
               "blockquote", "pre", "section", "article", "header", "footer"]

# 1 个 CJK 表意字符（含假名、韩文）单独计 1 unit
_CJK_RANGES = ((0x3040, 0x30FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
               (0xAC00, 0xD7AF), (0xF900, 0xFAFF), (0x20000, 0x2FA1F))
# 词元内部的连接符：GPT-5、v1.2、C++、C# 仍算 1 个词元
_TOKEN_JOINERS = frozenset("-_'.+#")


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def _unit_ends(text: str) -> list[int]:
    """Return the exclusive end offset of every counted unit in `text`.

    One CJK character is one unit; one maximal run of non-CJK alphanumerics,
    optionally joined by `-`, `_`, `'`, `.`, `+` or `#`, is one unit. Punctuation
    and whitespace are not units, and only a joiner between alphanumerics keeps
    the run open.
    """
    ends: list[int] = []
    start: int | None = None
    for index, char in enumerate(text):
        if _is_cjk(char):
            if start is not None:
                ends.append(index)
                start = None
            ends.append(index + 1)
        elif char.isalnum():
            if start is None:
                start = index
        elif start is not None and char in _TOKEN_JOINERS:
            nxt = text[index + 1] if index + 1 < len(text) else ''
            if nxt.isalnum() and not _is_cjk(nxt):
                continue
            ends.append(index)
            start = None
        elif start is not None:
            ends.append(index)
            start = None
    if start is not None:
        ends.append(len(text))
    return ends


def count_content_units(text: str) -> int:
    """Count CJK characters plus non-CJK word tokens, ignoring punctuation."""
    return len(_unit_ends(text))


def truncate_content_units(text: str, limit: int = 1000) -> tuple[str, int, bool]:
    """Cut `text` to at most `limit` content units.

    Returns (text, kept units, truncated). The cut happens right after the last
    kept unit, so trailing punctuation stays with it; the whitespace at the cut
    is collapsed to one separator (`\\n` if it spans a line break, else a space).
    """
    ends = _unit_ends(text)
    if len(ends) <= limit:
        return text, len(ends), False
    if limit <= 0:
        return '', 0, True
    cut = ends[limit - 1]
    trailing = re.match(r'\s*', text[cut:])
    assert trailing is not None
    boundary = trailing.group()
    kept = text[:cut].rstrip() + ('\n' if '\n' in boundary else ' ' if boundary else '')
    return kept, limit, True


def content_sha1(text: str) -> str:
    """Digest of a cleaned full body, compared before the ingest truncation."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def html_to_text(html: str) -> str:
    """Strip markup, keeping block boundaries as line breaks."""
    if "<" not in html and "&" not in html:
        return html.strip()
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    text = soup.get_text()
    lines = (re.sub(r"[ \t \xa0]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: max(n - 1, 0)] + "…"
