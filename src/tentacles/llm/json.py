"""Recover JSON from model prose without altering content inside strings."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterator


@dataclass
class _StringState:
    quoted: bool = False
    escaped: bool = False

    def consume(self, char: str, *, allow_start: bool = True) -> bool:
        """Advance lexical state; return whether this character belongs to a string."""
        if self.quoted:
            if self.escaped:
                self.escaped = False
            elif char == '\\':
                self.escaped = True
            elif char == '"':
                self.quoted = False
            return True
        if char == '"' and allow_start:
            self.quoted = True
            return True
        return False


def _balanced(text: str) -> Iterator[str]:
    stack: list[str] = []
    start = 0
    strings = _StringState()
    for i, char in enumerate(text):
        if strings.consume(char, allow_start=bool(stack)):
            continue
        if char in '{[':
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
    strings = _StringState()
    closing = re.compile(r'\s*[}\]]')
    for i, char in enumerate(text):
        if not strings.consume(char) and char == ',' and closing.match(text, i + 1):
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
