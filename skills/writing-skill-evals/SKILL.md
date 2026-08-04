---
name: writing-skill-evals
description: Use when writing, running, or auditing skill-eval eval suites for an Agent Skill — deciding which cases a skill needs, choosing between assertions, judge rubrics and trajectory checks, and reading a failing case correctly
---

# Writing skill evals

An eval suite is worth exactly what its cases check. The two failure modes are a suite
that is green because it checks nothing, and a red case that gets the skill edited when
the eval was wrong. Everything here exists to prevent one of those.

## Workflow

1. **Orient.** Read the target `SKILL.md`. Look for existing evals in `evals/` or
   `*.eval.yaml` beside it, and run `skill-eval list <path>` to see what the tool already
   discovers.
2. **Scaffold.** Run `skill-eval init <skill-dir>`. Do not hand-roll the file structure —
   the generated file already carries the triggering pair and the placeholders that stop
   an unfinished suite from running.
3. **Mine the skill for claims.** Every "always", "never" and "must" in the instructions
   is a candidate case. The frontmatter `description` is a claim too, and it is precisely
   what the `mode: offered` cases test.
4. **Propose the case list and confirm it.** Show the user the cases you intend to write,
   one line each, before writing any YAML. Ask only for what `SKILL.md` cannot tell you:
   which tools exist and what they return, which policy edges are real, what a good answer
   sounds like. Do not ask for what the file already says.
5. **Write the cases.** Replace every `TODO(skill-eval)`. Read
   `references/eval-file-syntax.md` for the fields, and `references/case-design.md` for
   patterns to draw from.
6. **Run it.** `skill-eval run <path>` — the fake runner first, which proves the file
   parses and the checks are well formed; then a real runner if the user has a key
   configured.
7. **Triage every red case** by the rule below before editing anything.

## Choosing the check

| Use | When |
| --- | --- |
| `assertions` | The check is mechanical and stable: an id appears, a traceback does not. |
| `judge` | The claim is about quality, tone or reasoning. "Explains it plainly" is not a substring. |
| `trajectory` | The failure is invisible in the output — deciding without looking the order up, calling the tool that was forbidden. |
| `budget` | Guarding against a regression into a tool-call loop or a runaway answer. |

## Triage: the eval or the skill?

A red case means one of two different things, and naming which comes before any edit.

**The eval is wrong** when the output was actually fine: a regex tight enough to feel
rigorous but that rejects phrasing a model may legitimately vary, an assertion on wording
the skill never promised, a budget below what the task honestly costs.
`examples/greeting/greeting.eval.yaml` in the skill-eval repo documents a real instance —
a single-sentence regex relaxed after real model output failed it for no good reason.

**The skill is wrong** when the output genuinely was not what the skill claims: the
instruction is missing, or is present but too weak to survive a plausible prompt.

Fixing the eval is yours to do. **Changing the target `SKILL.md` is proposed and
confirmed, never silent** — the user is the author of their skill, and an eval that gets
its own subject rewritten to match it has stopped measuring anything.

## Rules that are not negotiable

- **Ship the negative control.** A suite of triggering positives scores a skill that fires
  on everything at 100%. `mode: offered` cases come in pairs.
- **Never leave a case with no assertions.** It passes without checking anything. If there
  is nothing to assert, the case is not ready.
- **Every rubric entry must be independently checkable and evidenced.** skill-eval records
  a check that passes without citing evidence as a failure, so a vague entry costs a case
  rather than buying coverage.
- **Never leave a `TODO(skill-eval)` behind.** The run will refuse it, which is the point,
  but a suite that cannot run is not a suite.

## Auditing an existing suite

Read `references/auditing.md` and work its checklist. Report findings; do not rewrite the
user's suite unasked.
