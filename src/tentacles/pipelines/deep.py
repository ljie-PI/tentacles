"""Legacy event clustering and investigative prompt, one report per major event."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from tentacles.cluster import cluster_articles
from tentacles.config import AppConfig
from tentacles.errors import UpstreamError
from tentacles.llm.registry import ModelRegistry
from tentacles.models import Item
from tentacles.personas.models import PersonaView
from tentacles.personas.render import render_persona
from tentacles.pipelines.config import PipelineSpec
from tentacles.pipelines.models import Evaluation, ReportView
from tentacles.pipelines.report_path import new_report_location
from tentacles.prompts import MAX_PROMPT_CHARS, load_prompt
from tentacles.schemas import Completion
from tentacles.timeutil import to_utc

# Preserved from the original src/tentacles/llm/report.py (cfd7a5f).
_SYSTEM = '''你是一名 investigative 风格的深度报道记者。你会拿到同一事件的、来自多个独立来源的文章。请基于这些材料写一篇结构化的中文深度报道：
1. 导语：用 3~5 句话概括事件、最新进展与为什么重要；
2. 正文分节（背景 / 各方说法 / 数据与疑点 / 影响与后续）；
3. 每个事实性陈述都标注来源编号（如 来源1、来源2 对应文章列表中的编号），来源间有矛盾时明确指出；
4. 结尾列出「尚待确认的问题」；
5. 不使用材料之外的知识；输出 Markdown。'''


async def make_deep(config: AppConfig, spec: PipelineSpec, persona: PersonaView,
                    selected: list[tuple[Item, Evaluation]], models: ModelRegistry,
                    clock: Callable[[], datetime], on_completion: Callable[[Completion], None]) -> list[ReportView]:
    events = [event for event in cluster_articles([item for item, _ in selected])
              if event.size >= spec.processor.min_cluster_size][:spec.processor.max_reports]
    if not events:
        return []
    evaluations = {item.id: evaluation for item, evaluation in selected}
    name = models.resolve('process', spec.processor.model)
    prompt = load_prompt(config.resolve(spec.processor.prompt_file)) if spec.processor.prompt_file is not None else None
    reports = []
    for event in events:
        now = to_utc(clock())
        items = [{'number': i + 1, 'id': item.id, 'source': item.source_id, 'title': item.title,
                  'summary': item.summary[:1200] or '（无摘要）', 'url': item.url,
                  'score': evaluations[item.id].score, 'tags': evaluations[item.id].tags}
                 for i, item in enumerate(event.members)]
        user = f'事件标题：{event.title()}\n\n相关文章（编号-来源-标题-摘要）：\n' + '\n'.join(
            f'[来源{item["number"]}] ({item["source"]}) {item["title"]}\n{item["summary"]}' for item in items)
        system = _SYSTEM
        if prompt is not None:
            system = prompt.system
            user = prompt.render({'persona': render_persona(persona), 'items': items, 'groups': {'event': items},
                'tags': [tag.model_dump() for tag in persona.tags],
                'now': now.astimezone(ZoneInfo(config.app.timezone)).isoformat(),
                'pipeline': {'id': spec.id, 'name': spec.name}})
        if len(user) > MAX_PROMPT_CHARS:
            raise UpstreamError('deep prompt exceeds size limit')
        system += f'\n输出语言：{spec.processor.language}\n追加指令：{spec.processor.instructions}'
        result = await models.chat(name).complete([{'role': 'user', 'content': user}], system=system,
                                                 on_completion=on_completion)
        now = to_utc(clock())
        content = models.redact(result.text).strip()
        references = [int(a or b) for a, b in re.findall(r'\[(\d+)\]|来源\s*(\d+)', content)]
        if not content or not references or any(not 1 <= number <= len(items) for number in references):
            raise UpstreamError('deep returned no valid numbered citations')
        title = models.redact(f'深度报道：{event.title()}')
        appendix = '\n'.join(f'[来源{item["number"]}] {item["source"]}: {item["title"]} {item["url"]}' for item in items)
        markdown = models.redact(f'# {title}\n\n> 基于来自 {len(event.sources)} 个来源的 {event.size} 篇文章。\n\n'
                                 f'---\n\n{content}\n\n## Sources\n\n{appendix}\n')
        report_id, path = new_report_location(config, spec.id, 'deep', title, now)
        reports.append(ReportView(id=report_id, pipeline_id=spec.id, persona_version=persona.version, kind='deep',
            title=title, markdown=markdown, item_ids=[item.id for item in event.members], model=result.model,
            usage=result.usage, cost=result.cost, path=str(path), created_at=now))
    return reports
