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
    return [REPO_ROOT / name for name in out if not name.startswith(EXCLUDED_DIRS)]


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
