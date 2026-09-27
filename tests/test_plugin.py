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
what a client loads and nothing else.

No test run loads these files through a client, so nothing else would notice
them drift apart, go stale at a release, or pick up a field a strict client
refuses.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

import skill_lens
from skill_lens.skills.loader import parse_skill_file

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "skill-lens"
# How the marketplace names the plugin directory: relative to the repository
# root, with the `./` every reader requires of a local source.
PLUGIN_SOURCE = "./plugins/skill-lens"
PORTABLE = PLUGIN_ROOT / "plugin.json"
CLAUDE = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"
SKILLS_DIR = PLUGIN_ROOT / "skills"

# Everything an install copies: the two manifests, the license, and the skills.
PLUGIN_FILES = {"plugin.json", ".claude-plugin/plugin.json", "LICENSE"}
PLUGIN_DIRS = ("skills/",)

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
PORTABLE_STRING_FIELDS = ("version", "description", "homepage", "repository", "license")
AUTHOR_FIELDS = {"name", "email", "url"}

# Spec §5.5, as the official schema spells it.
PLUGIN_NAME_RE = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")

# The Agent Skills `name` rule: lowercase alphanumerics and single hyphens,
# neither first nor last, at most 64 characters.
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Same archive exclusion as tests/test_release_config.py: a manifest quoted in a
# plan is a record, not a file a release keeps current.
EXCLUDED_DIRS = ("docs/superpowers/",)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _tracked_files() -> list[str]:
    return subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()


def _tracked_plugin_manifests() -> list[str]:
    return [
        name
        for name in _tracked_files()
        if Path(name).name == "plugin.json" and not name.startswith(EXCLUDED_DIRS)
    ]


def _version_file_patterns() -> dict[str, list[re.Pattern]]:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns: dict[str, list[re.Pattern]] = {}
    for entry in config["tool"]["commitizen"]["version_files"]:
        name, _, regex = entry.partition(":")
        patterns.setdefault(name, []).append(re.compile(regex))
    return patterns


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


def test_the_plugin_carries_the_package_version():
    # The skill documents the CLI, so the plugin is released with it: an agent
    # sees an update exactly when a new skill-lens version ships.
    for path in (PORTABLE, CLAUDE):
        assert _load(path)["version"] == skill_lens.__version__, path.relative_to(REPO_ROOT)


def test_every_plugin_manifest_is_bumped_with_the_package():
    """A manifest cz bump does not rewrite spells a stale version at the first
    release. Every tracked `plugin.json` is checked, so a manifest added for
    another client later is covered the moment it exists.

    That a listed pattern still rewrites a line carrying the current version at
    release time is `tests/test_release_config.py`'s replay; this test proves
    the version line has a pattern at all.
    """
    manifests = _tracked_plugin_manifests()
    expected = {str(path.relative_to(REPO_ROOT)) for path in (PORTABLE, CLAUDE)}
    assert expected <= set(manifests)
    patterns = _version_file_patterns()
    uncovered = []
    for name in manifests:
        lines = (REPO_ROOT / name).read_text(encoding="utf-8").splitlines()
        version_lines = [line for line in lines if re.match(r'\s*"version"\s*:', line)]
        assert len(version_lines) == 1, f"{name} must spell its version on exactly one line"
        if not any(pattern.search(version_lines[0]) for pattern in patterns.get(name, [])):
            uncovered.append(name)
    assert not uncovered, f"no version_files pattern rewrites the version line of: {uncovered}"


def test_the_marketplace_lists_the_plugin_directory():
    marketplace = _load(MARKETPLACE)
    plugin = _load(PORTABLE)
    assert marketplace["name"] == plugin["name"]
    assert marketplace["owner"]["name"]
    [entry] = marketplace["plugins"]
    assert entry["name"] == plugin["name"]
    assert entry["source"] == PLUGIN_SOURCE
    assert (REPO_ROOT / PLUGIN_SOURCE).resolve() == PLUGIN_ROOT.resolve()
    assert entry["description"] == plugin["description"]
    # Claude Code lets the plugin.json version win silently over a marketplace
    # one, so a second spelling here could only ever be the stale one.
    assert "version" not in entry


def test_the_plugin_directory_is_named_after_the_plugin():
    # The spec does not require it, but a directory named otherwise is a second
    # name for the same plugin in every cache path and error message.
    assert PLUGIN_ROOT.name == _load(PORTABLE)["name"]


def test_the_plugin_directory_holds_what_a_client_loads_and_nothing_else():
    """An install copies this directory whole, so anything added here ships to
    every user of every agent. Keeping it to the manifests, the license and the
    skills is what keeps an install small."""
    prefix = str(PLUGIN_ROOT.relative_to(REPO_ROOT)) + "/"
    shipped = [name.removeprefix(prefix) for name in _tracked_files() if name.startswith(prefix)]
    unexpected = sorted(
        name for name in shipped if name not in PLUGIN_FILES and not name.startswith(PLUGIN_DIRS)
    )
    assert not unexpected, f"not a manifest, the license or a skill: {unexpected}"
    missing = sorted(PLUGIN_FILES - set(shipped))
    assert not missing, f"the plugin directory is missing: {missing}"


def test_the_plugin_ships_the_repository_license():
    # An install copies only the plugin directory, and the MIT license asks for
    # its notice in every copy, so the plugin carries the same text as the root.
    assert (PLUGIN_ROOT / "LICENSE").read_bytes() == (REPO_ROOT / "LICENSE").read_bytes()


def test_every_shipped_skill_is_one_a_plugin_client_loads():
    """Clients discover skills only as immediate children of `skills/` and skip
    one that breaks the Agent Skills rules, so a bad name would drop the skill
    from every install without an error anyone sees."""
    skill_dirs = sorted(path for path in SKILLS_DIR.iterdir() if (path / "SKILL.md").is_file())
    assert skill_dirs, "the plugin ships no skill"
    for skill_dir in skill_dirs:
        skill = parse_skill_file(skill_dir / "SKILL.md")
        assert skill.name == skill_dir.name, f"{skill_dir.name}: name must match its directory"
        assert len(skill.name) <= 64 and SKILL_NAME_RE.fullmatch(skill.name), skill.name
        assert 1 <= len(skill.description) <= 1024, f"{skill.name}: description length"


def test_nothing_in_the_plugin_resolves_outside_it():
    # A conformant client denies any package path that resolves outside the
    # plugin root, and an install copies only the plugin directory, so a symlink
    # out of it installs as a missing file.
    root = PLUGIN_ROOT.resolve()
    escaping = [
        str(path.relative_to(REPO_ROOT))
        for path in PLUGIN_ROOT.rglob("*")
        if path.is_symlink() and not path.resolve().is_relative_to(root)
    ]
    assert not escaping, f"resolves outside the plugin directory: {escaping}"
