"""Render a starter eval suite for a skill.

Structure is deterministic, so it belongs in the CLI; judgment about *which*
cases a given skill needs belongs to the writing-skill-evals skill. Rendering
is a pure function over a loaded `Skill` so it can be tested as a string, with
the file IO left to cli.py.
"""

from __future__ import annotations

import re
from pathlib import Path

from skill_lens.cases.loader import EVAL_SUFFIX, EVALS_DIRNAME, UNFILLED_SENTINEL
from skill_lens.models import Skill

# A judge block is deliberately absent: the default judge does not grade, so a
# generated rubric would error every case until the author configures one.
# references/eval-file-syntax.md in the writing-skill-evals skill covers it.
_TEMPLATE = """\
# Eval suite for the {name} skill, written by `skill-lens init`.
#
# Replace every {sentinel} below. Until you do, this file refuses to run:
# skill-lens treats an unfilled scaffold as an authoring error (exit 2) rather
# than reporting cases that check nothing as passes.
#
# Preserve the indentation of continuation lines after >- or |- so your text
# can safely include colons, apostrophes, and quotes.
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

  # 5. Does it produce the right artifact? `workspace:` gives the case a real,
  #    contained temporary directory seeded with the files named under
  #    `files:`, plus three built-in tools: list_files, read_file, write_file
  #    (six when the skill bundles scripts/, references/ or assets/:
  #    list_skill_files, read_skill_file and, under --allow-scripts, run_script).
  #    Assertions can then target a produced file instead of the chat output.
  #    Delete this case if the skill produces no files.
  - name: produces the expected file
    task: >-
      {sentinel} a prompt that asks for a file to be written
    workspace:
      files:
        input.txt: |-
          {sentinel} the input the skill reads; delete `files:` for an empty workspace
    assertions:
      - kind: file-produced
        file: >-
          {sentinel} the filename the skill must write
      - kind: contains
        file: >-
          {sentinel} the same filename
        value: >-
          {sentinel} a string that file must contain
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


def eval_filename(name: str) -> str:
    """A safe file name for a skill's eval suite.

    The name comes from user-supplied frontmatter, so it is not automatically
    a safe path component: `name: ../../x` would otherwise write outside the
    directory init was pointed at.
    """
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "skill"
    return f"{safe}{EVAL_SUFFIX}"


def scaffold_target(skill: Skill) -> Path:
    """Where `init` writes: wherever this skill already keeps its evals.

    Discovery prefers an `evals/` directory when one exists and only falls
    back to `*.eval.yaml` beside SKILL.md when it does not. An `init` that
    always created `evals/` would therefore hide any suite already sitting
    beside SKILL.md from every later run -- silently, with nothing red.
    """
    beside = list(skill.path.glob(f"*{EVAL_SUFFIX}"))
    if beside and not (skill.path / EVALS_DIRNAME).is_dir():
        return skill.path / eval_filename(skill.name)
    return skill.path / EVALS_DIRNAME / eval_filename(skill.name)
