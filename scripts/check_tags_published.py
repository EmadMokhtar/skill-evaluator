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

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
SPENT_FILE = Path(__file__).resolve().parent / "spent-tags.txt"

# What a version starts like under Commitizen's scheme; `v0` or `v1.0.0-docs`
# under the prefix is not a release tag.
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+")


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
    A release whose files were all deleted is listed but cannot be installed."""
    releases = payload.get("releases") or {}
    with_files = {version for version, files in releases.items() if files}
    without_files = set(releases) - with_files
    return with_files, without_files


def parse_spent(text: str) -> dict[str, str]:
    """`tag  # reason` per line; `#` starts a comment; blank lines are skipped."""
    spent: dict[str, str] = {}
    for line in text.splitlines():
        tag, _, reason = line.partition("#")
        tag = tag.strip()
        if tag:
            spent[tag] = reason.strip()
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
