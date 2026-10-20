#!/usr/bin/env python3
"""Verify every release tag on origin has its version on PyPI.

A version can be tagged and never published: `release` pushes the bump commit
and its tag, and `publish` then waits for a reviewer inside the `pypi`
environment. If the approval never comes, the tag stays on main with nothing
behind it, and every later run reads only the commits since that tag (issue
#103, v0.21.0). This check is what notices.

It reads the tags on origin (`git ls-remote`), PyPI's release list (no
credentials) and scripts/spent-tags.txt, the one list of tags acknowledged
as never going to be published. A tag that is on origin, not on PyPI with at
least one file, and not spent is a finding, reported with the release run
that should have published it so a maintainer can re-run its failed jobs.

Standard library only, so it runs where nothing has been installed.

Usage: check_tags_published.py

Exit 0: every release tag is on PyPI (or acknowledged as spent).
Exit 1: a tag is missing, or spent-tags.txt lists a tag PyPI does have.
Exit 2: the check could not run (origin or PyPI unreadable); nothing verified.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
SPENT_FILE = Path(__file__).resolve().parent / "spent-tags.txt"
SPENT_NAME = "scripts/spent-tags.txt"
DOCS_SECTION = 'docs/releasing.md, "A tag that never reached PyPI"'

PYPI_URL = "https://pypi.org/pypi/{name}/json"
PYPI_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2.0
HTTP_TIMEOUT_SECONDS = 30.0
GITHUB_API_URL = "https://api.github.com"
RELEASE_WORKFLOW = ".github/workflows/release.yml"
# GitHub lets a run's failed jobs be re-run for this long after it started.
RERUN_WINDOW = timedelta(days=30)
# `https://github.com/o/r.git`, `git@github.com:o/r.git`, `ssh://git@github.com/o/r`.
REPOSITORY_RE = re.compile(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$")

# What Commitizen's PEP 440 scheme emits -- `0.22.0`, `0.22.0rc1`, `0.22.0.post1`,
# `0.22.0.dev3` -- and nothing looser: `v0` or `v1.0.0-docs` under the prefix is
# not a release tag, and PyPI could never list it.
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?$")


class CheckUnavailable(Exception):
    """The check could not run; nothing has been verified either way."""


@dataclass(frozen=True)
class TagFormat:
    """`[tool.commitizen] tag_format` split at `$version`."""

    prefix: str
    suffix: str

    def version(self, tag: str) -> str | None:
        """The version a release tag spells, or None for any other tag."""
        if not tag.startswith(self.prefix):
            return None
        if self.suffix and not tag.endswith(self.suffix):
            return None
        end = len(tag) - len(self.suffix)
        middle = tag[len(self.prefix) : end]
        return middle if VERSION_RE.match(middle) else None


def read_tag_format(pyproject_text: str) -> TagFormat:
    try:
        tag_format = tomllib.loads(pyproject_text)["tool"]["commitizen"]["tag_format"]
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise CheckUnavailable(
            f"pyproject.toml has no [tool.commitizen] tag_format ({exc!r})"
        ) from exc
    prefix, sentinel, suffix = str(tag_format).partition("$version")
    if not sentinel:
        raise CheckUnavailable(f"tag_format {tag_format!r} does not interpolate $version")
    return TagFormat(prefix, suffix)


def read_project_name(pyproject_text: str) -> str:
    try:
        return str(tomllib.loads(pyproject_text)["project"]["name"])
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise CheckUnavailable(f"pyproject.toml has no [project] name ({exc!r})") from exc


@dataclass(frozen=True)
class ReleaseTag:
    name: str
    version: str
    commit: str  # the commit an annotated tag points at, or the tag's own ref


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


def parse_ls_remote(output: str, fmt: TagFormat) -> tuple[list[ReleaseTag], list[str]]:
    """Release tags in version order, and the names under the prefix that are
    not versions. The `^{}` line (the peeled commit of an annotated tag) wins
    over the plain line, which names the tag object itself."""
    refs: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for line in output.splitlines():
        sha, _, ref = line.partition("\t")
        if not ref.startswith("refs/tags/"):
            continue
        name = ref[len("refs/tags/") :]
        if name.endswith("^{}"):
            peeled[name[: -len("^{}")]] = sha
        else:
            refs[name] = sha
    tags: list[ReleaseTag] = []
    ignored: list[str] = []
    for name, sha in refs.items():
        version = fmt.version(name)
        if version is None:
            if name.startswith(fmt.prefix):
                ignored.append(name)
            continue
        tags.append(ReleaseTag(name, version, peeled.get(name, sha)))
    tags.sort(key=lambda tag: _version_key(tag.version))
    return tags, sorted(ignored)


def pypi_versions(payload: dict) -> tuple[set[str], set[str]]:
    """The versions PyPI lists with at least one file, and those with none.
    A release whose files were all deleted is listed but cannot be installed.

    PyPI documents `releases` as deprecated. The day it is dropped, reading
    it as empty would report every tag missing; so a payload without it is
    `CheckUnavailable` instead."""
    releases = payload.get("releases")
    if not isinstance(releases, dict):
        raise CheckUnavailable(
            "PyPI's response has no releases list (see https://docs.pypi.org/api/json/)"
        )
    with_files = {version for version, files in releases.items() if files}
    without_files = set(releases) - with_files
    return with_files, without_files


def parse_spent(text: str) -> dict[str, str]:
    """`tag  # reason` per line; `#` starts a comment; blank lines are skipped.
    An entry with no reason has lost the why the file exists to keep."""
    spent: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        tag, _, reason = line.partition("#")
        tag, reason = tag.strip(), reason.strip()
        if not tag:
            continue
        if not reason:
            raise CheckUnavailable(
                f"{SPENT_NAME}:{number}: {tag} has no reason; write `{tag}  # <date>: <why>`"
            )
        spent[tag] = reason
    return spent


@dataclass(frozen=True)
class Verdict:
    missing: list[ReleaseTag] = field(default_factory=list)  # on origin, not on PyPI
    empty: list[ReleaseTag] = field(default_factory=list)  # on PyPI with no files
    stale: list[ReleaseTag] = field(default_factory=list)  # spent, yet on PyPI
    accepted: list[ReleaseTag] = field(default_factory=list)  # spent and not on PyPI
    published: int = 0


def assess(
    tags: list[ReleaseTag],
    with_files: set[str],
    without_files: set[str],
    spent: dict[str, str],
) -> Verdict:
    missing: list[ReleaseTag] = []
    empty: list[ReleaseTag] = []
    stale: list[ReleaseTag] = []
    accepted: list[ReleaseTag] = []
    published = 0
    for tag in tags:
        is_published = tag.version in with_files
        if tag.name in spent:
            (stale if is_published else accepted).append(tag)
        elif is_published:
            published += 1
        elif tag.version in without_files:
            empty.append(tag)
        else:
            missing.append(tag)
    return Verdict(missing, empty, stale, accepted, published)


# --- IO ------------------------------------------------------------------------


def _get(url: str, headers: dict[str, str]) -> bytes:
    # S310 wants a literal scheme it can read; both callers build an https://
    # URL from the constants above, never from input, and this refuses the rest.
    if not url.startswith("https://"):
        raise CheckUnavailable(f"refusing to open a non-https URL: {url}")
    request = urllib.request.Request(url, headers=headers)  # noqa: S310
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:  # noqa: S310
        return response.read()


def ls_remote() -> str:
    """The tags on origin. A failed lookup prints nothing, exactly like a
    repository with no tags, so the exit status is what tells them apart."""
    argv = ["git", "ls-remote", "--tags", "origin"]
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or "no error output"
        raise CheckUnavailable(f"{' '.join(argv)} failed (exit {proc.returncode}): {detail}")
    return proc.stdout


def fetch_pypi(name: str, *, attempts: int = PYPI_ATTEMPTS, sleep=time.sleep) -> dict:
    """PyPI's JSON for the project, without credentials. A 404 is a project
    with no releases; anything else is retried, then `CheckUnavailable` --
    never an empty list, which would read as every tag missing."""
    url = PYPI_URL.format(name=name)
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return json.loads(_get(url, {"Accept": "application/json"}))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {"releases": {}}
            last = exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = exc
        if attempt < attempts:
            sleep(RETRY_DELAY_SECONDS)
    raise CheckUnavailable(f"PyPI could not be read after {attempts} attempts: {last}")


def fetch_github(path: str) -> Any:
    """One GitHub REST call. The token is optional; without it a shared
    runner address may be rate-limited, which the caller reports as a
    failed lookup, never as a different verdict."""
    base = os.environ.get("GITHUB_API_URL", GITHUB_API_URL).rstrip("/")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return json.loads(_get(base + path, headers))


def parse_repository(url: str) -> str | None:
    match = REPOSITORY_RE.search(url.strip())
    return match.group(1) if match else None


def repository() -> str | None:
    """`owner/name`: the Actions variable, else parsed from origin's URL."""
    configured = os.environ.get("GITHUB_REPOSITORY")
    if configured:
        return configured
    proc = subprocess.run(
        ["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=False
    )
    return parse_repository(proc.stdout) if proc.returncode == 0 else None


# --- the report ----------------------------------------------------------------


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def describe_run(repo: str | None, tag: ReleaseTag, now: datetime) -> list[str]:
    """The lines after a finding's first line: the release run that pushed
    the tag, and whether its failed jobs can still be re-run. Best effort --
    a lookup that fails changes these lines, never the verdict."""
    lead = "  Looking up the run that should have published it"
    if repo is None:
        return [f"{lead} was skipped: the repository is unknown (set GITHUB_REPOSITORY)."]
    try:
        commit = fetch_github(f"/repos/{repo}/commits/{tag.commit}")
        parents = commit.get("parents") or []
        if not parents:
            return [f"{lead} was skipped: {tag.commit[:7]} has no parent commit."]
        parent = str(parents[0]["sha"])
        # The bump commit sits on top of the pushed commit, so the run that
        # pushed the tag is the one whose head is the tag commit's parent.
        listing = fetch_github(f"/repos/{repo}/actions/runs?head_sha={parent}&event=push")
        runs = [r for r in listing.get("workflow_runs") or [] if r.get("path") == RELEASE_WORKFLOW]
    except (
        CheckUnavailable,
        urllib.error.URLError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:
        return [f"{lead} failed: {exc}."]
    if not runs:
        return [
            f"  No release run exists for its commit's parent {parent[:7]}: "
            "the release workflow did not push this tag."
        ]
    run = runs[0]
    conclusion = run.get("conclusion") or "in progress"
    if conclusion == "success":
        # A run that stranded a tag cannot have succeeded: `publish` ran, so
        # the version reached PyPI. Every push to main has a release run, so
        # for a tag pushed by hand this is main's previous tip's run, which
        # released something else or nothing -- and has no failed jobs.
        return [
            f"  The release run on its commit's parent, {run['html_url']}, succeeded, so it "
            "did not strand this tag: either the tag was pushed outside the workflow, or "
            "PyPI no longer has a version that run published. There are no failed jobs to "
            "re-run; the version is spent."
        ]
    started = _parse_time(str(run["created_at"]))
    deadline = started + RERUN_WINDOW
    lines = [
        f"  The run that should have published it: {run['html_url']} "
        f"(started {started:%Y-%m-%d}, {conclusion})."
    ]
    if now <= deadline:
        lines.append(
            f"  Re-run its failed jobs; GitHub allows that until {deadline:%Y-%m-%d} "
            f"({RERUN_WINDOW.days} days after the run started)."
        )
    else:
        lines.append(
            f"  Re-running it is no longer possible: GitHub's {RERUN_WINDOW.days}-day window "
            f"closed on {deadline:%Y-%m-%d}."
        )
    return lines


def _summarise(lines: list[str]) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None, *, now: datetime | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    now = now or datetime.now(UTC)

    try:
        pyproject_text = PYPROJECT.read_text(encoding="utf-8")
        fmt = read_tag_format(pyproject_text)
        project = read_project_name(pyproject_text)
        tags, ignored = parse_ls_remote(ls_remote(), fmt)
        with_files, without_files = pypi_versions(fetch_pypi(project))
        if with_files and not tags:
            # origin pointing at the wrong or an empty repository lists nothing
            # and exits 0; "0 tags, all published" would be a vacuous pass.
            raise CheckUnavailable(
                f"PyPI lists {len(with_files)} releases but origin has no release tag "
                f"under the prefix {fmt.prefix!r}"
            )
        spent = parse_spent(SPENT_FILE.read_text(encoding="utf-8"))
    except (CheckUnavailable, OSError) as exc:
        line = f"Could not check: {exc}. Nothing was verified."
        print(line, file=sys.stderr)
        _summarise([line])
        return 2

    verdict = assess(tags, with_files, without_files, spent)

    if ignored:
        print("Ignored under the tag prefix (not a version): " + ", ".join(ignored))
    for tag in verdict.accepted:
        reason = spent[tag.name] or "no reason given"
        print(f"Spent, accepted by {SPENT_NAME}: {tag.name} ({reason})")

    findings: list[str] = []
    repo = repository() if verdict.missing or verdict.empty else None
    footer = f"  Otherwise record the tag in {SPENT_NAME} with the reason. See {DOCS_SECTION}."
    for tag in verdict.missing:
        findings.append(
            f"Tag {tag.name} is on origin, but {tag.version} is not on PyPI ({project})."
        )
        findings.extend(describe_run(repo, tag, now))
        findings.append(footer)
    for tag in verdict.empty:
        findings.append(
            f"Tag {tag.name} is on origin, but PyPI lists {tag.version} with no files ({project})."
        )
        findings.extend(describe_run(repo, tag, now))
        findings.append(footer)
    for tag in verdict.stale:
        findings.append(
            f"Tag {tag.name} is listed in {SPENT_NAME}, but {tag.version} is on PyPI. "
            "Remove the entry."
        )
    if findings:
        print("\n".join(findings), file=sys.stderr)
        _summarise(findings)
        return 1

    plural = "tag" if verdict.published == 1 else "tags"
    line = f"Every release tag on origin is on PyPI ({verdict.published} {plural})."
    print(line)
    _summarise([line])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
