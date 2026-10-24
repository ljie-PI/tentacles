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
