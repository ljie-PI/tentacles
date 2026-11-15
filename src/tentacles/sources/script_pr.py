"""Publish one fixed browser script through an isolated worktree and `gh pr`.

A distilled or repaired script is repository code, but an Agent wrote it, so it
reaches the base branch only through human review. This module owns that
hand-off: a throwaway `git worktree` outside the repository commits exactly
`<scripts_dir>/<source_id>.py`, pushes it to the stable
`auto/browser-source-<source_id>` branch and opens — or comments on — a GitHub
PR with the configured labels. The base branch is never a push destination, no
second file is ever staged and the user's working tree and index are never
touched; the worktree and its scratch directory are removed on every path.

With an open PR the review branch is fast-forwarded from its own fetched head.
Without one the commit starts from a freshly fetched base and replaces the
same-named remote branch that a squash-merged or abandoned publish left behind:
that single update uses `--force-with-lease` pinned to the sha inspected right
before the push, and any computed branch that would land on the base branch is
refused outright.

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
# Provider key shapes. They are matched verbatim so an ordinary long string (a URL,
# a selector, a CSS class) is still publishable.
_SK_TOKEN = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")
_SK_RUN = re.compile(r"[A-Za-z0-9]{16,}")
_PROVIDER_KEYS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"), "a GitHub token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "a Slack token"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"), "a Google API key"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "a JWT"),
)
# The parameter names that always carry a credential, whatever the syntax around them.
_CREDENTIAL_NAME = (r"(?:api[_-]?key|apikey|access[_-]?token|token|secret|signature|sig|"
                    r"password|passwd|session(?:id)?|csrf|authorization|cookie)")
# `name = "literal"` where the name itself claims to be a credential.
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9])[A-Za-z0-9_.-]*?"
    r"(?:api[_-]?key|apikey|token|secret|password|passwd|authorization|cookie)"
    r"['\"]?\s*[:=]\s*(?P<quote>['\"])(?P<value>[^'\"\n]{12,})(?P=quote)")
# `https://x.test/?api_key=0123…` and every other unquoted position: only a
# credential-named query parameter with a non-trivial value is refused, so
# `page=2`, `utm_source=rss` and `id=123` stay publishable.
_CREDENTIAL_QUERY = re.compile(rf"(?i)[?&]{_CREDENTIAL_NAME}=[^\s&#\"']{{12,}}")
# `{"authorization": "Bearer abc123"}`: a credential-named header or field, quoted.
_CREDENTIAL_HEADER = re.compile(
    rf"(?i)['\"]?{_CREDENTIAL_NAME}['\"]?\s*:\s*(?P<quote>['\"])[^'\"\n]{{8,}}(?P=quote)")


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
        _review_refspec(branch, base=spec.base_branch)
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
    remote = await _remote(repo)
    open_pr = await _open_pr(repo, branch)
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
        await _push_review_branch(worktree, remote=remote, branch=branch,
                                  base=spec.base_branch, append=open_pr is not None)
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
    """The commit the worktree starts from: the fetched PR branch, else a fetched base."""
    if branch is not None:
        await _fetch(repo, remote, branch)
        for ref in (f"{remote}/{branch}", branch):
            if await _git_ok(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"):
                return ref
        raise ScriptPrError(f"neither {remote}/{branch} nor {branch} exists")
    await _fetch(repo, remote, base)
    for ref in (f"{remote}/{base}", base):
        if await _git_ok(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"):
            return ref
    raise ScriptPrError(f"neither {remote}/{base} nor {base} exists")


async def _fetch(repo: Path, remote: str, ref: str) -> None:
    """Refresh one remote-tracking ref; an unreachable remote falls back to what we have."""
    result = await _run(["git", "-C", str(repo), "fetch", "--no-tags", remote, ref])
    if result.code != 0:
        log.warning("browser: cannot fetch %s/%s (%s)", remote, ref, _reason(result))


def _review_refspec(branch: str, *, base: str) -> str:
    """The only push destination this module allows, and never the base branch."""
    destination = f"refs/heads/{branch}"
    if branch == base or destination == f"refs/heads/{base}":
        raise ScriptPrError(
            f"refusing to push {destination}: it is the base branch {base!r}")
    return destination


async def _push_review_branch(worktree: Path, *, remote: str, branch: str, base: str,
                              append: bool) -> None:
    """Fast-forward an open PR's branch, else replace the stale same-named branch.

    The replacement is a `--force-with-lease` pinned to the sha inspected just
    before the push, so a branch somebody else moved in between still fails.
    """
    destination = _review_refspec(branch, base=base)
    if append:
        await _git(worktree, "push", "--no-verify", remote, f"HEAD:{destination}")
        return
    previous = await _remote_branch_sha(worktree, remote=remote, branch=branch)
    lease = f"--force-with-lease={destination}:{previous or ''}"
    await _git(worktree, "push", "--no-verify", lease, remote, f"HEAD:{destination}")


async def _remote_branch_sha(repo: Path, *, remote: str, branch: str) -> str | None:
    """The commit the remote review branch points at now; None when it is absent."""
    ref = f"refs/heads/{branch}"
    result = await _run(["git", "-C", str(repo), "ls-remote", "--heads", remote, ref])
    if result.code != 0:
        raise ScriptPrError(f"cannot inspect {remote}/{branch}: {_reason(result)}")
    for line in result.out.splitlines():
        sha, _, found = line.partition("\t")
        if found.strip() == ref and sha.strip():
            return sha.strip()
    return None


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
                        body_file: Path) -> str:
    """The pull request URL `gh pr create` printed; no URL is a step we cannot report."""
    argv = ["pr", "create", "--base", spec.base_branch, "--head", branch,
            "--title", _pr_title(source_id), "--body-file", str(body_file)]
    for label in spec.labels:
        argv += ["--label", label]
    result = await _gh(repo, *argv)
    # `gh` prints the new pull request URL last on stdout; warnings may precede it.
    urls = _PR_URL.findall(result.out) or _PR_URL.findall(result.err)
    if not urls:
        raise ScriptPrError(
            f"gh pr create printed no pull request URL: {_reason(result)}")
    return urls[-1]


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
    if _sk_key(text):
        findings.append("an API key")
    findings.extend(reason for pattern, reason in _PROVIDER_KEYS if pattern.search(text))
    if _CREDENTIAL_ASSIGNMENT.search(text):
        findings.append("a hard-coded credential")
    if _CREDENTIAL_QUERY.search(text):
        findings.append("a credential in a URL query")
    if _CREDENTIAL_HEADER.search(text):
        findings.append("a hard-coded credential header")
    if any(secret and secret in text for secret in secrets):
        findings.append("a configured secret")
    return findings


def _sk_key(text: str) -> bool:
    """`sk-…` keys, ignoring the hyphenated identifiers a CSS class is made of."""
    return any(_SK_RUN.search(token) for token in _SK_TOKEN.findall(text))
