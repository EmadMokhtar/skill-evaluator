"""The shipped skill must parse, and its syntax reference must track the code.

The reference restates material docs/eval-files.md also carries, so it is
pinned to the code rather than to the prose: a new assertion kind or case field
fails here until the skill mentions it.
"""

from __future__ import annotations

import re
from pathlib import Path

from skill_eval.cases.loader import load_cases_for_skill
from skill_eval.evaluators.assertion import ASSERTION_KINDS
from skill_eval.models import EvalCase
from skill_eval.skills.loader import load_skills, parse_skill_file

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skills" / "writing-skill-evals"
SYNTAX = SKILL_DIR / "references" / "eval-file-syntax.md"

_HEADING_RE_TEMPLATE = r"^##\s+{}\s*$"


def _documented_names(text: str, heading: str) -> set[str]:
    """The backticked names in the first column of the markdown table under
    `heading`, skipping that table's header and separator rows.

    This makes `test_the_syntax_reference_lists_every_case_field` and
    `test_the_syntax_reference_lists_every_assertion_kind` bidirectional: a
    name the code drops but the reference keeps behind must fail here too,
    not just the reverse.
    """
    heading_match = re.search(_HEADING_RE_TEMPLATE.format(re.escape(heading)), text, re.MULTILINE)
    assert heading_match, f"heading {heading!r} not found in {SYNTAX.name}"
    table_lines = []
    started = False
    for line in text[heading_match.end() :].splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            table_lines.append(stripped)
            started = True
        elif started:
            break
    assert len(table_lines) > 2, f"no table rows found under {heading!r} in {SYNTAX.name}"
    names: set[str] = set()
    for line in table_lines[2:]:  # skip the header row and the --- separator
        first_cell = line.split("|")[1].strip()
        cell_match = re.fullmatch(r"`(\w+)`", first_cell)
        if cell_match:
            names.add(cell_match.group(1))
    return names


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


def test_the_syntax_reference_documents_exactly_the_case_fields_the_code_has():
    # The converse of the check above: a field removed from EvalCase must not
    # linger, undetected, in the reference's table.
    text = SYNTAX.read_text(encoding="utf-8")
    documented = _documented_names(text, "Case fields")
    assert documented == set(EvalCase.model_fields)


def test_the_syntax_reference_documents_exactly_the_assertion_kinds_the_code_has():
    text = SYNTAX.read_text(encoding="utf-8")
    documented = _documented_names(text, "Assertion kinds")
    assert documented == set(ASSERTION_KINDS)


def test_the_skill_is_linked_into_dot_claude():
    link = REPO_ROOT / ".claude" / "skills" / "writing-skill-evals"
    assert link.is_dir(), "the skill is not linked into .claude/skills/"
    assert (link / "SKILL.md").is_file()


SKILLS_DIR = REPO_ROOT / "skills"


def _find_skill(name: str):
    skills = load_skills(SKILLS_DIR)
    for skill in skills:
        if skill.name == name:
            return skill
    raise AssertionError(f"skill {name!r} not found among {[s.name for s in skills]}")


def test_the_shipped_skill_has_cases_that_parse():
    # This call also exercises the loader's cross-reference validation, so an
    # undeclared trajectory tool or a leftover placeholder surfaces here.
    skill = _find_skill("writing-skill-evals")
    cases = load_cases_for_skill(skill)
    assert cases


def test_the_shipped_suite_ships_both_halves_of_the_triggering_pair():
    skill = _find_skill("writing-skill-evals")
    triggered = [
        case.trajectory.skill_triggered
        for case in load_cases_for_skill(skill)
        if case.mode == "offered"
    ]
    assert sorted(triggered) == [False, True]
