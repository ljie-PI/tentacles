"""Sectioned local prompts; only user text is a sandboxed Jinja template."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import StrictUndefined, Template, TemplateError, meta, nodes
from jinja2.sandbox import ImmutableSandboxedEnvironment

from tentacles.errors import ConfigError

MAX_PROMPT_CHARS = 1_000_000
_FIELDS = {'persona', 'items', 'groups', 'tags', 'now', 'pipeline'}


@dataclass
class Prompt:
    system: str
    user: Template

    def render(self, context: dict[str, Any]) -> str:
        chunks, size = [], 0
        try:
            for chunk in self.user.generate(context):
                size += len(chunk)
                if size > MAX_PROMPT_CHARS:
                    raise ConfigError('rendered prompt exceeds size limit')
                chunks.append(chunk)
        except TemplateError:
            raise ConfigError('cannot render custom prompt using the available fields') from None
        return ''.join(chunks)


def load_prompt(path: Path) -> Prompt:
    try:
        with path.open(encoding='utf-8') as handle:
            text = handle.read(MAX_PROMPT_CHARS + 1)
    except (OSError, UnicodeError):
        raise ConfigError('cannot read UTF-8 processor prompt_file') from None
    markers = list(re.finditer(r'^\[(system|user)\]\s*$', text, re.M | re.I))
    if (len(text) > MAX_PROMPT_CHARS or [m.group(1).lower() for m in markers] != ['system', 'user'] or
            text[:markers[0].start()].strip()):
        raise ConfigError('prompt_file requires [system] followed by [user] sections')
    system = text[markers[0].end():markers[1].start()].strip()
    user = text[markers[1].end():].strip()
    if not system or not user:
        raise ConfigError('prompt_file sections must not be empty')
    env = ImmutableSandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
    env.globals.clear()
    try:
        ast = env.parse(user)
        if meta.find_undeclared_variables(ast) - _FIELDS or any(ast.find_all(
                (nodes.Include, nodes.Import, nodes.FromImport, nodes.Extends))):
            raise ConfigError('prompt_file uses unsupported variables or external templates')
        template = env.from_string(user)
    except TemplateError:
        raise ConfigError('invalid user template in processor prompt_file') from None
    return Prompt(system, template)
