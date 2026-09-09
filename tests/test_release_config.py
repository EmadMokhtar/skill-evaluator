"""Guard the release configuration that nothing else would notice breaking.

The action's pinned version and the package version are two places that must
agree; a release that changes one and not the other ships an action installing
somebody else's version.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import skill_lens
from skill_lens.yaml_loading import safe_load

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
ACTION = REPO_ROOT / "action.yml"

VERSION_IN_SPEC = re.compile(r"skill-lens\[pydantic-ai\]==(?P<version>[\w.]+)")

# docs/superpowers/ is a historical archive of specs and plans (see
# tests/test_naming.py, which excludes it for the same reason): a version
# spelled out there is a record of what was true when it was written, not a
# live file cz bump should keep current.
EXCLUDED_DIRS = ("docs/superpowers/",)

# pyproject.toml is where the version-spelling patterns themselves are
# configured, as the `version_files` regex strings below -- it contains the
# patterns as configuration, not as a version to bump, so it is excluded by
# exact path rather than folded into a broader rule that could hide a real
# offender elsewhere.
EXCLUDED_FILES = ("pyproject.toml",)

# The two ways a tracked file spells a version that a release must keep in
# sync: a reference to the published action, and a pinned install spec.
VERSION_SPELLINGS = {
    "action reference": re.compile(r"EmadMokhtar/skill-evaluator@v[\w.]+"),
    "pinned install spec": VERSION_IN_SPEC,
}


def _commitizen() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["commitizen"]


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


def test_the_action_pins_the_current_version():
    default = safe_load(ACTION.read_text(encoding="utf-8"))["inputs"]["install-spec"]["default"]
    match = VERSION_IN_SPEC.fullmatch(default)
    assert match, f"install-spec default {default!r} does not pin a version"
    assert match.group("version") == skill_lens.__version__


def test_every_file_spelling_a_version_is_bumped_with_it():
    """A version written into a file that cz bump does not rewrite goes stale
    at the first release, and nothing else would report it.

    This walks every tracked file (as tests/test_naming.py does) looking for
    the two ways a file spells a version -- an action reference
    (`EmadMokhtar/skill-evaluator@v<version>`) or a pinned install spec
    (`skill-lens[pydantic-ai]==<version>`) -- rather than checking a fixed
    list of filenames, so a new file that starts spelling a version is caught
    the moment it exists instead of being invisible to this test forever.
    """
    listed = {entry.split(":", 1)[0] for entry in _commitizen()["version_files"]}
    offenders = []
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        name = str(path.relative_to(REPO_ROOT))
        if name in listed:
            continue
        for pattern in VERSION_SPELLINGS.values():
            if pattern.search(text):
                offenders.append(name)
                break
    assert not offenders, f"spells a version but is not in version_files: {offenders}"


def test_breaking_changes_stay_inside_zero_x():
    """M6 and M7 are still expected to change the eval file format, so a
    breaking change must not promote the project to 1.0."""
    assert _commitizen()["major_version_zero"] is True
