"""Shared prompt limits, completion redaction and numbered citation policy."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Callable

from tentacles.diagnostics import DiagnosticEvent
from tentacles.errors import UpstreamError
from tentacles.llm.registry import ModelRegistry
from tentacles.pipelines.config import ProcessorSpec
from tentacles.prompts import MAX_PROMPT_CHARS
from tentacles.schemas import Completion


async def complete_report(processor: ProcessorSpec, models: ModelRegistry, system: str, user: str,
                          on_completion: Callable[[Completion], None], *, model: str) -> Completion:
    if len(user) > MAX_PROMPT_CHARS:
        raise UpstreamError(f'{processor.type} prompt exceeds size limit')
    system += f'\n输出语言：{processor.language}\n追加指令：{processor.instructions}'
    result = await models.chat(model).complete([{'role': 'user', 'content': user}], system=system,
                                             on_completion=on_completion)
    return result.model_copy(update={'text': models.redact(result.text).strip()})


def require_citations(kind: str, content: str, references: list[int], source_count: int, *,
                      pipeline_id: str, record_error: Callable[[DiagnosticEvent], None],
                      item_ids: Sequence[str] = ()) -> None:
    """Reject a report whose citations cannot be satisfied, leaving triage behind.

    A rejected report costs the whole selection, so the ledger row names the
    offending numbers and the largest number the model could legitimately use.
    """
    out_of_range = sorted({number for number in references if not 1 <= number <= source_count})
    if content and references and not out_of_range:
        return
    record_error(DiagnosticEvent(
        phase='report_process', error_code='citation_out_of_range', pipeline_id=pipeline_id,
        message=f'{kind} report has no valid numbered citations', retryable=True,
        debug_context={'kind': kind, 'out_of_range': out_of_range, 'max_number': source_count,
                       'reference_count': len(references), 'references': references[:100],
                       'empty_body': not content, 'item_ids': list(item_ids)}))
    raise UpstreamError(f'{kind} returned no valid numbered citations')
