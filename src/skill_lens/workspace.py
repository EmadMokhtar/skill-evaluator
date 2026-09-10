"""The per-run temporary directory, and every path decision about it.

Framework-neutral by construction: nothing here imports an agent framework, so
`tests/test_framework_isolation.py` keeps holding.

Methods here **raise**; the tools built on top of them (`runners/tools.py`)
**catch**. That split is deliberate. An evaluator wants an exception, because
a refused path there is a genuine authoring error. A tool must never raise,
because the model choosing a bad path is an eval signal and an exception would
surface it as an infra failure instead.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from skill_lens.models import WorkspaceSpec

WORKSPACE_PREFIX = "skill-lens-"

_LABEL_MAX = 60
_UNSAFE_LABEL_CHARS = re.compile(r"[^A-Za-z0-9_.-]")


class PathRefused(Exception):
    """A path the workspace will not read or write.

    The message is written for the model: a built-in tool catches this and
    returns the text as an ordinary tool result.
    """


class WorkspaceError(Exception):
    """Creating or seeding a workspace failed.

    An infra signal (a full disk, a permissions problem), never a skill
    signal, so the orchestrator turns it into an errored case rather than a
    failed one.
    """


@dataclass(frozen=True)
class WorkspaceLimits:
    """Runaway guards, not part of what an eval asserts.

    A language model can get stuck repeating itself -- write a file, read it
    back, append, write again -- and without a limit one bad run fills the
    disk. Concurrency sharpens it: eight workspaces can be alive at once.

    Roughly 100x a realistic artifact (a report or a JSON file is kilobytes),
    so they bind only on genuine runaways. Configurable per repository via
    `Config` so a skill that legitimately produces something large never
    forces anyone to edit installed source.
    """

    max_file_bytes: int = 1_000_000
    max_files: int = 200
    max_total_bytes: int = 5_000_000


DEFAULT_LIMITS = WorkspaceLimits()


def sanitise_label(label: str) -> str:
    """A directory-name fragment: legible under --keep-workspace, always valid.

    Truncated to 60 characters -- long enough to identify a run, short enough
    to stay clear of the path-length limits a deeply nested checkout can hit.
    """
    cleaned = _UNSAFE_LABEL_CHARS.sub("-", label).strip("-")
    return (cleaned or "case")[:_LABEL_MAX]


def check_relative_path(candidate: str) -> None:
    """Raise PathRefused unless `candidate` is a plain path inside a workspace.

    Root-independent on purpose: `cases/loader.py` calls it to validate a
    case's declared `files:` keys at load time, long before any directory
    exists. `Workspace.resolve` calls it too and then adds the one check that
    does need a root.
    """
    text = candidate.strip()
    if not text:
        raise PathRefused("refused: the path must not be empty")
    if "\x00" in text:
        raise PathRefused(f"refused: {candidate!r} contains a null byte")
    if any("\ud800" <= char <= "\udfff" for char in text):
        raise PathRefused(
            f"refused: {candidate!r} contains an unpaired UTF-16 surrogate, "
            "which cannot be encoded as UTF-8"
        )
    path = Path(text)
    if not path.parts:
        raise PathRefused(
            f"refused: {candidate!r} names the working directory itself, not a file in it"
        )
    # Three separate checks, not one: Path("C:foo") on Windows is
    # drive-relative but not absolute, and Path("\\foo") has a root and no
    # drive. Either would escape a test that only asked is_absolute().
    if path.is_absolute() or path.drive or path.root:
        raise PathRefused(
            f"refused: {candidate!r} is not a relative path; "
            "all paths are relative to the working directory"
        )
    # Checked before resolution so the message can name what is wrong, rather
    # than reporting a location the author never wrote.
    if ".." in path.parts:
        raise PathRefused(
            f"refused: {candidate!r} contains '..'; paths may not leave the working directory"
        )


@dataclass(frozen=True)
class Workspace:
    """A contained directory the agent may read and write.

    `root` is **always already resolved**: on macOS `/tmp` is a symbolic link
    to `/private/tmp`, and an unresolved root would make every containment
    check compare two spellings of the same directory.

    `limits` defaults so that reconstructing a Workspace from a bare path
    stays a one-argument call -- which is what `AssertionEvaluator` does. That
    is safe because limits constrain writes, and nothing but the tools writes.
    """

    root: Path
    limits: WorkspaceLimits = DEFAULT_LIMITS

    def resolve(self, candidate: str) -> Path:
        """The absolute path `candidate` names, or raise if it leaves the root."""
        check_relative_path(candidate)
        target = (self.root / candidate.strip()).resolve()
        # The backstop for anything the checks above missed. Rejecting the
        # root itself matters because every caller wants a file: without it,
        # "." would pass containment and then fail confusingly on read.
        if target == self.root or not target.is_relative_to(self.root):
            raise PathRefused(f"refused: {candidate!r} resolves outside the working directory")
        return target

    def listing(self) -> list[str]:
        """Every file, relative to the root, sorted, recursive."""
        return sorted(
            item.relative_to(self.root).as_posix()
            for item in self.root.rglob("*")
            if item.is_file()
        )

    def _totals(self) -> tuple[int, int]:
        """(file count, total bytes) as they are on disk right now."""
        files = [item for item in self.root.rglob("*") if item.is_file()]
        return len(files), sum(item.stat().st_size for item in files)

    def read(self, candidate: str) -> str:
        """The file's text. Raises OSError if it is missing or is a directory."""
        return self.resolve(candidate).read_text(encoding="utf-8")

    def write(self, candidate: str, content: str) -> int:
        """Create or replace a file, returning the bytes written.

        Every refusal names the limit it hit and that limit's value: a generic
        "too large" would leave an author guessing which of three caps stopped
        them and what to raise it to.
        """
        target = self.resolve(candidate)
        size = len(content.encode("utf-8"))
        if size > self.limits.max_file_bytes:
            raise PathRefused(
                f"refused: {candidate} would be {size:,} bytes; "
                f"max_file_bytes is {self.limits.max_file_bytes:,}"
            )
        count, total = self._totals()
        # An overwrite is not a new file, and its old bytes go away. Counting
        # either one twice would make an agent revising its own report hit a
        # cap for a directory whose size never grew.
        existing = target.stat().st_size if target.is_file() else None
        if existing is None and count >= self.limits.max_files:
            raise PathRefused(
                f"refused: the working directory already holds {count:,} files; "
                f"max_files is {self.limits.max_files:,}"
            )
        projected = total - (existing or 0) + size
        if projected > self.limits.max_total_bytes:
            raise PathRefused(
                f"refused: writing {candidate} would bring the working directory "
                f"to {projected:,} bytes; max_total_bytes is "
                f"{self.limits.max_total_bytes:,}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return size

    def cleanup(self) -> None:
        """Delete the directory. Never raises.

        Deliberately error-suppressing: deleting a temp directory is harness
        housekeeping, and a cleanup failure turning a passing case red would
        be the tool reporting on itself instead of on the skill.
        """
        shutil.rmtree(self.root, ignore_errors=True)


def create_workspace(
    spec: WorkspaceSpec,
    *,
    label: str,
    limits: WorkspaceLimits = DEFAULT_LIMITS,
) -> Workspace:
    """Make a fresh directory and seed it with the case's declared files.

    `mkdtemp` is atomic, so concurrent work items cannot collide on a name --
    which is what lets every arm and every repetition own a directory it never
    shares.

    Seeded content is subject to the same caps as anything the agent writes. A
    refusal here raises `WorkspaceError` (an errored case, naming the cap),
    not a scored failure: a seed the harness would not write says nothing
    about the skill.
    """
    prefix = f"{WORKSPACE_PREFIX}{sanitise_label(label)}-"
    try:
        root = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    except OSError as exc:
        raise WorkspaceError(f"cannot create a workspace: {exc}") from exc
    workspace = Workspace(root=root, limits=limits)
    for name, content in spec.files.items():
        try:
            workspace.write(name, content)
        except (PathRefused, OSError, ValueError) as exc:
            # A half-seeded directory would be worse than none: the agent
            # would see an environment nobody declared.
            workspace.cleanup()
            raise WorkspaceError(f"cannot seed {name!r} into the workspace: {exc}") from exc
    return workspace
