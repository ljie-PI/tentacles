"""Workspace initialization and bundled skill installation for wheel installs."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from tentacles.errors import ConfigError
from tentacles.sources.config_store import render_yaml_with_disabled_browser_sources

PKG_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PKG_ROOT.parent.parent
DEFAULT_WORKSPACE_DIR = Path("~/.tentacles")
DEFAULT_SKILLS_TARGET = Path("~/.agents/skills")


def _asset_root() -> Path:
    bundled = PKG_ROOT / "_bundled"
    if (bundled / "config" / "tentacles.yaml").is_file():
        return bundled
    if (REPO_ROOT / "config" / "tentacles.yaml").is_file():
        return REPO_ROOT
    raise ConfigError("bundled workspace templates are missing from this installation")


def _skills_root() -> Path:
    bundled = PKG_ROOT / "_bundled" / "skills"
    if bundled.is_dir():
        return bundled
    repo_skills = REPO_ROOT / "skills"
    if repo_skills.is_dir():
        return repo_skills
    raise ConfigError("bundled skills are missing from this installation")


@dataclass(frozen=True)
class InitOutcome:
    directory: str
    config_path: str
    env_path: str
    created: list[str]
    skipped: list[str]


@dataclass(frozen=True)
class SkillsOutcome:
    target_dir: str
    mode: str
    skills: list[str]


def _copy_if_needed(src: Path, dst: Path, *, force: bool, mode: int | None = None,
                    created: list[str], skipped: list[str]) -> None:
    if dst.exists() and not force:
        skipped.append(str(dst))
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if mode is not None and not dst.exists():
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(src.read_bytes())
    else:
        shutil.copy2(src, dst)
        if mode is not None:
            os.chmod(dst, mode)
    created.append(str(dst))


def _sanitize_init_config(src: Path, dst: Path, *, force: bool,
                          created: list[str], skipped: list[str]) -> None:
    """Copy default config into a fresh workspace with `type: browser` sources disabled
    until the user logs into a headed Chrome profile on that machine."""
    if dst.exists() and not force:
        skipped.append(str(dst))
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = render_yaml_with_disabled_browser_sources(src.read_text(encoding="utf-8"))
    dst.write_text(text, encoding="utf-8")
    created.append(str(dst))


def init_workspace(directory: Path | None = None, *, force: bool = False) -> InitOutcome:
    root = (directory or DEFAULT_WORKSPACE_DIR).expanduser().resolve()
    assets = _asset_root()
    created: list[str] = []
    skipped: list[str] = []

    config_dst = root / "config" / "tentacles.yaml"
    _sanitize_init_config(assets / "config" / "tentacles.yaml", config_dst, force=force,
                          created=created, skipped=skipped)

    prompts_src = assets / "config" / "prompts"
    if prompts_src.is_dir():
        for item in sorted(prompts_src.glob("*.md")):
            _copy_if_needed(item, root / "config" / "prompts" / item.name, force=force,
                            created=created, skipped=skipped)

    env_src = assets / ".env.example"
    env_dst = root / ".env"
    if env_src.is_file():
        # Never overwrite an existing .env even with --force: it holds live user secrets.
        _copy_if_needed(env_src, env_dst, force=False, mode=0o600,
                        created=created, skipped=skipped)

    browser_scripts_src = assets / "scripts" / "browser_sources"
    if browser_scripts_src.is_dir():
        for item in sorted(browser_scripts_src.glob("*.py")):
            _copy_if_needed(item, root / "scripts" / "browser_sources" / item.name, force=force,
                            created=created, skipped=skipped)

    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "reports").mkdir(parents=True, exist_ok=True)
    return InitOutcome(
        directory=str(root),
        config_path=str(config_dst),
        env_path=str(env_dst),
        created=created,
        skipped=skipped,
    )


def install_skills(target_dir: Path | None = None, *, symlink: bool = False) -> SkillsOutcome:
    src_root = _skills_root()
    dst_root = (target_dir or DEFAULT_SKILLS_TARGET).expanduser().resolve()
    dst_root.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    for entry in sorted(src_root.iterdir()):
        if not entry.is_dir() or not (entry / "SKILL.md").is_file():
            continue
        dest = dst_root / entry.name
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        elif dest.is_dir():
            shutil.rmtree(dest)
        if symlink:
            dest.symlink_to(entry.resolve())
        else:
            shutil.copytree(entry, dest)
        installed.append(entry.name)
    if not installed:
        raise ConfigError(f"no skills found under {src_root}")
    return SkillsOutcome(
        target_dir=str(dst_root),
        mode="symlink" if symlink else "copy",
        skills=installed,
    )


def uninstall_skills(target_dir: Path | None = None) -> SkillsOutcome:
    src_root = _skills_root()
    dst_root = (target_dir or DEFAULT_SKILLS_TARGET).expanduser().resolve()
    removed: list[str] = []
    for entry in sorted(src_root.iterdir()):
        if not entry.is_dir() or not (entry / "SKILL.md").is_file():
            continue
        dest = dst_root / entry.name
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
            removed.append(entry.name)
        elif dest.is_dir():
            shutil.rmtree(dest)
            removed.append(entry.name)
    return SkillsOutcome(target_dir=str(dst_root), mode="removed", skills=removed)
