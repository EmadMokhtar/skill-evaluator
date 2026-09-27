"""Resolve a skill's previous version from git history.

Nothing here raises for an environmental failure -- no git, no repo, an
untracked file, a history with nothing earlier in it. Those are facts about the
user's checkout, not authoring errors about their skill, so they come back as a
`BaselineUnavailable` the report can explain. The same discipline runners follow
for provider failures.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from skill_lens.bundle import BUNDLE_DIRS
from skill_lens.models import Skill
from skill_lens.skills.loader import SKILL_FILENAME, SkillParseError, parse_skill_text
from skill_lens.workspace import sanitise_label

# How far back to look. A skill edited hundreds of times still finds its
# previous version within the first few commits; the bound exists so a
# pathological history cannot turn one run into thousands of `git show` calls.
HISTORY_LIMIT = 50

# A hung git must not hang CI.
GIT_TIMEOUT_SECONDS = 10

# A full object name: SHA-1, or SHA-256 in a repository that uses it.
_OBJECT_NAME = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


@dataclass(frozen=True)
class BaselineUnavailable:
    """Why a previous version could not be resolved. Returned, never raised."""

    skill_name: str
    reason: str


def _git_bytes(args: list[str], cwd: Path) -> bytes | None:
    """Run git in `cwd`; return raw stdout, or None if the command failed."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            # S607: `git` is found on PATH on purpose. An absolute path would
            # be wrong on most machines, and a missing git must come back as
            # BaselineUnavailable (the OSError below), never as a crash.
            ["git", *args],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _git(args: list[str], cwd: Path) -> str | None:
    """`_git_bytes`, decoded -- for everything that is text."""
    raw = _git_bytes(args, cwd)
    return None if raw is None else raw.decode("utf-8", errors="replace")


def _qualifies(previous: Skill, working: Skill, previous_text: str, working_text: str) -> bool:
    """Is `previous` genuinely an earlier version of `working`?

    A declared version is the authority: an edit that did not bump it is still
    *this* version, however much the body changed. Without one, differing
    content is the best evidence available.
    """
    if working.version:
        return previous.version != working.version
    return previous_text != working_text


def _literal(path: str) -> str:
    """A pathspec matching `path` and nothing else.

    `[`, `*` and `?` are glob syntax in a plain pathspec, and a directory may be
    named with any of them.
    """
    return f":(literal){path}"


def _history(top: Path, rev: str, path: str, limit: int) -> list[tuple[str, str]]:
    """The commits reachable from `rev` that touched `path`, newest first.

    Each comes with the status git gives the file in that commit -- `A` where
    the commit created it -- or `""` for a merge, which git log shows without
    a diff. Paths come back quoted in this output, so only the status letter is
    read from those lines; the path is the one asked for.
    """
    out = _git(
        [
            "log",
            f"--max-count={limit}",
            "--format=%H",
            "--name-status",
            "--no-renames",
            rev,
            "--",
            _literal(path),
        ],
        cwd=top,
    )
    entries: list[tuple[str, str]] = []
    for line in (out or "").splitlines():
        if _OBJECT_NAME.fullmatch(line):
            entries.append((line, ""))
        elif "\t" in line and entries and not entries[-1][1]:
            entries[-1] = (entries[-1][0], line.split("\t", 1)[0])
    return entries


def _moved_from(top: Path, sha: str, path: str) -> str | None:
    """Where commit `sha` moved `path` from, if it moved the file byte for byte.

    `--find-renames=100%` pairs a deleted file with an added one only when
    their contents are identical, and a copy -- whose source still exists --
    is never a rename. That is the whole defence against borrowing another
    skill's history:
    `git log --follow` follows copies too, and by similarity, so a SKILL.md
    started from another skill's file would inherit that skill's old versions.
    A move that also edited the file is therefore not followed, and resolution
    reports that it found nothing rather than guess.
    """
    raw = _git_bytes(
        ["diff-tree", "-z", "-r", "--find-renames=100%", "--name-status", f"{sha}^", sha], cwd=top
    )
    if raw is None:
        return None
    fields = [os.fsdecode(field) for field in raw.split(b"\0")]
    index = 0
    while index + 1 < len(fields) and fields[index]:
        status = fields[index]
        if status[0] in "RC":
            if index + 2 >= len(fields):
                return None
            source, destination = fields[index + 1], fields[index + 2]
            if status == "R100" and destination == path:
                return source
            index += 3
        else:
            index += 2
    return None


def _materialise_bundle(
    skill: Skill, sha: str, directory: str, top: Path, into: Path
) -> Path | None | BaselineUnavailable:
    """The commit's bundle directories, extracted under `into`.

    `directory` is where the skill lived *at that commit*, relative to the
    repository root -- `""` at the root -- which is not where it lives now if
    the skill has moved since. `git archive <sha> -- <directory>`, run from the
    root, names each member by its full path; the directory prefix is stripped
    so that filtering on the first component (`scripts/x.py`) works.
    `filter="data"` is the safe extraction filter: no absolute paths, no `..`,
    no link escaping the target. A commit with none of the three directories
    yields None -- the baseline then gets no bundle tools, never the
    candidate's.
    """
    spec = _literal(directory) if directory else "."
    archive = _git_bytes(["archive", "--format=tar", sha, "--", spec], cwd=top)
    if archive is None:
        return BaselineUnavailable(skill.name, f"cannot archive commit {sha[:8]}")
    prefix = f"{directory}/" if directory else ""
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
            members = []
            for member in tar.getmembers():
                if not member.name.startswith(prefix):
                    continue
                relative = member.name[len(prefix) :]
                if relative.split("/", 1)[0] not in BUNDLE_DIRS:
                    continue
                member.name = relative
                members.append(member)
            if not members:
                return None
            target = Path(tempfile.mkdtemp(prefix=f"{sanitise_label(skill.name)}-", dir=into))
            try:
                tar.extractall(target, members=members, filter="data")
            except (tarfile.TarError, OSError, ValueError):
                shutil.rmtree(target, ignore_errors=True)
                raise
    except (tarfile.TarError, OSError, ValueError) as exc:
        return BaselineUnavailable(
            skill.name, f"cannot extract the bundle at commit {sha[:8]}: {exc}"
        )
    return target.resolve()


def _version_at(skill: Skill, top: Path, sha: str, path: str) -> tuple[Skill, str] | None:
    """The skill as `path` held it at `sha`, with its text, or None."""
    blob = _git(["show", f"{sha}:{path}"], cwd=top)
    if blob is None:
        return None
    try:
        previous = parse_skill_text(
            blob,
            name_fallback=skill.path.name,
            path=skill.path,
            source=f"{path} at commit {sha[:8]}",
        )
    except SkillParseError:
        # A historical version with broken frontmatter is not an authoring
        # error about the skill under test. Keep looking.
        return None
    return previous, blob


def resolve_previous(skill: Skill, *, into: Path) -> Skill | BaselineUnavailable:
    """The newest earlier version of `skill`, or why there isn't one.

    The search walks the commits that touched `SKILL.md` at its current path,
    newest first. Where one of them created that path by moving the file there
    byte for byte, it carries on from the old path, before the move; any other
    creation is where this file's history begins. `HISTORY_LIMIT` bounds the
    commits read across every path together.

    `into` is the run's baseline-bundle directory (see the orchestrator's
    `_BaselineStore`): the previous bundle is extracted into a fresh
    subdirectory of it, so two resolutions never share one, and the
    orchestrator deletes the whole thing when the run ends. Required, not
    optional: a caller who forgot it would get a baseline whose instructions
    say "run scripts/count.py" against a bundle that does not exist.
    """
    skill_md = skill.path / SKILL_FILENAME
    try:
        working_text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return BaselineUnavailable(skill.name, f"cannot read {skill_md}: {exc}")

    if shutil.which("git") is None:
        return BaselineUnavailable(skill.name, "git is not installed")
    toplevel = _git_bytes(["rev-parse", "--show-toplevel"], cwd=skill.path)
    prefix = _git_bytes(["rev-parse", "--show-prefix"], cwd=skill.path)
    if toplevel is None or prefix is None:
        return BaselineUnavailable(skill.name, f"{skill.path} is not inside a git repository")
    if _git(["ls-files", "--error-unmatch", SKILL_FILENAME], cwd=skill.path) is None:
        return BaselineUnavailable(skill.name, f"{SKILL_FILENAME} is not tracked by git")

    # From here on every command runs at the repository root with root-relative
    # paths, because an earlier path may name a directory that no longer exists.
    top = Path(os.fsdecode(toplevel.rstrip(b"\n")))
    path = os.fsdecode(prefix.rstrip(b"\n")) + SKILL_FILENAME
    rev = "HEAD"
    remaining = HISTORY_LIMIT
    while remaining > 0:
        moved_from = None
        for sha, status in _history(top, rev, path, remaining):
            remaining -= 1
            found = _version_at(skill, top, sha, path)
            if found is not None and _qualifies(found[0], skill, found[1], working_text):
                directory = str(PurePosixPath(path).parent)
                bundle_root = _materialise_bundle(
                    skill, sha, "" if directory == "." else directory, top, into
                )
                if isinstance(bundle_root, BaselineUnavailable):
                    return bundle_root
                return found[0].model_copy(
                    update={"variant": "baseline", "bundle_root": bundle_root}
                )
            if status == "A":
                moved_from = _moved_from(top, sha, path)
                if moved_from is not None:
                    rev = f"{sha}^"
                    break
        if moved_from is None:
            break
        path = moved_from

    return BaselineUnavailable(
        skill.name,
        f"no earlier version of {SKILL_FILENAME} found in the last {HISTORY_LIMIT} commits",
    )
