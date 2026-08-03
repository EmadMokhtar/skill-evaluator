# Design: a skill for writing evals, and the scaffolder under it

Date: 2026-08-03
Status: approved, not yet implemented

## Problem

`skill-eval` can run an eval suite. Nothing helps anyone *write* one. The schema has
depth — assertions, `tools:`, `trajectory:`, `budget:`, judge rubrics, `mode: offered` —
and the traps are not obvious from the schema: a case with no assertions passes
vacuously, a suite of triggering positives without a negative control scores a skill that
fires on everything at 100%, and a regex tight enough to feel rigorous fails on output
that was legitimately fine.

Two deliverables answer that, and they answer different halves of it. A CLI scaffolder
supplies the structure deterministically. An Agent Skill supplies the judgment: which
cases are worth writing for *this* skill, and what a red case actually means.

## Deliverable 1 — `skill-eval init`

### Interface

```
skill-eval init <skill-dir> [--force]
```

`<skill-dir>` is a directory containing `SKILL.md`, read through the existing skill
loader so a malformed skill file fails exactly as it does elsewhere. Output goes to
`<skill-dir>/evals/<skill-name>.eval.yaml` — the first branch of eval discovery, and a
directory that scales to more files later.

| Situation | Exit |
| --- | --- |
| File written | 0 |
| No `SKILL.md` at the path | 2 |
| Output file exists and no `--force` | 2 |
| Path unwritable | 2 |

Exit `2` throughout: these are user errors, matching the CI contract.

### Structure

`src/skill_eval/scaffold.py` holds `render_scaffold(skill: Skill) -> str` — a pure
function over a loaded `Skill`, no filesystem, testable as a string. `cli.py` keeps doing
the IO and nothing else. The template is a module-level string; a templating dependency
would buy nothing here.

### What it emits

Four cases, each preceded by a comment naming its job:

1. A happy-path case with `tags: [smoke]` and one output assertion.
2. A policy-edge case carrying `tools:` and `trajectory:`, for the failure an output
   assertion cannot see.
3. and 4. The `mode: offered` pair — positive and negative control — emitted together,
   because either alone is misleading.

The skill's frontmatter `description` is quoted in a comment beside the triggering cases:
that string is the thing those cases test, and having it in view while writing them is
the point.

## The sentinel

Every unfilled field holds the literal `TODO(skill-eval)`.

`cases/loader.py` scans each raw case mapping recursively **before** Pydantic validation
and raises `CaseParseError` naming the file, the case, and the key path. Scanning first
matters: a sentinel sitting in a field Pydantic would reject on type should produce
"fill this in", not a type complaint about a string nobody meant to keep.

`CaseParseError` is already in `cli._AUTHORING_ERRORS`, so this exits `2` and no gating
code changes. Two properties follow:

- YAML comments are discarded before the scan, so the generated header can name and
  explain the token freely.
- A hand-written stub gets the same protection as a generated one. The guarantee belongs
  to the loader, not the generator.

This preserves the existing invariant that authoring errors abort the run and never score
as failures: an unfilled scaffold is a statement about the author's progress, not about
the skill.

Rejected alternatives: a `draft: true` field on `EvalCase` (schema churn, and a finished
case that still errors because the flag outlived its purpose); a separate `skill-eval
lint` (the guarantee then holds only where CI remembers to invoke it).

## Deliverable 2 — the `writing-skill-evals` skill

### Placement

Canonical at `skills/writing-skill-evals/`, symlinked from `.claude/skills/` so this repo
runs the skill on itself. Users copy or symlink it into their own `.claude/skills/`;
documented, not automated. No new CLI surface for installation.

### SKILL.md

Short, and restricted to what is procedural or a judgment call. Bulk goes to
`references/`.

**Workflow.** Orient (locate `SKILL.md`, locate existing evals, `skill-eval list`) →
`skill-eval init` rather than hand-rolling structure → mine the skill for claims: every
"always / never / must" is a candidate case → propose the case list and confirm it with
the user → fill in every sentinel → run → triage.

**The interview.** Ask for what `SKILL.md` cannot supply: which tools exist and what they
return, which policy edges are real, what a good answer sounds like. Do not ask for what
the file already says.

**Choosing the check.** Assertion when the check is mechanical and stable. Judge when the
claim is about quality or tone — "explains it plainly" is not a substring. Trajectory
when the failure is invisible in the output, such as deciding without looking the order
up. Budget to catch a regression into a tool-call loop.

**Triage.** A red case means one of two different things, and naming which comes before
any edit: the eval is wrong (an over-tight regex, phrasing a model may legitimately vary
— `examples/greeting` documents exactly this) or the skill is wrong. Edits to the target
`SKILL.md` are proposed and confirmed, never silent.

**Non-negotiables.** Ship the negative control. Never leave a case with no assertions.
Rubric entries must be independently checkable and must cite evidence.

### references/

Loaded only when the work reaches them:

- `eval-file-syntax.md` — field and kind tables; `tools:`, `trajectory:`, `budget:`,
  `judge:`.
- `case-design.md` — case patterns by skill archetype, deriving cases from claims, and
  writing rubrics that can actually be evidenced.
- `auditing.md` — the audit checklist and the failure-triage table.

### Evals for the skill

`skills/writing-skill-evals/evals/`:

- The `mode: offered` pair: a positive ("write evals for my greeting skill") and a
  negative control on unrelated work.
- An authoring case with mock `read_file` / `write_file` / `run_command` tools,
  `trajectory.order: [read_file, write_file]`, and a judge rubric over the YAML produced:
  does it include a negative control, is every sentinel gone.
- An audit case whose mock file read returns a deliberately bad suite; the rubric asks
  whether the missing negative control and the assertion-less case were both named.

As with `examples/`, the zero-cost tier proves the suite parses and lists; the judged
cases need a key. CI's self-check step extends to `skill-eval list ./skills`.

### Drift control

The syntax reference restates material `docs/eval-files.md` also carries, so a test pins
it to the code rather than to the prose: the kinds it lists must equal
`evaluators.assertion.ASSERTION_KINDS`, and the fields it lists must equal
`EvalCase.model_fields`. Same mechanism as `tests/test_docs.py`.

## Documentation

| File | Change |
| --- | --- |
| `docs/cli.md` | the `init` command and its exit codes |
| `docs/eval-files.md` | the sentinel rule |
| new page + `mkdocs.yml` nav | the skill: what it does, how to install it |
| `ARCHITECTURE.md`, `CLAUDE.md` | the sentinel invariant; `scaffold.py` in the module map |
| `docs/roadmap.md` | M7 recorded as partly shipped |

## Testing

Tests first, and the pipeline tier stays offline and deterministic.

- `render_scaffold`: names the skill, contains the sentinel, parses as YAML, yields four
  cases.
- A freshly generated scaffold raises `CaseParseError` on load; the same file with
  sentinels replaced loads clean.
- Sentinel detection nested inside `tools:`, `judge.rubric`, and `assertions:`; a
  sentinel appearing only in a comment is not detected.
- The four CLI exit paths.
- The shipped skill's eval files parse — mirroring `tests/test_examples.py`.
- The drift test described above.

## Out of scope

Batch `init` over a directory of skills, `--stdout`, and any packaged installer for the
skill. Each is a small addition later if the need shows up.
