# skill-lens `sessions:` — Design

**Date:** 2026-10-23
**Status:** Approved (design)
**Issue:** #95

## 1. Scope

One case is one task in one session. A skill whose claim is "a fresh session can resume
from what an earlier session wrote" cannot be tested directly today. The closest
approximation seeds the workspace with hand-written files, which tests reading files the
author wrote, not files the skill wrote.

`planning-with-files` is the motivating case. Its main claim is recovery after a context
reset or a new session. Its `resumes-from-files` case seeds the three planning files by
hand. It passes with and without the skill under Claude Code, so it measures nothing.

This change adds `sessions:`, a list of tasks in place of `task:`. Each session is a fresh
runner call against the same workspace. Assertions, trajectory checks, the budget and the
judge run once, after the last session. It ships as **one pull request**
(`feat: run a case as several sessions that share one workspace`):

- `EvalCase.sessions`, exclusive with `task`, checked when the case is built.
- One new module, `sessions.py`, that runs a case's sessions and merges their results into
  one `RunResult`.
- One changed call site in `orchestrator._run_one`: `runner.run(...)` becomes
  `run_sessions(...)`.
- `JudgeEvaluator.build_request` names every session's task for a multi-session case.
- **No change to the `Runner`, `Evaluator` or `Judge` protocol, to `run_evals`'s
  signature, to any runner, or to any reporter.** A single-task case runs byte-identically.

## 2. Decisions

| Question | Decision | Why |
| --- | --- | --- |
| Where sessions run | In the orchestrator, one `Runner.run` per session, with a copy of the case whose `task` is that session's text | Every runner — third-party ones too — gets sessions with no change. A runner never sees `sessions:` |
| What `trajectory:` reads | **Every session's tool calls, in order** | Reading only the last session would make `forbidden:`, `max_calls` and a negative `skill_triggered` control silently miss earlier sessions — a vacuous pass |
| What assertions and the judge read | The last session's output, and the workspace after the last session | "Run after the last session", as the issue says. The workspace is what carries over |
| How `budget:` adds up | The sum over every session: tokens, cost and latency | A case's budget is what the whole case cost. An unpriced or uncounted session makes the total unmeasured, so the limit fails as today |
| An errored session | Errors the case, and **no later session runs** | `errored` is not `failed`. A later session would build on a broken state and spend money on a case that is already errored. The spend of the sessions that ran is kept |
| `mode: offered` | Every session is offered the skill. `trajectory.skill_triggered` must hold in every session, read from `RunResult.skill_triggered_by_session`; `skill_triggered` summarises "any session" | Each session is a fresh session where the skill is installed. A negative control must fail when any session fired, and a resume skill's positive control when any session did not; one flag cannot say both (revised after review) |
| `mode: loaded` | Every session has the skill loaded (a product invokes it in every session) | The same rule as one session, applied to each |
| `--repeat` | Repeats the whole sequence. Each repetition gets a fresh workspace | A repetition is one work item, and a work item owns one workspace |
| The baseline arm | Runs the same sessions without the skill, in its own workspace | Two arms never share a workspace |
| A mock tool's `returns:` sequence | Starts from the top in every session | Each session builds its tools afresh, like a retried attempt does |
| `sessions:` without `workspace:` | Authoring error (exit 2) | With no workspace, nothing carries over between sessions. The case would measure N unrelated tasks and score only the last one |
| A one-entry `sessions:` list | Authoring error (exit 2) | It is `task:` spelled longer, refused like `contains: {}` |

## 3. Syntax and load-time rules

```yaml
cases:
  - name: resumes-from-own-files
    sessions:
      - >-
        Plan the migration of the billing service to the new queue in three phases. Do
        phase 1 only, then stop.
      - Continue the migration work from where it stopped.
    workspace: {}
    assertions:
      # Only a resuming session can mark phase 2 done. A `file-produced` check
      # would pass on the file session 1 already wrote (revised after review).
      - kind: regex
        value: '(?im)phase 2.*(done|complete)'
        file_glob: .planning/*/progress.md
    judge:
      rubric:
        - progress.md records phase 2 as done, after phase 1, in the same plan
      artifacts:
        - glob: .planning/*/progress.md
```

- `EvalCase.task` becomes `str | None = None`. `EvalCase.sessions: list[str] | None = None`
  is new. `extra="forbid"` stays.
- A model validator on `EvalCase` (so a case built in code gets the same refusal) refuses:
  - both `task` and `sessions`;
  - neither of them;
  - `sessions` with fewer than two entries;
  - a blank session entry (a session that asks nothing);
  - `sessions` with no `workspace:` block.
- Every refusal is a `CaseParseError` from the loader: exit 2, before any case runs.
- The `TODO(skill-lens)` scan already walks list values, so an unfilled session entry is
  caught by the existing rule.
- `evals.json` is unchanged: its `prompt` is one task.

## 4. Running the sessions

`_run_one` keeps owning the workspace. It creates it once, before the first session, and
deletes it once, after scoring, in the existing `finally`. Between those two points it calls
`sessions.run_sessions(runner, skill, case, workspace, extra)`:

- A case with `task:` makes exactly the call it makes today: `runner.run(skill, case,
  workspace=workspace, **extra)`. Same object, same arguments.
- A case with `sessions:` makes one call per entry, in order. Each call receives
  `case.model_copy(update={"task": text, "sessions": None})`, so the runner sees an ordinary
  one-task case. Nothing else carries over: no conversation history, fresh mock tools, the
  skill delivered again by a product runner.
- The loop stops at the first errored result.

The orchestrator then stamps `RunResult.workspace` on the merged result, unconditionally, as
today.

## 5. Merging the results

`sessions.merge_results(results, tasks)` turns the per-session results into the one
`RunResult` the evaluators and reporters read. `results` holds the sessions that ran;
`tasks` is every session's text, so `n` is `len(tasks)` even when a session errored early.
It is a pure function.

| Field | Merged value |
| --- | --- |
| `output` | The last session's (the errored one's, when a session errored) |
| `tool_calls` | Every session's, in order |
| `transcript` | Every session's, each preceded by a marker `{"session": k, "of": n, "task": text}` |
| `input_tokens`, `output_tokens` | The sum |
| `latency_ms` | The sum (each session's own measurement; orchestrator overhead is not added) |
| `cost_usd` | The sum |
| `cost_note`, `usage_note` | Every distinct non-empty note, in session order, joined with `; ` |
| `model` | The first non-empty one |
| `skill_triggered` | `None` when every session says `None`; otherwise `True` when any session says `True`, else `False` |
| `skill_triggered_by_session` | Every session's own `skill_triggered`, in order; empty when every session says `None` |
| `error` | `session k of n: <the session's error>`, when session k errored |

A non-empty `cost_note` or `usage_note` on any one session leaves the merged note non-empty.
`BudgetEvaluator` then fails `max_cost_usd` or `max_tokens` as *not evaluated*, exactly as
for one unpriced run. A sum that silently left out one session would not be a measurement.

## 6. Scoring

No evaluator changes. Each reads the merged result:

- `AssertionEvaluator`: the last session's output; a `file:` or `file_glob` reads the
  workspace after the last session.
- `TrajectoryEvaluator`: every session's calls. `order:` may therefore span sessions.
- `BudgetEvaluator`: the sums.
- `JudgeEvaluator`: `build_request` gives the judge every session's task, so it knows what
  each session was asked:

  ```text
  This work ran as 2 separate sessions that shared one workspace. Each session started
  fresh; only the files carried over. The response below is the last session's.

  Session 1:
  <task 1>

  Session 2:
  <task 2>
  ```

  The response is the last session's output; the artifacts are read after the last
  session. A one-task case's request is unchanged, so no cassette moves.

## 7. Runners

- **Framework runners and `FakeRunner`**: no change. `FakeRunner` scripts responses and
  writes by task text, so a test scripts each session by its own task.
- **Product runners**: no change. `deliver_skill` already overwrites `SKILL.md` and every
  shipped file, so delivering again in each session undoes an edit to a delivered file. It
  never deletes, so a file a session added inside the skill directory stays. The MCP bridge is written, checked (`Bridge.connected`) and deleted per
  call, so per session. The prompt-size limit applies per session.
- **Preflight** sees the case as written, with `sessions:`; `check_trajectory_names` and the
  product checks read neither `task` nor `sessions`.

## 8. The existing invariants, checked

| Invariant | How this change keeps it |
| --- | --- |
| Every work item gets its own workspace, and two arms never share one | The sessions are inside one work item; arms and repetitions are still separate work items with separate `mkdtemp` directories |
| `run_evals` is library API: a new parameter is appended, never inserted | No new parameter. Sessions come from the case |
| `extra="forbid"` on every user-authored model | `sessions` is a declared field on `EvalCase`; the model keeps `extra="forbid"` |
| `errored` is not `failed` | An errored session errors the case |
| An errored product run keeps the spend its trace reported | The merged result carries the spend of every session that ran, the errored one included |
| Nothing scores a vacuous pass | `sessions:` without a workspace and a one-entry list are refused; trajectory reads every session |
| Cost lookup degrades, never raises; a limit the product cannot measure fails | One session's note makes the merged note non-empty |
| `RunResult.workspace` is stamped unconditionally | Stamped on the merged result |
| Workspace cleanup lives in a `finally` | Unchanged: one workspace, one cleanup, after scoring |
| The workspace preamble is byte-identical in both arms and never names the skill | Each session gets the same preamble as a one-task case |
| A sequence repeats its last entry once used up; the counter lives in the built tool | Each session builds its tools, so each session starts from the top. The invariant's body gains "session" beside "arm, attempt and work item" |
| Runners, judges and evaluators must be safe to share across threads | Sessions run sequentially inside one work item; work items still run in parallel |
| The baseline arm never receives the skill's name | Sessions are the author's task text verbatim |
| Outcome order is submission order | One outcome per work item, as today |
| `skill_lens` never appears in user-facing output | The transcript marker's keys are `session`, `of` and `task` |

## 9. New invariants

Added to `docs/invariants.md` (under "Workspaces and files") and to `CLAUDE.md`, spelled the
same byte for byte:

- **`task:` and `sessions:` are exclusive, and `sessions:` needs a `workspace:` and at least
  two entries.**
- **The sessions of one work item share its workspace, and nothing else carries over.**
- **A multi-session case is one outcome: an errored session errors it and stops the rest,
  and every evaluator reads the merged result.**

## 10. Documentation

- `docs/eval-files.md`: a new section, "Several sessions in one workspace", and a row in
  "Which runners serve which case features".
- `docs/runners.md`: the workspace section (one workspace across sessions; mock sequences
  restart), product runners (delivered again each session), budget (sums).
- `ARCHITECTURE.md`: the `sessions.py` row in the module map, `sessions` on `EvalCase`, and
  "once per session" in the data flow.

## 11. Testing

All in the zero-cost `FakeRunner` tier:

- Model: exclusivity both ways, a missing prompt, one entry, a blank entry, no workspace;
  each through the loader too (exit 2 from `cli`).
- Running: N runner calls, in order, each with that session's task and the same workspace;
  a file written by session 1 is read back by session 2's scripted writes and asserted on
  after the last; a one-task case makes exactly one call with the original case object.
- Merging: every row of the table in §5, the error prefix, and that no later session runs
  after an errored one.
- Scoring: trajectory sees calls from every session; budget fails on the summed tokens;
  `max_cost_usd` fails as not evaluated when one session is unpriced; the judge request names
  every session.
- Baseline and repeat: separate workspaces per arm and per repetition, each running every
  session.

## 12. Not in this change

- Per-session checks (a `trajectory:` scoped to one session). `sessions:` entries are strings
  now; a later mapping form (`- task: ...` with its own `trajectory:`) stays compatible.
- Loading the skill in some sessions and not others.
- Carrying a mock tool's sequence position from one session into the next.
- Sessions in `evals.json`.
