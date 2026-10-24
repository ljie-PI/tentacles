"""LLM-powered report generation: daily digest + in-depth reports."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from jinja2 import Template

from tentacles.cluster import Cluster
from tentacles.llm.client import LLMClient
from tentacles.models import Article

_DAILY_SYSTEM = """\
你是一名专业的新闻编辑。你会拿到最近一段时间从多个来源抓取的去重文章列表，\
请基于这些文章写一份中文新闻日报。要求：
1. 严格遵守给定的编号引用方式（[编号]），不要编造来源；
2. 只依据提供的文章内容，不要加入你自己的知识或推测；
3. 按重要程度排序，先给出「今日要闻」3~7 条一句话摘要，再分板块（如国际、科技、财经等）给详细一点的段落；
4. 若能识别持续发酵的重大事件，单独列「重点关注」段落简述各方说法的分歧与共识；
5. 输出 Markdown。"""

_DEEP_SYSTEM = """\
你是一名 investigative 风格的深度报道记者。你会拿到同一事件的、来自多个独立来源的文章。\
请基于这些材料写一篇结构化的中文深度报道：
1. 导语：用 3~5 句话概括事件、最新进展与为什么重要；
2. 正文分节（背景 / 各方说法 / 数据与疑点 / 影响与后续）；
3. 每个事实性陈述都标注来源编号（如 来源1、来源2 对应文章列表中的编号），来源间有矛盾时明确指出；
4. 结尾列出「尚待确认的问题」；
5. 不使用材料之外的知识；输出 Markdown。"""

_DAILY_USER_TEMPLATE = Template("""\
以下是需要整理的文章（编号-来源-标题-摘要）：
{% for a in articles %}
[{{ loop.index }}] ({{ a.source }}) {{ a.title }}
{{ a.summary[:600] or "（无摘要）" }}
{% endfor %}
""")

_DEEP_USER_TEMPLATE = Template("""\
事件标题：{{ title }}

相关文章（编号-来源-标题-摘要）：
{% for a in articles %}
[来源{{ loop.index }}] ({{ a.source }}) {{ a.title }}
{{ a.summary[:1200] or "（无摘要）" }}
{% endfor %}
""")


def _format_daily_markdown(date_str: str, articles: list[Article],
                           major_titles: list[str], report: str) -> str:
    header = f"# 新闻日报 {date_str}\n\n"
    lines = [
        f"> 共基于 {len(articles)} 篇去重后的文章，来源 "
        f"{len({a.source for a in articles})} 个。\n"
    ]
    if major_titles:
        lines.append("**今日重点事件**：" + "；".join(major_titles) + "\n")
    lines.append("---\n")
    return header + "\n".join(lines) + "\n" + report + "\n"


async def generate_daily_report(clusters: list[Cluster], llm: LLMClient,
                                *, max_articles: int = 60) -> str:
    articles: list[Article] = []
    for c in clusters:
        articles.extend(c.members)
    articles = articles[:max_articles]

    user = _DAILY_USER_TEMPLATE.render(articles=articles)
    daily = await llm.chat(_DAILY_SYSTEM, user, max_tokens=4096, temperature=0.35)
    date_str = datetime.now().strftime("%Y-%m-%d")
    majors = [c.title() for c in clusters if c.is_major]
    return _format_daily_markdown(date_str, articles, majors, daily)


async def generate_deep_report(cluster: Cluster, llm: LLMClient) -> str:
    user = _DEEP_USER_TEMPLATE.render(
        title=cluster.title(), articles=cluster.members
    )
    deep = await llm.chat(_DEEP_SYSTEM, user, max_tokens=8192, temperature=0.3)
    title_clean = re.sub(r"[\\/:*?\"<>|]", "-", cluster.title())[:60]
    header = f"# 深度报道：{cluster.title()}\n\n> 基于来自 {len(cluster.sources)} 个来源的 {cluster.size} 篇文章。\n\n---\n\n"
    return header + deep + "\n"


def slugify(text: str, max_len: int = 40) -> str:
    text = re.sub(r"\s+", "-", text.strip())
    return re.sub(r"[^\w\u4e00-\u9fff-]", "", text)[:max_len].strip("-") or "untitled"
