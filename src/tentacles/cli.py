"""CLI adapter over the Service facade.

With --json (or TENTACLES_JSON=1) stdout carries exactly one JSON value: the
result on success, or {"error": {"code", "message"}} on failure with an exit
code mapped from the error code. Logs always go to stderr.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

import typer
from pydantic import BaseModel
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text
try:  # typer >= 0.27 内置 click；更早的版本依赖外部 click
    from typer._click.exceptions import ClickException, UsageError
except ImportError:  # pragma: no cover
    from click.exceptions import ClickException, UsageError  # type: ignore[no-redef]
from typer.core import TyperGroup

from tentacles.errors import TentaclesError, exit_code
from tentacles.service import TentaclesService

# 与 click 布尔参数的环境变量取值保持一致
_TRUTHY = {"1", "true", "t", "yes", "y", "on"}


def _json_requested(argv: list[str]) -> bool:
    return "--json" in argv or os.environ.get("TENTACLES_JSON", "").lower() in _TRUTHY


class _JsonErrorsGroup(TyperGroup):
    """In JSON mode, argument/usage errors caught by click also become a JSON error
    object on stdout (exit 2) instead of a usage message on stderr."""

    def main(self, args=None, prog_name=None, complete_var=None, standalone_mode=True, **extra):
        if not _json_requested(list(sys.argv[1:] if args is None else args)):
            return super().main(args, prog_name, complete_var, standalone_mode, **extra)
        try:
            rv = super().main(args, prog_name, complete_var, standalone_mode=False, **extra)
        except UsageError as exc:
            _emit_error("invalid", exc.format_message(), 2)
        except ClickException as exc:
            _emit_error("error", exc.format_message(), exc.exit_code)
        except (typer.Abort, KeyboardInterrupt):
            _emit_error("aborted", "interrupted", 130)
        sys.exit(rv if isinstance(rv, int) else 0)


def _emit_error(code: str, message: str, status: int) -> None:
    typer.echo(json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False))
    sys.exit(status)


app = typer.Typer(help="tentacles — 个人信息与新闻管理系统", no_args_is_help=True,
                  pretty_exceptions_enable=False, cls=_JsonErrorsGroup)
config_app = typer.Typer(help="配置", no_args_is_help=True)
sources_app = typer.Typer(help="信息源", no_args_is_help=True)
items_app = typer.Typer(help="条目", no_args_is_help=True)
runs_app = typer.Typer(help="运行记录", no_args_is_help=True)
app.add_typer(config_app, name="config")
app.add_typer(sources_app, name="sources")
app.add_typer(items_app, name="items")
app.add_typer(runs_app, name="runs")


@dataclass
class State:
    config: Optional[Path]
    json: bool
    transport: Any = None  # tests inject an httpx transport via ctx.obj


@app.callback()
def main(
    ctx: typer.Context,
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径（默认 $TENTACLES_CONFIG 或 config/tentacles.yaml）"),
    json_mode: bool = typer.Option(False, "--json", envvar="TENTACLES_JSON", help="只向 stdout 输出一个 JSON 值"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="输出 INFO 日志到 stderr"),
) -> None:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s", force=True)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    obj = ctx.obj if isinstance(ctx.obj, dict) else {}
    ctx.obj = State(config=config, json=json_mode, transport=obj.get("transport"))


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


TimeFmt = Callable[[Optional[datetime]], str]


def _run(ctx: typer.Context, op: Callable[[TentaclesService], Awaitable[Any] | Any],
         render: Callable[[Any, Console, TimeFmt], None]) -> None:
    """Open the service, run one operation, print its result, map errors to exit codes."""
    state: State = ctx.obj

    tz: list[ZoneInfo] = []

    async def go() -> Any:
        svc = TentaclesService.open(state.config, transport=state.transport)
        tz.append(ZoneInfo(svc.config.app.timezone))  # 人类可读输出按配置的时区展示时间
        try:
            result = op(svc)
            return await result if asyncio.iscoroutine(result) else result
        finally:
            await svc.aclose()

    out = Console(soft_wrap=True)
    try:
        result = asyncio.run(go())
    except TentaclesError as exc:
        _fail(state, exc.code, exc.message)
    except KeyboardInterrupt:
        _fail(state, "aborted", "interrupted")
    except Exception as exc:  # noqa: BLE001 — unexpected bugs still yield a structured error
        logging.getLogger(__name__).exception("unexpected error")
        _fail(state, "error", f"{type(exc).__name__}: {exc}")
    if state.json:
        typer.echo(json.dumps(_jsonable(result), ensure_ascii=False))
    else:
        render(result, out, lambda dt: _time(dt, tz[0]))


def _fail(state: State, code: str, message: str) -> None:
    if state.json:
        typer.echo(json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False))
    else:
        Console(stderr=True).print(f"[red]error[/red] ({code}): {escape(message)}")
    raise typer.Exit(exit_code(code))


def _table(title: str, columns: list[str], rows: list[list[Any]]) -> Table:
    t = Table(title=title)
    for c in columns:
        t.add_column(c)
    for r in rows:
        t.add_row(*(Text("" if v is None else str(v)) for v in r))  # 外部文本不当作 markup
    return t


def _time(dt: Optional[datetime], tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M") if dt else ""


# ---- config -----------------------------------------------------------------

@config_app.command("check")
def config_check(ctx: typer.Context) -> None:
    """校验配置文件。"""
    _run(ctx, lambda s: s.check_config(),
         lambda r, out, when: out.print(f"[green]ok[/green] {escape(r.path)}（{r.sources} 个信息源）"))


# ---- sources ----------------------------------------------------------------

@sources_app.command("list")
def sources_list(ctx: typer.Context) -> None:
    """列出信息源及其抓取状态。"""
    _run(ctx, lambda s: s.list_sources(), lambda rows, out, when: out.print(_table(
        "Sources", ["id", "type", "schedule", "enabled", "last fetch", "status", "items", "error"],
        [[r.id, r.type, r.schedule, r.enabled, when(r.last_fetch_at), r.last_status,
          r.total_items, r.last_error] for r in rows])))


@sources_app.command("fetch")
def sources_fetch(ctx: typer.Context,
                  ids: Optional[list[str]] = typer.Argument(None, help="信息源 id；省略则抓取全部启用的源")) -> None:
    """立即抓取信息源。单个源失败不影响其他源，结果中 status=error。"""
    _run(ctx, lambda s: s.fetch_sources(ids or None), lambda rows, out, when: out.print(_table(
        "Fetch", ["source", "status", "new", "dup", "error"],
        [[r.source_id, r.status, r.new, r.dup, r.error] for r in rows])))


# ---- items ------------------------------------------------------------------

@items_app.command("list")
def items_list(
    ctx: typer.Context,
    source: Optional[list[str]] = typer.Option(None, "--source", "-s", help="按信息源筛选，可重复"),
    since: Optional[str] = typer.Option(None, help="起始时间：时长（如 24h）或 ISO 时间"),
    until: Optional[str] = typer.Option(None, help="截止时间：时长或 ISO 时间"),
    search: Optional[str] = typer.Option(None, "--search", "-q", help="在标题、摘要、作者中搜索"),
    limit: int = typer.Option(50, help="每页条数（1–500）"),
    offset: int = typer.Option(0, help="偏移量"),
) -> None:
    """列出条目，按发布时间倒序。"""
    _run(ctx, lambda s: s.list_items(sources=source, since=since, until=until, search=search,
                                     limit=limit, offset=offset),
         lambda page, out, when: out.print(_table(
             f"Items ({page.offset + 1}-{page.offset + len(page.items)} / {page.total})",
             ["id", "source", "published", "title"],
             [[i.id, i.source_id, when(i.published or i.fetched_at), i.title] for i in page.items])))


@items_app.command("show")
def items_show(ctx: typer.Context, item_id: str = typer.Argument(..., help="条目 id")) -> None:
    """查看条目详情（含正文）。"""
    def render(d, out: Console, when: TimeFmt) -> None:
        out.print(Text(d.title, style="bold"))
        out.print(f"{d.source_id} · {when(d.published or d.fetched_at)} · {d.url}\n", markup=False)
        out.print(d.content or d.summary, markup=False)
    _run(ctx, lambda s: s.get_item(item_id), render)


# ---- runs -------------------------------------------------------------------

@runs_app.command("list")
def runs_list(ctx: typer.Context,
              kind: Optional[str] = typer.Option(None, help="运行类型，如 fetch"),
              target: Optional[str] = typer.Option(None, help="目标，如信息源 id"),
              limit: int = typer.Option(50, help="条数")) -> None:
    """列出最近的运行记录。"""
    _run(ctx, lambda s: s.list_runs(kind=kind, target=target, limit=limit), lambda rows, out, when: out.print(_table(
        "Runs", ["id", "kind", "target", "status", "started", "stats", "error"],
        [[r.id, r.kind, r.target, r.status, when(r.started_at), json.dumps(r.stats, ensure_ascii=False),
          r.error] for r in rows])))


if __name__ == "__main__":
    app()
