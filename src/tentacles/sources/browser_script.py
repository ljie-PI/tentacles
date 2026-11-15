"""Static gate and loader for the fixed `type: browser` replay scripts.

A repository-controlled script `scripts/browser_sources/<source_id>.py` is
replayed on every fetch, so it is loaded as data, not as a trusted module: the
AST gate below rejects anything outside a small allowlist of pure data modules
before the source is compiled. The script never receives credentials; it only
sees the headed work tab.

This module owns the filesystem and the interpreter entry points, which is
exactly why it is not importable from a script: `browser_contract` stays the
allowlisted half and holds nothing but the output contract.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import ModuleType
from typing import Any

from tentacles.sources.browser_contract import ExtractScript, ScriptGateError

# Scripts may only import pure data helpers plus the one tentacles module that
# defines their output contract. Anything able to execute processes, open
# sockets, touch the filesystem or reach the interpreter guts stays out, and
# every allowlisted `tentacles.*` module must expose pure data only: a module
# that re-exports `Path` or a loader would make the allowlist a launcher.
ALLOWED_IMPORTS: frozenset[str] = frozenset({
    "__future__", "re", "json", "math", "datetime", "urllib.parse", "typing", "dataclasses",
    "playwright.async_api", "tentacles.sources.browser_contract",
})
FORBIDDEN_CALLS: frozenset[str] = frozenset({
    "__import__", "eval", "exec", "compile", "open", "breakpoint", "input", "globals", "locals",
    "vars", "dir", "getattr", "setattr", "delattr", "exit", "quit",
})
# Attribute names no script may reach at all: the gate can only see the name, so
# it cannot vet what the attribute resolves to, and each of these reaches the
# filesystem or the import machinery. Checked for both calls and plain reads,
# because `p = module.Path` would launder the same capability.
FORBIDDEN_ATTRIBUTES: frozenset[str] = frozenset({
    "load_script", "read_text", "open", "Path", "import_module", "__import__", "eval", "exec",
    "compile", "getattr",
})
# A script that never returns is a bug, not a page that changed.
MAX_SCRIPT_BYTES = 512 * 1024


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
                    raise ScriptGateError(_rejected(name, node, f"dangerous import of {alias.name!r}"))
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in ALLOWED_IMPORTS:
                raise ScriptGateError(_rejected(name, node, f"dangerous import from {node.module or '.'!r}"))
            # `from module import load_script` is the same capability as the
            # attribute read, so an imported name is checked the same way.
            for alias in node.names:
                if alias.name in FORBIDDEN_ATTRIBUTES:
                    raise ScriptGateError(_rejected(name, node, f"dangerous import of {alias.name!r}"))
        elif isinstance(node, ast.Call) and (called := _call_name(node.func)) is not None:
            if called in FORBIDDEN_CALLS or called in FORBIDDEN_ATTRIBUTES:
                raise ScriptGateError(_rejected(name, node, f"dangerous call to {called}()"))
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRIBUTES:
                raise ScriptGateError(_rejected(name, node, f"dangerous attribute {node.attr!r}"))
            if node.attr.startswith("__") and node.attr.endswith("__"):
                raise ScriptGateError(_rejected(name, node, f"dangerous attribute {node.attr!r}"))
    return tree


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _rejected(name: str, node: ast.AST, violation: str) -> str:
    return f"browser script {name} rejected: {violation} at line {getattr(node, 'lineno', 0)}"


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
