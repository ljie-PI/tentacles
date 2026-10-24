"""Text helpers shared by sources."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

_BLOCK_TAGS = ["p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
               "blockquote", "pre", "section", "article", "header", "footer"]


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
    lines = (re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: max(n - 1, 0)] + "…"
