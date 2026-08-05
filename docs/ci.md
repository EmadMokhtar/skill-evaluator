# Running skill-eval in CI

skill-eval is built to be a CI gate: the exit code is the contract — `0` gate passed, `1` gate
failed, `2` a user or authoring error. Everything else on this page is about making that
verdict legible.

## Reports

| Flag | Format | Read by |
| --- | --- | --- |
| `--json-output` | JSON | Tooling, dashboards, artifact storage |
| `--junit-output` | JUnit XML | GitHub, GitLab, Jenkins, CircleCI, Buildkite test panes |
| `--markdown-output` | Markdown | `$GITHUB_STEP_SUMMARY`, PR comments |

The JUnit mapping — including why only the candidate arm becomes test cases, and why an
`<error>` can carry an evaluator's own diagnostic rather than the runner's — is documented in
[Gating and exit codes](gating.md#junit-xml).

skill-eval never talks to the GitHub API. It renders a Markdown file; your workflow decides
where that goes.

`--markdown-max-chars` only makes sense together with `--markdown-output`: it is rejected as a
user error (exit `2`) without it, and rejected outright below `1`. Leave it unset for a step
summary, which allows 1 MiB; set it near GitHub's 65,536-character comment cap when the same
file will also be posted as a PR comment. Below the budget, truncation gives up detail before
it gives up meaning — optional sections (totals, per-skill table, delta, failure detail) are
dropped first, then gate reasons are elided behind a truthful `+N more reasons` count so a
clipped comment never implies the reasons it shows were all of them. See
[`--markdown-max-chars`](cli.md) for the exact rule, including the one case (a budget too small
to hold even the verdict) that falls back to a hard character cut.

## The composite action

```yaml
- uses: EmadMokhtar/skill-evaluator@v1
  with:
    path: ./skills
    runner: pydantic-ai
    model: openai:gpt-4o-mini
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

This repository has no tags yet, so `@v1` does not resolve until the first release. Until then,
pin the action to a commit SHA, or reference it as `uses: ./` from a workflow inside this
repository.

Every `skill-eval run` flag is available as a kebab-cased input (`--min-pass-rate` becomes
`min-pass-rate`), plus three inputs about the environment rather than the run:

| Input | Default | Purpose |
| --- | --- | --- |
| `install-spec` | `skill-eval[pydantic-ai]` | Passed verbatim to `uv tool install`. Accepts a PyPI name, a pinned version, a git ref, or a local path. |
| `working-directory` | `.` | Directory to run in. |
| `step-summary` | `true` | Append the Markdown summary to `$GITHUB_STEP_SUMMARY`. |

Outputs: `exit-code`, `passed`, `pass-rate`, `json-report`, `junit-report`, `markdown-report`.

`json-output`, `junit-output` and `markdown-output` default to real paths rather than being
unset, because the action reads the JSON back to produce `passed` and `pass-rate`.

The action runs its steps with `shell: bash`, which GitHub Actions executes under
`bash --noprofile --norc -eo pipefail` — `-e` is already on before the action's own script
runs a line. The run step captures the CLI's exit code explicitly (`code=0; skill-eval run
"${args[@]}" || code=$?`) precisely so that `-e` cannot swallow a red gate before it is
recorded, every reporting step after it carries `if: always()` so a failed run still gets its
summary published and its outputs read, and the final step re-raises with `exit
"${CODE:-1}"` — an *empty* code (the run step never completing at all: a failed install, a
cancelled job) fails closed rather than defaulting to success. A gate that cannot prove it
passed must fail.

Two CI jobs guard that behavior end to end, beyond the unit test that only compares
`action.yml`'s inputs against the CLI's flags: **`action-smoke`** runs the action against a
small passing fixture skill and asserts all three report files appear and are well-formed;
**`action-smoke-failing-gate`** runs it against a fixture that deliberately fails and asserts
the exit code is `1`, `passed` is `"false"`, and the reports still exist. The second job is the
one that matters — a passing-only fixture would happily pass even if a future edit broke the
exit-code capture (a bare failing command under `-e`, a step that silently swallows `$?`), so
only a fixture that is *supposed* to go red can catch a regression in how red gets reported.

## A complete workflow

```yaml
--8<-- "examples/ci/skill-eval.yml"
```

## Without the action

```yaml
--8<-- "examples/ci/skill-eval-cli.yml"
```

## Pull request comments on forks

GitHub deliberately gives `pull_request` runs triggered from a **fork** a read-only token, so a
comment step fails there no matter what `permissions:` says. The example above guards the
comment step with:

```yaml
if: github.event.pull_request.head.repo.full_name == github.repository
```

Fork PRs still get the step summary, the JUnit rendering and the correct exit code — the
substance of the report. Only the comment is skipped.

If you need comments on fork PRs, use the two-workflow `workflow_run` pattern: the
`pull_request` workflow runs the evaluation and uploads the Markdown as an artifact, never
holding a write token; a second workflow triggered on `workflow_run` downloads it and posts the
comment with `pull-requests: write`.

Do **not** reach for `pull_request_target` instead. It runs the base repository's workflow with
a write token and access to secrets, and checking out the pull request's head under it is one
of the best-known ways to hand a fork's code your repository's credentials.

## Concurrency and cost

`--concurrency N` runs N cases at once. The work is network-bound — one provider round trip per
case against sub-millisecond of local work — so this overlaps waiting rather than using more
cores, and the practical ceiling is your provider's rate limit.

It does not change what a run costs. `--baseline` and `--repeat` do: `--baseline previous
--repeat 3` is six runs per case, not one. `skill-eval run` prints a ceiling estimate before it
starts whenever the runner needs an API key.

Discovery (walking skills, loading eval files, filtering by `--tag`) always finishes, for every
skill, before any case runs — independent of `--concurrency`. A malformed eval file anywhere
therefore aborts the whole run before a single provider call is made, rather than after some
other skill's cases already ran and were paid for.
