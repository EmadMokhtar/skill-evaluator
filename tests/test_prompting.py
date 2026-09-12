"""The system prompt both adapters build -- pure, no model, no network.

These are the rules `--min-delta` measures against, so they live beside the
one function every runner calls rather than inside either adapter's tests.
"""

from pathlib import Path

from skill_lens.models import EvalCase, Skill
from skill_lens.runners.prompting import (
    BASELINE_PREAMBLE,
    OFFERED_PREAMBLE,
    WORKSPACE_PREAMBLE,
    instructions,
    system_prompt,
)

SKILL = Skill(
    name="order-support",
    description="Handle refund requests",
    instructions="Always look up the order first.",
    path=Path("."),
)

EMPTY_SKILL = Skill(
    name="order-support",
    description="",
    instructions="",
    variant="baseline",
    path=Path("."),
)


def case(**kwargs) -> EvalCase:
    kwargs.setdefault("name", "c")
    kwargs.setdefault("task", "refund order 1234")
    return EvalCase(**kwargs)


def test_the_system_prompt_puts_identity_before_instructions():
    prompt = system_prompt(SKILL)
    assert prompt == "# order-support\n\nHandle refund requests\n\nAlways look up the order first."


def test_a_skill_with_no_description_keeps_its_header_and_instructions():
    skill = Skill(name="terse", description="", instructions="Do it.", path=Path("."))
    assert system_prompt(skill) == "# terse\n\nDo it."


def test_a_skill_with_nothing_to_say_gets_the_neutral_preamble():
    # The rule keys on emptiness, not on the arm: a runner that could branch
    # on the arm could cheat.
    assert system_prompt(EMPTY_SKILL) == BASELINE_PREAMBLE


def test_the_neutral_preamble_never_names_the_skill():
    assert "order-support" not in system_prompt(EMPTY_SKILL)


def test_a_baseline_resolved_from_git_still_gets_its_own_prompt():
    previous = Skill(
        name="order-support",
        description="Handle refunds",
        instructions="Old instructions.",
        variant="baseline",
        path=Path("."),
    )
    assert "Old instructions." in system_prompt(previous)


def test_an_offered_case_gets_only_the_offered_preamble():
    # Anything appended beyond OFFERED_PREAMBLE -- even a hint about what the
    # skill does -- would turn the trigger rate into a measurement of the
    # prompt, not the skill.
    assert instructions(SKILL, case(mode="offered"), has_workspace=False) == OFFERED_PREAMBLE


def test_no_workspace_leaves_the_instructions_untouched():
    plain = instructions(SKILL, case(), has_workspace=False)
    assert plain == system_prompt(SKILL)
    assert WORKSPACE_PREAMBLE not in plain


def test_the_workspace_preamble_is_byte_identical_in_both_arms():
    # If it were added to the candidate arm only, --min-delta would be
    # measuring the preamble rather than the skill.
    candidate = instructions(SKILL, case(), has_workspace=True)
    baseline = instructions(EMPTY_SKILL, case(), has_workspace=True)
    assert candidate.endswith(WORKSPACE_PREAMBLE)
    assert baseline.endswith(WORKSPACE_PREAMBLE)
    assert baseline == f"{BASELINE_PREAMBLE}\n\n{WORKSPACE_PREAMBLE}"


def test_the_workspace_preamble_never_names_the_skill():
    assert "order-support" not in WORKSPACE_PREAMBLE
    assert EMPTY_SKILL.name not in instructions(EMPTY_SKILL, case(), has_workspace=True)


def test_an_offered_case_keeps_its_own_preamble_and_gains_the_workspace_one():
    offered = instructions(SKILL, case(mode="offered"), has_workspace=True)
    assert offered == f"{OFFERED_PREAMBLE}\n\n{WORKSPACE_PREAMBLE}"
