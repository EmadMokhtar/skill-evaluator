"""The shipped examples must always parse and be well formed.

They are no longer run through FakeRunner: their assertions describe real model
behaviour now, so the zero-cost check is that discovery and schema validation
work on real files. The full run path is covered by the cassette tier.
"""

from pathlib import Path

from skill_eval.cases.loader import load_cases_for_skill
from skill_eval.skills.loader import load_skills

EXAMPLES = Path(__file__).parent.parent / "examples"


def test_every_example_skill_is_discovered():
    names = [skill.name for skill in load_skills(EXAMPLES)]
    assert names == ["greeting", "order-support"]


def test_every_example_skill_has_at_least_one_case():
    # This call also exercises the loader's cross-reference validation (an
    # undeclared trajectory tool or a duplicate tool name raises CaseParseError
    # -- see tests/test_case_loader.py), so a regression here surfaces as this
    # test erroring rather than needing its own dedicated example-only check.
    for skill in load_skills(EXAMPLES):
        assert load_cases_for_skill(skill), f"{skill.name} has no eval cases"
