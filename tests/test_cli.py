"""Contract tests for the CLI adapter: JSON shape, stdout purity, exit codes."""

import json

import pytest
from typer.testing import CliRunner

from conftest import rss_feed
from tentacles.cli import app

FEED = "https://news.example.com/rss"


@pytest.fixture
def cli(web, write_config):
    path = write_config({"sources": [{"id": "news", "type": "rss", "url": FEED, "schedule": "30m"}]})
    runner = CliRunner()

    def run(*args, config=path, json_mode=True):
        argv = ["--config", str(config), *(["--json"] if json_mode else []), *args]
        return runner.invoke(app, argv, obj={"transport": web.transport()}, env={})

    return run


def payload(result):
    assert result.stdout.count("\n") <= 1, "stdout must hold exactly one JSON value"
    return json.loads(result.stdout)


def test_fetch_then_list_and_show_items(cli, web):
    web.serve(FEED, rss_feed({"title": "Hello", "link": "https://news.example.com/h", "description": "hi"}))

    fetched = cli("sources", "fetch")
    listed = cli("items", "list", "--limit", "5")
    item_id = payload(listed)["items"][0]["id"]
    shown = cli("items", "show", item_id)

    assert fetched.exit_code == 0
    assert payload(fetched)[0] | {"run_id": 0, "stats": {}} == {
        "source_id": "news", "run_id": 0, "status": "ok", "new": 1, "dup": 0, "error": None, "stats": {}}
    page = payload(listed)
    assert (page["total"], page["limit"], page["offset"]) == (1, 5, 0)
    assert page["items"][0]["title"] == "Hello"
    assert payload(shown)["summary"] == "hi"


def test_sources_runs_and_config_check(cli, web):
    web.serve(FEED, rss_feed())
    cli("sources", "fetch", "news")

    [source] = payload(cli("sources", "list"))
    [run] = payload(cli("runs", "list", "--kind", "fetch"))
    check = payload(cli("config", "check"))

    assert (source["id"], source["schedule"], source["last_status"]) == ("news", "30m", "ok")
    assert (run["kind"], run["target"], run["status"]) == ("fetch", "news", "ok")
    assert check["ok"] is True and check["sources"] == 1


@pytest.mark.parametrize("args, code, error_code", [
    (("items", "show", "nope"), 3, "not_found"),
    (("sources", "fetch", "nope"), 3, "not_found"),
    (("items", "list", "--since", "whenever"), 2, "invalid"),
])
def test_errors_are_json_objects_with_mapped_exit_codes(cli, args, code, error_code):
    result = cli(*args)

    assert result.exit_code == code
    assert payload(result)["error"]["code"] == error_code
    assert payload(result)["error"]["message"]


def test_invalid_config_exits_2(cli, write_config):
    bad = write_config({"sources": [{"id": "x", "type": "rss", "url": "ftp://x", "schedule": "1h"}]}, name="bad.yaml")

    result = cli("config", "check", config=bad)

    assert result.exit_code == 2
    assert "sources[0].url" in payload(result)["error"]["message"]


def test_logs_go_to_stderr_not_stdout(cli, web):
    web.serve(FEED, "broken", status=500)

    result = cli("-v", "sources", "fetch")

    assert payload(result)[0]["status"] == "error"
    assert "fetch news failed" in result.stderr


def test_human_mode_renders_tables(cli, web):
    web.serve(FEED, rss_feed({"title": "Readable", "link": "https://news.example.com/r"}))
    cli("sources", "fetch")

    result = cli("items", "list", json_mode=False)

    assert result.exit_code == 0
    assert "Readable" in result.stdout


def test_titles_with_brackets_render_literally(cli, web):
    web.serve(FEED, rss_feed({"title": "Ask HN: tips for [/r/programming] [video]",
                              "link": "https://news.example.com/b"}))
    cli("sources", "fetch")
    item_id = payload(cli("items", "list"))["items"][0]["id"]

    listed = cli("items", "list", json_mode=False)
    shown = cli("items", "show", item_id, json_mode=False)

    assert listed.exit_code == 0 and shown.exit_code == 0
    assert "[video]" in shown.stdout and "[/r/programming]" in shown.stdout


@pytest.mark.parametrize("args", [("items", "list", "--limit", "abc"), ("items", "show"), ("bogus",)])
def test_usage_errors_are_json_in_json_mode(cli, args):
    result = cli(*args)

    assert result.exit_code == 2
    assert payload(result)["error"]["code"] == "invalid"


def test_naive_since_is_read_in_the_configured_timezone(cli, web, write_config):
    # 2026-10-24 01:00 上海时间 == 2026-10-23 17:00 UTC
    web.serve(FEED, rss_feed({"title": "Early", "link": "https://news.example.com/e",
                              "pubDate": "Fri, 23 Oct 2026 17:00:00 GMT"}))
    cli("sources", "fetch")

    result = cli("items", "list", "--since", "2026-10-24T00:00")

    assert [i["title"] for i in payload(result)["items"]] == ["Early"]


@pytest.mark.parametrize("limit", ["0", "-1", "501"])
def test_runs_limit_is_bounded(cli, limit):
    result = cli("runs", "list", "--limit", limit)

    assert result.exit_code == 2 and payload(result)["error"]["code"] == "invalid"


@pytest.mark.parametrize("value", ["y", "t", "TRUE", "on"])
def test_json_env_values_also_cover_usage_errors(web, write_config, value):
    path = write_config({"sources": []})

    result = CliRunner().invoke(app, ["--config", str(path), "items", "list", "--limit", "abc"],
                                env={"TENTACLES_JSON": value})

    assert result.exit_code == 2
    assert payload(result)["error"]["code"] == "invalid"


def test_interrupt_in_json_mode_still_yields_one_json_value(cli, web):
    def interrupt(request):
        raise KeyboardInterrupt

    web.handle(FEED, interrupt)

    result = cli("sources", "fetch")

    assert result.exit_code == 130
    assert payload(result)["error"]["code"] == "aborted"


@pytest.mark.parametrize("group", [(), ("config",), ("sources",), ("items",), ("runs",)])
@pytest.mark.parametrize("env_mode", [False, True])
def test_missing_subcommands_are_json_errors(write_config, group, env_mode):
    path = write_config({"sources": []})
    args = ["--config", str(path), *([] if env_mode else ["--json"]), *group]
    result = CliRunner().invoke(app, args, env={"TENTACLES_JSON": "1" if env_mode else "0"})

    assert result.exit_code == 2
    assert payload(result)["error"]["code"] == "invalid"
    assert "Usage:" not in result.stdout


def test_scheduler_commands_are_json_and_show_next_run(cli, web):
    web.serve(FEED, rss_feed())
    [task] = payload(cli('scheduler', 'status'))
    assert task['kind'] == 'fetch' and task['target'] == 'news'
    assert task['next_run_at'] and task['last_run_at'] is None
    assert payload(cli('tick'))[0]['status'] == 'ok'
    assert payload(cli('tick')) == []
    [task] = payload(cli('scheduler', 'status'))
    assert task['last_run_at'] and task['last_status'] == 'ok' and not task['running']
    assert payload(cli('sources', 'list'))[0]['next_run_at'] == task['next_run_at']
    assert 'next' in cli('sources', 'list', json_mode=False).stdout
    assert cli('scheduler', 'status', json_mode=False).exit_code == 0


def test_model_commands_json_and_human_contracts(cli, web, write_config):
    import httpx
    from test_models import model_config, chat_reply, CHAT_URL
    path = write_config(model_config())
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply('Hello [sample]')))
    listed = cli('models', 'list', config=path)
    assert listed.exit_code == 0 and 'private-test-key' not in listed.stdout
    assert {r['name'] for r in payload(listed)} == {'fast', 'decision'}
    tested = cli('models', 'test', 'fast', config=path)
    assert tested.exit_code == 0 and payload(tested)['ok']
    assert payload(tested)['sample'] == 'Hello [sample]'
    human = cli('models', 'test', 'fast', config=path, json_mode=False)
    assert human.exit_code == 0 and '[sample]' in human.stdout
    assert cli('models', 'list', config=path, json_mode=False).exit_code == 0
    missing = cli('models', 'test', 'absent', config=path)
    assert missing.exit_code == 3 and payload(missing)['error']['code'] == 'not_found'


def test_model_probe_failure_remains_structured(cli, write_config):
    from test_models import model_config
    path = write_config(model_config(api_key=''))
    result = cli('models', 'test', 'fast', config=path)
    assert result.exit_code == 0
    assert payload(result)['ok'] is False and 'fast' in payload(result)['error']


def test_persona_cli_reads_text_file_and_stdin_and_exports_snapshot(cli, write_config, tmp_path):
    import yaml
    path = write_config({})
    text = cli('persona', 'set', 'default', '--text', '# First', '--include', 'AI',
               '--include', 'ai', '--exclude', 'Ads', '--skip-tags', '--note', 'created', config=path)
    assert text.exit_code == 0 and payload(text)['version'] == 1
    assert payload(text)['include'] == ['AI']
    profile = tmp_path / 'profile.md'
    profile.write_text('# From file\n中文', encoding='utf-8')
    from_file = cli('persona', 'set', 'default', '--file', str(profile), '--skip-tags', config=path)
    assert from_file.exit_code == 0 and payload(from_file)['profile'] == '# From file\n中文'
    piped = CliRunner().invoke(app, ['--config', str(path), '--json', 'persona', 'set', 'default',
        '--stdin', '--skip-tags', '--remove-exclude', 'ADS'], input='# From stdin\n')
    assert piped.exit_code == 0 and payload(piped)['version'] == 3 and payload(piped)['exclude'] == []
    shown = cli('persona', 'show', 'default', '--version', '1', config=path)
    assert shown.exit_code == 0 and payload(shown)['profile'] == '# First'
    assert payload(cli('persona', 'list', config=path))[0]['version'] == 3
    assert len(payload(cli('persona', 'history', 'default', config=path))) == 3
    exported = cli('persona', 'export', 'default', config=path)
    assert exported.exit_code == 0 and yaml.safe_load(payload(exported))['version'] == 3
    human = cli('persona', 'export', 'default', config=path, json_mode=False)
    assert yaml.safe_load(human.stdout)['profile'] == '# From stdin\n'
    rolled = cli('persona', 'rollback', 'default', '1', config=path)
    assert rolled.exit_code == 0 and payload(rolled)['version'] == 4 and payload(rolled)['source'] == 'rollback'
    assert cli('persona', 'show', 'default', config=path, json_mode=False).exit_code == 0


@pytest.mark.parametrize('args,code', [
    (('show', 'missing'), 3), (('history', 'missing'), 3), (('export', 'missing'), 3),
    (('set', 'new'), 2), (('set', 'new', '--text', 'a', '--stdin'), 2),
    (('set', 'new', '--file', '/does/not/exist'), 2), (('show', 'missing', '--version', '0'), 2),
])
def test_persona_cli_errors_remain_structured(cli, args, code):
    result = cli('persona', *args)
    assert result.exit_code == code
    assert payload(result)['error']['code'] == ('not_found' if code == 3 else 'invalid')


def test_pipeline_and_report_commands_and_item_filters_are_json_contracts(cli, web, write_config):
    import httpx
    from test_pipelines import pipeline_config, three_entries
    from test_models import model_config, chat_reply, CHAT_URL
    path = write_config({**model_config(), **pipeline_config(processor={'type': 'digest'})})
    web.serve(FEED, three_entries())
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply('Research [1]. Weather [2].')))
    assert cli('sources', 'fetch', config=path).exit_code == 0
    filtered = cli('pipelines', 'filter', 'daily', config=path)
    assert filtered.exit_code == 0 and payload(filtered)['status'] == 'ok'
    [pipeline] = payload(cli('pipelines', 'list', config=path))
    assert pipeline['pending'] == 2
    passed = payload(cli('items', 'list', '--pipeline', 'daily', '--passed', config=path))
    assert passed['total'] == 2 and all(i['score'] >= 0.5 and i['stage'] == 'keyword' for i in passed['items'])
    assert payload(cli('items', 'list', '--pipeline', 'daily', '--rejected', config=path))['total'] == 1
    ran = cli('pipelines', 'run', 'daily', config=path)
    assert ran.exit_code == 0 and payload(ran)['status'] == 'ok'
    report_id = payload(ran)['report_id']
    assert payload(cli('reports', 'list', '--pipeline', 'daily', config=path))['total'] == 1
    assert 'Research [1]' in payload(cli('reports', 'show', report_id, config=path))['markdown']
    assert cli('reports', 'show', report_id, config=path, json_mode=False).exit_code == 0
    assert cli('pipelines', 'list', config=path, json_mode=False).exit_code == 0
    assert cli('items', 'list', '--pipeline', 'daily', config=path, json_mode=False).exit_code == 0


@pytest.mark.parametrize('args,code', [
    (('pipelines', 'run', 'missing'), 3), (('pipelines', 'filter', 'missing'), 3),
    (('reports', 'show', 'missing'), 3), (('reports', 'list', '--limit', '0'), 2),
    (('items', 'list', '--passed'), 2),
    (('items', 'list', '--pipeline', 'x', '--passed', '--rejected'), 2),
])
def test_pipeline_usage_and_lookup_errors_are_structured(cli, args, code):
    result = cli(*args)
    assert result.exit_code == code and payload(result)['error']['code'] == ('not_found' if code == 3 else 'invalid')


def test_tick_contract_includes_pipeline_kinds_and_scheduler_pending_counts(cli, web, write_config):
    from test_pipeline_scheduler import scheduled_config
    from test_pipelines import three_entries
    path = write_config(scheduled_config())
    web.serve(FEED, three_entries())
    result = cli('tick', config=path)
    assert result.exit_code == 0
    rows = payload(result)
    assert [r['kind'] for r in rows] == ['fetch', 'filter', 'pipeline']
    assert [r['target'] for r in rows] == ['news', 'daily', 'daily']
    assert rows[0]['source_id'] == 'news' and rows[-1]['pipeline_id'] == 'daily'
    assert payload(cli('tick', config=path)) == []
    tasks = payload(cli('scheduler', 'status', config=path))
    assert {t['kind'] for t in tasks} == {'fetch', 'filter', 'pipeline'}
    assert all(t['pending'] == 0 for t in tasks if t['kind'] != 'fetch')
    [pipeline] = payload(cli('pipelines', 'list', config=path))
    assert pipeline['next_run_at'] and pipeline['filter_next_run_at'] and pipeline['filter_schedule'] == '30m'
    assert cli('scheduler', 'status', config=path, json_mode=False).exit_code == 0
    assert cli('pipelines', 'list', config=path, json_mode=False).exit_code == 0
    assert cli('tick', config=path, json_mode=False).exit_code == 0
    other = scheduled_config()
    other['pipelines'][0]['id'] = 'other'
    other_path = write_config(other, name='other.yaml')
    human = cli('tick', config=other_path, json_mode=False)
    assert human.exit_code == 0 and 'other' in human.stdout and 'filter' in human.stdout


def test_feedback_evolve_preview_apply_pipe_contract(write_config, web, tmp_path):
    from test_models import CHAT_URL, chat_reply, model_config
    from test_pipelines import pipeline_config
    from test_persona_evolution import evolved_content
    config = {**pipeline_config(), **model_config()}
    path = write_config(config)
    runner = CliRunner()

    def run(*args, input=None, json_mode=True):
        return runner.invoke(app, ['-c', str(path), *(['--json'] if json_mode else []), *args],
                             obj={'transport': web.transport()}, input=input, env={})

    web.serve(FEED, rss_feed({'guid': 'one', 'title': 'AI research'}))
    web.serve(CHAT_URL, json.dumps(chat_reply(json.dumps(evolved_content()))),
              headers={'content-type': 'application/json'})
    assert run('sources', 'fetch').exit_code == 0
    item_id = payload(run('items', 'list'))['items'][0]['id']
    result = run('feedback', item_id, '--dislike', '--note', 'Less hype')
    assert result.exit_code == 0 and payload(result)['rating'] == -1
    assert payload(result)['persona'] == 'default'
    for flags in [[], ['--like', '--dislike']]:
        result = run('feedback', item_id, *flags)
        assert result.exit_code == 2 and payload(result)['error']['code'] == 'invalid'
    missing = run('feedback', 'missing', '--like')
    assert missing.exit_code == 3 and payload(missing)['error']['code'] == 'not_found'
    preview = run('persona', 'evolve', 'default', '--instruction', 'More robotics', '--dry-run')
    assert preview.exit_code == 0
    assert payload(preview)['base_version'] == 1 and payload(preview)['feedback_count'] == 1
    applied = run('persona', 'apply', '--proposal', '-', input=preview.stdout)
    assert applied.exit_code == 0 and payload(applied)['source'] == 'llm'
    stale = run('persona', 'apply', '--proposal', '-', input=preview.stdout)
    assert stale.exit_code == 4 and payload(stale)['error']['code'] == 'conflict'
    invalid = run('persona', 'apply', '--proposal', '-', input='not json')
    assert invalid.exit_code == 2 and payload(invalid)['error']['code'] == 'invalid'
    invalid = run('persona', 'apply', '--proposal', str(tmp_path / 'missing.json'))
    assert invalid.exit_code == 2 and payload(invalid)['error']['code'] == 'invalid'
    updated = run('persona', 'evolve', '--instruction', 'More robotics')
    assert updated.exit_code == 0 and payload(updated)['source'] == 'llm'
    # UTF-8 proposal files and human rendering also work.
    preview = run('persona', 'evolve', '--instruction', 'More robotics', '--dry-run')
    proposal_file = tmp_path / 'proposal.json'
    proposal_file.write_text(preview.stdout, encoding='utf-8')
    applied = run('persona', 'apply', '--proposal', str(proposal_file))
    assert applied.exit_code == 0 and payload(applied)['version'] == 4
    human = run('persona', 'evolve', '--instruction', 'More robotics', '--dry-run', json_mode=False)
    assert human.exit_code == 0 and 'Strengthen robotics' in human.stdout
    reevaluated = run('pipelines', 'filter', 'daily', '--reevaluate', '--since', '24h')
    assert reevaluated.exit_code == 0 and payload(reevaluated)['stats']['evaluated'] == 1


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('instruction', ['Update', ''])
def test_evolution_dry_run_never_persists_service_initialization(write_config, web, tmp_path, existing, instruction):
    import asyncio
    from test_models import CHAT_URL, chat_reply, model_config
    from test_persona_evolution import evolved_content
    from tentacles.service import TentaclesService

    original = {**model_config(), 'personas': {'default': {'profile': 'Original'}}}
    path = write_config(original)
    if existing:
        service = TentaclesService.open(path, env={})
        asyncio.run(service.set_persona('default', profile='Stored version', skip_tags=True))
        asyncio.run(service.aclose())
    changed = {**original, 'personas': {**original['personas'], 'new-persona': {'profile': 'New seed'}}}
    write_config(changed)
    web.serve(CHAT_URL, json.dumps(chat_reply(json.dumps(evolved_content()))),
              headers={'content-type': 'application/json'})
    result = CliRunner().invoke(app, ['-c', str(path), '--json', 'persona', 'evolve', 'default',
                                      '--instruction', instruction, '--dry-run'],
                                obj={'transport': web.transport()}, env={})
    assert result.exit_code == (0 if instruction else 2)
    if instruction:
        assert payload(result)['base_version'] == (2 if existing else 1)
    else:
        assert payload(result)['error']['code'] == 'invalid'
    if not existing:
        assert not (tmp_path / 'data').exists()
    write_config(original)
    service = TentaclesService.open(path, env={})
    try:
        assert [p.name for p in service.list_personas()] == ['default']
        assert service.get_persona('default').profile == ('Stored version' if existing else 'Original')
        assert len(service.persona_history('default')) == (2 if existing else 1)
    finally:
        asyncio.run(service.aclose())


@pytest.mark.parametrize(('profile', 'proposed', 'removed', 'added'), [
    ('Original profile', 'AI and robotics first', '-Original profile', '+AI and robotics first'),
    ('-- old rule', '++ new rule', '--- old rule', '+++ new rule'),
])
def test_evolution_human_diff_is_colored_while_json_stays_plain(write_config, web, profile, proposed, removed, added):
    from test_models import CHAT_URL, chat_reply, model_config
    from test_persona_evolution import evolved_content
    path = write_config({**model_config(), 'personas': {'default': {'profile': profile}}})
    web.serve(CHAT_URL, json.dumps(chat_reply(json.dumps(evolved_content(profile=proposed)))),
              headers={'content-type': 'application/json'})
    runner = CliRunner()
    args = ['-c', str(path), 'persona', 'evolve', '--instruction', 'More robotics', '--dry-run']
    result = runner.invoke(app, args, obj={'transport': web.transport()}, color=True,
                           env={'FORCE_COLOR': '1', 'TERM': 'xterm-256color', 'NO_COLOR': None})
    assert result.exit_code == 0
    assert '\x1b[31m' + removed in result.stdout
    assert '\x1b[32m' + added in result.stdout
    no_color = runner.invoke(app, args, obj={'transport': web.transport()}, color=True,
                              env={'FORCE_COLOR': '1', 'NO_COLOR': '1'})
    assert no_color.exit_code == 0 and '\x1b[31m' not in no_color.stdout and '\x1b[32m' not in no_color.stdout
    result = runner.invoke(app, ['--json', *args], obj={'transport': web.transport()}, color=True,
                           env={'FORCE_COLOR': '1', 'TERM': 'xterm-256color', 'NO_COLOR': None})
    assert result.exit_code == 0 and '\x1b[' not in result.stdout
    assert payload(result)['base_version'] == 1


def test_imap_fetch_items_runs_and_tick_json_contract(write_config):
    from test_fetch_imap import FakeIMAP, Mailbox, imap_config, mail
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={1: mail(subject='邮件进展')})})
    path = write_config(imap_config())

    def run(*args):
        return CliRunner().invoke(app, ['-c', str(path), '--json', *args],
                                   obj={'imap_factory': fake.factory},
                                   env={'MAIL_PASSWORD': 'secret-app-password'})

    fetched = run('sources', 'fetch', 'inbox')
    assert fetched.exit_code == 0 and payload(fetched)[0]['new'] == 1
    listed = run('items', 'list', '--source', 'inbox')
    assert listed.exit_code == 0 and payload(listed)['items'][0]['kind'] == 'email'
    item_id = payload(listed)['items'][0]['id']
    detail = run('items', 'show', item_id)
    assert detail.exit_code == 0 and payload(detail)['title'] == '邮件进展'
    assert payload(detail)['meta']['uid'] == 1
    runs = run('runs', 'list', '--kind', 'fetch', '--target', 'inbox')
    assert runs.exit_code == 0 and payload(runs)[0]['status'] == 'ok'
    assert 'secret-app-password' not in runs.stdout
    ticked = run('tick')
    assert ticked.exit_code == 0 and payload(ticked) == []


def test_summarize_pipeline_and_report_json_contract(write_config, web):
    from test_fetch_imap import FakeIMAP, Mailbox, mail
    from test_models import CHAT_URL, chat_reply
    from test_summarize import row, summary_config
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={1: mail(subject='CLI mail')})})
    path = write_config(summary_config())

    def respond(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        return httpx.Response(200, json=chat_reply(json.dumps({'items': [
            row(item['id'], summary='CLI brief', action_required=True,
                deadline='2026-11-01', todos=['Call team']) for item in payload['items']]})))

    import httpx
    web.handle(CHAT_URL, respond)

    def run(*args):
        return CliRunner().invoke(app, ['-c', str(path), '--json', *args],
                                   obj={'imap_factory': fake.factory, 'transport': web.transport()},
                                   env={'MAIL_PASSWORD': 'secret-app-password'})

    assert run('sources', 'fetch', 'inbox').exit_code == 0
    pipelines = run('pipelines', 'list')
    assert pipelines.exit_code == 0 and payload(pipelines)[0]['processor'] == 'summarize'
    result = run('pipelines', 'run', 'mail-summary')
    assert result.exit_code == 0 and payload(result)['status'] == 'ok'
    report_id = payload(result)['report_id']
    listed = run('reports', 'list', '--pipeline', 'mail-summary')
    assert listed.exit_code == 0 and payload(listed)['items'][0]['id'] == report_id
    shown = run('reports', 'show', report_id)
    assert shown.exit_code == 0 and payload(shown)['kind'] == 'summarize'
    assert '- [ ] Call team' in payload(shown)['markdown'] and 'CLI brief' in payload(shown)['markdown']
    assert payload(run('pipelines', 'run', 'mail-summary'))['status'] == 'no_new'
    assert len(web.requests_to(CHAT_URL)) == 1
