import pytest

from tentacles.errors import ConfigError
from tentacles.service import TentaclesService

FEED = "https://news.example.com/rss"


def src(**kw):
    return {"id": "news", "type": "rss", "url": FEED, "schedule": "30m", **kw}


def test_env_vars_and_defaults_are_interpolated(write_config, open_service):
    path = write_config({"sources": [src(url="${FEED_HOST}/rss", headers={"X-Key": "${KEY:-none}"})]})

    svc = open_service(path, env={"FEED_HOST": "https://env.example.com"})

    spec = svc.config.source("news")
    assert spec.options["url"] == "https://env.example.com/rss"
    assert spec.options["headers"] == {"X-Key": "none"}


def test_dotenv_is_read_but_never_overrides_the_environment(write_config, open_service, tmp_path):
    path = write_config({"sources": [src(url="${HOST}/rss", name="${NAME}")]})
    (tmp_path / ".env").write_text("HOST=https://dotenv.example.com\nNAME=from-dotenv\n")

    svc = open_service(path, env={"NAME": "from-env"})

    spec = svc.config.source("news")
    assert spec.options["url"] == "https://dotenv.example.com/rss"
    assert spec.name == "from-env"


def test_relative_paths_resolve_against_the_config_directory(write_config, open_service, tmp_path):
    svc = open_service(write_config({"app": {"data_dir": "../state"}, "sources": []}))

    assert svc.config.db_path == (tmp_path / "state" / "tentacles.db").resolve()
    assert svc.config.db_path.exists()


@pytest.mark.parametrize("data, where", [
    ({"sources": [src(url="${MISSING}")]}, "missing env MISSING at sources[0].url"),
    ({"sources": [src(), src()]}, "sources[1].id: duplicate source id"),
    ({"sources": [src(type="carrier-pigeon")]}, "sources[0].type: unknown source type 'carrier-pigeon'"),
    ({"sources": [src(url="ftp://x")]}, "sources[0].url"),
    ({"sources": [src(max_age_days="soon")]}, "sources[0].max_age_days"),
    ({"sources": [src(colour="blue")]}, "sources[0].colour"),
    ({"sources": [{"id": "x", "type": "rss", "url": FEED}]}, "sources[0].schedule"),
    ({"app": {"timezone": "Mars/Olympus"}}, "app.timezone"),
    ({"nonsense": 1}, "nonsense"),
])
def test_invalid_config_names_the_offending_location(write_config, data, where):
    with pytest.raises(ConfigError) as exc:
        TentaclesService.open(write_config(data), env={})
    assert where in exc.value.message


def test_missing_or_unparseable_file_is_invalid(write_config, tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        TentaclesService.open(tmp_path / "nope.yaml", env={})
    with pytest.raises(ConfigError, match="invalid YAML"):
        TentaclesService.open(write_config("sources: [unclosed"), env={})


def test_config_path_comes_from_env_when_not_given(write_config, open_service):
    path = write_config({"sources": [src()]}, name="custom.yaml")

    svc = TentaclesService.open(env={"TENTACLES_CONFIG": str(path)})
    try:
        assert svc.check_config().path == str(path.resolve())
        assert svc.check_config().sources == 1
    finally:
        svc.store.close()


@pytest.mark.parametrize("schedule", ["every hour", "1hr", "* * *", "0 8 * * * *", "x 8 * * *"])
def test_malformed_schedules_are_rejected(write_config, schedule):
    with pytest.raises(ConfigError, match=r"sources\[0\].schedule"):
        TentaclesService.open(write_config({"sources": [src(schedule=schedule)]}), env={})


@pytest.mark.parametrize("schedule", ["90s", "30m", "@daily", "0 8,20 * * *", "*/15 9-18 * * 1-5"])
def test_wellformed_schedules_are_accepted(write_config, open_service, schedule):
    assert open_service(write_config({"sources": [src(schedule=schedule)]})).config.sources[0].schedule == schedule


def test_blank_proxy_from_an_unset_env_var_means_no_proxy(write_config, open_service, web):
    import asyncio
    from conftest import rss_feed

    web.serve(FEED, rss_feed({"title": "A", "link": "https://news.example.com/a"}))
    svc = open_service(write_config({"sources": [src(proxy="${HTTP_PROXY:-}")]}))

    assert asyncio.run(svc.fetch_sources())[0].status == "ok"


def test_home_relative_paths_expand(write_config, open_service, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    svc = open_service(write_config({"app": {"data_dir": "~/tentacles-data"}, "sources": []}))

    assert svc.config.db_path == tmp_path / "home" / "tentacles-data" / "tentacles.db"


@pytest.mark.parametrize("text", ["[]", "false", "0", '""'])
def test_falsey_non_mapping_configs_are_invalid(write_config, text):
    with pytest.raises(ConfigError, match="top level must be a mapping"):
        TentaclesService.open(write_config(text), env={})
