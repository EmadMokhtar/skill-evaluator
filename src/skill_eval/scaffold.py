"""Render a starter eval suite for a skill.

Structure is deterministic, so it belongs in the CLI; judgment about *which*
cases a given skill needs belongs to the writing-skill-evals skill. Rendering
is a pure function over a loaded `Skill` so it can be tested as a string, with
the file IO left to cli.py.
"""

from __future__ import annotations

from skill_eval.cases.loader import UNFILLED_SENTINEL
from skill_eval.models import Skill

# A judge block is deliberately absent: the default judge does not grade, so a
# generated rubric would error every case until the author configures one.
# references/eval-file-syntax.md in the writing-skill-evals skill covers it.
_TEMPLATE = """\
# Eval suite for the {name} skill, written by `skill-eval init`.
#
# Replace every {sentinel} below. Until you do, this file refuses to run:
# skill-eval treats an unfilled scaffold as an authoring error (exit 2) rather
# than reporting cases that check nothing as passes.
#
# Reference: https://emadmokhtar.github.io/skill-evaluator/eval-files/
cases:
  # 1. The common case. Keep at least one assertion -- a case with none passes
  #    without checking anything.
  - name: handles the common case
    task: >-
      {sentinel} the prompt a user would type
    tags: [smoke]
    assertions:
      - kind: contains
        value: >-
          {sentinel} a string every good answer contains

  # 2. The edge this skill exists to get right. Mock tools execute nothing:
  #    calling one records the call and returns `returns` verbatim, so the
  #    trajectory is genuinely the model's choice. `trajectory` catches the
  #    failure an output assertion cannot see -- deciding without looking.
  - name: takes the right path on the hard case
    task: >-
      {sentinel} the prompt that reaches the policy edge
    tools:
      - name: lookup_something
        description: >-
          {sentinel} what this tool does
        parameters:
          query: string
        returns: |-
          {sentinel} the JSON this tool returns
    trajectory:
      called: [lookup_something]
      max_calls: 3
    assertions:
      - kind: contains
        value: >-
          {sentinel} a string every good answer contains

  # 3 and 4. Does the agent reach for the skill at all? `mode: offered` stops
  #    force-loading it and registers it as a tool described by its frontmatter
  #    description, which for this skill reads:
  #
  #      {description}
  #
  #    Ship both halves. Positives alone score a skill that fires on
  #    everything at 100%.
  - name: reaches for the skill when it should
    mode: offered
    task: >-
      {sentinel} a prompt this skill is for
    tags: [triggering]
    trajectory:
      skill_triggered: true

  - name: leaves unrelated work alone
    mode: offered
    task: >-
      {sentinel} a prompt this skill is NOT for
    tags: [triggering]
    trajectory:
      skill_triggered: false
"""


def render_scaffold(skill: Skill) -> str:
    """Return the text of a starter eval suite for `skill`."""
    # Collapsed to one line: the description is interpolated into a YAML
    # comment, and a newline in it would end the comment mid-sentence and
    # leave the remainder as syntax.
    description = " ".join(skill.description.split()) or "(this skill has no description)"
    return _TEMPLATE.format(
        name=skill.name,
        description=description,
        sentinel=UNFILLED_SENTINEL,
    )
