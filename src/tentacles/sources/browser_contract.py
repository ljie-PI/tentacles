"""Fixed-script contract for `type: browser` sources, plus its static import gate.

A repository-controlled script `scripts/browser_sources/<source_id>.py` is
replayed on every fetch, so it is loaded as data, not as a trusted module: the
AST gate below rejects anything outside a small allowlist of pure data modules
before the source is compiled. The script never receives credentials; it only
sees the headed work tab.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import ModuleType
from typing import Annotated, Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from tentacles.errors import ConfigError

# Scripts may only import pure data helpers plus the two tentacles modules that
# define their output contract. Anything able to execute processes, open
# sockets, touch the filesystem or reach the interpreter guts stays out.
ALLOWED_IMPORTS: frozenset[str] = frozenset({
    "__future__", "re", "json", "math", "datetime", "urllib.parse", "typing", "dataclasses",
    "playwright.async_api", "tentacles.sources.browser_contract",
})
FORBIDDEN_CALLS: frozenset[str] = frozenset({
    "__import__", "eval", "exec", "compile", "open", "breakpoint", "input", "globals", "locals",
    "vars", "dir", "getattr", "setattr", "delattr", "exit", "quit",
})
# A script that never returns is a bug, not a page that changed.
MAX_SCRIPT_BYTES = 512 * 1024


class RawBrowserItem(BaseModel):
    """One entry a replay script extracted; `Item` truncation happens on ingest."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    url: str | None = Field(default=None, max_length=2000)
    author: str = Field(default="", max_length=200)
    summary: str = Field(default="", max_length=4000)
    content: str = Field(default="", max_length=200000)
    published: str | None = Field(default=None, max_length=200)
    comments_url: str | None = Field(default=None, max_length=2000)


class BrowserExtractOutput(BaseModel):
    """What `extract()` must return: the entry address and its entries."""

    model_config = ConfigDict(extra="forbid")

    page_url: str = Field(max_length=2000)
    items: list[RawBrowserItem] = Field(default_factory=list, max_length=500)


# `extract(page, *, page_url, max_items, timeout_ms=30000) -> BrowserExtractOutput`
ExtractScript = Callable[..., Awaitable[BrowserExtractOutput]]


class ScriptGateError(ConfigError):
    """A replay script failed the static import/whitelist check."""


def check_script(source: str, *, name: str) -> ast.Module:
    """Statically reject a script outside the import and call allowlist.

    Returns the parsed module so the caller compiles exactly what was checked.
    """
    if len(source.encode("utf-8", "ignore")) > MAX_SCRIPT_BYTES:
        raise ScriptGateError(f"browser script {name} exceeds {MAX_SCRIPT_BYTES} bytes")
    try:
        tree = ast.parse(source, filename=name)
    except SyntaxError as exc:
        raise ScriptGateError(f"browser script {name} is not valid Python (line {exc.lineno})") from None
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ALLOWED_IMPORTS:
                    raise ScriptGateError(_rejected(name, node, f"import {alias.name!r}"))
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in ALLOWED_IMPORTS:
                raise ScriptGateError(_rejected(name, node, f"import from {node.module or '.'!r}"))
        elif isinstance(node, ast.Call) and (called := _call_name(node.func)) is not None:
            if called in FORBIDDEN_CALLS:
                raise ScriptGateError(_rejected(name, node, f"call to {called}()"))
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
            raise ScriptGateError(_rejected(name, node, f"dunder attribute {node.attr!r}"))
    return tree


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _rejected(name: str, node: ast.AST, what: str) -> str:
    return f"browser script {name} rejected by the import allowlist: {what} at line {getattr(node, 'lineno', 0)}"


def load_script(path: Path) -> ExtractScript:
    """Gate, compile and execute one fixed script; return its `extract` callable."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ScriptGateError(f"browser script {path.name} cannot be read as UTF-8") from None
    code = compile(check_script(source, name=path.name), str(path), "exec")
    module = ModuleType(f"tentacles_browser_scripts.{path.stem}")
    module.__file__ = str(path)
    exec(code, module.__dict__)  # noqa: S102 - the AST gate above is the trust boundary
    extract: Any = module.__dict__.get("extract")
    if not callable(extract):
        raise ScriptGateError(f"browser script {path.name} must define async def extract(page, *, page_url, max_items, timeout_ms)")
    return extract
