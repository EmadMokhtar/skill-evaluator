from pathlib import Path

import pytest

from skill_eval.skills.loader import (
    SkillParseError,
    load_skills,
    parse_skill_file,
    parse_skill_text,
)

SKILL_MD = """---
name: pdf
description: Work with PDF files
---

Use pdfplumber to extract text.
"""


def _write_skill(root, name, body=SKILL_MD):
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
    return skill_dir


def test_loads_a_single_skill_directory(tmp_path):
    _write_skill(tmp_path, "pdf")
    skills = load_skills(tmp_path / "pdf")
    assert len(skills) == 1
    assert skills[0].name == "pdf"
    assert skills[0].description == "Work with PDF files"
    assert "pdfplumber" in skills[0].instructions


def test_discovers_many_skills_under_a_parent_dir(tmp_path):
    _write_skill(tmp_path, "pdf")
    _write_skill(tmp_path, "xlsx", SKILL_MD.replace("name: pdf", "name: xlsx"))
    skills = load_skills(tmp_path)
    assert [s.name for s in skills] == ["pdf", "xlsx"]


def test_falls_back_to_directory_name_when_frontmatter_lacks_name(tmp_path):
    _write_skill(tmp_path, "fallback", "---\ndescription: no name here\n---\n\nBody.\n")
    skills = load_skills(tmp_path / "fallback")
    assert skills[0].name == "fallback"


def test_skill_without_frontmatter_uses_whole_file_as_instructions(tmp_path):
    _write_skill(tmp_path, "plain", "Just instructions, no frontmatter.\n")
    skills = load_skills(tmp_path / "plain")
    assert skills[0].name == "plain"
    assert "Just instructions" in skills[0].instructions


def test_missing_path_raises(tmp_path):
    with pytest.raises(SkillParseError, match="does not exist"):
        load_skills(tmp_path / "nope")


def test_path_without_any_skill_md_returns_empty(tmp_path):
    (tmp_path / "empty").mkdir()
    assert load_skills(tmp_path) == []


def test_malformed_frontmatter_raises_with_file_path(tmp_path):
    _write_skill(tmp_path, "bad", "---\nname: [unclosed\n---\n\nBody.\n")
    with pytest.raises(SkillParseError, match="bad/SKILL.md"):
        load_skills(tmp_path / "bad")


def test_frontmatter_value_containing_triple_dash_is_preserved(tmp_path):
    """Item 7: text.split("---", 2) matches "---" anywhere, including inside
    a value, silently truncating a description like "a---b" to "a" and
    mangling the body. A line-anchored split must treat only a "---" line
    on its own as a delimiter, so an embedded "---" inside a value is left
    alone.
    """
    _write_skill(
        tmp_path,
        "dashy",
        "---\nname: dashy\ndescription: a---b\n---\n\nBody text.\n",
    )
    skills = load_skills(tmp_path / "dashy")
    assert skills[0].description == "a---b"
    assert "Body text." in skills[0].instructions


def test_unreadable_non_utf8_skill_md_raises_skill_parse_error(tmp_path):
    """Item 4: a non-UTF-8 byte must fail fast with a precise message naming
    the file, not escape as a raw UnicodeDecodeError traceback.
    """
    skill_dir = tmp_path / "broken"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_bytes(b"\xff\xfe invalid")
    with pytest.raises(SkillParseError, match="SKILL.md"):
        load_skills(skill_dir)


def test_bare_on_frontmatter_name_parses_as_string_not_bool(tmp_path):
    # Regression test: PyYAML's YAML-1.1 implicit bool resolver treats bare
    # `on`/`off`/`yes`/`no` as booleans. Without the shared StrictBoolLoader,
    # `name: on` would parse to the Python bool True and then get stringified
    # to "True" by parse_skill_file's `str(...)` fallback, instead of "on".
    _write_skill(
        tmp_path,
        "onskill",
        "---\nname: on\ndescription: bare bool-like name\n---\n\nBody.\n",
    )
    skills = load_skills(tmp_path / "onskill")
    assert skills[0].name == "on"


def test_non_ascii_skill_md_loads_regardless_of_platform_encoding(tmp_path):
    # Regression test: read_text() without an explicit encoding uses the platform
    # default (e.g. cp1252 on Windows, ASCII under LC_ALL=C), which mojibakes or
    # raises on UTF-8 content. SKILL.md is always UTF-8, so the loader pins it.
    _write_skill(
        tmp_path,
        "accented",
        "---\nname: café\ndescription: naïve — 日本語\n---\n\nBody with émojis: 🎯\n",
    )
    skills = load_skills(tmp_path / "accented")
    assert skills[0].name == "café"
    assert skills[0].description == "naïve — 日本語"
    assert "🎯" in skills[0].instructions


def test_the_frontmatter_version_is_parsed(tmp_path):
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(
        "---\nname: pdf\ndescription: d\nversion: 1.3.0\n---\n\nBody.\n",
        encoding="utf-8",
    )
    assert parse_skill_file(skill_md).version == "1.3.0"


def test_a_missing_version_is_an_empty_string_not_an_error(tmp_path):
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("---\nname: pdf\n---\n\nBody.\n", encoding="utf-8")
    assert parse_skill_file(skill_md).version == ""


def test_a_numeric_version_is_kept_as_text(tmp_path):
    # YAML turns `1.2` into a float; a version is an identifier, not a number,
    # and 1.20 must not compare equal to 1.2.
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("---\nname: pdf\nversion: 1.2\n---\n\nBody.\n", encoding="utf-8")
    assert parse_skill_file(skill_md).version == "1.2"


def test_a_skill_is_a_candidate_unless_told_otherwise(tmp_path):
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("---\nname: pdf\n---\n\nBody.\n", encoding="utf-8")
    assert parse_skill_file(skill_md).variant == "candidate"


def test_a_skill_parses_from_text_without_touching_the_filesystem():
    text = "---\nname: pdf\ndescription: d\nversion: 2.0.0\n---\n\nBody.\n"
    skill = parse_skill_text(
        text, name_fallback="fallback", path=Path("/nowhere"), source="commit abc1234"
    )
    assert (skill.name, skill.version, skill.instructions) == ("pdf", "2.0.0", "Body.")


def test_malformed_text_names_its_source_not_a_file_path():
    with pytest.raises(SkillParseError, match="commit abc1234"):
        parse_skill_text(
            "---\nname: [unclosed\n---\n\nBody.\n",
            name_fallback="fallback",
            path=Path("/nowhere"),
            source="commit abc1234",
        )
