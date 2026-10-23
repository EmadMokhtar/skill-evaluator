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
from collections.abc import Callable, Iterator, Mapping, Sequence
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
# Directories never part of what a skill ships, at any depth.
WITHHELD_DIRS = frozenset({EVALS_DIRNAME, ".git"})

WITHHELD_NOTE = "SKILL.md, eval files (evals/, *.eval.yaml, evals.json) and .git are never readable"


def withheld(relative: str) -> bool:
    """Is this path, relative to the skill's directory, kept from the agent?

    The top-level `SKILL.md`; anything under an `evals/` or `.git` directory;
    and any `*.eval.yaml` or `evals.json` file -- at any depth, so an eval file
    tucked under `references/` is withheld as surely as one beside `SKILL.md`.
    """
    parts = PurePosixPath(relative.strip()).parts
    if not parts:
        return False
    if parts == (SKILL_FILENAME,):
        return True
    if any(part in WITHHELD_DIRS for part in parts):
        return True
    return parts[-1].endswith(EVAL_SUFFIX) or parts[-1] == EVALS_JSON_FILENAME


def _resolves_withheld(item: Path, root: Path) -> bool:
    """A link (or a path through one) that lands outside `root` or on a
    withheld file. A loop or an unreadable link counts as withheld."""
    try:
        resolved = item.resolve()
    except (OSError, RuntimeError):
        return True
    return not resolved.is_relative_to(root) or withheld(resolved.relative_to(root).as_posix())


def _shipped(root: Path) -> Iterator[str]:
    """Every file the skill ships, relative to `root`, in walk order.

    Withheld directories are pruned, not walked, so `.git` costs nothing. A
    file is a regular file, directly or through a link, that resolves inside
    `root` to a path that is not withheld itself.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        prefix = here.relative_to(root)
        dirnames[:] = [d for d in dirnames if not withheld((prefix / d).as_posix())]
        for name in filenames:
            relative = (prefix / name).as_posix()
            item = here / name
            if withheld(relative) or not item.is_file() or _resolves_withheld(item, root):
                continue
            yield relative


def bundle_files(root: Path) -> list[str]:
    """Every file the skill ships, relative to `root`, sorted."""
    return sorted(_shipped(root.resolve()))


def has_bundle(directory: Path) -> bool:
    """Does `directory` ship at least one file besides `SKILL.md` and its evals?"""
    return next(_shipped(directory.resolve()), None) is not None


def _lands_on_withheld(item: Path, root: Path) -> bool:
    """A link that resolves inside `root` onto a withheld file, or cannot be
    resolved at all. A link out of `root` is not this: it is copied as a link,
    as `git archive` preserves it."""
    try:
        resolved = item.resolve()
    except (OSError, RuntimeError):
        return True
    return resolved.is_relative_to(root) and withheld(resolved.relative_to(root).as_posix())


def copy_ignore(root: Path) -> Callable[[str, list[str]], set[str]]:
    """A `shutil.copytree(root, ..., ignore=...)` callable that leaves out what
    `withheld` names and links onto it, so a delivered copy never holds an eval
    file. `root` must be the path passed to `copytree`."""
    resolved_root = root.resolve()

    def ignore(directory: str, names: list[str]) -> set[str]:
        here = Path(directory)
        prefix = here.relative_to(root)
        return {
            name
            for name in names
            if withheld((prefix / name).as_posix())
            or ((here / name).is_symlink() and _lands_on_withheld(here / name, resolved_root))
        }

    return ignore


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
        out, so the listing never advertises a file `read` would refuse.
        """
        return bundle_files(self.root)

    def scripts(self) -> list[str]:
        """The listing, narrowed to `scripts/`."""
        return [entry for entry in self.listing() if entry.split("/", 1)[0] == SCRIPTS_DIR]

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
