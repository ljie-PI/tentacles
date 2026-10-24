"""Recover JSON from model prose without altering content inside strings."""
from __future__ import annotations

import json
import re
from typing import Any, Iterator


def _balanced(text: str) -> Iterator[str]:
    stack: list[str] = []
    start = 0
    quoted = escaped = False
    for i, char in enumerate(text):
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"' and stack:
            quoted = True
        elif char in '{[':
            if not stack:
                start = i
            stack.append(char)
        elif char in '}]' and stack:
            if stack[-1] != ('{' if char == '}' else '['):
                stack.clear()
                continue
            stack.pop()
            if not stack:
                yield text[start:i + 1]


def _without_trailing_commas(text: str) -> str:
    out = []
    quoted = escaped = False
    for i, char in enumerate(text):
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == ',' and text[i + 1:].lstrip().startswith(('}', ']')):
            continue
        out.append(char)
    return ''.join(out)


def _invalid_constant(value: str) -> None:
    raise ValueError('non-finite JSON number')


def extract_json(text: str) -> Any:
    candidates = [text.strip(), *re.findall(r'```(?:json)?\s*(.*?)```', text, re.I | re.S),
                  *_balanced(text)]
    for repair in (False, True):
        for candidate in candidates:
            try:
                return json.loads(_without_trailing_commas(candidate) if repair else candidate,
                                  parse_constant=_invalid_constant)
            except (ValueError, RecursionError):
                pass
    raise ValueError('no valid JSON in model output')
