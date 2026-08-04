"""The shipped skill must parse, and its syntax reference must track the code.

The reference restates material docs/eval-files.md also carries, so it is
pinned to the code rather than to the prose: a new assertion kind or case field
fails here until the skill mentions it.
"""

from __future__ import annotations

from pathlib import Path

from skill_eval.evaluators.assertion import ASSERTION_KINDS
from skill_eval.models import EvalCase
from skill_eval.skills.loader import parse_skill_file

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skills" / "writing-skill-evals"
SYNTAX = SKILL_DIR / "references" / "eval-file-syntax.md"


def test_the_skill_parses():
    skill = parse_skill_file(SKILL_DIR / "SKILL.md")
    assert skill.name == "writing-skill-evals"
    assert skill.description


def test_the_syntax_reference_lists_every_assertion_kind():
    text = SYNTAX.read_text(encoding="utf-8")
    for kind in ASSERTION_KINDS:
        assert f"`{kind}`" in text, f"assertion kind {kind!r} is missing from {SYNTAX.name}"


def test_the_syntax_reference_lists_every_case_field():
    text = SYNTAX.read_text(encoding="utf-8")
    for field in EvalCase.model_fields:
        assert f"`{field}`" in text, f"case field {field!r} is missing from {SYNTAX.name}"


def test_the_skill_is_linked_into_dot_claude():
    link = REPO_ROOT / ".claude" / "skills" / "writing-skill-evals"
    assert link.is_dir(), "the skill is not linked into .claude/skills/"
    assert (link / "SKILL.md").is_file()
