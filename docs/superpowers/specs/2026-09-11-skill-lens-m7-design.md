# skill-lens M7 — Design

**Date:** 2026-09-11
**Status:** Approved (design), pending implementation plan
**Parent:** `2026-07-30-skill-eval-design.md` (§9 M7)

## 1. Scope

The parent spec defines M7 as "DX & docs: `init` scaffolder, docs, more `examples/` skills
+ eval suites, quickstart". Most of that shipped early: `init` and the `writing-skill-evals`
skill in #6, the documentation site in #4, a third example (`csv-report`) with M6. What is
left of the original list is thin, so this milestone re-scopes M7 around the developer
experience gaps that using the tool since then has exposed. It ships as **one pull
request**.

**In scope:**

- **Failure legibility.** A non-passing case shows the agent's output and its tool calls
  in every reporter (console, Markdown, JUnit), capped, with a cut that is never silent.
  A `full_output` config key and a `--full-output` / `--no-full-output` flag pair lift
  the cap.
- **`--case <text>`.** Run only the cases whose name contains `<text>`, so a red case in a
  CI log can be reproduced with one command. A filter that matches nothing fails the gate.
- **`init` catches up with M6.** A fifth scaffold case exercises `workspace:` and the file
  assertion kinds. `init` writes where the skill already keeps its evals instead of
  creating an `evals/` directory that shadows existing files. `init` over a directory of
  skills scaffolds every skill that has no suite and touches nothing else. The
  unfilled-scaffold scan also covers dictionary keys.
- **Examples covering the two features no example shows.** `greeting` becomes `1.1.0` with
  a real instruction change and an assertion only the new version satisfies, so
  `--baseline previous` works from a checkout. `examples/skill-lens.toml` is an annotated,
  realistic config.
- **The end-to-end quickstart.** `docs/getting-started.md` is rewritten as a tutorial from
  install to a CI gate. Every stale status line (M5) is brought up to date.

### Explicitly deferred

| Deferred | Why |
| --- | --- |
| A `references/` layout example | The skill loader ignores every file beside `SKILL.md` and the workspace tools cannot read them, so a skill saying "see `references/policy.md`" would point the agent at a file it cannot reach. That is M6 Part 2. A sentence in `docs/runners.md` says so instead. |
| A new example skill | Every feature now has an example: assertions and budgets (`greeting`), tools, judge and triggering (`order-support`), workspaces (`csv-report`), comparison (`greeting` 1.1.0). A fourth skill with no new feature to show is weight. |
| A repeatable `--case` | `--tag` is not repeatable either, and a substring already selects "all the refund cases". |
| A `--skill` filter | Pointing `<path>` at one skill directory already does this. |
| A config key for `--case` | A filter chooses *which* cases an invocation runs. A config file that permanently narrowed the suite would let a green run measure less than the repository declares — the vacuous-pass pattern this project rejects everywhere. Rendering knobs (`keep_workspace`, `full_output`) have no such failure mode, which is why they may live in the file. |
| Printing the transcript | `RunResult.transcript` is provider-shaped and large. Output plus tool calls answer "why did it fail"; the JSON report has the rest. |
| Per-skill `min_delta`, `--baseline-ref`, both-arms-fail flagging | Gating features carried over from the M4 and M5 deferred lists; not DX. |
| M6 Part 2 (running a bundled script) | Its own spec, unchanged. |

## 2. Decisions

| Decision | Why |
| --- | --- |
| Output is expanded **only under non-passing candidate outcomes**. | Fifty green cases must not print fifty transcripts. Baseline outcomes are not the verdict; the delta block is. |
| The cap is **500 characters** and a cut is **never silent**: `… (N more characters; --full-output prints them)`. | The count is exact so the reader knows what they are missing; the hint names the way to see it. |
| Empty output prints **`output: (empty)`**. | "The agent said nothing" is the single most useful fact about a failed assertion. Omitting the line would make it look like the line was not implemented. |
| **One shared formatter**, three reporters. | Console, Markdown and JUnit must show the same excerpt. A helper returning the excerpt, the number of characters cut and the tool-call lines makes disagreement impossible by construction. |
| `full_output` gets **both** a config key and a flag pair, like `keep_workspace`. | Consistency with every other run default. `--no-full-output` turns it off for one run without editing the file. |
| `--case` is **flag-only**, like `--tag`. | See the deferred table. |
| `--case` matches a **case-insensitive substring** of the case name. | The CI log shows the name; the user copies any distinctive part. Exact match would make one typo a silent "no cases ran" — which fails, but wastes a round trip. |
| A `--case` matching nothing **fails the gate** with a reason naming the flag, and the JSON report gains an additive `case_filtered_skills` field beside `tag_filtered_skills`. | A typo in a filter is not a pass. Additive because the JSON report is a contract; a rename would break consumers. |
| The fifth scaffold case is **always included**, never behind a flag. | The scaffold is how an author discovers that file assertions exist. Every other case in it is already "fill in or delete". |
| The seeded filename in the scaffold is a concrete `input.txt`; sentinels live only in values. | Today the unfilled scan walks values, not keys. The scan is extended to keys as well so hand-written stubs get the same protection. |
| `init` **writes where the skill already keeps its evals**. | Discovery prefers `evals/` when it exists. An `init` that creates `evals/` beside an existing `foo.eval.yaml` silently stops discovery seeing `foo.eval.yaml`. |
| Batch `init` **skips any skill that already has an eval file** and rejects `--force`. | Batch mode scaffolds the missing suites; rewriting every suite in a repository must never be one flag away. |
| Batch `init` with every skill already covered exits **0**. | The user asked for missing suites and there were none. It is not the evaluation gate; "nothing ran" semantics do not apply. |
| `greeting` is bumped rather than a new skill added. | `resolve_previous` keys on a declared version change found in git history. A squash-merged pull request lands as one commit, so only a skill whose earlier version is already on `main` can demonstrate `--baseline previous` from one PR. |
| `examples/skill-lens.toml` is **realistic, not defaults**, and lives under `examples/`. | A file of defaults documents nothing. Under `examples/` it is reached only through `--config`, so the CI self-check (`skill-lens list ./examples` from the repo root) never picks it up. |
| `getting-started.md` is **rewritten**, not joined by a `quickstart.md`. | Two entry pages compete. |

## 3. Failure context — `reporters/failure_context.py`

A new module with no reporter-specific formatting:

```python
OUTPUT_LIMIT = 500          # characters of agent output shown by default
ARGUMENT_LIMIT = 80         # characters per tool-call argument value
TOOL_CALL_LIMIT = 20        # tool calls listed before "+N more calls"

@dataclass(frozen=True)
class FailureContext:
    output: str             # the excerpt, or "" when the agent produced nothing
    cut: int                # characters removed from the output; 0 when none
    tool_calls: list[str]   # "name(arg=value, ...)" lines, in call order
    more_calls: int         # tool calls beyond TOOL_CALL_LIMIT; 0 when none

def failure_context(outcome: CaseOutcome, *, limit: int | None) -> FailureContext | None
```

- Returns `None` when `outcome.status == "passed"`, when `outcome.arm != "candidate"`, or
  when `outcome.result is None`. Every reporter calls it and renders nothing on `None`.
- `limit=None` means no cap (the `full_output` path). Otherwise the output is cut at
  `limit` characters and `cut = len(output) - limit`.
- Each tool call renders as `name(a=<json>, b=<json>)`, arguments in the order the model
  sent them, each value JSON-encoded (`json.dumps(value, ensure_ascii=False)`) and cut at
  `ARGUMENT_LIMIT` with a trailing `…`. Calls beyond `TOOL_CALL_LIMIT` are counted, not
  listed.

### Per reporter

All three renderers gain `output_limit: int | None`, threaded from the CLI.

**Console.** Under the existing evaluator/check/error lines, in both the single-arm and
the comparative branch:

```
        output: I'm sorry, but that order was delivered 45 days ago, so it is
        outside our 30-day return window and I can't refund it.
        … (1,842 more characters; --full-output prints them)
        tool calls:
            lookup_order(order_id="1234")
            … +3 more calls
```

Multi-line output keeps its line breaks, every line indented. Empty output renders
`output: (empty)`. The `tool calls:` block is omitted when there were none.

**Markdown.** Inside the existing failures `<details>` block, after the evaluator lines:
the output in a fenced block (via `_fenced`, whose fence already outlives any run of
backticks; `</details>` inside a fenced block is literal), the cut line as prose, then the
tool-call lines as a fenced block. The truncation rule is unchanged: the failures block is
already an optional block dropped whole when `--markdown-max-chars` is too small.

**JUnit.** Appended to the `<failure>` / `<error>` body after the existing detail, as plain
text lines, through the existing `_xml_safe` control-character stripping. `_message`
(the `message=` attribute) is unchanged — it stays the one-line summary.

### CLI and config

- `Config.full_output: bool = False`, documented in `docs/configuration.md` beside
  `keep_workspace`.
- `run` gains `--full-output/--no-full-output` (`bool | None`, default `None`), resolved
  exactly as `keep_workspace` is: the flag wins in either direction; omitting both leaves
  the file's value in effect. Resolved to `output_limit = None if full else OUTPUT_LIMIT`
  and passed to the three renderers.

## 4. `--case` — orchestrator, gating, report

- `run` gains `--case <text>` (`str | None`). No config key.
- `_plan_work` applies `--tag` first, then `--case` (`text.casefold() in c.name.casefold()`).
  A skill whose cases were all removed is appended to `plan.tag_filtered` if `--tag`
  emptied it, else to a new `plan.case_filtered`. Only skills that *had* cases are recorded;
  a skill with no cases at all stays in `skipped_skills`.
- `RunReport.case_filtered_skills: list[str] = []` (additive; `extra="forbid"` unaffected).
  The JSON reporter emits it beside `tag_filtered_skills`.
- `gating.evaluate_gate` gains the fourth zero-cases cause:
  `no eval cases ran: the --case filter matched no case for skill(s): {names}`. With both
  flags set, a run can carry one reason per list.
- The `Plan:` line counts the cases surviving both filters.

## 5. `init` — `scaffold.py`, `cli.py`, `cases/loader.py`

### 5a. The fifth scaffold case

Appended to `_TEMPLATE` after the triggering pair:

```yaml
  # 5. Does it produce the right artifact? `workspace:` gives the case a real,
  #    contained temporary directory seeded with the files named under
  #    `files:`, plus three built-in tools: list_files, read_file, write_file.
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
        file: {sentinel} the filename the skill must write
      - kind: contains
        file: {sentinel} the same filename
        value: >-
          {sentinel} a string that file must contain
```

The rendered file must parse as YAML and every case must be refused by `_reject_unfilled`
with a message naming the field — the existing scaffold test extends to five cases.

### 5b. Keys in the unfilled scan

`_reject_unfilled` also checks each dictionary **key** for the sentinel, reporting the trail
of the containing mapping plus the key. Nothing else changes.

### 5c. Target selection

```python
def scaffold_target(skill: Skill) -> Path:
    beside = sorted(skill.path.glob(f"*{EVAL_SUFFIX}"))
    if beside and not (skill.path / EVALS_DIRNAME).is_dir():
        return skill.path / _eval_filename(skill.name)
    return skill.path / EVALS_DIRNAME / _eval_filename(skill.name)
```

Used by both modes. `init` therefore never creates an `evals/` directory beside existing
`*.eval.yaml` files.

### 5d. Batch mode

`init <path>`:

- `<path>/SKILL.md` exists → **single mode**, semantics unchanged (target from 5c; exit 2
  if it exists and `--force` is absent; `--force` overwrites).
- Otherwise → **batch mode**:
  - `--force` given → exit 2: `--force applies to one skill; point init at that skill's
    directory`.
  - `load_skills(path)` (recursive; `SkillParseError` → exit 2, as in `run`/`list`). Zero
    skills → exit 2: `no SKILL.md under {path}`.
  - For each skill, in discovery order: `_discover_paths(skill)` non-empty →
    `Skipped {name}: already has {n} eval file(s)`; else write the scaffold to
    `scaffold_target(skill)` → `Wrote {target}`. A write failure → exit 2 naming the file;
    files already written stay (they are correct scaffolds).
  - Then, if anything was written: `Fill in every TODO(skill-lens), then run: skill-lens
    list {path}`. If nothing was written: `Nothing to do: every skill under {path} already
    has an eval suite`. Exit 0 either way.

## 6. Examples

### 6a. `greeting` 1.1.0

- `SKILL.md`: `version: 1.1.0`; body gains "Do not use exclamation marks — greet warmly,
  not loudly."
- `greeting.eval.yaml`: a new assertion `not_contains: "!"` with a comment: this is the
  assertion `--baseline previous` measures — version 1.0.0 typically answers `Hello, Ada!`,
  so the baseline arm fails it and the delta shows the improvement.
- `tests/test_examples.py`: a test pinning `greeting.version == "1.1.0"`, with a comment
  explaining that the bump is what makes the comparative example resolvable and must not
  be reverted or re-used for an unrelated edit.
- `tests/test_integration_live.py`: `report.total == 3` is stale (the examples carry seven
  cases). Fixed to the real count in passing.

### 6b. `examples/skill-lens.toml`

A realistic committed config, every key present: the ones a repository running real agents
would set are live (`default_runner = "pydantic-ai"`, `judge = "pydantic-ai"`,
`concurrency = 4`, `min_pass_rate = 1.0`, one `[per_skill_min]` entry); every other key is
commented out with its default and a one-line meaning. `tests/test_examples.py` asserts it
parses through `load_config(path=...)` and that the live keys carry those values.
`docs/configuration.md` links to it as the annotated reference.

### 6c. The `references/` note

One paragraph in `docs/runners.md#the-workspace`: files beside `SKILL.md` (`references/`,
`scripts/`) are not loaded into the prompt, and the workspace tools cannot read them;
running a bundled script is M6 Part 2 (roadmap).

## 7. Documentation

Documentation ships with the change; the `docs` and `docs-freshness` jobs enforce it.

| Page | What lands |
| --- | --- |
| `docs/getting-started.md` | rewritten as the end-to-end tutorial: install → `init` → fill → `list` → `run` offline and read a failure (the `output:` block) → `run --runner pydantic-ai`, the `Plan:` line, `--case` → commit `skill-lens.toml` → gate in CI → `--baseline previous`. The `version:` parsing trap moves to `comparative-evals.md`, linked from the tutorial. |
| `docs/cli.md` | `--case`, `--full-output/--no-full-output`; the `init` section (fifth case, target rule, batch mode, `--force` rules, exit codes); `list` output shows three skills |
| `docs/configuration.md` | `full_output`; link to `examples/skill-lens.toml` |
| `docs/gating.md` | the fourth zero-cases reason; `case_filtered_skills` in the JSON report; what a failure shows in each reporter |
| `docs/eval-files.md` | unfilled scan covers keys |
| `docs/runners.md` | the `references/` note |
| `docs/comparative-evals.md` | the `version:` parsing trap; the `greeting` walkthrough |
| `docs/ci.md`, `action.yml` | the `case` and `full-output` inputs (§10) |
| `docs/index.md` | status box M5 → M7 |
| `docs/roadmap.md` | M7 shipped; "What M7 shipped"; M6 Part 2 still planned |
| `README.md` | examples tree and `list` output show three skills; the sample run shows the `output:` block; "Milestone 7" |
| `ARCHITECTURE.md` | `failure_context.py` in the module map; §9 invariants |
| `CLAUDE.md` | status paragraph (M6 Part 1 + M7, spec paths); the condensed form of §9 |
| `skills/writing-skill-evals/SKILL.md`, `references/eval-file-syntax.md` | step 2 covers batch `init`; the fifth case and when to keep it |

## 8. Testing

All zero-cost, offline, deterministic.

- **`failure_context`:** `None` for passed, baseline and result-less outcomes; exact `cut`
  arithmetic at the boundary (499, 500, 501 characters); `limit=None` cuts nothing;
  argument encoding and cut; call cap and `more_calls` count; `ensure_ascii=False` keeps
  non-ASCII arguments readable.
- **Console:** the block appears only under non-passing candidate outcomes, in both
  branches; `(empty)`; multi-line indentation; the cut line; `tool calls:` omitted when
  none.
- **Markdown:** output containing triple backticks and `</details>` leaves the document
  well-formed (the existing structural assertions); the block is inside the failures
  `<details>`; dropped whole under a small `--markdown-max-chars`.
- **JUnit:** control characters in output still yield well-formed XML; `message=` unchanged.
- **CLI:** `--full-output` prints everything; `--no-full-output` overrides
  `full_output = true` in config; omitting both honours the file.
- **`--case`:** case-insensitive substring; AND with `--tag`; no match → exit 1 with the
  reason naming the flag and the skill; `case_filtered_skills` in the JSON report; a skill
  emptied by `--tag` is not also listed under `--case`; the `Plan:` count.
- **Scaffold:** five cases render, parse, and are each refused naming the field; a
  sentinel in a key is refused; `scaffold_target` for both layouts; `init` on a
  beside-layout skill creates no `evals/`.
- **Batch `init`:** writes missing, skips existing, exact output lines; `--force` → exit 2;
  no skills → exit 2; all skipped → exit 0 with the "Nothing to do" line; a
  `SkillParseError` under the directory → exit 2.
- **Examples:** greeting is `1.1.0`; `examples/skill-lens.toml` parses with the stated
  live values; `test_every_example_skill_is_discovered` unchanged.
- **Docs:** `tests/test_docs.py` (every new flag, config key and field documented; links
  resolve) and `mkdocs build --strict`.

## 9. Invariants this milestone must not break

1. **Output is expanded only under non-passing candidate outcomes.** Passing cases stay one
   line; baseline outcomes are never expanded.
2. **A cut is never silent.** Every truncated excerpt carries the exact number of characters
   removed and how to see them.
3. **The three reporters render one `FailureContext`.** They may differ in markup, never in
   what was shown.
4. **A `--case` matching nothing fails the gate**, with a reason naming the flag and the
   skills — the fourth zero-cases cause beside no skills, no cases and `--tag`.
5. **`--case` has no config key.** A filter is a property of an invocation.
6. **`init` never creates an `evals/` directory beside existing `*.eval.yaml` files.**
   Discovery prefers `evals/`; creating it would shadow the files already there.
7. **Batch `init` never overwrites.** A skill with any eval file is skipped; `--force` in
   batch mode is a user error.
8. **The unfilled-scaffold scan covers dictionary keys as well as values.**
9. **`greeting` stays at `1.1.0` or above, and its `1.0.0` stays in history.** The bump is
   what makes the comparative example resolvable.

## 10. Release shape

One pull request, title `feat: show why a case failed, rerun one case, and scaffold
file-producing skills`. `cz bump` yields `0.4.0`. Nothing breaking: exit codes unchanged;
the JSON report gains one additive field; console text is not a contract.

**The action gains two inputs**, because `tests/test_action.py` requires every `run` flag to
be exposed: `case` (forwarded with `add --case`, like `tag`) and `full-output` (`true` or
`false`, forwarded with `add_flag --full-output ... --no-full-output`, like
`keep-workspace`). Both are described in `action.yml` and listed in `docs/ci.md`.
