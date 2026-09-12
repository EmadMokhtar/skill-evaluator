"""A read-only view of the files a skill ships beside SKILL.md.

The Agent Skills layout puts three directories next to `SKILL.md`: `scripts/`
(code the agent may run), `references/` (documents it may read) and `assets/`
(files it may use). This module exposes exactly those three and nothing else.
That exclusion is the point: `*.eval.yaml` and `evals/` sit beside `SKILL.md`
too and hold the expected answers, so "any file beside SKILL.md" would hand
the agent its own answer key.

Framework-neutral, and it follows `workspace.py`'s split: methods here
**raise** (`PathRefused`, `OSError`, `UnicodeDecodeError`); the tools built on
top of them in `runners/tools.py` **catch**, because a model asking for a bad
path is an eval signal and an exception would surface it as an infra error.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from skill_lens.workspace import (
    DEFAULT_LIMITS,
    PathRefused,
    check_relative_path,
    resolve_under,
    stat_regular,
)

BUNDLE_DIRS: tuple[str, ...] = ("scripts", "references", "assets")
SCRIPTS_DIR = "scripts"


def has_bundle(directory: Path) -> bool:
    """Does `directory` hold at least one of the three bundle directories?"""
    return any((directory / name).is_dir() for name in BUNDLE_DIRS)


def script_extension(candidate: str) -> str:
    """The extension the interpreter map is keyed by: lower case, no dot."""
    return Path(candidate.strip()).suffix.lstrip(".").lower()


@dataclass(frozen=True)
class SkillBundle:
    """The three bundle directories under one skill, read-only.

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
        if Path(text).parts[0] not in BUNDLE_DIRS:
            raise PathRefused(
                f"refused: {candidate!r} is not under scripts/, references/ or assets/; "
                "only those three directories are readable"
            )
        return target, stat_regular(target, candidate)

    def _contained(self, candidate: str) -> Path:
        """The absolute path `candidate` names, or raise if it is not bundle."""
        return self._inspect(candidate)[0]

    def listing(self) -> list[str]:
        """Every bundled file, relative to the root, sorted, recursive.

        A symbolic link whose target lies outside the root is left out, so the
        listing never advertises a file `read` would refuse.
        """
        found: list[str] = []
        for name in BUNDLE_DIRS:
            directory = self.root / name
            if not directory.is_dir():
                continue
            for item in directory.rglob("*"):
                if item.is_file() and item.resolve().is_relative_to(self.root):
                    found.append(item.relative_to(self.root).as_posix())
        return sorted(found)

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
            raise PathRefused(
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
