# skill-lens `evals.json` support — Design

**Date:** 2026-10-05
**Status:** Approved (design); the implementation plan follows in a separate document

## 1. Scope

The Agent Skills specification's own evaluation guide (agentskills.io, "Evaluating skill
output quality") and Anthropic's `skill-creator` both store test cases in
`evals/evals.json` beside `SKILL.md`. skill-lens reads only its own YAML eval files, so a
skill that already has an `evals.json` must be rewritten by hand before it can be run. This
change lets skill-lens read the shared file directly.

This change ships as **one pull request** (`feat: read evals.json eval files`):

- **A converter**, `cases/evals_json.py`, that turns a parsed `evals.json` into the raw case
  mappings the YAML loader already produces. Those mappings go through the same
  `EvalCase.model_validate` and `_validate_*` chain, so no existing check is bypassed.
- **Discovery** of `evals/evals.json`, and `--evals PATH` accepting a `.json` file.
- **Strict reading** of the shared fields only. Unknown keys are refused, naming the key.
- **No change to any runner, evaluator, reporter or the `EvalCase` model.** An `evals.json`
  case is an ordinary `EvalCase` with a `judge:` block and, when it names input files, a
  `workspace:` block.

### The format, as found

Two published schemas, one shape:

| Source | Cases key | Assertions key | `files` |
| --- | --- | --- | --- |
| agentskills.io guide | `evals[]` | `assertions` | relative to the skill root |
| Anthropic `skill-creator` (`references/schemas.md`) | `evals[]` | `expectations` | relative to the skill root |

Each case has `id`, `prompt`, `expected_output`, optional `files` and a list of plain-English
statements. The guide's first-stage files carry only `prompt` and `expected_output`; the
statements are added after a first run. The two spellings are a known inconsistency in the
spec repository (issue #459), so skill-lens accepts both.

A third file shape exists in the wild: Addy Osmani's `agent-skills` keeps one file per skill at
`evals/cases/<skill>.json`, adds a top-level `trigger` block and a per-case `kind`, and
resolves `files` against `evals/fixtures/` (entries may be folders). That is a dialect of the
format, not the format, and it is deferred below.

### Explicitly deferred

| Deferred | Why |
| --- | --- |
| Osmani's dialect (`evals/cases/*.json`, `trigger`, `kind`, fixture-relative and folder `files`) | A different file location and different path semantics. Its `trigger` block maps naturally to `mode: offered` with negative controls, which makes it a good follow-up, but it is its own design. |
| Writing `grading.json`, `benchmark.json`, `timing.json` or `feedback.json` | This change reads the input format only. skill-lens keeps its own report formats. |
| Blind comparison of two outputs | The guide's optional holistic judge. Nothing in `evals.json` asks for it. |
| Binary input files (images, spreadsheets, PDFs) | Workspace files are UTF-8 text today (`WorkspaceSpec.files: dict[str, str]`). Supporting bytes changes the model and the seeding code. |
| The judge reading files the agent produced | `evals.json` does not name output files, so there is nothing to pass to `judge.artifacts`. The judge sees the agent's reply. |
| Ignoring unknown keys | Rejected for now; see the decisions table. |

## 2. Decisions

| Decision | Why |
| --- | --- |
| **Read the file directly; no separate converter command.** | The goal is `skill-lens run ./their-skill` working on a skill that already has `evals/evals.json`. A command that prints YAML adds a step and a second copy of the cases to keep in sync. |
| **Convert to raw mappings, then reuse the existing pipeline.** | `EvalCase.model_validate` and `_validate_cross_references` / `_workspace` / `_assertions` / `_tools` already encode the invariants (`extra="forbid"`, path containment, empty-rubric refusal). A second loader would copy them and drift. |
| **Strict: any key outside the shared set is refused with exit 2, naming the key and where it is.** | This is the project's typo rule (`extra="forbid"` on every authored model). It is also the only thing that stops a misspelled `assertion:` from silently dropping the checks. The cost is that Osmani-style files fail with a clear message until the dialect is added. |
| **Accept `assertions` or `expectations`; both in one case is refused.** | The two published schemas use different names for the same list. Both keys in one case is ambiguous about which list is meant. |
| **`assertions` / `expectations` become `judge.rubric`; `expected_output` becomes `judge.expected`.** | The statements are plain English that need evidence, which is what a rubric check is. `expected` is already the judge prompt's "what a good response looks like" section. skill-lens never asks the judge for a blended number, so the guide's PASS/FAIL-with-evidence model maps one to one. |
| **With no statements, `expected_output` is the single rubric check, worded `The output satisfies: <expected_output>`.** | The guide tells authors to start with prompts and expected outputs only. Refusing those files would fail the most common starting point. The guide defines `expected_output` as "a human-readable description of what success looks like", which is a statement a judge can check. With neither present the case has nothing to grade, which is an authoring error, not a pass. |
| **`id` is required: an integer or a non-empty string, never a boolean, unique in the file. The case name is `eval-<id>`.** | `True` is an `int` in Python and would otherwise read as id 1. A stable name is needed because reports, `--case` and baseline pairing key on it. |
| **`prompt` becomes `task`, and must be a non-empty string.** | The task is the one thing every case needs. |
| **`skill_name`, when present, must equal the skill's `name`.** | Both schemas say it matches the frontmatter. A mismatch almost always means the file belongs to another skill, and running it would score the wrong skill with no warning. Skipped when a file is parsed without a skill. |
| **`files` are read at load time as UTF-8 text, relative to the skill directory, and become `workspace.files` keyed by the path as written.** | The agent finds `evals/files/sales.csv` where the file says it is. Reading at load time means a missing or binary file aborts before any money is spent, like every other authoring error. |
| **A `workspace:` block exists only when `files` is non-empty.** | A case without `files` keeps its current shape: no temporary directory, no file tools, no preamble. Adding a workspace to every case would change what the agent is offered. |
| **`files` reuse `check_relative_path`, `resolve_under` and `stat_regular` from `workspace.py`.** | The same containment rules the workspace uses: nothing outside the skill directory, only regular files, a FIFO or device refused from a `stat` and never opened, a size above `max_file_bytes` refused before reading. A folder entry, a missing file, non-UTF-8 content and a path listed twice are errors that name the eval and the path. |
| **The load-time size limit is the default `max_file_bytes` (1,000,000).** | The loader has no access to configuration. The configured limit still applies when the workspace is seeded, so a lower configured value is not bypassed. A file above the default cannot be used from `evals.json` even if the configuration raises the cap; the docs say so. |
| **An empty `evals` list is an authoring error.** | Without it the run fails later with the generic "no cases" gate reason, which does not point at the file. |
| **Discovery: `evals/evals.json` is found together with `evals/*.yaml`; both load.** | `discover_eval_paths` already prefers the `evals/` directory over `*.eval.yaml` beside `SKILL.md`; adding the JSON file does not change that rule. `list` and batch `init` already call it, so batch `init` skips a skill that has an `evals.json`; single-skill `init` writes its scaffold beside the JSON file and never touches it. |
| **In a directory override, only a file named `evals.json` is read as JSON.** | A directory can hold other JSON (fixtures, schemas). Reading every `.json` would turn them into parse errors. |
| **A real judge is required, and the existing rule says so.** | The default `judge = "fake"` errors on a rubric nothing scripted, which is the project's rule for an unchecked rubric. The docs show how to pick a judge; the quickstart for `evals.json` names it. |

## 3. Mapping

```
{                                   EvalCase
  "skill_name": "csv-analyzer",   -> (checked against Skill.name)
  "evals": [
    {
      "id": 1,                    -> name: "eval-1"
      "prompt": "...",            -> task
      "expected_output": "...",   -> judge.expected
      "files": ["evals/files/a.csv"],
                                  -> workspace.files: {"evals/files/a.csv": <text>}
      "assertions": ["...", "..."]   (or "expectations")
                                  -> judge.rubric: ["...", "..."]
    }
  ]
}
```

Allowed top-level keys: `skill_name`, `evals`. Allowed per-case keys: `id`, `prompt`,
`expected_output`, `files`, `assertions`, `expectations`. Everything else is refused.

## 4. `cases/evals_json.py`

- `EVALS_JSON_FILENAME = "evals.json"`.
- `evals_json_to_raw_cases(path, data, skill) -> list[dict[str, Any]]` — validates the shape
  and key set, applies the mapping, reads the `files`, and returns mappings in the form
  `EvalCase.model_validate` expects. It raises `CaseParseError` with the file, the eval's id
  and the field, in the style of the YAML loader's messages.
- Private helpers per concern: key-set check, id, statements, files.
- When `skill` is `None` (a file parsed on its own), an eval with `files` is refused with a
  message that the skill directory is needed to resolve them.

`CaseParseError` moves to a small `cases/errors.py` and is re-exported from
`cases/loader.py`, so `from skill_lens.cases.loader import CaseParseError` keeps working. The
move avoids an import cycle: the loader imports the converter, which raises the error.

## 5. `cases/loader.py`

- `parse_cases_file` reads the text as today, then, for a `.json` suffix, calls
  `json.loads` (an invalid document is `CaseParseError("invalid JSON in …")`; any
  `ValueError` counts, because an integer of more than 4300 digits raises a plain
  `ValueError`, not a `JSONDecodeError`) and `evals_json_to_raw_cases`, and continues
  into the same per-case validation. The unfilled-scaffold scan applies to the converted
  mappings, as it does to YAML: the guard is unconditional, so a hand-written stub gets it
  too. Tool-library resolution does not apply to JSON, which has no `tools`.
  A leading byte-order mark is removed first, because Windows editors write one and
  `json.loads` refuses it.
- `discover_eval_paths` adds `evals/evals.json` to the paths from `evals/`.
- `load_cases_for_skill` accepts a `.json` file as `evals_path`, and in a directory override
  includes a file named `evals.json`.

`cli.py`: the `--evals` help text names the JSON file. No new option.

## 6. Documentation

| Page | Change |
| --- | --- |
| `docs/eval-files.md` | A section "Reading `evals.json`": the mapping table, the two spellings, the `expected_output` fallback, the `files` rules, and the refusals. |
| `docs/cli.md` | `--evals` accepts a `.json` file. |
| `docs/configuration.md` | Unchanged. The "Reading `evals.json`" section links to the existing `judge` key. |
| `docs/runners.md` | One sentence: a case from `evals.json` runs like any case with a `judge:` block. |
| `ARCHITECTURE.md` | Module-map row for `cases/evals_json.py` and `cases/errors.py`; an "`evals.json` input" invariants section. |
| `CLAUDE.md` | The "What this is" paragraph; the invariant bullets from section 7. |
| `skills/writing-skill-evals` | A short note: a skill with `evals/evals.json` can be run unchanged, and when to move to YAML (tools, trajectory, budget, `offered` mode). |
| `README.md` | Unchanged; it stays a landing page. |

## 7. Invariants this change adds

- **`evals.json` is converted, never interpreted twice.** The converter produces raw case
  mappings; `EvalCase` and the loader's `_validate_*` functions are the only validators, so a
  JSON case and a YAML case meet identical rules.
- **Strict keys.** An unknown top-level or per-case key is an authoring error (exit 2) naming
  the key. No leniency flag exists.
- **`assertions` and `expectations` are one list under two names; both together is refused.**
- **A case with nothing to grade is refused, never passed.** No statements and no
  `expected_output` is an authoring error; `expected_output` alone becomes one rubric check.
- **`id` is never a boolean, and is unique.** The case name is `eval-<id>`.
- **`skill_name`, when present and a skill is known, must match.**
- **Input files are text inside the skill directory.** Read at load time through the
  workspace's own containment helpers; a missing, binary, folder, oversized, duplicate or
  escaping entry aborts the run before any case executes.
- **A `workspace:` block exists only when `files` is non-empty.**
- **No runner, evaluator, reporter, model or exit code changes.**

## 8. Testing

All offline, `FakeRunner` and scripted `FakeJudge` tier:

- `tests/test_evals_json.py` (new) — the full mapping; both spellings; both together refused;
  `expected_output` fallback and its exact wording; neither refused; each unknown key
  (top-level and per-case) refused and named; id as int and string, boolean refused, duplicate
  refused; empty `evals` and a non-list `evals` refused; `skill_name` match and mismatch, and
  skipped without a skill; `files` — read as written, missing, a folder, binary bytes,
  `../` escape, an absolute path, a symlink out, a FIFO (read in a thread with a join
  timeout), over the size limit, a duplicate entry, and refused without a skill; no
  `workspace` when `files` is absent or empty.
- `tests/test_case_loader.py` — `parse_cases_file` on a `.json` path; invalid JSON names the
  file; `discover_eval_paths` finds `evals/evals.json` beside YAML files; `--evals` as a file
  and as a directory containing `evals.json` plus an unrelated `.json`.
- `tests/test_orchestrator.py` — an `evals.json` case is graded by a scripted judge and passes;
  under the default `fake` judge it is `errored`; an input file reaches the workspace; an
  unknown key aborts the run.
- `tests/test_cli.py` — `list` counts the cases; a first-stage file (only `prompt` and
  `expected_output`) lists and runs; `run` over an `evals.json` exits 1 with `1 errored` under
  the default judge; an unknown key exits 2 naming it; `--evals` accepts a `.json` file.
- `tests/test_cli_init.py` — batch `init` skips a skill that has an `evals.json`; single
  `init` writes beside it and leaves it unchanged.
- `tests/test_case_loader.py` — `CaseParseError` is the same class when imported from
  `cases.errors` and from `cases.loader`.
- `tests/test_framework_isolation.py` and `tests/test_naming.py` keep passing;
  `tests/test_docs.py` and `mkdocs build --strict` keep the docs honest.

## 9. Release shape

One `feat:` commit → a minor bump. Additive: no existing eval file, report field or exit
code changes. A repository with an `evals/evals.json` that skill-lens previously ignored will
now run it, and its cases will count toward the gate. That is the point of the change, and the
changelog line says so.
