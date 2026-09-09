"""Guard the release configuration that nothing else would notice breaking.

The action's pinned version and the package version are two places that must
agree; a release that changes one and not the other ships an action installing
somebody else's version.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import skill_lens
from skill_lens.yaml_loading import safe_load

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
ACTION = REPO_ROOT / "action.yml"

VERSION_IN_SPEC = re.compile(r"skill-lens\[pydantic-ai\]==(?P<version>[\w.]+)")


def _commitizen() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["commitizen"]


def test_the_action_pins_the_current_version():
    default = safe_load(ACTION.read_text(encoding="utf-8"))["inputs"]["install-spec"]["default"]
    match = VERSION_IN_SPEC.fullmatch(default)
    assert match, f"install-spec default {default!r} does not pin a version"
    assert match.group("version") == skill_lens.__version__


def test_every_file_spelling_a_version_is_bumped_with_it():
    """A version written into a file that cz bump does not rewrite goes stale
    at the first release, and nothing else would report it."""
    listed = {entry.split(":", 1)[0] for entry in _commitizen()["version_files"]}
    spellings = {
        "action.yml": VERSION_IN_SPEC,
        "README.md": re.compile(r"skill-evaluator@v[\w.]+"),
        "docs/ci.md": re.compile(r"skill-evaluator@v[\w.]+"),
    }
    for name, pattern in spellings.items():
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        if pattern.search(text):
            assert name in listed, f"{name} spells a version but is not in version_files"


def test_breaking_changes_stay_inside_zero_x():
    """M6 and M7 are still expected to change the eval file format, so a
    breaking change must not promote the project to 1.0."""
    assert _commitizen()["major_version_zero"] is True
