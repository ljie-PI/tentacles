"""Interest-label rules adapted from the local TrendRadar reference prompts.

The first extraction favors specific, distinct topics. Updates preserve old
names and make removals explicit; an order list interleaves kept/new priorities.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from tentacles.personas.models import InterestTag

_EXTRACT = """把用户 profile 转成 5–20 个兴趣标签，按 profile 的优先级排序。
标签用简短名称（通常 2–6 字），尽量细分且不重叠；description 应提供具体话题、实体和关键词。
不要凭空扩展用户兴趣。仅输出 JSON：{"tags":[{"tag":"名称","description":"含义"}]}。"""
_UPDATE = """根据新的 profile 更新已有兴趣标签；语义相同必须保留原名，不要只因措辞变化重命名。
仅删除用户明确不再关注的方向，新增真实的新兴趣；尽量保持细分且不重叠，description 提供具体实体和关键词。
最后保留 5–20 个标签。keep 中必须使用旧名；keep/remove 应覆盖所有旧标签。
按新 profile 的优先级给出 order，允许新标签排在旧标签之前。
仅输出 JSON：{"keep":[{"tag":"旧名","description":"含义"}],
"add":[{"tag":"新名","description":"含义"}],"remove":["移除的旧名"],"order":["最终标签名"]}。"""


def tag_messages(profile: str, old: list[InterestTag]) -> tuple[str, list[dict[str, str]]]:
    system = _UPDATE if old else _EXTRACT
    body: dict[str, Any] = {'profile': profile}
    if old:
        body['old_tags'] = [tag.model_dump() for tag in old]
    return system, [{'role': 'user', 'content': json.dumps(body, ensure_ascii=False)}]


def _tag_list(value: Any) -> list[InterestTag]:
    if not isinstance(value, list):
        raise ValueError('tag output must contain lists')
    try:
        return [InterestTag.model_validate(tag) for tag in value]
    except ValidationError:
        raise ValueError('invalid interest tag entry') from None


def parse_tags(data: Any, old: list[InterestTag]) -> list[InterestTag]:
    if not isinstance(data, dict):
        raise ValueError('tag output must be an object')
    old_names = {tag.tag.casefold(): tag.tag for tag in old}
    if old:
        kept = _tag_list(data.get('keep'))
        added = _tag_list(data.get('add'))
        removed = data.get('remove')
        if not isinstance(removed, list) or not all(isinstance(n, str) for n in removed):
            raise ValueError('remove must contain old tag names')
        kept_names = {tag.tag.casefold() for tag in kept}
        removed_names = {name.strip().casefold() for name in removed}
        if (kept_names | removed_names != old_names.keys() or kept_names & removed_names):
            raise ValueError('keep/remove must account for the old tags without renaming them')
        result = kept + added
    else:
        result = _tag_list(data.get('tags'))
    if not 5 <= len(result) <= 20:
        raise ValueError('expected 5–20 interest tags')
    names = [tag.tag.casefold() for tag in result]
    if len(set(names)) != len(names):
        raise ValueError('duplicate interest tags')
    for tag in result:
        tag.tag = old_names.get(tag.tag.casefold(), tag.tag)
    if 'order' in data:
        order = data['order']
        if not isinstance(order, list) or not all(isinstance(n, str) for n in order):
            raise ValueError('order must be a list of tag names')
        order = [name.strip().casefold() for name in order]
        if len(order) != len(names) or set(order) != set(names):
            raise ValueError('order must include every tag exactly once')
        indexed = {tag.tag.casefold(): tag for tag in result}
        result = [indexed[name] for name in order]
    return result
