"""Settings (env-driven) and feed list loading."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TENTACLES_", env_file=".env", extra="ignore"
    )

    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    db_path: Path = ROOT_DIR / "data" / "tentacles.db"
    reports_dir: Path = ROOT_DIR / "reports"
    feeds_file: Path = ROOT_DIR / "feeds.yaml"

    fetch_timeout: float = 30.0
    daily_hours: int = 24        # 日报覆盖最近多少小时的文章


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_feeds(path: Path | None = None) -> dict[str, str]:
    """feeds.yaml -> {feed 名称: feed URL}"""
    path = path or get_settings().feeds_file
    if not path.exists():
        raise FileNotFoundError(f"feeds file not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {name: str(url) for name, url in data.get("feeds", {}).items()}
