"""The system prompt each arm of a case receives -- shared by every adapter.

These rules are what `--min-delta` measures against: the baseline arm must
never see the skill's name, and the workspace preamble must be byte-identical
in both arms. One function every runner calls is how two adapters stay in
step; a second copy could drift, and the delta would then measure the drift.
This module imports no agent framework, which is what lets it sit outside the
adapter boundary `tests/test_framework_isolation.py` guards.
"""

from __future__ import annotations

from skill_lens.models import EvalCase, Skill

# In offered mode the agent must be able to *decline* the skill, so the system
# prompt says nothing about what the skill does -- only that tools exist and
# describe themselves. Anything more would be a nudge, and a nudged trigger
# rate measures the prompt rather than the skill.
OFFERED_PREAMBLE = (
    "You are a helpful assistant. Some capabilities are available to you as tools. "
    "Read their descriptions and use one when it genuinely fits the request. "
    "If none fits, just answer directly."
)

# A skill with no description and no instructions has nothing to say. Emitting
# the usual `# {name}` header anyway would put the skill's name into a baseline
# run's prompt, and the delta would then measure that leak rather than the
# skill. The rule keys on emptiness, not on the arm, so no runner has to know
# which arm it is serving -- a runner that *could* branch on the arm could cheat.
BASELINE_PREAMBLE = "You are a helpful assistant."

# Appended to whatever preamble the arm already uses, byte-identically in both
# arms, and naming no skill. The agent has to be told a working directory
# exists or it cannot use it; added to the candidate arm only, this text would
# become part of what --min-delta measures.
WORKSPACE_PREAMBLE = (
    "You have a working directory. Use `list_files` to see what is in it, "
    "`read_file` to read a file, and `write_file` to create or replace one. "
    "All paths are relative to that directory."
)


def system_prompt(skill: Skill) -> str:
    """The skill, as the agent sees it: identity first, then its instructions."""
    if not skill.description and not skill.instructions:
        return BASELINE_PREAMBLE
    header = f"# {skill.name}"
    if skill.description:
        header = f"{header}\n\n{skill.description}"
    return f"{header}\n\n{skill.instructions}".strip()


def instructions(skill: Skill, case: EvalCase, has_workspace: bool) -> str:
    """The full system prompt for one arm of one case.

    Kept apart from any agent construction so the arm-identical rule above can
    be tested without a model, a provider or a network.
    """
    base = OFFERED_PREAMBLE if case.mode == "offered" else system_prompt(skill)
    return f"{base}\n\n{WORKSPACE_PREAMBLE}" if has_workspace else base
