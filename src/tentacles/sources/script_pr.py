"""Publish one fixed browser script through an isolated worktree and `gh pr`.

A distilled or repaired script is repository code, but an Agent wrote it, so it
reaches the base branch only through human review. This module owns that
hand-off: a throwaway `git worktree` outside the repository commits exactly
`<scripts_dir>/<source_id>.py`, pushes it to the stable
`auto/browser-source-<source_id>` branch and opens — or comments on — a GitHub
PR with the configured labels. It never pushes to the base branch, never
force-pushes, never stages a second file and never touches the user's working
tree or index; the worktree and its scratch directory are removed on every path.

A `git`/`gh` failure is a result, not an exception: the script is already
installed and the fetched items still belong in the Store, so the caller records
`git_or_pr_failed` and keeps the round. `browser.pr.enabled: false` keeps the
local half (one commit on the review branch) and skips push and `gh`. Only a
byte-identical script is a no-op.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Sequence

from tentacles.config import BrowserPrSpec
from tentacles.diagnostics import clip

log = logging.getLogger(__name__)

COMMAND_TIMEOUT_S = 120.0
MAX_COMMAND_OUTPUT = 4000
_PR_URL = re.compile(r"https?://\S+/pull/\d+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")
_COOKIE = re.compile(r"(?im)^\s*(?:set-)?cookie\s*[:=]\s*\S{8,}")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[A-Za-z]{2,}\b")
_HOME_PATH = re.compile(r"(?:/Users|/home)/[^/\s\"']+|[A-Za-z]:\\Users\\[^\\\s\"']+")


class ScriptPrError(Exception):
    """One `git`/`gh` step refused to publish; reported through `ScriptPublication`."""


@dataclass(frozen=True)
class ScriptPublication:
    """What happened to one script's review branch, ready for `FetchOutcome.stats`."""

    status: Literal["created", "updated", "unchanged", "disabled", "error"]
    branch: str
    pr_url: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class _OpenPr:
    number: int
    url: str | None


@dataclass(frozen=True)
class _Completed:
    code: int
    out: str
    err: str


async def publish_script(script: Path, *, source_id: str, spec: BrowserPrSpec, reason: str,
                         detail: str, secrets: Sequence[str] = ()) -> ScriptPublication:
    """Commit one gated script on its review branch and open/update its PR.

    Never raises for an expected environment problem (no repository, no `gh`
    login, no push permission): the caller keeps the installed script and the
    fetched items and records the degradation itself.
    """
    branch = f"{spec.branch_prefix}{source_id}"
    try:
        text = script.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ScriptPublication("error", branch, detail=f"{script.name} is not readable as UTF-8")
    body = _pr_body(source_id=source_id, reason=reason, detail=detail, script_name=script.name)
    findings = sensitive_findings(text, secrets) + sensitive_findings(body, ())
    if findings:
        return ScriptPublication(
            "error", branch,
            detail="refusing to publish credential-like content: " + ", ".join(sorted(set(findings))))
    try:
        repo = await _repo_root(script)
        relative = _relative_script(repo, script)
        if not spec.enabled:
            return await _publish_local(repo, relative, text, branch=branch, spec=spec,
                                        source_id=source_id, reason=reason)
        return await _publish(repo, relative, text, body, branch=branch, spec=spec,
                              source_id=source_id, reason=reason)
    except ScriptPrError as exc:
        log.warning("browser: cannot publish the script PR for %s (%s)", source_id, exc)
        return ScriptPublication("error", branch, detail=str(exc))


async def _publish_local(repo: Path, relative: str, text: str, *, branch: str,
                         spec: BrowserPrSpec, source_id: str, reason: str) -> ScriptPublication:
    """`browser.pr.enabled: false`: version the script locally, never reach the remote."""
    start = await _local_start(repo, branch=branch, base=spec.base_branch)
    parent = Path(tempfile.mkdtemp(prefix="tentacles-script-pr-"))
    worktree = parent / "worktree"
    try:
        await _git(repo, "worktree", "add", "--detach", str(worktree), start)
        if await _stage_one(worktree, relative, text):
            return ScriptPublication("disabled", branch,
                                     detail="browser.pr.enabled is false; the script is unchanged")
        await _git(worktree, "commit", "--no-verify", "-m", _commit_message(source_id, reason))
        commit = await _git(worktree, "rev-parse", "HEAD")
        await _git(repo, "branch", "--force", branch, commit.out.strip())
        return ScriptPublication(
            "disabled", branch,
            detail=f"browser.pr.enabled is false; committed to the local {branch} branch")
    finally:
        await _remove_worktree(repo, worktree, parent)


async def _local_start(repo: Path, *, branch: str, base: str) -> str:
    """A local-only commit starts from its own branch, else the base, else HEAD."""
    for ref in (branch, base, f"origin/{base}", "HEAD"):
        if await _git_ok(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"):
            return ref
    raise ScriptPrError(f"neither {branch} nor {base} nor HEAD exists")


async def _stage_one(worktree: Path, relative: str, text: str) -> bool:
    """Write and stage the single allowed path; True when it already matches."""
    target = worktree / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    await _git(worktree, "add", "--", relative)
    return bool(await _git_ok(worktree, "diff", "--cached", "--quiet"))


async def _publish(repo: Path, relative: str, text: str, body: str, *, branch: str,
                   spec: BrowserPrSpec, source_id: str, reason: str) -> ScriptPublication:
    """The worktree lifecycle: one file, one commit, one push, one PR action."""
    open_pr = await _open_pr(repo, branch)
    remote = await _remote(repo)
    start = await _start_ref(repo, remote=remote, branch=branch if open_pr else None,
                             base=spec.base_branch)
    parent = Path(tempfile.mkdtemp(prefix="tentacles-script-pr-"))
    worktree = parent / "worktree"
    body_file = parent / "pr-body.md"
    body_file.write_text(body, encoding="utf-8")
    try:
        await _git(repo, "worktree", "add", "--detach", str(worktree), start)
        if await _stage_one(worktree, relative, text):
            return ScriptPublication("unchanged", branch, pr_url=open_pr.url if open_pr else None,
                                     detail=f"{relative} already matches {start}")
        await _git(worktree, "commit", "--no-verify", "-m", _commit_message(source_id, reason))
        # A plain refspec push: the base branch is never the destination and no
        # forced update is ever requested.
        await _git(worktree, "push", "--no-verify", remote, f"HEAD:refs/heads/{branch}")
        if open_pr is not None:
            await _gh(repo, "pr", "comment", str(open_pr.number), "--body-file", str(body_file))
            return ScriptPublication("updated", branch, pr_url=open_pr.url,
                                     detail=f"commented on PR #{open_pr.number}")
        url = await _gh_pr_create(repo, spec=spec, branch=branch, source_id=source_id,
                                  body_file=body_file)
        return ScriptPublication("created", branch, pr_url=url,
                                 detail=f"opened a PR for {relative}")
    finally:
        await _remove_worktree(repo, worktree, parent)


async def _repo_root(script: Path) -> Path:
    """The repository that owns the script, whatever its checkout is called."""
    result = await _run(["git", "-C", str(script.parent), "rev-parse", "--show-toplevel"])
    if result.code != 0 or not result.out.strip():
        raise ScriptPrError("the replay script is not inside a git repository")
    return Path(result.out.strip()).resolve()


def _relative_script(repo: Path, script: Path) -> str:
    """The one path this module is allowed to stage, relative to the repository."""
    try:
        relative = script.resolve().relative_to(repo)
    except ValueError:
        raise ScriptPrError("the replay script is outside its git repository") from None
    if relative.suffix != ".py" or not relative.parts:
        raise ScriptPrError(f"{relative} is not a browser script")
    return relative.as_posix()


async def _remote(repo: Path) -> str:
    result = await _run(["git", "-C", str(repo), "remote"])
    names = [line.strip() for line in result.out.splitlines() if line.strip()]
    if result.code != 0 or not names:
        raise ScriptPrError("the git repository has no remote to push a review branch to")
    return "origin" if "origin" in names else names[0]


async def _start_ref(repo: Path, *, remote: str, branch: str | None, base: str) -> str:
    """The commit the worktree starts from: the existing PR branch, else the base."""
    if branch is not None:
        for ref in (f"{remote}/{branch}", branch):
            if await _git_ok(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"):
                return ref
        await _git(repo, "fetch", "--no-tags", remote, branch)
        return f"{remote}/{branch}"
    for ref in (f"{remote}/{base}", base):
        if await _git_ok(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"):
            return ref
    raise ScriptPrError(f"neither {remote}/{base} nor {base} exists")


async def _open_pr(repo: Path, branch: str) -> _OpenPr | None:
    """One active PR per source: a second heal appends to it instead of a new one."""
    result = await _gh(repo, "pr", "list", "--state", "open", "--head", branch,
                       "--json", "number,url")
    try:
        found: Any = json.loads(result.out or "[]")
    except json.JSONDecodeError:
        raise ScriptPrError("gh pr list did not return JSON") from None
    if not isinstance(found, list) or not found:
        return None
    first = found[0]
    if not isinstance(first, dict) or not isinstance(first.get("number"), int):
        raise ScriptPrError("gh pr list returned an unexpected record")
    url = first.get("url")
    return _OpenPr(number=first["number"], url=url if isinstance(url, str) else None)


async def _gh_pr_create(repo: Path, *, spec: BrowserPrSpec, branch: str, source_id: str,
                        body_file: Path) -> str | None:
    argv = ["pr", "create", "--base", spec.base_branch, "--head", branch,
            "--title", _pr_title(source_id), "--body-file", str(body_file)]
    for label in spec.labels:
        argv += ["--label", label]
    result = await _gh(repo, *argv)
    match = _PR_URL.search(result.out + "\n" + result.err)
    return match.group(0) if match else None


async def _remove_worktree(repo: Path, worktree: Path, parent: Path) -> None:
    """Drop the worktree and the scratch directory on success, failure and cancellation."""
    try:
        await _run(["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)])
    except ScriptPrError as exc:
        log.warning("browser: cannot remove the temporary worktree (%s)", exc)
    shutil.rmtree(worktree, ignore_errors=True)
    try:
        await _run(["git", "-C", str(repo), "worktree", "prune"])
    except ScriptPrError:
        pass
    shutil.rmtree(parent, ignore_errors=True)


async def _git(path: Path, *args: str) -> _Completed:
    result = await _run(["git", "-C", str(path), *args])
    if result.code != 0:
        raise ScriptPrError(f"git {args[0]} failed: {_reason(result)}")
    return result


async def _git_ok(path: Path, *args: str) -> bool:
    return (await _run(["git", "-C", str(path), *args])).code == 0


async def _gh(repo: Path, *args: str) -> _Completed:
    result = await _run(["gh", *args], cwd=repo)
    if result.code != 0:
        raise ScriptPrError(f"gh {args[0]} {args[1] if len(args) > 1 else ''} failed: {_reason(result)}")
    return result


async def _run(argv: list[str], *, cwd: Path | None = None,
               timeout: float = COMMAND_TIMEOUT_S) -> _Completed:
    """Run one command non-interactively and capture bounded output."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1",
           "GH_NO_UPDATE_NOTIFIER": "1"}
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=str(cwd) if cwd is not None else None, env=env)
    except OSError as exc:
        raise ScriptPrError(f"cannot run {argv[0]}: {type(exc).__name__}") from None
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise ScriptPrError(f"{argv[0]} did not finish within {timeout:g}s") from None
    return _Completed(code=process.returncode if process.returncode is not None else -1,
                      out=clip(out.decode("utf-8", "replace"), MAX_COMMAND_OUTPUT),
                      err=clip(err.decode("utf-8", "replace"), MAX_COMMAND_OUTPUT))


def _reason(result: _Completed) -> str:
    """The line that explains a failed command, never its whole output."""
    lines = [line.strip() for line in (result.err or result.out).splitlines() if line.strip()]
    for line in lines:
        if line.lower().startswith(("fatal:", "error:", "remote:")):
            return clip(line, 300)
    return clip(lines[-1], 300) if lines else "no output"


def _commit_message(source_id: str, reason: str) -> str:
    if reason == "heal":
        return f"fix(browser-sources): repair the {source_id} replay script"
    return f"feat(browser-sources): distill the {source_id} replay script"


def _pr_title(source_id: str) -> str:
    return f"browser source: review the {source_id} replay script"


def _pr_body(*, source_id: str, reason: str, detail: str, script_name: str) -> str:
    action = ("repaired itself after a replay failure" if reason == "heal"
              else "distilled a new replay script")
    return "\n".join([
        "> **Machine-generated. Do not merge without review.**",
        ">",
        "> This pull request was produced by the controlled `pi` Agent of tentacles.",
        "> A human must review the selectors and the field mapping before merging.",
        "",
        f"`tentacles` {action} for the `type: browser` source `{source_id}`.",
        "",
        f"- Script: `{script_name}` (the only committed file)",
        f"- Reason: `{reason}`",
        f"- {detail}",
        "",
        "Action trajectories and `*.meta.json` stay under the local, ignored `data/`",
        "directory and are not part of this PR.",
        "",
        "Review checklist:",
        "- [ ] Selectors describe the live page structure, not one lucky run.",
        "- [ ] Fields map to the site as documented and `published` is ISO 8601.",
        "- [ ] The replay does not depend on today's date, a fixed item count or scroll order.",
    ])


def sensitive_findings(text: str, secrets: Sequence[str] = ()) -> list[str]:
    """Credential-like content that must never reach a pushed commit or a PR body."""
    findings: list[str] = []
    if _BEARER.search(text):
        findings.append("a bearer token")
    if _COOKIE.search(text):
        findings.append("a cookie value")
    if _EMAIL.search(text):
        findings.append("an email address")
    if _HOME_PATH.search(text):
        findings.append("a local user path")
    if any(secret and secret in text for secret in secrets):
        findings.append("a configured secret")
    return findings
