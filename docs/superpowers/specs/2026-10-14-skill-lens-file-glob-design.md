# skill-lens `file_glob` — Design

**Date:** 2026-10-14
**Status:** Approved (design); the implementation plan follows in a separate document

## 1. Scope

A `file:` assertion and a `judge.artifacts` entry name one workspace file by its exact path.
Some skills write to a path the model chooses. `planning-with-files` v3 writes
`task_plan.md`, `findings.md` and `progress.md` into `.planning/<date>-<slug>/`, where the
date and the slug change on every run. Under the current syntax, no eval can check those
files.

This change adds a **pattern** form of the path, `file_glob:`, that must match exactly one
file. It ships as **one pull request** (`feat: match workspace files by glob pattern`):

- `AssertionSpec.file_glob`, allowed wherever `file` is allowed.
- A `judge.artifacts` entry may be `{glob: <pattern>}` as well as a plain path.
- One matcher, `Workspace.match(pattern)`, used by both the assertion evaluator and the
  judge's artifact loader.
- **No change to any runner, reporter, `RunResult`, or the meaning of any existing file.**
  `file:` stays a literal path, so a real file named `report[1].md` keeps working.

Found while writing evals for `planning-with-files` (github.com/OthmanAdi/planning-with-files):
a `claude-code` run followed the skill correctly and still failed seven checks, because the
files were under `.planning/2026-10-11-python-cli-todo-tool/`.

## 2. Decisions

| Question | Decision | Why |
| --- | --- | --- |
| How a pattern is spelled | A new key, `file_glob:`; for artifacts, `{glob: ...}` | Reusing `file:` and detecting `*?[` would change what existing suites check |
| How many matches | Exactly one | Two plan directories must never pass by luck; one rule for assertions and artifacts |
| Grammar | `*`, `?`, `[...]` within one path segment; no `**` | Covers the need; cannot sweep into the delivered skill directory by accident |
| Where resolution happens | At check time, in a shared helper (approach A) | Resolving in the orchestrator would put a model-chosen path into the case and break arm pairing |

## 3. Syntax and load-time rules

```yaml
assertions:
  - kind: file-produced
    file_glob: .planning/*/task_plan.md
  - kind: regex
    value: "(?m)^## Goal"
    file_glob: .planning/*/task_plan.md
judge:
  artifacts:
    - progress.md
    - glob: .planning/*/progress.md
```

- `AssertionSpec` gains `file_glob: str | None = None`. The per-kind table in
  `cases/loader.py` treats it exactly as it treats `file`: allowed on `contains`,
  `not_contains`, `regex`, `equals`, `file-produced` and `json-schema`. A spec carrying both
  `file` and `file_glob` is an authoring error.
- `JudgeSpec.artifacts` becomes `list[str | ArtifactGlob]`. `ArtifactGlob` is a model with one
  field, `glob: str`, and `extra="forbid"`.
- `workspace.check_glob_pattern(pattern)` is the one load-time check. It runs
  `check_relative_path` on the pattern, then refuses:
  - a segment equal to `**`, or containing it;
  - a pattern with no wildcard character in any segment (`*`, `?`, `[`). That is a `file:`
    spelled longer, refused for the same reason `contains: {}` is;
  - an unclosed `[`.
- A `file_glob:` or a glob artifact in a case with no `workspace:` block is an authoring
  error, the same rule as `file:`.

Every refusal is a `CaseParseError` naming the eval file, the case and the assertion position
or artifact entry: exit 2, before any case runs.

## 4. Matching

`Workspace.match(pattern) -> list[str]` returns the workspace-relative paths of the regular
files the pattern matches, sorted.

- The pattern is split into segments on `/`. Each segment is compared with
  `fnmatch.fnmatchcase`, which is case-sensitive on every platform, so a run gives the same
  answer on macOS and Linux.
- A wildcard never crosses `/`. A leading dot is matched by `*`, so `.planning/*/x.md` and
  `*/x.md` against `.planning/x.md` both behave as written.
- The walk follows the segments: one directory listing per segment, never a recursive walk.
  It does not follow symlinked directories. A symlink to a file is a candidate; the read that
  follows decides whether it is allowed.
- Directories are never matches. FIFOs, devices and sockets are never matches (checked with
  `stat_regular`, never `open`), so a planted FIFO cannot hang the walk.
- No special exclusion for the delivered skill directory (`.claude/skills/<name>/`,
  `.agents/skills/<name>/`). With one-level wildcards, a pattern reaches into it only when
  its author writes those segments. `docs/eval-files.md` says so.

The single match then goes through the existing `Workspace.resolve` / `Workspace.read` path,
unchanged. A planted symlink pointing outside, an over-size file, a file that is not UTF-8:
each is a failed check carrying the refusal as evidence, as it is for `file:` today.

## 5. Results

| Matches | Assertion | Judge artifact |
| --- | --- | --- |
| 0 | failed: `no file matched <pattern>; workspace held …` | `(not produced)` |
| 1 | the check runs on that file | the file's content |
| 2+ | failed: `<pattern> matched N files: a, b, +M more` | `(matched N files; exactly one is required)` |

- Check ids stay `{kind}[{index}]`, derived from the case, so both arms pair.
- `_describe` renders `file_glob=` in place of `file=`, so a reader sees a pattern was used.
- The match list in evidence is capped at the existing `_LISTING_LIMIT`, with a truthful
  `+N more`.

## 6. Judge artifacts

- The artifact's **name and id are the pattern**, which the author wrote and is trusted. The
  matched path is chosen by the model and is never placed in the prompt, as a name or
  anywhere else.
- The new sentinel for several matches is fixed harness text. Like every other sentinel it
  does not consume the artifact content budget.
- Deduplication keys on the entry as written, so `progress.md` and
  `{glob: "*/progress.md"}` are two artifacts even when they name the same file.
- The rule that a `judge.artifacts` entry that could never be produced is an authoring error
  applies to a glob too: a pattern refused by `check_glob_pattern` never reaches a run.

## 7. Invariants

Two new entries, added to `docs/invariants.md` (a heading) and `CLAUDE.md` (a bold-led
bullet), spelled the same:

- **A `file_glob` must match exactly one regular file.** Zero or several is a failed check,
  never an error and never a pass; `**` and a pattern with no wildcard are authoring errors.
- **A matched path never reaches the judge prompt.** The artifact is named by its pattern;
  the path a model chose is untrusted and stays out of the prompt.

## 8. Testing

Written first, all offline, through `FakeRunner`:

- `Workspace.match`: one, zero, several; a wildcard not crossing `/`; dot directories;
  directories not matched; a FIFO not matched and not opened (in a thread with a join
  timeout); a symlinked directory not followed; sorted output; case sensitivity.
- `check_glob_pattern`: `**`, no wildcard, `..`, absolute, empty, unclosed `[`.
- Loader: `file` and `file_glob` together; `file_glob` with no workspace; glob artifact with
  no workspace; an unknown key under `{glob: ...}`.
- `AssertionEvaluator`: each kind with `file_glob`, for 0 / 1 / 2+ matches; a match that is a
  symlink pointing outside is a failed check with the refusal as evidence.
- Judge: the prompt contains the pattern and the content, and never the matched path; the
  several-matches sentinel; the content budget unchanged by the sentinel.
- An end-to-end `FakeRunner` case whose scripted writes land in `.planning/run-1/`.

## 9. Documentation

- `docs/eval-files.md`: the `file_glob` key, the artifact form, the grammar, the
  exactly-one rule and the delivered-skill-directory note.
- `plugins/skill-lens/skills/writing-skill-evals/references/eval-file-syntax.md`: the same,
  short form.
- `docs/invariants.md` and `CLAUDE.md`: the two invariants.

## 10. Not in scope

- `**` and any recursive match.
- "Every match must hold" or "any match may hold".
- Globs in `workspace.files` keys (inputs are authored, so their paths are always known).
