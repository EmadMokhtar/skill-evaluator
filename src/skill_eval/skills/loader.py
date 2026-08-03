"""Discover and parse SKILL.md files into Skill models."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from skill_eval.models import Skill
from skill_eval.yaml_loading import safe_load

SKILL_FILENAME = "SKILL.md"

_FRONTMATTER_DELIMITER = re.compile(r"^---\s*$", re.MULTILINE)


class SkillParseError(Exception):
    """Raised when a skill path is missing or a SKILL.md cannot be parsed."""


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter, body). Missing frontmatter yields ({}, whole text).

    The delimiter must be a line containing only ``---`` (optionally
    trailing whitespace); a "---" occurring inside a value on an otherwise
    non-delimiter line (e.g. ``description: a---b``) is left alone instead
    of being treated as a frontmatter boundary.
    """
    if not text.startswith("---"):
        return {}, text
    parts = _FRONTMATTER_DELIMITER.split(text, maxsplit=2)
    if len(parts) < 3:
        return {}, text
    return safe_load(parts[1]) or {}, parts[2]


def _version(frontmatter: dict, source: str) -> str:
    """A skill's declared version, which must be text.

    YAML resolves `version: 1.20` to the float 1.2, which is the same object it
    resolves `version: 1.2` to -- so a bare decimal version cannot round-trip,
    and two genuinely different versions would silently compare equal in
    `--baseline previous`. Rather than claim a guarantee `str()` cannot deliver,
    anything YAML did not hand back as a string is rejected here and the author
    is told to quote it.
    """
    declared = frontmatter.get("version")
    if declared is None:
        return ""
    if not isinstance(declared, str):
        raise SkillParseError(
            f"invalid frontmatter in {source}: version must be quoted text, got "
            f'{declared!r} -- write version: "{declared}"'
        )
    return declared


def parse_skill_text(text: str, *, name_fallback: str, path: Path, source: str) -> Skill:
    """Parse SKILL.md content into a Skill.

    `source` only ever appears in error messages: the content may have come
    from a file or from `git show`, and an error that says "commit 4f2a1c" is
    the difference between a useful report and a confusing one.
    """
    try:
        frontmatter, body = _split_frontmatter(text)
    except yaml.YAMLError as exc:
        raise SkillParseError(f"invalid frontmatter in {source}: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise SkillParseError(f"invalid frontmatter in {source}: expected a mapping")
    return Skill(
        name=str(frontmatter.get("name") or name_fallback),
        description=str(frontmatter.get("description") or ""),
        instructions=body.strip(),
        version=_version(frontmatter, source),
        path=path,
    )


def parse_skill_file(skill_md: Path) -> Skill:
    """Parse one SKILL.md into a Skill, falling back to the dir name."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SkillParseError(f"cannot read {skill_md}: {exc}") from exc
    return parse_skill_text(
        text,
        name_fallback=skill_md.parent.name,
        path=skill_md.parent,
        source=str(skill_md),
    )


def load_skills(path: Path) -> list[Skill]:
    """Walk `path` for SKILL.md files and return the skills, sorted by name."""
    path = Path(path)
    if not path.exists():
        raise SkillParseError(f"skill path does not exist: {path}")
    if path.is_file():
        return [parse_skill_file(path)]
    if (path / SKILL_FILENAME).is_file():
        return [parse_skill_file(path / SKILL_FILENAME)]
    skills = [parse_skill_file(md) for md in sorted(path.rglob(SKILL_FILENAME))]
    return sorted(skills, key=lambda s: s.name)
