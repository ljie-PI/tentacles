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
from pydantic import BaseModel, ValidationError
from rich.console import Console
from rich.markup import escape
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text
try:  # typer >= 0.27 内置 click；更早的版本依赖外部 click
    from typer._click.exceptions import ClickException, UsageError
except ImportError:  # pragma: no cover
    from click.exceptions import ClickException, UsageError  # type: ignore[assignment,no-redef]
from typer.core import TyperGroup

from tentacles.errors import ConfigError, TentaclesError, exit_code
from tentacles.service import TentaclesService
from tentacles.personas.models import PersonaProposal
from tentacles.sources.imap_client import ImapFactory, connect_imap

# 与 click 布尔参数的环境变量取值保持一致
_TRUTHY = {"1", "true", "t", "yes", "y", "on"}


def _json_requested(argv: list[str]) -> bool:
    return "--json" in argv or os.environ.get("TENTACLES_JSON", "").lower() in _TRUTHY


class _JsonErrorsGroup(TyperGroup):
    """In JSON mode, argument/usage errors caught by click also become a JSON error
    object on stdout (exit 2) instead of a usage message on stderr."""

    def parse_args(self, ctx, args):
        json_mode = ctx.find_root().params.get("json_mode") or _json_requested(args)
        if not args and self.no_args_is_help and json_mode:
            raise UsageError("Missing command.", ctx)
        return super().parse_args(ctx, args)

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
config_app = typer.Typer(help="配置", no_args_is_help=True, cls=_JsonErrorsGroup)
sources_app = typer.Typer(help="信息源", no_args_is_help=True, cls=_JsonErrorsGroup)
items_app = typer.Typer(help="条目", no_args_is_help=True, cls=_JsonErrorsGroup)
runs_app = typer.Typer(help="运行记录", no_args_is_help=True, cls=_JsonErrorsGroup)
scheduler_app = typer.Typer(help="调度器", no_args_is_help=True, cls=_JsonErrorsGroup)
models_app = typer.Typer(help="命名模型", no_args_is_help=True, cls=_JsonErrorsGroup)
persona_app = typer.Typer(help="Persona 与版本管理", no_args_is_help=True, cls=_JsonErrorsGroup)
pipelines_app = typer.Typer(help="Pipeline 过滤与运行", no_args_is_help=True, cls=_JsonErrorsGroup)
reports_app = typer.Typer(help="报告", no_args_is_help=True, cls=_JsonErrorsGroup)
app.add_typer(pipelines_app, name="pipelines")
app.add_typer(reports_app, name="reports")
app.add_typer(persona_app, name="persona")
app.add_typer(models_app, name="models")
app.add_typer(scheduler_app, name="scheduler")
app.add_typer(config_app, name="config")
app.add_typer(sources_app, name="sources")
app.add_typer(items_app, name="items")
app.add_typer(runs_app, name="runs")


@dataclass
class State:
    config: Optional[Path]
    json: bool
    transport: Any = None  # tests inject external IO boundaries via ctx.obj
    imap_factory: ImapFactory = connect_imap


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
    ctx.obj = State(config=config, json=json_mode, transport=obj.get("transport"),
                    imap_factory=obj.get("imap_factory", connect_imap))


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


TimeFmt = Callable[[Optional[datetime]], str]


def _run(ctx: typer.Context, op: Callable[[TentaclesService], Awaitable[Any] | Any],
         render: Callable[[Any, Console, TimeFmt], None], *, snapshot: bool = False) -> None:
    """Open the service, run one operation, print its result, map errors to exit codes."""
    state: State = ctx.obj

    tz: list[ZoneInfo] = []

    async def go() -> Any:
        svc = TentaclesService.open(state.config, transport=state.transport, snapshot=snapshot,
                                   imap_factory=state.imap_factory)
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
        logging.getLogger(__name__).error("unexpected operation failure (%s)", type(exc).__name__)
        _fail(state, "error", f"operation failed ({type(exc).__name__})")
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


# ---- pipelines and reports --------------------------------------------------

@pipelines_app.command("list")
def pipelines_list(ctx: typer.Context) -> None:
    """列出 pipeline 及当前待报告数量。"""
    _run(ctx, lambda s: s.list_pipelines(), lambda rows, out, when: out.print(_table(
        "Pipelines", ["id", "persona", "processor", "schedule", "next run", "next filter", "enabled", "pending", "status"],
        [[r.id, r.persona, r.processor, r.schedule, when(r.next_run_at), when(r.filter_next_run_at),
          r.enabled, r.pending, r.last_status] for r in rows])))


def _render_pipeline(result, out: Console, when: TimeFmt) -> None:
    out.print(_table("Pipeline", ["pipeline", "status", "run", "evaluated", "report", "error"],
        [[result.pipeline_id, result.status, result.run_id, result.stats.get('evaluated', 0),
          result.report_id, result.error]]))


@pipelines_app.command("filter")
def pipelines_filter(ctx: typer.Context, pipeline_id: str = typer.Argument(...),
                     reevaluate: bool = typer.Option(False, help="删除窗口内当前版本的评估，再重新过滤"),
                     since: Optional[str] = typer.Option(None, help="覆盖 pipeline 窗口起点；时长或 ISO 时间，须配合 --reevaluate")) -> None:
    """只过滤，不调用报告处理器。"""
    _run(ctx, lambda s: s.filter_pipeline(pipeline_id, reevaluate=reevaluate, since=since), _render_pipeline)


@pipelines_app.command("run")
def pipelines_run(ctx: typer.Context, pipeline_id: str = typer.Argument(...)) -> None:
    """先过滤，再处理尚未报告的条目；单项失败返回 status=error。"""
    _run(ctx, lambda s: s.run_pipeline(pipeline_id), _render_pipeline)


@reports_app.command("list")
def reports_list(ctx: typer.Context, pipeline: Optional[str] = typer.Option(None),
                 limit: int = typer.Option(50), offset: int = typer.Option(0)) -> None:
    """分页列出报告。"""
    _run(ctx, lambda s: s.list_reports(pipeline=pipeline, limit=limit, offset=offset),
         lambda page, out, when: out.print(_table("Reports", ["id", "pipeline", "kind", "created", "title"],
            [[r.id, r.pipeline_id, r.kind, when(r.created_at), r.title] for r in page.items])))


@reports_app.command("show")
def reports_show(ctx: typer.Context, report_id: str = typer.Argument(...)) -> None:
    """查看报告全文及引用元数据。"""
    _run(ctx, lambda s: s.get_report(report_id),
         lambda report, out, when: out.print(Markdown(report.markdown)))


# ---- persona ----------------------------------------------------------------

def _render_persona(view, out: Console, when: TimeFmt) -> None:
    out.print(Text(f"{view.name} v{view.version} · {view.source} · {when(view.created_at)}", style="bold"))
    out.print(Markdown(view.profile))
    out.print(Text(f"include: {', '.join(view.include)}\nexclude: {', '.join(view.exclude)}"))
    if view.tags:
        out.print(_table("Interest tags", ["tag", "description"],
                         [[tag.tag, tag.description] for tag in view.tags]))
    if view.note:
        out.print(Text(view.note))
    if view.tag_error:
        out.print(Text(view.tag_error, style="yellow"))


@persona_app.command("list")
def persona_list(ctx: typer.Context) -> None:
    """列出每个 persona 的最新版本。"""
    _run(ctx, lambda s: s.list_personas(), lambda rows, out, when: out.print(_table(
        "Personas", ["name", "version", "source", "created", "tags", "tag status"],
        [[r.name, r.version, r.source, when(r.created_at), len(r.tags), r.tag_status] for r in rows])))


@persona_app.command("show")
def persona_show(ctx: typer.Context, name: str = typer.Argument(...),
                 version: Optional[int] = typer.Option(None, min=1)) -> None:
    """查看最新或指定版本的完整内容。"""
    _run(ctx, lambda s: s.get_persona(name, version), _render_persona)


@persona_app.command("history")
def persona_history(ctx: typer.Context, name: str = typer.Argument(...)) -> None:
    """按版本号倒序列出历史。"""
    _run(ctx, lambda s: s.persona_history(name), lambda rows, out, when: out.print(_table(
        "Persona history", ["version", "source", "created", "note", "tag status"],
        [[r.version, r.source, when(r.created_at), r.note, r.tag_status] for r in rows])))


@persona_app.command("export")
def persona_export(ctx: typer.Context, name: str = typer.Argument(...),
                   version: Optional[int] = typer.Option(None, min=1)) -> None:
    """导出完整 YAML 版本快照；JSON 模式返回一个包含 YAML 的字符串。"""
    _run(ctx, lambda s: s.export_persona(name, version),
         lambda content, out, when: typer.echo(content, nl=False))


@persona_app.command("set")
def persona_set(
    ctx: typer.Context,
    name: str = typer.Argument(...),
    text: Optional[str] = typer.Option(None, "--text", help="替换 profile 的文本"),
    profile_file: Optional[Path] = typer.Option(None, "--file", help="UTF-8 profile 文件；- 表示标准输入"),
    stdin: bool = typer.Option(False, "--stdin", help="从标准输入读取 profile"),
    include: Optional[list[str]] = typer.Option(None, "--include", help="增加 include 关键词，可重复"),
    remove_include: Optional[list[str]] = typer.Option(None, "--remove-include", help="删除 include 关键词，可重复"),
    exclude: Optional[list[str]] = typer.Option(None, "--exclude", help="增加 exclude 关键词，可重复"),
    remove_exclude: Optional[list[str]] = typer.Option(None, "--remove-exclude", help="删除 exclude 关键词，可重复"),
    note: Optional[str] = typer.Option(None, help="版本备注"),
    skip_tags: bool = typer.Option(False, help="跳过标签模型调用，保留已有标签"),
    expected_version: Optional[int] = typer.Option(None, min=0, help="可选并发校验；0 表示预期名称不存在"),
) -> None:
    """修改或创建 persona；成功后写成新版本。"""
    def apply(service: TentaclesService):
        if sum((text is not None, profile_file is not None, stdin)) > 1:
            raise ConfigError('choose only one of --text, --file or --stdin')
        profile = text
        if stdin or profile_file == Path('-'):
            profile = sys.stdin.read()
        elif profile_file is not None:
            try:
                profile = profile_file.read_text(encoding='utf-8')
            except (OSError, UnicodeError):
                raise ConfigError(f'cannot read UTF-8 profile file {profile_file}') from None
        return service.set_persona(name, profile=profile, include_add=include, include_remove=remove_include,
                                   exclude_add=exclude, exclude_remove=remove_exclude,
                                   note=note, skip_tags=skip_tags, expected_version=expected_version)
    _run(ctx, apply, _render_persona)


def _render_evolution(result, out: Console, when: TimeFmt) -> None:
    if not isinstance(result, PersonaProposal):
        _render_persona(result, out, when)
        return
    out.print(Text(f"{result.name} · base v{result.base_version} · {result.feedback_count} feedback", style="bold"))
    out.print(Text(result.summary))
    diff = Text()
    for index, line in enumerate(result.diff.splitlines(keepends=True)):
        style = ('bold cyan' if index < 2 or line.startswith('@@ ') else
                 'green' if line.startswith('+') else 'red' if line.startswith('-') else '')
        diff.append(line, style=style)
    out.print(diff)
    out.print(Text(f"include: {', '.join(result.include)}\nexclude: {', '.join(result.exclude)}"))
    out.print(_table("Proposed tags", ["tag", "description"],
                     [[tag.tag, tag.description] for tag in result.tags]))


@persona_app.command("evolve")
def persona_evolve(ctx: typer.Context, name: str = typer.Argument('default'),
                   instruction: str = typer.Option('', help="一句话指令；也可只使用近期反馈"),
                   dry_run: bool = typer.Option(False, help="只返回提案，不写库；模型调用可能产生费用"),
                   use_feedback: bool = typer.Option(True, "--feedback/--no-feedback", help="是否参考近期反馈"),
                   expected_version: Optional[int] = typer.Option(None, min=0, help="可选基准版本并发校验")) -> None:
    """根据指令和/或反馈改写 persona；默认直接应用，--dry-run 仅预览。"""
    _run(ctx, lambda s: s.evolve_persona(name, instruction=instruction, dry_run=dry_run, use_feedback=use_feedback, expected_version=expected_version),
         _render_evolution, snapshot=dry_run)


@persona_app.command("apply")
def persona_apply(ctx: typer.Context,
                  proposal: Path = typer.Option(..., help="UTF-8 JSON 提案文件；- 从标准输入读取")) -> None:
    """应用提案；基准版本过期返回 conflict，不会覆盖其他修改。"""
    def apply(service: TentaclesService):
        try:
            text = sys.stdin.read() if proposal == Path('-') else proposal.read_text(encoding='utf-8')
            value = json.loads(text)
        except (OSError, UnicodeError, ValueError):
            raise ConfigError('cannot read a valid UTF-8 JSON proposal') from None
        return service.apply_persona(value)
    _run(ctx, apply, _render_persona)


@persona_app.command("rollback")
def persona_rollback(ctx: typer.Context, name: str = typer.Argument(...),
                     version: int = typer.Argument(..., min=1),
                     note: Optional[str] = typer.Option(None),
                     expected_version: Optional[int] = typer.Option(None, min=0, help="可选当前版本并发校验")) -> None:
    """复制旧内容为新版本，不删除历史，也不重新抽取标签。"""
    _run(ctx, lambda s: s.rollback_persona(name, version, note=note, expected_version=expected_version), _render_persona)


# ---- models -----------------------------------------------------------------

@models_app.command("list")
def models_list(ctx: typer.Context) -> None:
    """列出命名模型；只显示 key 是否已配置，不输出密钥。"""
    _run(ctx, lambda s: s.list_models(), lambda rows, out, when: out.print(_table(
        "Models", ["name", "provider", "kind", "model", "base URL", "key configured"],
        [[r.name, r.provider, r.kind, r.model, r.base_url, r.key_configured] for r in rows])))


@models_app.command("test")
def models_test(ctx: typer.Context, name: str = typer.Argument(..., help="配置中的模型名")) -> None:
    """实际调用模型并返回可用性、延迟、样例、用量；失败时 ok=false。"""
    def render(result, out: Console, when: TimeFmt) -> None:
        out.print(Text(f"{result.name}: {'ok' if result.ok else 'error'} ({result.latency_ms:.0f} ms)"))
        out.print(Text(result.sample if result.ok else result.error or ''))
    _run(ctx, lambda s: s.test_model(name), render)


# ---- sources ----------------------------------------------------------------

@sources_app.command("list")
def sources_list(ctx: typer.Context) -> None:
    """列出信息源及其抓取状态。"""
    _run(ctx, lambda s: s.list_sources(), lambda rows, out, when: out.print(_table(
        "Sources", ["id", "type", "schedule", "enabled", "last fetch", "next run", "status", "items", "error"],
        [[r.id, r.type, r.schedule, r.enabled, when(r.last_fetch_at), when(r.next_run_at), r.last_status,
          r.total_items, r.last_error] for r in rows])))


@sources_app.command("fetch")
def sources_fetch(ctx: typer.Context,
                  ids: Optional[list[str]] = typer.Argument(None, help="信息源 id；省略则抓取全部启用的源")) -> None:
    """立即抓取信息源。单个源失败不影响其他源，结果中 status=error。"""
    _run(ctx, lambda s: s.fetch_sources(ids or None), lambda rows, out, when: out.print(_table(
        "Fetch", ["source", "status", "new", "dup", "error"],
        [[r.source_id, r.status, r.new, r.dup, r.error] for r in rows])))


# ---- scheduler --------------------------------------------------------------

@app.command("tick")
def tick(ctx: typer.Context) -> None:
    """执行所有到期任务；被占用的任务返回 busy。"""
    _run(ctx, lambda s: s.tick(), lambda rows, out, when: out.print(_table(
        "Tick", ["kind", "target", "status", "new", "dup", "report", "error"],
        [[r.kind, r.target, r.status, r.new if r.kind == 'fetch' else None,
          r.dup if r.kind == 'fetch' else None, r.report_id, r.error] for r in rows])))


@scheduler_app.command("status")
def scheduler_status(ctx: typer.Context) -> None:
    """列出任务频率、上次/下次运行时间、运行状态及最近错误。"""
    _run(ctx, lambda s: s.scheduler_status(), lambda rows, out, when: out.print(_table(
        "Scheduler", ["kind", "target", "schedule", "last run", "next run", "running", "pending", "status", "error"],
        [[r.kind, r.target, r.schedule, when(r.last_run_at), when(r.next_run_at), r.running,
          r.pending, r.last_status, r.last_error] for r in rows])))


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
    pipeline: Optional[str] = typer.Option(None, help="按 pipeline 的最新评估筛选"),
    passed: bool = typer.Option(False, "--passed", help="只看通过的条目，需要 --pipeline"),
    rejected: bool = typer.Option(False, "--rejected", help="只看淘汰的条目，需要 --pipeline"),
) -> None:
    """列出条目，按发布时间倒序。"""
    def query(service: TentaclesService):
        if passed and rejected:
            raise ConfigError('choose either --passed or --rejected')
        return service.list_items(sources=source, since=since, until=until, search=search,
                                  limit=limit, offset=offset, pipeline=pipeline,
                                  passed=True if passed else False if rejected else None)

    def render(page, out: Console, when: TimeFmt) -> None:
        columns = ["id", "source", "published", "title"]
        if pipeline is not None:
            columns += ["score", "tags", "reason", "stage"]
        rows = []
        for item in page.items:
            row = [item.id, item.source_id, when(item.published or item.fetched_at), item.title]
            if pipeline is not None:
                row += [item.score, ', '.join(item.tags), item.reason, item.stage]
            rows.append(row)
        out.print(_table(f"Items ({page.offset + 1}-{page.offset + len(page.items)} / {page.total})", columns, rows))
    _run(ctx, query, render)


@items_app.command("show")
def items_show(ctx: typer.Context, item_id: str = typer.Argument(..., help="条目 id")) -> None:
    """查看条目详情（含正文）。"""
    def render(d, out: Console, when: TimeFmt) -> None:
        out.print(Text(d.title, style="bold"))
        out.print(f"{d.source_id} · {when(d.published or d.fetched_at)} · {d.url}\n", markup=False)
        out.print(d.content or d.summary, markup=False)
    _run(ctx, lambda s: s.get_item(item_id), render)


@items_app.command('feedback')
def items_feedback(ctx: typer.Context, item_id: str = typer.Argument(...),
                   persona: Optional[str] = typer.Option(None)) -> None:
    """查看该条目在指定画像下的最新反馈，未反馈时 JSON 为 null。"""
    _run(ctx, lambda service: service.get_feedback(item_id, persona=persona),
         lambda value, out, when: out.print(Text('尚无反馈') if value is None else _table(
             'Latest feedback', ['item', 'persona', 'rating', 'note', 'created'],
             [[value.item_id, value.persona, value.rating, value.note, when(value.created_at)]])))


@app.command("feedback")
def feedback(ctx: typer.Context, item_id: str = typer.Argument(...),
             like: bool = typer.Option(False, "--like"), dislike: bool = typer.Option(False, "--dislike"),
             persona: Optional[str] = typer.Option(None), note: str = typer.Option('')) -> None:
    """对条目点赞或点踩，并可附备注。"""
    def submit(service: TentaclesService):
        if like == dislike:
            raise ConfigError('choose exactly one of --like or --dislike')
        return service.submit_feedback(item_id, rating=1 if like else -1, persona=persona, note=note)
    _run(ctx, submit, lambda result, out, when: out.print(_table(
        "Feedback", ["item", "persona", "rating", "note"],
        [[result.item_id, result.persona, result.rating, result.note]])))


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


@app.command('serve')
def serve(
    ctx: typer.Context,
    host: Optional[str] = typer.Option(None, help='覆盖 server.host（默认仅本机）'),
    port: Optional[int] = typer.Option(None, min=1, max=65535, help='覆盖 server.port'),
    scheduler: Optional[bool] = typer.Option(None, '--scheduler/--no-scheduler', help='覆盖后台调度器开关'),
) -> None:
    """同时启动 REST API、可选调度器和已构建前端；Ctrl-C 关闭。"""
    import uvicorn
    from tentacles.config import load_config
    from tentacles.server_config import ServerSpec
    from tentacles.server import create_app, warn_if_exposed
    state: State = ctx.obj
    try:
        config = load_config(state.config)
        overrides = {key: value for key, value in {'host': host, 'port': port, 'scheduler': scheduler}.items()
                     if value is not None}
        try:
            config.server = ServerSpec.model_validate({**config.server.model_dump(), **overrides})
        except ValidationError:
            raise ConfigError('invalid server options') from None
        warn_if_exposed(config)
        uvicorn.run(create_app(config, transport=state.transport, imap_factory=state.imap_factory),
                    host=config.server.host, port=config.server.port, access_log=False, log_level='warning')
    except TentaclesError as exc:
        _fail(state, exc.code, exc.message)
    except (KeyboardInterrupt, asyncio.CancelledError):
        _fail(state, 'aborted', 'interrupted')
    except SystemExit as exc:
        if exc.code:
            _fail(state, 'error', 'server failed to start')
    except Exception as exc:
        logging.getLogger(__name__).error('server failed (%s)', type(exc).__name__)
        _fail(state, 'error', f'server failed ({type(exc).__name__})')
    if state.json:
        typer.echo(json.dumps({'status': 'stopped'}))


if __name__ == "__main__":
    app()
