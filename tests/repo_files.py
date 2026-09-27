"""The repository's tracked files, as git lists them.

Several tests walk what git tracks rather than a fixed list of names, so a new
file is covered the moment it exists. They share this one implementation so a
fix to how the list is read reaches all of them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def tracked_files() -> list[str]:
    """Every tracked path, relative to the repository root, `/`-separated.

    `-z` makes git print each path raw and NUL-terminated. Without it git
    C-quotes a name holding a non-ASCII byte (`"caf\\303\\251.md"`, which no
    longer starts with its own directory), and splitting on whitespace breaks a
    name holding a space into two.
    """
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout
    return [name for name in out.decode("utf-8", errors="surrogateescape").split("\0") if name]
