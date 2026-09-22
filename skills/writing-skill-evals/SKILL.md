---
name: writing-skill-evals
description: Use when writing, running, or auditing skill-lens eval suites for an Agent Skill — deciding which cases a skill needs, choosing between assertions, judge rubrics and trajectory checks, and reading a failing case correctly
---

# Writing skill evals

An eval suite is worth exactly what its cases check. The two failure modes are a suite
that is green because it checks nothing, and a red case that gets the skill edited when
the eval was wrong. Everything here exists to prevent one of those.

## Workflow

1. **Orient.** Read the target `SKILL.md`. Look for existing evals in `evals/` or
   `*.eval.yaml` beside it, and run `skill-lens list <path>` to see what the tool already
   discovers.
2. **Scaffold, or extend.** If step 1 found no suite, run `skill-lens init <skill-dir>` —
   do not hand-roll the file structure, the generated file already carries the triggering
   pair, a `workspace:` case for a skill that produces a file (delete it if this one does
   not), and the placeholders that stop an unfinished suite from running. Pointed at a
   directory of skills, `init` scaffolds every skill that has no suite and skips the rest.
   If a suite already exists, do not run `init` on that skill: it never overwrites a file,
   but it may add a second scaffold beside the one you have, which is not what you want.
   Read `references/auditing.md` and extend the suite you found instead.
   (`--force` overwrites the file outright, which is not what you want when a suite is
   already there to build on.)
3. **Mine the skill for claims.** Every "always", "never" and "must" in the instructions
   is a candidate case. The frontmatter `description` is a claim too, and it is precisely
   what the `mode: offered` cases test.
4. **Propose the case list and confirm it.** Show the user the cases you intend to write,
   one line each, before writing any YAML. Ask only for what `SKILL.md` cannot tell you:
   which tools exist and what they return, which policy edges are real, what a good answer
   sounds like. Do not ask for what the file already says.
5. **Write the cases.** Replace every `TODO(skill-lens)`. Read
   `references/eval-file-syntax.md` for the fields, and `references/case-design.md` for
   patterns to draw from.
6. **Validate, then run.** `skill-lens list <path>` is the structural check — it parses
   every case at zero cost and catches a malformed file before anything else runs; it is
   what this repo's own CI self-check uses. `skill-lens run <path>` is a different thing:
   it needs a configured runner, and under the defaults (`FakeRunner`, `FakeJudge`) a
   `mode: offered` case and any case with a `judge:` block come back `errored`, not
   `failed` — the fake runner cannot report a triggering decision and the fake judge does
   not grade. Neither is a statement about the skill. Configure a real runner (and a judge,
   if any case needs one) before reading those results as anything.
7. **Triage every red case** by the rule below before editing anything.

## Choosing the check

| Use | When |
| --- | --- |
| `assertions` | The check is mechanical and stable: an id appears, a traceback does not. |
| `judge` | The claim is about quality, tone or reasoning. "Explains it plainly" is not a substring. |
| `trajectory` | The failure is invisible in the output — deciding without looking the order up, calling the tool that was forbidden, calling it without the filter the skill demands (`call_args`). |
| `budget` | Guarding against a regression into a tool-call loop or a runaway answer. |

## Assert on the artifact, not the sentence about it

When a skill's job is to produce a file, assert on the file. `contains` against
the chat output only proves the agent *said* it wrote a total; `contains` with
`file: report.md` proves the total is in the report.

Reach for a judge `artifacts:` rubric when quality lives inside the document —
structure, completeness, tone. Reach for `file-produced` and `json-schema` when
the requirement is exact. A rubric that asks "is the JSON valid" is a schema
assertion wearing a judge's costume: it costs tokens and it is less reliable.

## Triage: the eval, the skill, or the harness?

A red case means one of three different things, and naming which comes before any edit.
Start with whether the case is `failed` or `errored` — that alone rules one branch out or
in before you look at anything else.

**The eval is wrong** when the case `failed` and the output was actually fine: a regex
tight enough to feel rigorous but that rejects phrasing a model may legitimately vary, an
assertion on wording the skill never promised, a budget below what the task honestly
costs. `examples/greeting/greeting.eval.yaml` in the skill-lens repo documents a real
instance — a single-sentence regex relaxed after real model output failed it for no good
reason.

**The skill is wrong** when the case `failed` and the output genuinely was not what the
skill claims: the instruction is missing, or is present but too weak to survive a
plausible prompt.

**The harness is wrong** when the case `errored`. An errored case never ran to a real
verdict — the runner or the judge broke, or was never configured for what the case asks
of it. A `mode: offered` case under the default fake runner, or a `judge:` case under the
default fake judge, errors this way by design: the fake runner cannot report a triggering
decision, and the fake judge does not grade. An errored case says nothing about the
skill — fix the configuration (a real runner, a real judge), and change neither the eval
nor the SKILL.md.

Fixing the eval is yours to do. **Changing the target `SKILL.md` is proposed and
confirmed, never silent** — the user is the author of their skill, and an eval that gets
its own subject rewritten to match it has stopped measuring anything.

## Rules that are not negotiable

- **Ship the negative control.** A suite of triggering positives scores a skill that fires
  on everything at 100%. `mode: offered` cases come in pairs.
- **Never leave a case with no check.** At least one of `assertions`, `trajectory` or
  `judge` — a case with none of the three passes without checking anything. `mode:
  offered` cases legitimately carry `trajectory.skill_triggered` and no `assertions`;
  that still counts as a check.
- **Every rubric entry must be independently checkable and evidenced.** skill-lens records
  a check that passes without citing evidence as a failure, so a vague entry costs a case
  rather than buying coverage.
- **Never leave a `TODO(skill-lens)` behind.** The run will refuse it, which is the point,
  but a suite that cannot run is not a suite.

## Auditing an existing suite

Read `references/auditing.md` and work its checklist. Report findings; do not rewrite the
user's suite unasked.
