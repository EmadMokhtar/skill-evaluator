# The eval-writing skill

`skill-lens init` gives you the structure of a suite. It cannot tell you which cases
*this* skill needs, or what a red case means. That judgment ships as an Agent Skill.

## What it does

Given a skill to evaluate, it reads the `SKILL.md` for its claims, proposes a case list
and confirms it with you, scaffolds and fills in the suite, runs it, and triages the
failures — distinguishing an eval that is wrong from a skill that is wrong, and proposing
changes to your `SKILL.md` rather than making them silently. It also audits suites you
already have: missing negative controls, cases that assert nothing, rubric entries no
evidence could support.

## Installing it

The skill lives in [`skills/writing-skill-evals/`](https://github.com/EmadMokhtar/skill-evaluator/tree/main/skills/writing-skill-evals).
Copy or symlink it into the skills directory your agent reads:

```bash
git clone https://github.com/EmadMokhtar/skill-evaluator
ln -s "$PWD/skill-evaluator/skills/writing-skill-evals" ~/.claude/skills/writing-skill-evals
```

Then ask for it by name, or describe the task — "write evals for my order-support skill".

## Using it

It expects `skill-lens` on `PATH`:

--8<-- "docs/snippets/install.md"

A project that already depends on `skill-lens` works as well. Everything the skill writes is
an ordinary eval file: nothing about the suite depends on the skill afterwards.

## Where to go next

Every field the skill writes is described in [Eval files](eval-files.md), and every term it
uses is defined in [Concepts](concepts.md). Once the suite passes, [CI integration](ci.md)
turns it into a gate on every pull request.
