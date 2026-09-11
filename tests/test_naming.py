"""The old name must not survive the rename.

A rename done with a text substitution is only safe if something fails when it
misses a file. This test is that something. It deliberately reads the working
tree rather than the package, because most occurrences were in prose.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# docs/superpowers/ is a historical record of what was decided when. Renaming
# inside it would make the archive lie about the past.
EXCLUDED_DIRS = ("docs/superpowers/",)

# This file itself is excluded from the offender scan below. A test that
# searches the repository for a literal string necessarily contains that
# literal string (in its own regex and docstrings) to describe what it is
# looking for -- that self-reference is inherent, not a naming leak, so it
# is excluded by exact path rather than folded into a broader pattern that
# could hide a real offender elsewhere.
SELF_PATH = "tests/test_naming.py"

# CHANGELOG.md is excluded for the same reason as docs/superpowers/: it is a
# record of the past, not a surface that was renamed. `cz bump` regenerates it
# from commit subjects and breaking-change footers, several of which were
# written before the rename, so the old name there is quoting history rather
# than surviving a missed substitution. Editing it would be both untrue and
# futile: the next bump rebuilds the file from the same immutable commits and
# writes the old name straight back.
#
# The exclusion is not a hole in the scan. The narrower
# `test_the_changelog_quotes_only_what_history_says` below still reads the file
# and still fails on any old-name line that history did not produce, so a leak
# arriving through a commit subject written after the rename is caught. That is
# what the module docstring asks of this file -- something has to fail -- and
# the reason the broad regex was not widened instead: tolerating these two
# spellings everywhere would silence the same words in a file where they really
# would be a missed rename.
CHANGELOG = "CHANGELOG.md"

EXCLUDED_FILES = (SELF_PATH, CHANGELOG)

# Tokens that merely start with the old name and are not this project's name.
ALLOWED = re.compile(r"skill-eval(?:uator|s\b|-m\d|-design)")

OLD_NAME = re.compile(r"skill[-_]eval")


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [
        REPO_ROOT / name
        for name in out
        if not name.startswith(EXCLUDED_DIRS) and name not in EXCLUDED_FILES
    ]


def test_the_old_name_survives_nowhere_outside_the_archive():
    offenders: list[str] = []
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            remainder = ALLOWED.sub("", line)
            if OLD_NAME.search(remainder):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
    assert not offenders, "the old name survives in:\n" + "\n".join(offenders[:40])


def test_no_path_still_carries_the_old_name():
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _tracked_files()
        if OLD_NAME.search(str(path.relative_to(REPO_ROOT)))
        and not ALLOWED.search(str(path.relative_to(REPO_ROOT)))
    ]
    assert not offenders, f"paths still carrying the old name: {offenders}"


# What git history actually says: the M0+M1 commit subject, and the continuation
# line of the rename commit's breaking-change footer. `cz bump` copies both into
# CHANGELOG.md verbatim on every release. Both were written before the rename, so
# neither can be corrected without misquoting the commit it came from.
#
# This list should only ever shrink, never grow -- a new entry would mean a commit
# subject written after the rename used the old name, and the fix for that is the
# commit message, not an addition here.
HISTORICAL_CHANGELOG_LINES = frozenset(
    {
        "are renamed from skill-eval to skill-lens.",
        "- add skill-eval design and zero-cost eval engine (M0+M1) (#1)",
    }
)


def test_the_changelog_quotes_only_what_history_says():
    """CHANGELOG.md may carry the old name only where a pre-rename commit did.

    The file is excluded from the repository-wide scan because it is generated,
    but excluding it outright would let the old name back into the tree through
    a commit subject. This is the narrower check that keeps it covered.
    """
    offenders: list[str] = []
    text = (REPO_ROOT / CHANGELOG).read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped in HISTORICAL_CHANGELOG_LINES:
            continue
        if OLD_NAME.search(ALLOWED.sub("", stripped)):
            offenders.append(f"{CHANGELOG}:{number}: {stripped}")
    assert not offenders, (
        "the old name reached the changelog from a commit written after the rename:\n"
        + "\n".join(offenders[:40])
        + f"\n\n{CHANGELOG} is generated by `cz bump` from commit history, so the fix is the "
        "commit subject -- and PRs are squash-merged, so that means the PR title -- "
        "not an edit to the file or an addition to HISTORICAL_CHANGELOG_LINES."
    )
