"""Path and pattern checks that need no filesystem, and the one glob rule.

Pure string work, so both `models.py` (which validates a case as it is
built) and `workspace.py` (which walks the disk) can import it at the top.
Imports nothing from the rest of the project.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path, PurePosixPath


class PathRefused(Exception):
    """A path the workspace will not read or write.

    The message is written for the model: a built-in tool catches this and
    returns the text as an ordinary tool result.
    """


class PatternRefused(PathRefused):
    """A `file_glob` or glob artifact that could never match what its author meant.

    Only an author writes a pattern, so this is always an authoring error:
    `cli.py` lists it in `_AUTHORING_ERRORS` and the run exits 2. A
    `PathRefused` too, so code that already catches that keeps working.
    """


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


# The characters that make a `file_glob` a pattern. A pattern with none of
# them is a `file:` spelled longer, so `check_glob_pattern` refuses it.
_WILDCARDS = frozenset("*?[")


def glob_segments(pattern: str) -> tuple[str, ...]:
    """A pattern's segments, one per directory level. `./` and `//` add none."""
    return PurePosixPath(pattern.strip()).parts


def segment_matches(name: str, segment: str) -> bool:
    """The one rule for one level: `fnmatchcase`, the same on every platform.

    `Workspace.match` walks the disk with it and `glob_matches_path` compares
    a known path with it, so the two can never disagree.
    """
    return fnmatch.fnmatchcase(name, segment)


def _bracket_problem(segment: str) -> str | None:
    """Why a `[...]` set in `segment` could never match as written, or None.

    An unclosed `[` is found the way `fnmatch` finds a set's end: an optional
    `!`, then a `]` that is a member rather than the end. `fnmatch` reads an
    unclosed `[` as a literal character, so `report[1.md` would silently look
    for a bracket in a file name. Whether a closed set matches anything is
    left to `fnmatch` itself, which writes a set that matches nothing (a
    reversed range such as `[z-a]`) as the empty pattern `(?!)`; a second
    reading of its own would drift from it.
    """
    index = 0
    while index < len(segment):
        if segment[index] != "[":
            index += 1
            continue
        start = index + 1
        if start < len(segment) and segment[start] == "!":
            start += 1
        end = start + 1 if start < len(segment) and segment[start] == "]" else start
        close = segment.find("]", end)
        if close == -1:
            return "has a '[' with no closing ']'"
        index = close + 1
    if "(?!)" in fnmatch.translate(segment):
        return "has a '[...]' set that matches nothing, such as the reversed range '[z-a]'"
    return None


def check_glob_pattern(pattern: str) -> None:
    """Raise PatternRefused unless `pattern` is a usable `file_glob`.

    Root-independent, like `check_relative_path`, so a model can call it as
    it validates. Every refusal is the author's mistake: a pattern that can
    never match what its author meant.
    """
    try:
        check_relative_path(pattern)
    except PathRefused as exc:
        raise PatternRefused(str(exc)) from exc
    if pattern.strip().split("/")[-1] in ("", "."):
        raise PatternRefused(
            f"refused: {pattern!r} ends with '/' or '/.'; a pattern names files, not directories"
        )
    if "\\" in pattern:
        raise PatternRefused(
            f"refused: {pattern!r} contains a backslash; separate directories with '/'"
        )
    segments = glob_segments(pattern)
    if any("**" in segment for segment in segments):
        raise PatternRefused(
            f"refused: {pattern!r} uses '**'; a wildcard matches within one "
            "directory, so write one '*' for each level"
        )
    if not any(_WILDCARDS & set(segment) for segment in segments):
        raise PatternRefused(
            f"refused: {pattern!r} has no wildcard (*, ?, [...]); use 'file:' for a literal path"
        )
    for segment in segments:
        problem = _bracket_problem(segment)
        if problem is not None:
            raise PatternRefused(f"refused: {pattern!r} {problem}")


def glob_matches_path(pattern: str, path: str) -> bool:
    """Would `pattern` match a file at `path`? Pure: no filesystem involved.

    For the paths a case or a runner knows before any directory exists (a
    seeded file, the delivered skill), by the same `segment_matches` rule
    `Workspace.match` applies on disk.
    """
    wanted = glob_segments(pattern)
    actual = glob_segments(path)
    return len(wanted) == len(actual) and all(
        segment_matches(name, segment) for name, segment in zip(actual, wanted, strict=True)
    )
