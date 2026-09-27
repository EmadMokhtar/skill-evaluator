"""`plugins/skill-lens/` is an Agent Plugin that ships the eval-writing skill.

Three files make it one:

- `plugins/skill-lens/plugin.json`, the portable Agent Plugins 1.0.0 manifest
  (https://agent-plugins.org/) that GitHub Copilot, VS Code, Cursor and Codex
  read;
- `plugins/skill-lens/.claude-plugin/plugin.json`, the manifest Claude Code
  reads instead, since it does not implement Agent Plugins;
- `.claude-plugin/marketplace.json` at the repository root, the one catalog
  path Claude Code, Copilot, VS Code and Codex all look for, listing the plugin
  directory.

An install copies the plugin directory and nothing else, so the directory holds
the manifests, the license and the skills, and nothing else.

No test run loads these files through a client, so nothing else would notice
them drift apart, pick up a field a strict client refuses, or ship a file
nobody meant to ship.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from repo_files import tracked_files
from skill_lens.yaml_loading import safe_load

REPO_ROOT = Path(__file__).resolve().parents[1]
# How the marketplace names the plugin directory: relative to the repository
# root, with the `./` every reader requires of a local source. Everything else
# is derived from it, so the entry and the directory cannot disagree.
PLUGIN_SOURCE = "./plugins/skill-lens"
PLUGIN_PREFIX = PLUGIN_SOURCE.removeprefix("./") + "/"  # as git spells it, `/` on every OS
PLUGIN_ROOT = REPO_ROOT / PLUGIN_PREFIX
PORTABLE = PLUGIN_ROOT / "plugin.json"
CLAUDE = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"
SKILLS_DIR = PLUGIN_ROOT / "skills"

# 1.0.0 is the published release; 1.1.0 is a working draft. Codex supports
# exactly this identifier and rejects a plugin declaring any other Agent Plugins
# version, so moving to a newer one waits until the clients do.
AGENT_PLUGINS_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"

# The manifest schema is closed (spec §5.2): these ten fields and no others.
PORTABLE_FIELDS = {
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}
PORTABLE_STRING_FIELDS = ("description", "homepage", "repository", "license")
AUTHOR_FIELDS = {"name", "email", "url"}

# Spec §5.5, as the official schema spells it.
PLUGIN_NAME_RE = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")

# The Agent Skills `name` rule: lowercase alphanumerics and single hyphens,
# neither first nor last, at most 64 characters.
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)

# What an install copies, relative to the plugin directory: the two manifests
# and the license, then per skill its SKILL.md, the Agent Skills bundle
# directories, and the skill's own eval suite -- which ships because skill-lens
# finds a suite only beside the SKILL.md it tests.
PLUGIN_FILES = {"plugin.json", ".claude-plugin/plugin.json", "LICENSE"}
SKILL_FILE = "SKILL.md"
SKILL_BUNDLE_DIRS = ("references/", "scripts/", "assets/")
SKILL_SUITE_RE = re.compile(r"evals/[^/]+\.eval\.yaml")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _shipped() -> list[str]:
    """The tracked files an install copies, relative to the plugin directory."""
    return [
        name.removeprefix(PLUGIN_PREFIX)
        for name in tracked_files()
        if name.startswith(PLUGIN_PREFIX)
    ]


def _skill_names() -> list[str]:
    return sorted(
        {
            name.split("/")[1]
            for name in _shipped()
            if name.startswith("skills/") and name.count("/") >= 2
        }
    )


def _is_skill_file(name: str) -> bool:
    parts = name.split("/", 2)
    if len(parts) != 3 or parts[0] != "skills":
        return False
    inside = parts[2]
    return (
        inside == SKILL_FILE
        or inside.startswith(SKILL_BUNDLE_DIRS)
        or SKILL_SUITE_RE.fullmatch(inside) is not None
    )


def test_the_portable_manifest_declares_agent_plugins_1_0_0():
    manifest = _load(PORTABLE)
    unknown = sorted(set(manifest) - PORTABLE_FIELDS)
    assert not unknown, f"fields Agent Plugins 1.0.0 does not define: {unknown}"
    assert manifest["$schema"] == AGENT_PLUGINS_SCHEMA
    name = manifest["name"]
    assert isinstance(name, str) and 1 <= len(name) <= 64 and PLUGIN_NAME_RE.fullmatch(name)
    for field in PORTABLE_STRING_FIELDS:
        if field in manifest:
            assert isinstance(manifest[field], str), f"{field} must be a string"
    author = manifest.get("author", {})
    extra_author = sorted(set(author) - AUTHOR_FIELDS)
    assert not extra_author, f"author fields not in the spec: {extra_author}"
    assert all(isinstance(value, str) for value in author.values())
    keywords = manifest.get("keywords", [])
    assert isinstance(keywords, list) and all(isinstance(word, str) for word in keywords)


def test_the_portable_manifest_is_a_regular_file():
    # Codex refuses a plugin.json that is a symlink, and the spec rejects a
    # plugin whose manifest resolves outside the plugin root.
    assert PORTABLE.is_file()
    assert not PORTABLE.is_symlink()


def test_both_manifests_describe_the_same_plugin():
    # Two files, one plugin: a description, keyword or license edited in one
    # alone would list the plugin differently depending on the agent.
    portable = _load(PORTABLE)
    claude = _load(CLAUDE)
    shared = set(portable) - {"$schema", "extensions"}
    assert set(claude) == shared, f"field sets differ: {sorted(set(claude) ^ shared)}"
    for field in sorted(shared):
        assert claude[field] == portable[field], f"{field} differs between the two manifests"


def test_the_plugin_spells_no_version():
    """Agents install the plugin from the tip of `main`, not from a release
    tag, and treat `version` as a cache key. With one pinned, an edit that
    ships without a release never reaches an existing install: Claude Code
    answers "already at the latest version" and keeps the old files, while a
    fresh install gets the new ones under the same number. With none, Claude
    Code keys each install on the commit, so every change reaches an existing
    install at its next update."""
    for path in (PORTABLE, CLAUDE):
        assert "version" not in _load(path), path.relative_to(REPO_ROOT).as_posix()
    [entry] = _load(MARKETPLACE)["plugins"]
    assert "version" not in entry


def test_the_marketplace_lists_the_plugin_directory():
    marketplace = _load(MARKETPLACE)
    plugin = _load(PORTABLE)
    assert marketplace["name"] == plugin["name"]
    assert marketplace["owner"]["name"]
    [entry] = marketplace["plugins"]
    assert entry["name"] == plugin["name"]
    assert entry["source"] == PLUGIN_SOURCE
    assert entry["description"] == plugin["description"]


def test_the_plugin_directory_is_named_after_the_plugin():
    # The spec does not require it, but a directory named otherwise is a second
    # name for the same plugin in every cache path and error message.
    assert PLUGIN_ROOT.name == _load(PORTABLE)["name"]


def test_the_plugin_directory_ships_the_manifests_the_license_and_the_skills_only():
    """An install copies this directory whole, so anything added here ships to
    every user of every agent. Inside `skills/` only the Agent Skills layout and
    each skill's own eval suite are allowed: a stray note, a `.DS_Store` or a
    misspelt `Skill.md` fails here rather than shipping."""
    shipped = _shipped()
    missing = sorted(PLUGIN_FILES - set(shipped))
    assert not missing, f"the plugin directory is missing: {missing}"
    unexpected = sorted(
        name for name in shipped if name not in PLUGIN_FILES and not _is_skill_file(name)
    )
    assert not unexpected, f"not a manifest, the license or part of a skill: {unexpected}"


def test_every_skill_directory_has_its_skill_md():
    # Spelled exactly, as git records it: a case-insensitive disk would find a
    # `Skill.md` that a client on Linux skips.
    shipped = set(_shipped())
    skills = _skill_names()
    assert skills, "the plugin ships no skill"
    missing = [name for name in skills if f"skills/{name}/{SKILL_FILE}" not in shipped]
    assert not missing, f"skill directories without an exact {SKILL_FILE}: {missing}"


def test_the_plugin_ships_the_repository_license():
    # An install copies only the plugin directory, and the MIT license asks for
    # its notice in every copy, so the plugin carries the same text as the root.
    assert (PLUGIN_ROOT / "LICENSE").read_bytes() == (REPO_ROOT / "LICENSE").read_bytes()


def test_every_shipped_skill_meets_the_agent_skills_rules():
    """A client skips a skill that breaks the Agent Skills rules, so a bad name
    would drop it from every install without an error anyone sees. The
    frontmatter is read directly: skill-lens's own loader falls back to the
    directory name when `name:` is missing, which a client does not."""
    for directory in _skill_names():
        text = (SKILLS_DIR / directory / SKILL_FILE).read_text(encoding="utf-8")
        match = FRONTMATTER_RE.match(text)
        assert match, f"{directory}: {SKILL_FILE} has no frontmatter"
        meta = safe_load(match.group(1))
        assert isinstance(meta, dict), f"{directory}: frontmatter is not a mapping"
        name = meta.get("name")
        assert name == directory, f"{directory}: `name` is {name!r}, not the directory's name"
        assert len(name) <= 64 and SKILL_NAME_RE.fullmatch(name), name
        description = meta.get("description")
        assert isinstance(description, str), f"{directory}: `description` is missing"
        assert 1 <= len(description.strip()) <= 1024, f"{directory}: description length"


def test_nothing_in_the_plugin_resolves_outside_it():
    # A conformant client denies any package path that resolves outside the
    # plugin root, and an install copies only the plugin directory, so a symlink
    # out of it -- or to nothing -- installs as a missing file.
    root = PLUGIN_ROOT.resolve()
    offending = []
    for directory, dirnames, filenames in os.walk(PLUGIN_ROOT, followlinks=False):
        for entry in dirnames + filenames:
            path = Path(directory) / entry
            if not path.is_symlink():
                continue
            shown = path.relative_to(REPO_ROOT).as_posix()
            try:
                target = path.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                # A dangling link, or a loop: RuntimeError on 3.11/3.12, ELOOP after.
                offending.append(f"{shown} ({exc})")
                continue
            if not target.is_relative_to(root):
                offending.append(shown)
    assert not offending, f"resolves outside the plugin directory: {offending}"
