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
    for skill in load_skills(EXAMPLES):
        assert load_cases_for_skill(skill), f"{skill.name} has no eval cases"


def test_declared_trajectory_tools_are_actually_declared_as_tools():
    # A trajectory check naming a tool the case never declares can never pass,
    # and would look like a skill failure rather than the typo it is.
    for skill in load_skills(EXAMPLES):
        for case in load_cases_for_skill(skill):
            declared = {tool.name for tool in case.tools}
            if case.trajectory is None:
                continue
            referenced = set(
                case.trajectory.called + case.trajectory.forbidden + case.trajectory.order
            )
            assert referenced <= declared, f"{skill.name}/{case.name}: {referenced - declared}"
