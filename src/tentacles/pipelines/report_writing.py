"""Shared prompt limits, completion redaction and numbered citation policy."""
from __future__ import annotations

from typing import Callable

from tentacles.errors import UpstreamError
from tentacles.llm.registry import ModelRegistry
from tentacles.pipelines.config import ProcessorSpec
from tentacles.prompts import MAX_PROMPT_CHARS
from tentacles.schemas import Completion


async def complete_report(processor: ProcessorSpec, models: ModelRegistry, system: str, user: str,
                          on_completion: Callable[[Completion], None]) -> Completion:
    if len(user) > MAX_PROMPT_CHARS:
        raise UpstreamError(f'{processor.type} prompt exceeds size limit')
    system += f'\n输出语言：{processor.language}\n追加指令：{processor.instructions}'
    name = models.resolve('process', processor.model)
    result = await models.chat(name).complete([{'role': 'user', 'content': user}], system=system,
                                             on_completion=on_completion)
    return result.model_copy(update={'text': models.redact(result.text).strip()})


def require_citations(kind: str, content: str, references: list[int], source_count: int) -> None:
    if not content or not references or any(not 1 <= number <= source_count for number in references):
        raise UpstreamError(f'{kind} returned no valid numbered citations')
