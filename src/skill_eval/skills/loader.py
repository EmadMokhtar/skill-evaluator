"""Discover and parse SKILL.md files into Skill models."""

from __future__ import annotations

from pathlib import Path

import yaml

from skill_eval.models import Skill
from skill_eval.yaml_loading import safe_load

SKILL_FILENAME = "SKILL.md"


class SkillParseError(Exception):
    """Raised when a skill path is missing or a SKILL.md cannot be parsed."""


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter, body). Missing frontmatter yields ({}, whole text)."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    return safe_load(parts[1]) or {}, parts[2]


def parse_skill_file(skill_md: Path) -> Skill:
    """Parse one SKILL.md into a Skill, falling back to the dir name."""
    try:
        frontmatter, body = _split_frontmatter(skill_md.read_text())
    except yaml.YAMLError as exc:
        raise SkillParseError(f"invalid frontmatter in {skill_md}: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise SkillParseError(f"invalid frontmatter in {skill_md}: expected a mapping")
    return Skill(
        name=str(frontmatter.get("name") or skill_md.parent.name),
        description=str(frontmatter.get("description") or ""),
        instructions=body.strip(),
        path=skill_md.parent,
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
