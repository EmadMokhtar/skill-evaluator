"""Resolve a skill's previous version from git history.

Nothing here raises for an environmental failure -- no git, no repo, an
untracked file, a history with nothing earlier in it. Those are facts about the
user's checkout, not authoring errors about their skill, so they come back as a
`BaselineUnavailable` the report can explain. The same discipline runners follow
for provider failures.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

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


def _materialise_bundle(skill: Skill, sha: str, into: Path) -> Path | None | BaselineUnavailable:
    """The commit's bundle directories, extracted under `into`.

    `git archive <sha> -- .` from the skill directory yields the subtree with
    paths relative to it (`scripts/x.py`), which is what makes filtering on the
    first path component possible. `filter="data"` is the safe extraction
    filter: no absolute paths, no `..`, no link escaping the target. A commit
    with none of the three directories yields None -- the baseline then gets
    no bundle tools, never the candidate's.
    """
    archive = _git_bytes(["archive", "--format=tar", sha, "--", "."], cwd=skill.path)
    if archive is None:
        return BaselineUnavailable(skill.name, f"cannot archive commit {sha[:8]}")
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
            members = [
                member for member in tar.getmembers() if member.name.split("/", 1)[0] in BUNDLE_DIRS
            ]
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


def resolve_previous(skill: Skill, *, into: Path) -> Skill | BaselineUnavailable:
    """The newest earlier version of `skill`, or why there isn't one.

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
    if _git(["rev-parse", "--show-toplevel"], cwd=skill.path) is None:
        return BaselineUnavailable(skill.name, f"{skill.path} is not inside a git repository")
    if _git(["ls-files", "--error-unmatch", SKILL_FILENAME], cwd=skill.path) is None:
        return BaselineUnavailable(skill.name, f"{SKILL_FILENAME} is not tracked by git")

    log = _git(
        ["log", f"--max-count={HISTORY_LIMIT}", "--format=%H", "--", SKILL_FILENAME],
        cwd=skill.path,
    )
    for sha in (log or "").split():
        # `<sha>:./<file>` resolves the path relative to cwd, which is the
        # skill's directory -- not the repository root.
        blob = _git(["show", f"{sha}:./{SKILL_FILENAME}"], cwd=skill.path)
        if blob is None:
            continue
        try:
            previous = parse_skill_text(
                blob,
                name_fallback=skill.path.name,
                path=skill.path,
                source=f"{SKILL_FILENAME} at commit {sha[:8]}",
            )
        except SkillParseError:
            # A historical version with broken frontmatter is not an authoring
            # error about the skill under test. Keep looking.
            continue
        if _qualifies(previous, skill, blob, working_text):
            bundle_root = _materialise_bundle(skill, sha, into)
            if isinstance(bundle_root, BaselineUnavailable):
                return bundle_root
            return previous.model_copy(update={"variant": "baseline", "bundle_root": bundle_root})

    return BaselineUnavailable(
        skill.name,
        f"no earlier version of {SKILL_FILENAME} found in the last {HISTORY_LIMIT} commits",
    )
