"""Headed-only Chrome session for `type: browser` sources.

One replay at a time may drive a given Chrome profile: the window and its focus
are shared with the user. The session attaches to an already running headed
Chrome over CDP (so the logged-in cookies are reused), verifies that the
attached instance really runs the configured profile, and opens one isolated
work tab. Only that tab is ever closed — user tabs and the browser itself stay
untouched, and no code path can start a headless browser.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from tentacles.config import DEFAULT_CHROME_PROFILE, AppConfig, BrowserSpec
from tentacles.diagnostics import clip
from tentacles.errors import ConfigError, UpstreamError

log = logging.getLogger(__name__)

CDP_CONNECT_TIMEOUT_MS = 15_000
LAUNCH_TIMEOUT_S = 30.0
VERSION_PAGE_TIMEOUT_MS = 10_000
A11Y_SNAPSHOT_CHARS = 4096
# chrome://version renders the raw argv, so a profile name may contain spaces and
# runs up to the next flag (or the end of the command line).
_PROFILE_ARG = re.compile(r"--profile-directory=(.*?)(?=\s--|\n|$)")
_USER_DATA_ARG = re.compile(r"--user-data-dir=(.*?)(?=\s--|\n|$)")
_HEADLESS_ARG = re.compile(r"(?:^|\s)--headless(?:=|\s|$)")
# A fresh Chrome install, wherever the user keeps it.
_CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)
_CHROME_COMMANDS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")

# Focus mutex keyed by (CDP endpoint, profile): one work tab per Chrome window.
_locks: dict[tuple[str, str], asyncio.Lock] = {}


def focus_lock(cdp_url: str, profile: str) -> asyncio.Lock:
    """The serial focus guard every replay of this endpoint/profile acquires."""
    key = (cdp_url, profile)
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = asyncio.Lock()
    return lock


def profile_of(config: AppConfig, chrome_profile: str | None) -> str:
    """The source-level `chrome_profile` wins over `browser.default_profile`."""
    return chrome_profile or config.browser.default_profile


@asynccontextmanager
async def work_tab(config: AppConfig, *, chrome_profile: str | None = None,
                   timeout_ms: int = CDP_CONNECT_TIMEOUT_MS) -> AsyncIterator[Page]:
    """Attach to (or launch) headed Chrome and yield one isolated work tab.

    The tab is closed on success, failure and cancellation; an attached browser
    is only disconnected, never closed.
    """
    spec, profile = config.browser, profile_of(config, chrome_profile)
    async with focus_lock(spec.cdp_url, profile):
        async with async_playwright() as playwright:
            browser, launched = await _attach(playwright, config, spec, profile, timeout_ms)
            try:
                context = await _default_context(browser)
                await _verify_profile(context, config, spec, profile)
                page = await context.new_page()
                await _disable_cache(context, page)
                try:
                    yield page
                finally:
                    await close_page(page)
            finally:
                await _detach(browser, launched)


def _prune_restored_tabs_sync(cdp_url: str) -> None:
    """On a freshly auto-launched Chrome, close excess restored tabs so Playwright's
    `Target.setAutoAttach` handshake cannot stall on dozens of restoring heavy pages."""
    base = cdp_url.rstrip("/")
    with contextlib.suppress(Exception):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{base}/json/list", timeout=2.0) as resp:
            targets = json.loads(resp.read().decode("utf-8", "ignore"))
        if not isinstance(targets, list):
            return
        pages = [t for t in targets if isinstance(t, dict) and t.get("type") == "page"]
        if len(pages) <= 3:
            return
        for extra in pages[1:]:
            tid = extra.get("id")
            if isinstance(tid, str) and tid:
                with contextlib.suppress(Exception):
                    with opener.open(f"{base}/json/close/{tid}", timeout=1.5) as _:
                        pass


async def _attach(playwright: Playwright, config: AppConfig, spec: BrowserSpec, profile: str,
                  timeout_ms: int) -> tuple[Browser, subprocess.Popen[bytes] | None]:
    """Reuse the running headed Chrome, or launch one when `auto_launch` allows it."""
    try:
        return await playwright.chromium.connect_over_cdp(spec.cdp_url, timeout=timeout_ms), None
    except Exception as exc:
        if not spec.auto_launch:
            raise ConfigError(_attach_hint(config, spec, profile)) from exc
        log.info("browser: no CDP endpoint at %s (%s); launching headed Chrome for %s",
                 spec.cdp_url, type(exc).__name__, profile)
    process = _launch(config, spec, profile)
    deadline = time.monotonic() + LAUNCH_TIMEOUT_S
    pruned = False
    while True:
        await asyncio.sleep(0.25)
        if not pruned:
            await asyncio.to_thread(_prune_restored_tabs_sync, spec.cdp_url)
            pruned = True
        try:
            return await playwright.chromium.connect_over_cdp(spec.cdp_url, timeout=10000), process
        except Exception as exc:
            pruned = False
            if process.poll() is not None and spec.chrome_user_data_dir is not None and time.monotonic() <= deadline:
                _clean_stale_singleton_lock(config.resolve(spec.chrome_user_data_dir))
            if process.poll() is not None or time.monotonic() > deadline:
                _terminate(process)
                raise UpstreamError(
                    f"headed Chrome for profile {profile!r} did not expose CDP at {spec.cdp_url}") from exc


def _attach_hint(config: AppConfig, spec: BrowserSpec, profile: str) -> str:
    command = " ".join(f'"{part}"' if " " in part else part for part in _launch_argv(config, spec, profile))
    return (f"no headed Chrome is listening on {spec.cdp_url}; start it with "
            f"{command} or set browser.auto_launch: true")


def _clean_stale_singleton_lock(user_data_dir: Path) -> None:
    """Remove Chrome Singleton* symlinks left behind by a dead local process."""
    lock = user_data_dir / "SingletonLock"
    if not lock.is_symlink():
        return
    with contextlib.suppress(OSError):
        target = os.readlink(lock)
        host, sep, pid_text = target.rpartition("-")
        if not sep or not pid_text.isdigit():
            return
        local_names = {socket.gethostname().lower(), socket.getfqdn().lower()}
        if host.lower() not in local_names:
            return
        try:
            os.kill(int(pid_text), 0)
        except ProcessLookupError:
            log.warning("browser: removing stale SingletonLock for dead PID %s in %s", pid_text, user_data_dir)
            for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
                (user_data_dir / name).unlink(missing_ok=True)


def _launch(config: AppConfig, spec: BrowserSpec, profile: str) -> subprocess.Popen[bytes]:
    """Start a real windowed Chrome for this profile. `--headless` is never passed."""
    if spec.chrome_user_data_dir is not None:
        user_data_dir = config.resolve(spec.chrome_user_data_dir)
        user_data_dir.mkdir(parents=True, exist_ok=True)
        _clean_stale_singleton_lock(user_data_dir)
    argv = _launch_argv(config, spec, profile)
    log.info("browser: launching headed Chrome (%s) for profile %s", argv[0], profile)
    return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def _launch_argv(config: AppConfig, spec: BrowserSpec, profile: str) -> list[str]:
    argv = [_chrome_binary(config, spec), f"--remote-debugging-port={_port(spec.cdp_url)}",
            f"--profile-directory={profile}", "--no-first-run", "--no-default-browser-check"]
    if spec.chrome_user_data_dir is not None:
        argv.append(f"--user-data-dir={config.resolve(spec.chrome_user_data_dir)}")
    argv.append("about:blank")
    return argv


def _chrome_binary(config: AppConfig, spec: BrowserSpec) -> str:
    if spec.chrome_executable is not None:
        return str(config.resolve(spec.chrome_executable))
    for candidate in _CHROME_PATHS:
        if Path(candidate).is_file():
            return candidate
    for name in _CHROME_COMMANDS:
        found = shutil.which(name)
        if found:
            return found
    raise ConfigError("Google Chrome was not found; set browser.chrome_executable")


def _port(cdp_url: str) -> int:
    port = urlsplit(cdp_url).port
    if port is None:
        raise ConfigError("browser.cdp_url must carry the Remote Debugging port")
    return port


async def _default_context(browser: Browser) -> BrowserContext:
    """The pre-existing context of an attached Chrome (the user's login state)."""
    return browser.contexts[0] if browser.contexts else await browser.new_context()


async def _disable_cache(context: BrowserContext, page: Page) -> None:
    """A replay must read the live page, not the user's Chrome HTTP cache.

    Set per tab over CDP: the shared context (and the user's other tabs) is untouched.
    """
    try:
        session = await context.new_cdp_session(page)
        await session.send("Network.setCacheDisabled", {"cacheDisabled": True})
    except Exception as exc:
        log.warning("browser: cannot disable the HTTP cache for the work tab (%s)", type(exc).__name__)


async def _verify_profile(context: BrowserContext, config: AppConfig, spec: BrowserSpec, profile: str) -> None:
    """Prove the attached Chrome runs the requested profile in a visible window."""
    probe = await context.new_page()
    try:
        try:
            await probe.goto("chrome://version", wait_until="domcontentloaded", timeout=VERSION_PAGE_TIMEOUT_MS)
            text = await probe.inner_text("body")
        except Exception as exc:
            raise ConfigError(
                "cannot verify the attached Chrome profile: chrome://version is unavailable; "
                f"start Chrome with --profile-directory={profile!r} or set browser.auto_launch: true") from exc
    finally:
        await close_page(probe)
    _check_version_page(text, config, spec, profile)


def _check_version_page(text: str, config: AppConfig, spec: BrowserSpec, profile: str) -> None:
    if _HEADLESS_ARG.search(text):
        raise ConfigError(f"the Chrome attached at {spec.cdp_url} runs headless; browser sources require a "
                          "visible headed window using your logged-in profile")
    found = _PROFILE_ARG.search(text)
    actual = (found.group(1) if found else DEFAULT_CHROME_PROFILE).strip()
    if actual.casefold() != profile.casefold():
        raise ConfigError(f"the Chrome attached at {spec.cdp_url} runs profile {actual!r}, not {profile!r}; "
                          f"start Chrome with --profile-directory={profile!r}"
                          + ("" if spec.auto_launch else " or set browser.auto_launch: true"))
    if spec.chrome_user_data_dir is not None:
        data_dir = _USER_DATA_ARG.search(text)
        expected = str(config.resolve(spec.chrome_user_data_dir))
        if data_dir is not None and not _same_path(data_dir.group(1), expected):
            raise ConfigError(f"the Chrome attached at {spec.cdp_url} uses user data dir {data_dir.group(1)!r}, "
                              f"not {expected!r}; fix browser.chrome_user_data_dir or launch Chrome yourself")


def _same_path(actual: str, expected: str) -> bool:
    return os.path.realpath(actual) == os.path.realpath(expected)


async def page_snapshot(page: Page) -> dict[str, Any]:
    """Bounded failure site: current URL, title and a slim A11y tree excerpt."""
    snapshot: dict[str, Any] = {"current_url": page.url}
    try:
        snapshot["page_title"] = await page.title()
    except Exception as exc:
        snapshot["page_title"] = f"<unavailable: {type(exc).__name__}>"
    try:
        snapshot["a11y_snapshot"] = clip(await page.locator("body").aria_snapshot(timeout=5000), A11Y_SNAPSHOT_CHARS)
    except Exception as exc:
        snapshot["a11y_snapshot"] = f"<unavailable: {type(exc).__name__}>"
    return snapshot


async def close_page(page: Page) -> None:
    """Close only this tab; a vanished tab or a closed browser is not an error."""
    with contextlib.suppress(Exception):
        if not page.is_closed():
            await page.close()


async def _detach(browser: Browser, launched: subprocess.Popen[bytes] | None) -> None:
    """Disconnect from the user's Chrome; only a Chrome we launched is shut down."""
    if launched is None:
        return  # Stopping Playwright disconnects; closing would kill the user's window.
    _terminate(launched)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(browser.close(), timeout=2.0)
    await asyncio.sleep(0.35)


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    with contextlib.suppress(OSError):
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            process.kill()
