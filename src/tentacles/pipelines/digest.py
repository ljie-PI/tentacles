"""Persona-guided digest with fixed numbered sources and label grouping."""
from __future__ import annotations

import json
import re
from datetime import datetime
from uuid import uuid4
from typing import Any, Callable
from zoneinfo import ZoneInfo

from tentacles.config import AppConfig
from tentacles.errors import UpstreamError
from tentacles.llm.registry import ModelRegistry
from tentacles.models import Item
from tentacles.personas.models import PersonaView
from tentacles.personas.render import render_persona
from tentacles.pipelines.config import PipelineSpec
from tentacles.pipelines.models import Evaluation, ReportView
from tentacles.schemas import Completion
from tentacles.prompts import load_prompt, MAX_PROMPT_CHARS
from tentacles.timeutil import to_utc

_SYSTEM = '''基于给定材料编写新闻日报，按用户画像取舍、排序，并按兴趣标签分板块。
先给出“今日要闻”3–7 条一句话摘要（材料不足时不要补足），再分板块详述。
持续事件可列“重点关注”，区分来源间的共识、分歧与尚未确认的信息。
每个事实用给定编号 [编号] 引用；不得编造来源、引用编号或使用材料之外的知识。
输出 Markdown。'''



async def make_digest(config: AppConfig, spec: PipelineSpec, persona: PersonaView,
                      selected: list[tuple[Item, Evaluation]], models: ModelRegistry,
                      clock: Callable[[], datetime], on_completion: Callable[[Completion], None]) -> ReportView:
    now = to_utc(clock())
    items: list[dict[str, Any]] = [{'number': i + 1, 'id': item.id, 'source': item.source_id, 'title': item.title,
              'summary': item.summary[:600] or '（无摘要）', 'url': item.url,
              'score': evaluation.score, 'tags': evaluation.tags}
             for i, (item, evaluation) in enumerate(selected)]
    groups: dict[str, list[dict[str, Any]]] = {tag.tag: [] for tag in persona.tags}
    for item in items:
        label = next((tag for tag in item['tags'] if tag in groups), 'other')
        groups.setdefault(label, []).append(item)
    payload = {'persona': render_persona(persona), 'groups': {k: v for k, v in groups.items() if v},
               'now': now.astimezone(ZoneInfo(config.app.timezone)).isoformat(),
               'pipeline': {'id': spec.id, 'name': spec.name}}
    system, user = _SYSTEM, json.dumps(payload, ensure_ascii=False)
    if spec.processor.prompt_file is not None:
        prompt = load_prompt(config.resolve(spec.processor.prompt_file))
        system = prompt.system
        user = prompt.render({**payload, 'items': items, 'tags': [tag.model_dump() for tag in persona.tags]})
    if len(user) > MAX_PROMPT_CHARS:
        raise UpstreamError('digest prompt exceeds size limit')
    system += f'\n输出语言：{spec.processor.language}\n追加指令：{spec.processor.instructions}'
    name = models.resolve('process', spec.processor.model)
    result = await models.chat(name).complete([{'role': 'user', 'content': user}], system=system,
                                             on_completion=on_completion)
    now = to_utc(clock())
    content = models.redact(result.text).strip()
    references = [int(value) for value in re.findall(r'\[(\d+)\]', content)]
    if not content or not references or any(not 1 <= number <= len(items) for number in references):
        raise UpstreamError('digest returned no valid numbered citations')
    title = f'{spec.name or spec.id} {now.astimezone(ZoneInfo(config.app.timezone)):%Y-%m-%d}'
    appendix = '\n'.join(f'[{item["number"]}] {item["source"]}: {item["title"]} {item["url"]}' for item in items)
    markdown = f'# {title}\n\n{content}\n\n## Sources\n\n{appendix}\n'
    report_id = uuid4().hex
    slug = re.sub(r'[^\w-]', '-', title).strip('-')[:60] or 'digest'
    root = config.reports_dir.resolve()
    folder = (root / spec.id).resolve()
    if not folder.is_relative_to(root):
        raise UpstreamError('pipeline report folder escapes reports_dir')
    path = folder / f'{now:%Y%m%d-%H%M%S-%f}-digest-{slug}-{report_id}.md'
    return ReportView(id=report_id, pipeline_id=spec.id, persona_version=persona.version, kind='digest',
        title=title, markdown=markdown, item_ids=[item.id for item, _ in selected], model=result.model,
        usage=result.usage, cost=result.cost, path=str(path), created_at=now)
