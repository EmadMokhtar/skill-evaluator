"""A read-only view of the files a skill ships beside SKILL.md.

The bundle is every file in the skill's directory, as an install copies it
(issue #92) -- `scripts/`, `references/` and `assets/`, and also a skill's own
`templates/` or `reference.md` -- except what `withheld` names: `SKILL.md`
itself (delivered on its own path, from the skill's markdown), `.git`, and the
eval files, which sit beside `SKILL.md` and hold the expected answers. Those
are withheld at any depth, so the agent is never handed its own answer key.
Only `scripts/` is runnable.

Framework-neutral, and it follows `workspace.py`'s split: methods here
**raise** (`PathRefused`, `OSError`, `UnicodeDecodeError`); the tools built on
top of them in `runners/tools.py` **catch**, because a model asking for a bad
path is an eval signal and an exception would surface it as an infra error.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from skill_lens.workspace import (
    DEFAULT_LIMITS,
    FileTooLarge,
    PathRefused,
    check_relative_path,
    resolve_under,
    stat_regular,
)

SCRIPTS_DIR = "scripts"
SKILL_FILENAME = "SKILL.md"
# The eval files' names, as `cases/loader.py` and `cases/evals_json.py` spell
# them (a test keeps them equal: importing them here would be a cycle).
EVALS_DIRNAME = "evals"
EVAL_SUFFIX = ".eval.yaml"
EVALS_JSON_FILENAME = "evals.json"
# Directories never part of what a skill ships, at any depth: its evals, version
# control, and the dependency and cache trees an install never carries.
WITHHELD_DIRS = frozenset(
    {EVALS_DIRNAME, ".git", ".hg", ".svn", "node_modules", ".venv", "__pycache__"}
)
# Files never shipped, at any depth: the evals, secrets that would reach the
# model provider, and the harness's own configuration. `.env.*` is matched too.
WITHHELD_FILES = frozenset(
    {EVALS_JSON_FILENAME, ".env", ".npmrc", ".netrc", ".pypirc", "skill-lens.toml"}
)
# A skill whose SKILL.md sits at a repository root ships the repository; past
# either cap the bundle is refused rather than copied into every work item.
MAX_BUNDLE_FILES = 1_000
MAX_BUNDLE_BYTES = 100 * 1024 * 1024

WITHHELD_NOTE = (
    "SKILL.md, eval files (evals/, *.eval.yaml, evals.json), version control, dependency "
    "directories and secret files (.env, .npmrc, ...) are never readable"
)

_FOLDED_DIRS = frozenset(name.casefold() for name in WITHHELD_DIRS)
_FOLDED_FILES = frozenset(name.casefold() for name in WITHHELD_FILES)


class BundleTooLarge(PathRefused):
    """The skill ships more than `MAX_BUNDLE_FILES` files or `MAX_BUNDLE_BYTES`.

    A refusal like any other to the tools; product delivery turns it into a
    `ProductSetupError`, so preflight exits 2 before anything is spent."""


def withheld(relative: str) -> bool:
    """Is this path, relative to the skill's directory, kept from the agent?

    The top-level `SKILL.md`; anything under a `WITHHELD_DIRS` directory; any
    `WITHHELD_FILES`, `.env.*` or `*.eval.yaml` file -- at any depth, and
    compared case-insensitively, because macOS and Windows find `Evals/` as
    the loader's `evals/` (and on Linux withholding it costs nothing).
    """
    parts = [part.casefold() for part in PurePosixPath(relative.strip()).parts]
    if not parts:
        return False
    if parts == [SKILL_FILENAME.casefold()]:
        return True
    if any(part in _FOLDED_DIRS for part in parts):
        return True
    name = parts[-1]
    return name in _FOLDED_FILES or name.startswith(".env.") or name.endswith(EVAL_SUFFIX)


def _resolves_withheld(item: Path, root: Path) -> bool:
    """`item` resolves outside `root`, or onto a withheld path. A link that
    cannot be resolved (a loop) counts as withheld."""
    try:
        resolved = item.resolve()
    except (OSError, RuntimeError):
        return True
    return not resolved.is_relative_to(root) or withheld(resolved.relative_to(root).as_posix())


def _shipped(root: Path, start: str = "") -> Iterator[tuple[str, int]]:
    """Every file the skill ships under `start`, relative to `root`, with its size.

    The one rule every consumer uses -- the read tools, product delivery and
    `has_bundle`. Withheld directories are pruned, not walked; a directory
    link is never descended. A file is a regular file, directly or through a
    link, that resolves inside `root` onto a path not withheld itself: a FIFO
    or a device is never yielded, so nothing downstream opens one.
    """
    top = root / start if start else root
    if top.is_symlink() or not top.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(top):
        here = Path(dirpath)
        prefix = here.relative_to(root)
        dirnames[:] = [d for d in dirnames if not withheld((prefix / d).as_posix())]
        for name in filenames:
            relative = (prefix / name).as_posix()
            item = here / name
            if withheld(relative):
                continue
            try:
                found = item.stat()
            except OSError:
                continue
            if not stat.S_ISREG(found.st_mode) or _resolves_withheld(item, root):
                continue
            yield relative, found.st_size


def bundle_files(root: Path) -> list[str]:
    """Every file the skill ships, relative to `root`, sorted.

    Raises `BundleTooLarge` past either cap, stopping the walk there.
    """
    root = root.resolve()
    found: list[str] = []
    total = 0
    for relative, size in _shipped(root):
        found.append(relative)
        total += size
        if len(found) > MAX_BUNDLE_FILES:
            raise BundleTooLarge(
                f"refused: {root} ships more than {MAX_BUNDLE_FILES:,} files (the cap is "
                f"{MAX_BUNDLE_FILES:,}); a SKILL.md at a repository root ships the whole "
                "repository, so give the skill a directory of its own"
            )
        if total > MAX_BUNDLE_BYTES:
            raise BundleTooLarge(
                f"refused: {root} ships more than {MAX_BUNDLE_BYTES:,} bytes (the cap is "
                f"{MAX_BUNDLE_BYTES:,}); give the skill a directory of its own"
            )
    return sorted(found)


def has_bundle(directory: Path) -> bool:
    """Does `directory` ship at least one file besides what `withheld` names?"""
    return next(_shipped(directory.resolve()), None) is not None


def script_extension(candidate: str) -> str:
    """The extension the interpreter map is keyed by: lower case, no dot."""
    return Path(candidate.strip()).suffix.lstrip(".").lower()


@dataclass(frozen=True)
class SkillBundle:
    """The files one skill ships, read-only.

    `root` is **always already resolved**, for the same reason `Workspace.root`
    is: a containment check that compared `/tmp/...` against `/private/tmp/...`
    would compare two spellings of one directory.
    """

    root: Path

    def _inspect(self, candidate: str) -> tuple[Path, os.stat_result | None]:
        """The contained path plus its stat (None when there is no such file).

        The same rule as `Workspace`: only a regular file or a directory is
        ever resolved. A checkout can carry a FIFO or a symlink loop as
        easily as a script can plant one, and `read_skill_file` must not
        block on the former or crash on the latter.
        """
        check_relative_path(candidate)
        text = candidate.strip()
        target = resolve_under(self.root, candidate)
        if target == self.root or not target.is_relative_to(self.root):
            raise PathRefused(f"refused: {candidate!r} resolves outside the skill's directory")
        if withheld(text) or withheld(target.relative_to(self.root).as_posix()):
            raise PathRefused(
                f"refused: {candidate!r} is not a file the skill ships; {WITHHELD_NOTE}"
            )
        return target, stat_regular(target, candidate)

    def _contained(self, candidate: str) -> Path:
        """The absolute path `candidate` names, or raise if it is not bundle."""
        return self._inspect(candidate)[0]

    def listing(self) -> list[str]:
        """Every file the skill ships, relative to the root, sorted, recursive.

        A link that resolves outside the root or onto a withheld file is left
        out, so the listing never advertises a file `read` would refuse. Raises
        `BundleTooLarge` past either cap.
        """
        return bundle_files(self.root)

    def scripts(self) -> list[str]:
        """The shipped files under `scripts/`, sorted. Walks only `scripts/`: it
        is called per work item, and must not cost a walk of the whole skill."""
        return sorted(relative for relative, _ in _shipped(self.root, SCRIPTS_DIR))

    def read(self, candidate: str) -> str:
        """The file's text. Raises OSError if missing, UnicodeDecodeError if binary.

        Capped at the default `max_file_bytes` before a byte is read, like
        `Workspace.read`: a bundle is committed content, but a sparse file
        in a checkout has any apparent size, and what reaches the model is
        bounded by this cap rather than by the repository's disk.
        """
        target, found = self._inspect(candidate)
        cap = DEFAULT_LIMITS.max_file_bytes
        if found is not None and found.st_size > cap:
            raise FileTooLarge(
                f"refused: {candidate} is {found.st_size:,} bytes; max_file_bytes is {cap:,}"
            )
        return target.read_text(encoding="utf-8")

    def script(self, candidate: str, interpreters: Mapping[str, Sequence[str]]) -> Path:
        """The absolute path of a runnable script, or raise saying why not.

        Every refusal carries what the model needs for its next call to be
        right: the bundled scripts when the name was wrong, the allowed
        extensions when the interpreter was.
        """
        target = self._contained(candidate)
        if Path(candidate.strip()).parts[0] != SCRIPTS_DIR:
            raise PathRefused(
                f"refused: {candidate!r} is not under scripts/; only bundled scripts can be run"
            )
        if not target.is_file():
            bundled = ", ".join(self.scripts()) or "(none)"
            raise PathRefused(f"refused: no such script {candidate!r}; bundled scripts: {bundled}")
        extension = script_extension(candidate)
        if extension not in interpreters:
            allowed = ", ".join(sorted(interpreters)) or "(none)"
            raise PathRefused(
                f"refused: {candidate!r} has no configured interpreter; "
                f"script_interpreters allows: {allowed}"
            )
        return target
