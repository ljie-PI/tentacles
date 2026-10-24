"""Command line interface."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from tentacles.cluster import cluster_articles
from tentacles.config import get_settings, load_feeds
from tentacles.dedup import dedup_articles
from tentacles.llm.client import LLMClient, LLMError
from tentacles.llm.report import generate_daily_report, generate_deep_report, slugify
from tentacles.sources import fetch_feeds
from tentacles.store import Store

app = typer.Typer(help="tentacles — 抓取多源 feed 并生成新闻日报 / 深度报道")
console = Console()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
# 第三方库的请求日志太吵
for _noisy in ("httpx", "httpcore", "feedparser"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


def _save_report(content: str, filename: str) -> Path:
    settings = get_settings()
    out_dir = Path(settings.reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename
    path.write_text(content, encoding="utf-8")
    return path


@app.command()
def fetch() -> None:
    """抓取全部配置的 feed，去重后入库。"""
    feeds = load_feeds()
    console.print(f"[bold]{len(feeds)}[/bold] feeds to fetch…")
    articles = asyncio.run(fetch_feeds(
        feeds, timeout=get_settings().fetch_timeout
    ))
    articles = dedup_articles(articles)
    store = Store(get_settings().db_path)
    try:
        new, dup = store.upsert(articles)
    finally:
        store.close()
    console.print(f"已入库 [green]{new}[/green] 篇新文章，跳过 [yellow]{dup}[/yellow] 篇重复")


@app.command()
def daily(hours: int = typer.Option(None, help="覆盖最近多少小时的文章，默认取配置值")) -> None:
    """对最近的文章聚类并生成 LLM 日报（重大事件同时生成深度报道）。"""
    settings = get_settings()
    store = Store(settings.db_path)
    articles = store.articles_last_n_hours(hours or settings.daily_hours)
    store.close()
    if not articles:
        console.print("[red]没有可用文章，先运行 `tentacles fetch`[/red]")
        raise typer.Exit(1)

    clusters = cluster_articles(dedup_articles(articles))
    major = [c for c in clusters if c.is_major]
    console.print(f"{len(articles)} 篇文章聚为 [bold]{len(clusters)}[/bold] 个事件，"
                  f"其中重大事件 [bold red]{len(major)}[/bold red] 个")

    try:
        llm = LLMClient()
    except LLMError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    date_str = datetime.now().strftime("%Y-%m-%d")

    report = asyncio.run(generate_daily_report(clusters, llm))
    daily_path = _save_report(report, f"{date_str}-daily.md")
    console.print(f"日报已生成 → [bold]{daily_path}[/bold]")

    for i, cluster in enumerate(major[:3], 1):
        console.print(f"生成深度报道 ({i}/{len(major[:3])}): {cluster.title()}")
        deep = asyncio.run(generate_deep_report(cluster, llm))
        deep_path = _save_report(deep, f"{date_str}-deep-{slugify(cluster.title())}.md")
        console.print(f"  → [bold]{deep_path}[/bold]")


@app.command("list")
def list_feeds() -> None:
    """列出配置的 feed 源。"""
    table = Table(title="Feeds")
    table.add_column("名称")
    table.add_column("URL")
    for name, url in load_feeds().items():
        table.add_row(name, url)
    console.print(table)


if __name__ == "__main__":
    app()
