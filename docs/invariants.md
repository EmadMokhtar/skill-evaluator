# Invariants

These are decided behaviours, not accidents. Several were bugs caught in review. Each one
has a test asserting it, so a later change cannot quietly undo it.

This page is a reference to look things up in, not a page to read end to end. For how the
tool is built — the protocols, the module map, the data flow — see
[Architecture](architecture.md).

## The core contract

### `errored` is not `failed`

`failed` means the case ran and scored below the bar — an *eval* signal about the skill.
`errored` means the runner itself blew up — an *infra* signal about the harness. Conflating
them makes a broken API key look like a bad skill. Runners therefore **never raise** for
provider failures; they set `RunResult.error`. Errored cases fail the gate by default so CI
never goes green on a run that did not actually happen.

### A run executing zero cases fails the gate

"Nothing ran" is a broken run, not a pass — otherwise a mistyped path reports success
forever. `gating.evaluate_gate` distinguishes the causes: no skills found, all skills
skipped for having no cases, every case filtered out by `--tag`, or no case name matched
`--case`.

### Authoring errors abort the run; they never score as failures

An unknown assertion `kind`, a malformed regex, an undeclared tool name in a `trajectory`
block, or an unknown YAML key is a mistake in the user's files — it says nothing about the
skill. Scoring it as a failure would be a lie about the skill's quality.
`orchestrator.run_evals` lets these propagate; `cli.py` catches them via `_AUTHORING_ERRORS`
and exits 2.

### Which tools a case has is the runner's to know, so the declared-name rule for `trajectory:` is made in preflight, per runner — never in the loader

A framework runner offers a case its mock `tools:` and, with a `workspace:`, the six
built-ins; a product runner offers the product's own tools (`Bash`), which skill-lens cannot
list; and one invocation may run the same case through both. So `FakeRunner`,
`PydanticAIRunner` and `LangChainRunner` refuse a `called` / `forbidden` / `order` /
`call_args` name outside their set (`UndeclaredTool`, exit 2, for the cases planned for that
runner, before any case runs and before any spend), `ProductRunner` refuses no name, `cli`
refuses `trajectory:` outright, and the loader — hence `skill-lens list` — accepts any name.
The earlier loader check made the documented product combination unwritable: `tools:` is
refused under a product, and the loader refused a `trajectory:` naming anything else.
`check_trajectory_names` in `runners/preflight.py` is the one implementation; the message
names the runner, so a multi-runner invocation says which one refused.

### Exit codes are the CI contract

Gate passed `0`, gate failed `1`, user or authoring error `2`. In `cli.py`, a JSON-write
failure escalates to 2 only when the gate itself passed — a write problem must never mask an
already-failing gate.

### An unfilled scaffold is an authoring error, not a failure

`skill-lens init` writes `TODO(skill-lens)` into every field the author must supply, and
`cases/loader.py` rejects any case still containing it — before schema validation, so the
message names the field rather than its type. Enforcing this in the loader rather than the
generator makes it unconditional: hand-written stubs get it too, and no CI configuration can
opt out of it.

### `extra="forbid"` on every user-authored model

`EvalCase`, `AssertionSpec`, `ToolSpec`, `TrajectorySpec`, `BudgetSpec`, `Config`. Without
it, a typo like `assertion:` yields a case that passes vacuously — the worst possible
failure mode for an eval tool. It is also on `RunResult`, where it makes writing the derived
`tokens` field a loud error rather than a total that silently disagrees with the split it
was priced from.

### All file IO pins `encoding="utf-8"`

All file IO pins `encoding="utf-8"` and re-raises as a typed parse error (`SkillParseError`,
`CaseParseError`, `ConfigError`) naming the file and the field.

### YAML goes through `yaml_loading.safe_load`

PyYAML's `SafeLoader` implements YAML 1.1, which turns bare `yes`/`no`/`on`/`off` into
booleans. An assertion `value: yes` is meant as the string.

### Secrets come from environment variables only

Secrets come from environment variables only — never from `skill-lens.toml`. A config file
is committed; a key must not be.

### A base URL is config; a key is environment

A base URL is config; a key is environment (issue #54). A self-hosted endpoint is not a
secret and is the same for every contributor, so `base_url` and `judge_base_url` live in the
file and `--base-url` overrides the first, in the order every other key follows: flag > file >
the provider's own variable (`OPENAI_BASE_URL`, `OLLAMA_BASE_URL`, which the framework reads
when skill-lens passes nothing) > the provider's default. The variable is a fallback, never
an override — an override would break the documented order, and would need skill-lens to
keep a per-provider table of variable names. Three rules keep the value honest:

- **`validate_base_url` is the one check**, called by the field validator and by the
  flag: a bare `http(s)://host[...]`, and never a userinfo part (`user:secret@host`),
  which is a credential in a committed file — refused with the secrets message. A
  malformed URL is refused at load time (exit 2) because the client library would
  otherwise surface it from the first case as a connection error: an *errored* case,
  when the mistake is in the file.
- **The endpoint travels with the model.** `judge_base_url` empty means the judge uses
  `base_url` exactly when it uses `model` — `judge_model` unset and no `--judge-model`.
  A judge with a model of its own gets its provider's default unless `judge_base_url`
  names one, so a cloud judge under a local runner is never pointed at `localhost`. An
  unconditional fallback would do exactly that; no fallback at all would make the
  common case — one local model for both seats — two lines instead of one. `--base-url`
  is read wherever `--model` is and refused wherever `--model` is (exit 2): a product
  reaches its own endpoint.
- **A provider that cannot take the endpoint is refused in preflight**, before any spend,
  never silently ignored. Under PydanticAI, `resolve_model` builds the provider with
  `infer_model(model, provider_factory=...)` and raises `UnsupportedBaseURL` when the
  provider class's constructor has no `base_url` parameter (`deepseek`, `azure`,
  `openrouter`, ...) or when the model is an object that already carries a client; under
  LangChain, `check_base_url` builds the chat model once with `base_url=` and reports any
  refusal under the seat (`runner langchain`, `judge langchain`). Both keyed judges gained
  a no-argument `preflight()` for it, returning `None` — no product status. With no
  `base_url` nothing is checked and nothing changes: the string reaches the framework
  untouched, so the pre-issue behaviour is the fallback by construction, not by a second
  code path. The API-key check is unchanged: `ollama:` needs none, and `openai:` at a
  local server still needs `OPENAI_API_KEY` exported, because the provider's client
  requires one. The value is never printed in a report.

### Agent-framework imports appear in exactly four modules

Agent-framework imports appear in exactly four modules — `runners/pydantic_ai.py`,
`judges/pydantic_ai.py`, `runners/langchain.py` and `judges/langchain.py`.
`runners/tools.py` builds framework-neutral mock tools and the adapters wrap them. The
prompt rules (`runners/prompting.py`) and the retry loop (`runners/retry.py`) import no
framework, which is what lets both adapters share them. The product runner
(`runners/product.py`), the product judge (`judges/product.py`), the trace parsers
(`runners/traces.py`) and the MCP bridge's runner side (`runners/mcp.py`) import
`subprocess` and `json`, not a framework: a product is an executable and a trace grammar,
and `process.py` and `mcp_bridge.py` beneath them import nothing from the project at all.
`tests/test_framework_isolation.py` scans the whole package for top-level framework imports
and allows only those four files; it matches import *forms*, so `cli.py` importing our own
`skill_lens.runners.pydantic_ai` is not a false positive. This is what keeps the `Runner`
and `Judge` seams real rather than nominal.

### Cost lookup degrades, never raises

An unpriced model yields `cost_usd = 0.0` plus a `cost_note`. Pricing is reporting metadata;
it must never be why a run errors. In `BudgetEvaluator`, an unpriceable `max_cost_usd` limit
is *skipped* — not counted as passed — and that skip is recorded as a failing `CheckResult`.
`passed` requires every declared limit to hold, so **any** budget block that declares an
unpriceable `max_cost_usd` fails the case, whether or not it is the only check declared: a
case whose `max_tokens` and `max_latency_ms` both hold still fails if `max_cost_usd` could
not be priced, because that one check was never verified. `score`, by contrast, is the
fraction of *evaluated* limits that held — the unpriced limit is excluded from that divisor
entirely, so it neither inflates nor deflates the score the way a false pass would. A
repository running an unpriced model with a `budget:` block that mixes a priced limit with
`max_cost_usd` will see those cases turn red on an upgrade to this behavior; the fix is to
drop `max_cost_usd` for that provider, not to treat the skip as a pass.

### Nothing scores a vacuous pass

The rule that an unpriceable budget limit fails rather than passing generalises: a rubric
with no configured judge is *errored*, and a judge check that passes without citing evidence
is recorded as a *failure*. An unsupported PASS is an LLM judge's characteristic failure
mode, so it gets a mechanical defence rather than a prompt asking nicely.

### A rubric entry phrased against a mock tool's `returns:` is an authoring error

A rubric entry phrased against a mock tool's `returns:` is an authoring error (exit 2, load
time). The judge is shown the task, `expected`, the response and the files `judge.artifacts`
names — never what a tool returned — so "does not invent any detail not present in the
mocked data" is unverifiable as written: a judge that follows its own "fail when ambiguous"
rule turns it red, and one that does not passes it unread, which is the worse outcome
because nothing in the report says the check was never verified.
`cases/checks.find_hidden_data_reference` matches a fixed, documented vocabulary — `mock` or
`mocked` before `data`/`response`/`result`/`return`/`value`/`output`/`tool`, `tool` or
`mock` (possessive or not) before `returned`/`returns`/`return value`/`response`/`result`/
`output`/`data`/`value`, and `returned by the tool`/`mock` — each alternative needing a
second word, so a rubric about a testing skill ("proposes a mock for the HTTP client") or
about the response ("names the tool it would use") is not caught; the loader raises
`CaseParseError` naming the entry and the phrase, with both fixes: reword against the
response, or put the data in a `workspace:` file named under `judge.artifacts`. It is a
heuristic, applied to every case whether or not it declares `tools:` (the judge never sees a
return in any case), and rewording is the escape hatch. The second layer is the prompt:
`SYSTEM_PROMPT` tells every judge — framework and product alike — what it was not shown
(tool returns, tool calls, mock data) and that a check decidable only against those is a
fail with the evidence saying so, so a line the vocabulary does not catch fails honestly
rather than passing unread under a lenient model.

### Judge spend never enters `RunResult`

It lives on `EvalScore.cost_usd` and is reported as judge overhead. `budget:` measures the
skill's efficiency, not the harness's.

### Mock tools accept any arguments

A model hallucinating an argument is an eval signal about the skill; raising would surface
it as an infra error instead.

### Cassettes are replay-only and secret-free

Recording is a deliberate, key-bearing act. A missing cassette skips; a mismatched request
fails rather than reaching the network.

### `skill_lens` (underscore) never appears in user-facing output

The user-facing name is `skill-lens` everywhere: command, config file, distribution. The
GitHub repository keeps its older name, `skill-evaluator`, so
`uses: EmadMokhtar/skill-evaluator@v<version>` installing `skill-lens` is expected, not a
mistake. `tests/test_naming.py` fails if the pre-rename name reappears outside
`docs/superpowers/`, which is a historical archive and is never rewritten. `CHANGELOG.md` is
exempt from that scan on the same grounds and for one more: `cz bump` regenerates it from
commit subjects and footers written before the rename, so an edit there would misquote the
commit it came from *and* be undone by the next release. It is not left unguarded — a second
test reads the file and allows only the exact lines history produced, so the old name
arriving through a commit subject written after the rename still fails.

### `FakeRunner.run` returns `model_copy(deep=True)`

`FakeRunner.run` returns `model_copy(deep=True)` so a caller cannot corrupt scripted state.

## Comparative evals

### Absent `--baseline`, what runs is identical to a single-arm run

One arm, no delta block, the same one-line-per-outcome layout, and JSON that keeps every
prior key and value with additive ones alongside (`arm`, `repeat_index`, a null `delta`,
`baseline_notes`). Console output is *not* byte-identical: a failing case now prints one
indented line per failed check, because the assertion, trajectory and budget evaluators emit
per-check evidence, where once only the judge did. That is strictly more information, not a
change in what runs; the Comparative evals page covers it in full. `none` names a *kind* of
baseline — the flag being unset, not `--baseline none`, is what turns comparison off.
Upgrading must never silently double a bill.

### Baseline outcomes never count toward the gate

Baseline outcomes never count toward the gate, and never toward `errored`. `RunReport.total`
/ `passed` / `failed` / `errored` / `pass_rate` / `pass_rate_by_skill` all read
`candidate_outcomes`; `baseline_outcomes` and `baseline_errored` exist so the comparison
side is visible without ever feeding the numbers the gate reads. A strong baseline means the
skill was unnecessary, not that CI should go red.

### The baseline arm never receives the skill's name, description or instructions

The baseline arm never receives the skill's name, description or instructions under
`--baseline none`. `prompting.system_prompt` emits a neutral `BASELINE_PREAMBLE` instead of
the normal `# {name}` header whenever both `description` and `instructions` are empty. The
rule keys on emptiness, not on `variant`, so no runner can — or has to — branch on which arm
it is serving; a runner that could branch on the arm could cheat the comparison.

### A baseline that cannot be resolved is reported, never assumed to be "no change"

`resolve_previous` returns a `BaselineUnavailable` rather than treating silence as evidence.
Without `--min-delta` it is a note; with `--min-delta` it fails the gate, because treating
"we couldn't check" as "nothing changed" would let a repository pass forever by deleting its
git history.

### The delta is paired

`comparison.build_delta` excludes a case from *both* halves of the delta the moment either
arm cannot be honestly compared — a skipped baseline, an unresolvable one, or every
repetition of an arm erroring. Keeping the surviving half would bias the aggregate with data
that has no partner to be measured against.

### `--min-delta` without `--baseline` is a user error (exit 2)

A delta gate that checks nothing must never report a pass — the same vacuous-pass rejection
every other gate rule in this project applies. Gating on a delta with no comparable case
fails for the same reason, mirroring "a run executing zero cases fails the gate".

### Low-signal and high-variance flags never change the exit code

They are diagnostics about the *eval suite* — a weak assertion, an unstable case — not
verdicts on the skill. A flag that could block a merge trains people to ignore flags, and a
flaky provider would be indistinguishable from a genuinely bad skill.

### `resolve_previous` never raises for environmental failures

`resolve_previous` never raises for environmental failures — no `git`, no repository, an
untracked `SKILL.md`, an exhausted history window. Each comes back as a
`BaselineUnavailable` with a reason, the same discipline runners and judges follow for
provider failures. Subprocesses run without a shell, decode as UTF-8, and carry a timeout,
so a hung `git` cannot hang CI.

### Deterministic evaluators emit per-check verdicts

Deterministic evaluators emit per-check verdicts, ids derived from the case (never the
result) so the same id names the same check in both arms: `{kind}[{index}]` for assertions,
`called:{tool}` / `forbidden:{tool}` / `order` / `max_calls` / `skill_triggered` /
`call_args[{index}]` for trajectory, `max_tokens` / `max_cost_usd` / `max_latency_ms` for
budget. This is what lets `comparison.py` name a specific low-signal check rather than only
flag a whole case.

## Reporting and concurrency

### JUnit reports the candidate arm only

Under `--baseline`, a failing baseline is the evidence that the skill helped. Rendering it
as `<failure>` would paint CI red for the skill working — the same reason every `RunReport`
aggregate reads `candidate_outcomes`.

### `<failure>` is `failed`; `<error>` is `errored`

The project's central distinction, given a native rendering: an exploded runner must not
look like a skill that got worse. `errored` covers two different sources, and both must
render as `<error>`: a runner that raised, and an evaluator that did (a judge endpoint
returning 500 leaves `RunResult.error` unset and puts its diagnostic on `EvalScore.detail`
instead). `reporters/junit.py`'s `_error_body` reads the runner's error first and falls back
to the errored evaluators' own details, so an evaluator's own diagnostic is what gets
reported — never attributed to the runner that ran cleanly.

### JUnit output is always well-formed XML

`ElementTree` escapes `&`, `<` and `>` but emits control characters raw, so a model
returning `\x00` would produce a file every parser rejects. Illegal characters are stripped
before they reach the tree.

### A zero-case run produces a JUnit `<error>`, not an empty green suite

`tests="0"` renders green in most CI UIs, which would contradict the exit code of 1.

### Markdown truncation gives up detail before it gives up meaning, and never hides how much it gave up

Optional blocks (totals, per-skill table, delta, failure detail, low-signal / high-variance,
skipped skills) are dropped first, from the end. If gate reasons still do not fit, they are
elided behind a truthful `+N more reasons` count rather than being cut silently, so a
clipped comment can never imply the reasons it shows were all of them. Only a budget too
small to hold even the verdict and summary falls back to a hard character cut on the
assembled text. Truncation lives in the renderer, not the caller — a caller slicing the
returned string after the fact would cut a `<details>` block open, or a count line in half.

### Reporters never do IO to a service

They return a string. The CLI writes files; the workflow posts comments. A GitHub client
inside a reporter would put token scopes and network failure inside a pure function.

### `--concurrency 1` constructs no executor

The plain sequential loop it falls back to produces the same ordering and the same exception
propagation as any other concurrency level reading its futures in submission order, and it
is what lets the cassette tier (vcrpy is order-sensitive and not thread-safe) still match
requests. It is not, though, a literal replay of the pre-concurrency behaviour in every
respect: discovery is now always a separate, sequential pass that loads every skill's cases
before any of them run, so a malformed eval file anywhere aborts the whole run before a
single case runs — where once discovery and execution were interleaved per skill, and an
earlier skill's cases could complete (and be paid for) before a later skill's bad file was
even read.

### Outcome order is submission order, never completion order

`render_console` iterates `report.outcomes` and `build_delta` groups by insertion order, so
completion-order results would make output churn between identical runs.

### Concurrency never turns an authoring error into a case failure, and the surfaced error is deterministic

Futures are read in submission order, so the lowest-index failure is always the one that
propagates out of `run_evals`. On a failure, only the futures queued *after* it are
cancelled — a worker dequeues an item before marking its own future running, so a
lower-index future can still be pending, and cancelling it would let a higher-index error
surface instead of the lowest one. The executor is shut down with `cancel_futures=True` on
any exception, including a `submit` call that itself raises (an executor left running keeps
its workers alive, so the interpreter's own exit handler would finish the very work the
abort exists to abandon). If a custom `executor_factory` ever returns fewer results than
work items with no exception to explain it, `_execute` raises rather than handing the gate a
quietly partial run.

### Runners, judges and evaluators must be safe to share across threads

No mutable instance state touched by `run`/`evaluate`/`judge`. This holds today for free:
the fakes read immutable dicts and return `model_copy(deep=True)`, the deterministic
evaluators have no instance state, and `PydanticAIRunner` builds a fresh agent per run. It
is a constraint on what comes next.

## Release and supply chain

### The action fails closed

`shell: bash` steps already run under `bash --noprofile --norc -eo pipefail`, so `-e` is on
before the action's own script runs a line; the run step captures the CLI's exit code itself
(`code=0; skill-lens run ... || code=$?`) before `-e` gets a chance to discard it. Every
step after that — publishing the step summary, reading the JSON report, re-raising the exit
code — carries `if: always()`, so a failing run still gets its summary published and its
outputs read. The final step exits `"${CODE:-1}"`: an *empty* code means the run step never
completed at all (a failed install, a cancelled job), and a gate that cannot prove it passed
must fail rather than default to success.

### Nothing publishes that has not been verified in the same run

`release.yml` chains three jobs — `verify` (lint, format, the full suite), `release`
(`cz bump`, push, build, upload) and `publish` (download that artifact, upload to PyPI) —
with `needs:`, not across separate workflows. That shape is forced: GitHub starts no new
workflow run from a push made with `GITHUB_TOKEN`, so a `publish` workflow listening on tag
pushes would never fire, and the release would tag and then silently ship nothing. Chaining
also gives the property worth having — `publish` is unreachable except through a green
`verify`, and it uploads the artifact `release` built rather than rebuilding, so the bytes
that ship are the bytes that were tested. Publishing is irreversible: PyPI refuses a
re-upload of a version that already exists, which is why every gate here fails closed. The
corollary is that there is **no manual path to PyPI**; a locally bumped and pushed tag
produces a run with nothing to release.

### A merge with no releasable commit publishes nothing and fails nothing

`cz bump` signals "nothing to release" through its exit code — `21` (`NoneIncrementExit`)
and `3` (`NoCommitsFoundError`) — so the bump step deliberately runs without `set -e`, which
would discard the code before it could be read, and checks every other command by hand
instead.

### A run that `main` outran publishes nothing and fails nothing

Two merges a few minutes apart each start a release run, and the `concurrency` group does
not keep one alone from start to finish — GitHub checks the group when a *job* starts, so a
run between its `verify` and `release` jobs can be overtaken; and a merge that lands during
`verify` has moved `main` regardless. The earlier run's `cz bump` then rests on a commit
that is no longer the tip, and its `--atomic` push is rejected as a non-fast-forward with
nothing landed. The push step treats that as the same no-op as cz's exit `21` and `3`: it
fetches `main`, and if this run's commit is a *strict ancestor* of the new tip it records
`pushed=false` and exits `0`, naming the commit that overtook it in the summary. That is
sound because the later push has a run of its own whose `cz bump` reads every commit since
the last tag, the earlier commit included. The ancestor test is what keeps it from being a
vacuous pass: `main` unmoved but the push refused, or `main` rewritten to a history without
this commit, still fail with the push's own exit code, because no later run is known to
carry the commit. Every step after the push, and the `bumped` output `publish` reads, gate
on the push step's `pushed` rather than on the bump step's `bumped`, so a version that was
cut but never pushed is never verified, built or published. `tests/test_release_workflow.py`
runs the step's script verbatim against a local bare origin for each of these cases, which
is also why the version reaches the script as an environment variable rather than a `${{ }}`
expression.

### The pushed release tag is annotated, and the job proves the tag on `origin` is this run's

`git push --follow-tags` pushes only *annotated* tags, and Commitizen creates a lightweight
one unless told otherwise, so `annotated_tag = true` is what stops the bump commit reaching
`main` while its tag dies on the runner. Because `publish` is reached through `needs:` and
not through the tag, that loss would not stop a release: a `git ls-remote` check runs right
after the push and fails loudly instead. That check compares targets, not existence:
`--follow-tags` also sends only the tags origin does *not* already have, so a same-named tag
already on origin — pushed by hand, pointing elsewhere — is not rejected but silently
skipped, and the bump commit lands under a tag that names unreleased code. The step lists
`refs/tags/<tag>` and `refs/tags/<tag>^{}` (the peeled commit; a lightweight tag has no
peeled line and its ref is the commit) and requires the target to be the commit it just
pushed; `tests/test_release_workflow.py` runs that script verbatim against a local origin
holding such a tag. The push is `--atomic` so the commit and tag land together or not at
all, and `cz bump --check-consistency` aborts before writing anything if a file listed in
`version_files` no longer contains the current version — a flag on the command, because
Commitizen reads it only from the CLI and never from `pyproject.toml`. That abort is the
last line rather than the first: `tests/test_release_config.py` asserts the same property on
every pull request, so a reformatted pin is caught where it is cheap to fix instead of
costing a release on `main`. It asserts it by replaying Commitizen's own algorithm — each
`(file, pattern)` pair in sorted order, the file rewritten in place after every pair —
because Commitizen writes the file back between patterns, and a line that spells two
versions has only one to give: the first pattern takes it, the second finds the line already
bumped, and the release aborts if that pattern has no other line. Every version spelling
gets a line of its own. A further test requires every spelled version to be the current one,
so a line a long-lived branch added at an older version cannot ride along untouched release
after release.

The tag the lookup builds is checked against `tag_format` rather than trusted. The workflow
hardcodes the `v` prefix that `[tool.commitizen] tag_format` configures — two copies of one
string in two files — so `tests/test_release_workflow.py` derives the prefix from the setting
and requires the workflow to use it. Changing the format alone would tag correctly, push
successfully, and break only the lookup, failing *after* the push: the one unrecoverable state
here, since the version is spent, `publish` never became eligible to re-run, and a fresh run
finds nothing to release.

### No long-lived publishing credential exists

PyPI accepts the upload because the job proves its identity with a short-lived token
(Trusted Publishing, over OIDC), so `publish` needs `id-token: write` and nothing else — it
cannot write to the repository. Permissions are granted per job against a workflow-level
`permissions: {}`, so a job added later inherits nothing.

### A cassette refresh proves its recordings replay, and checks them for secrets, before pushing

It re-records with `--record-mode=rewrite`: `once` only fills in a *missing* cassette and
write-protects one already loaded, so it cannot refresh an existing recording. It then
stages the recordings before either check, because `git diff` cannot see an untracked file
and a freshly recorded cassette is exactly that — without staging, the scan would read as a
lock while checking nothing on the one path that creates a file. It hands back a **branch**,
never a pull request, because a pull request opened with `GITHUB_TOKEN` gets no CI checks,
and on a cassette refresh those checks are the whole point of the review.

### The dependency audit is one command, spelled identically in three places, and its exceptions live in one table

`uv audit` reads `uv.lock` — the exact set anyone installs — and asks OSV about every
package in every extra and group, dev tooling included, because that is what runs on
maintainers' machines and in CI. It runs in `security.yml` on every pull request, every push
to `main`, weekly on a schedule and on demand; in `release.yml`'s `verify` job; and in the
`pre-push` hook. The schedule is what makes it a monitor rather than a check: an advisory
can be published against a lockfile nobody has touched, and only a timer notices. `verify`
re-runs it rather than trusting the pull request's green check because an advisory can land
between the merge and the tag, and nothing publishes that `verify` did not pass.
`tests/test_security_checks.py` requires the three commands to be byte-identical, so an
exception can never apply to CI and not to the release, or to a laptop and not to CI.

Exceptions go in `[tool.uv.audit]` in `pyproject.toml`, which every copy of the command reads,
and only as `ignore-until-fixed`: it stops hiding an advisory the day a fixed version exists,
so the list can only shrink on its own. Plain `ignore` hides a finding forever and is rejected.
uv does not validate that table — a misspelled key is silently dropped and the finding silently
kept — so the same test rejects any key but the allowed one. Any finding fails; a severity
threshold is a decision someone has to defend for every advisory, while "fix it or record why
not" is a decision made once. `--locked` makes uv fail when `pyproject.toml` and `uv.lock`
disagree instead of quietly auditing a fresh resolution nobody installs, and
`--preview-features audit` acknowledges that the command is still a uv preview feature — if
its interface changes, the wiring tests fail on the pull request that bumps uv, not in a
release.

The build backend is audited and pinned too. `uv.lock` records what the project installs, not
what builds it: `[build-system] requires` is resolved fresh at build time, so the artifact
could be produced by a `hatchling` the audit never saw. A `build` dependency group mirrors
those requirements — a test keeps the two lists equal, or the group would audit a backend the
build does not use — which puts the backend and its own dependencies in the lockfile. The
release then exports that group (`uv export --frozen --only-group build`) as a constraint file
for `uv build --build-constraint`, so what builds the published wheel is exactly what `verify`
audited.

### The audit runs at push time locally, not commit time

It needs the network. A commit hook would fail offline and teach people to skip it; a push
needs the network anyway, so the check costs nothing extra there and cannot be blamed on a
bad connection.

### Ruff's `S` rules are on, and a false positive is suppressed at the site with its reason

The `flake8-bandit` family rides on the existing `ruff check`, so it runs everywhere lint
does with no extra step to forget. Five `src/` sites trip it and all are deliberate:
`baseline.py` starts `git` by name because an absolute path is wrong on most machines and a
missing git must come back as `BaselineUnavailable`, never a crash; `yaml_loading.py` passes
`StrictBoolLoader` to `yaml.load`, and ruff cannot see that the loader subclasses
`SafeLoader`; `scripts.py` starts the sandbox probe and the interpreter from an argv list
with no shell — the module's whole point; `process.py` runs `taskkill` on Windows the same
way and finds it on `PATH` by name for the same reason `baseline.py` finds `git`;
`runners/product.py` starts the agent product and its version probe from an argv list with
no shell, the prompt as one element. Each carries an inline `noqa` with that reason.
`tests/**` and `scripts/**` have per-directory ignores for `assert`,
subprocess-with-fixed-argv, XML parsing and literal `/tmp` strings used as fake path values.
A rule is never switched off for `src/` because one site trips it.

### Every action is pinned to a commit SHA with a `# vX.Y.Z` comment, and nothing grants write access at the workflow level

`actions/checkout@v4` runs whatever `v4` points at on the day, so a compromised or mistaken
re-tag would run different code in CI with no change in this repository; a commit hash
cannot be moved. The trailing version comment is what keeps a pin readable, and it is the
comment Dependabot rewrites when it bumps the SHA, so the two never disagree. Dependabot
watches both `uv.lock` and the actions weekly, because a pinned hash never moves on its own;
its commit prefixes are Conventional Commit types, checked by running them through
`cz check`, since the pull-request title becomes the commit that `cz bump` parses.
Permissions are granted per job against a top-level block that is empty or read-only, so a
job added later inherits nothing and the docs `build` job — third-party tooling on the
checkout — never holds the token that publishes to Pages. `tests/test_supply_chain.py` holds
all of it.

### The SBOM never enters `dist/`, and the GitHub Release is created only after PyPI accepted the upload

The CycloneDX export reads the lockfile `verify` just audited, `--frozen`, for the runtime
dependencies and the `pydantic-ai` extra — what an installer gets, not the dev or docs
groups that ship to nobody. It is written to `sbom/` and uploaded as its own artifact
because `publish` sends every file in the `dist` artifact to PyPI, which would reject an
SBOM and fail the upload after the tag is already pushed. `github-release` needs `publish`:
a GitHub Release is the first outward-facing sign of a version, and one that exists before
the upload could advertise a version `pip install` cannot find. The job is re-run safe — the
documented recovery for any release step — because creation is skipped when
`gh release view` finds the release, and assets are uploaded with `--clobber` so a partial
run converges instead of refusing. Its notes are `cz changelog <version> --dry-run`: the
same commits that chose the version number, and nothing else, are the source of truth for
what a release contains. The notes are written in `release`, which already has the checkout,
the history and the tools, and handed on as an artifact — so `github-release`, the one job
holding `contents: write`, has no checkout, runs no `uv`, and installs nothing. Everything
it publishes was built and verified by an earlier job; a compromised dependency or build
hook never executes under the token that can write releases. (The docs `build` job, by
contrast, needs `pages: read` and not only `contents: read`: `actions/configure-pages` calls
`GET /repos/{owner}/{repo}/pages` and fails the job when that call is refused — a tightening
that would only have shown up on the next push to `main`.)

## Workspaces and files

### A built-in tool never raises; it returns a message the model can read

`list_files`, `read_file` and `write_file` in `runners/tools.py` catch `PathRefused`,
`OSError` and `UnicodeError` and turn every one into an ordinary tool-result string.
`Workspace`'s own methods (`resolve`, `read`, `write`) *do* raise — that split is
deliberate: an evaluator or the loader wants an exception, because a refused path there is a
genuine authoring error, but a tool must never raise, because the model choosing a bad path
is an eval signal and an exception would surface it as an infra failure instead.

### No path outside the workspace root can be read or written

Every candidate path is resolved and then checked against the root with
`Path.is_relative_to`, never trusted from its spelling alone — `check_relative_path` rejects
an absolute path, a drive, or a `..` segment before resolution even runs, so the refusal
message can name what's wrong rather than a location the author never wrote. The root itself
is resolved once, at creation (`Path(tempfile.mkdtemp(...)).resolve()`): macOS resolves
`/tmp` to `/private/tmp`, and an unresolved root would make every later containment check
compare two spellings of the same directory.

### Every work item gets its own workspace, and two arms never share one

`orchestrator._run_one` creates a workspace fresh for each (skill, case, runner, arm,
repeat_index) combination via `tempfile.mkdtemp`, which is atomic — there is no window in
which two work items racing for a directory name could collide. A shared directory would let
the baseline arm read what the candidate wrote, or vice versa, corrupting the very
comparison `--baseline` exists to make.

### The workspace preamble is byte-identical in both arms and never names the skill

`WORKSPACE_PREAMBLE` is appended in `prompting.instructions` purely on whether the case has
a workspace, never on which arm is running — the same discipline `BASELINE_PREAMBLE` already
follows for the skill's own text. Added to the candidate arm only, that text would itself
become part of what `--min-delta` measures, inflating (or deflating) a comparison that is
supposed to isolate the skill's contribution.

### `RunResult.workspace` is non-null only while the directory exists, and the orchestrator stamps it unconditionally

`_run_one` overwrites whatever the runner returned with
`workspace.root if workspace is not None else None` after every run, including the `None`
case — so a non-conforming adapter that ignores the `workspace` parameter cannot smuggle a
path of its own into the report, and a case with no `workspace:` block can never show one
either. Once the directory is deleted, the field is cleared in the same step, so it can
never point at something that is already gone.

### Workspace cleanup never changes a verdict, and lives in a `finally`

`Workspace.cleanup` suppresses its own errors (`shutil.rmtree(..., ignore_errors=True)`) —
deleting a temp directory is harness housekeeping, and a cleanup failure turning a passing
case red would be the tool reporting on itself instead of on the skill. It runs in
`_run_one`'s `finally` block specifically so that an authoring error raised by an evaluator
still deletes the directory on its way out, rather than leaking it.

### A workspace creation or seeding failure is `errored`, never `failed`

A full disk or a permissions problem says nothing about the skill under test, so
`create_workspace` raises `WorkspaceError` and `_run_one` reports the case as `errored`,
with the exception's message in `RunResult.error`. Seeding is subject to exactly the same
rule: a seed file that cannot be written trips the same path, because a seed the harness
itself could not write says nothing about the skill either — and a half-seeded directory
would be worse than none, so `create_workspace` cleans up before re-raising.

### Artifacts reach the judge as fenced, untrusted data

Each is wrapped in its own `<artifact id="..." name="...">...</artifact id="...">` block,
and its id is derived from both the trusted **name** and the untrusted **content** — salted
with the name because `sha256("")` is a published constant (`e3b0c442...`) any model could
reproduce from memory, so an empty artifact would otherwise get a guessable fence id a
*later* artifact could echo as a forged closer. An artifact's boundary is its **first**
matching closing tag — the opposite of the response fence's "last matching closer wins" rule
— because each artifact block is closed immediately: unlike the response, which is always
the last thing before the checks, an artifact may be followed by more attacker-controlled
text (another artifact, or the checks list itself), so "last wins" would not be a safe rule
there.

### `file:` or `judge.artifacts` in a case with no `workspace:` block is an authoring error

`file:` or `judge.artifacts` in a case with no `workspace:` block is an authoring error
(exit 2), and so is a `judge.artifacts` entry that could never be produced (one that fails
`check_relative_path`, e.g. `../escape.txt`). Both are caught in `cases/loader.py` before
any case runs — a case with no filesystem can never satisfy either, so scoring it as a
failure would blame the skill for a check that could not have held under any output.

### Judge artifact bytes are capped, and truncation is visible

`MAX_ARTIFACT_BYTES` and `MAX_ARTIFACTS_TOTAL_BYTES` bound the untrusted, model-produced
*content* a judge prompt can carry — a judge handed a large volume of irrelevant text grades
worse, not better — and a cut file is marked with a visible
`... [truncated, N bytes omitted]` note rather than being silently shortened. A sentinel
(`(not produced)`, `(not valid UTF-8 text)`, `(omitted, artifact budget exhausted)`) never
consumes that budget: the cap bounds untrusted model content, and a sentinel is fixed,
harness-authored text with nothing for a content budget to police.

### An unknown assertion kind is caught at load time

An unknown assertion kind is caught at load time, before any case runs and before any money
is spent. `cases/loader.py`'s `_validate_assertions` checks every `kind` against its own
field-requirements table — deliberately a second table, not a reuse of
`AssertionEvaluator`'s `_CHECKS` (one maps a kind to a predicate, the other to which fields
it requires and allows), pinned together by a test rather than an import. Discovery is a
separate sequential pass ahead of execution, so a bad kind anywhere in a suite aborts the
whole run before the first case, real or fake, is charged for.

### A configured cap reaches the workspace

`max_file_bytes`, `max_files` and `max_total_bytes` flow from `Config` through `cli.py`'s
`WorkspaceLimits` construction, through `orchestrator.run_evals`'s `options`
(`RunOptions.limits`), into every `create_workspace` call. A limit read from config and then
dropped somewhere on that path would leave the built-in default silently in force, and the
only symptom would be a refusal message quoting a number the user never set.

### `run_evals` is library API: a new parameter is appended, never inserted

A caller that bound a parameter positionally before a newer one existed must still bind the
same thing. `keep_workspace` and `workspace_limits` therefore keep their positions after
`executor_factory`, and `options` — added after both — sits last. The legacy pair still
works (`keep_workspace=True` keeps the directory, `workspace_limits=` reaches the workspace)
but can never enable scripts, which it predates; passing `options` together with either
legacy argument raises `ValueError`, the same rejection `evaluators` with `judge` gets. The
CLI passes `options` only. `tests/test_orchestrator.py` pins the order and both forms.

### Every kept directory is printed, however keeping was turned on

`_kept_workspaces` in `reporters/console.py` renders a `Kept workspaces` section whenever
*any* outcome carries a non-null `workspace`, regardless of whether `--keep-workspace` or
the config file's `keep_workspace` is what kept it. That is what makes
`keep_workspace = true` safe to commit: a persistent setting that produced no visible output
would fill a disk with nothing on screen to explain why.

## Bundled scripts and the sandbox

### Script execution is off unless the run turned it on

`allow_scripts` / `--allow-scripts` is the only switch; nothing in an eval file or a
`SKILL.md` can enable it. A `SKILL.md` under evaluation is, by construction, code nobody has
vetted, and the person who writes the eval is usually the person who wrote the skill.
Reading the bundle needs no opt-in: reading a file the repository already contains changes
nothing.

### The bundle is `scripts/`, `references/`, `assets/` and nothing else

`SkillBundle` refuses any other first path component, so `*.eval.yaml` and `evals/` — the
expected answers — are never readable by the agent. `listing()` also leaves out a symbolic
link whose target resolves outside the root, so it never advertises a file `read` would
refuse.

### `Skill.bundle_root` defaults to `None`, and only the loader and the baseline resolver set it

Keying the bundle tools on `Skill.path` would leak the candidate's scripts into the
`--baseline none` arm, since every `Skill` has a path; a `Skill` built by hand in a test has
no bundle for the same reason.

### A script's environment is built from an allowlist, never inherited

`PATH`, `HOME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TZ` (plus what Python needs to start on
Windows), then `TMPDIR`/`TMP`/`TEMP`, `PYTHONDONTWRITEBYTECODE` and `PYTHONIOENCODING`. The
key that pays for the run is absent by construction, not by remembering to delete it.

### The allowlist stops inheritance; hiding the harness's own environment is the OS's job, and the report says whether it was done

A same-user process can ask the kernel for the harness's exec-time environment. On Linux
(`/proc/<pid>/environ`) `scripts.harden_process` calls `prctl(PR_SET_DUMPABLE, 0)` from
`preflight` — after the interpreter and sandbox checks, so a run that stops there never pays
the cost (no core dumps, no same-user debugger) — and its note travels
`ScriptRuntime.hardening` → `ScriptStatus.hardening` → the console line, the Markdown line
and the JSON report. It is best effort and never raises; root ignores the flag; `bwrap`
makes it moot by unsharing the PID namespace. On macOS the read is the `kern.procargs2`
sysctl behind `ps -E`, which `sandbox-exec` does not gate at all (a blanket
`(deny sysctl-read)` refusing every other sysctl still admits it; `process-info*` rules do
not touch it; `/bin/ps` merely cannot exec because it is setuid), so `harden_process`
returns None there and the docs say the key is exposed. `tests/test_sandbox_live.py` pins
both facts on macOS against a target spawned with the secret in its exec-time environment —
never `monkeypatch.setenv`, because the kernel serves the block it copied at `exec`, and a
test that planted the secret afterwards could not fail.

### A script runs with `shell=False`, its arguments as argv, always

Nothing the model sends is joined into a command line; a NUL byte in an argument is refused
by `Popen` before anything is spawned, and that refusal is text the model reads.

### The process group is killed after every exit, not only a timeout

`process.reap_and_kill_group`, called from `scripts.py` and `runners/product.py`, runs
whether the script exited on its own or ran past `script_timeout_seconds`, so a script that
starts `sleep 1000` and exits at once leaves nothing behind — the promise the docs make,
which a kill confined to the `TimeoutExpired` branch did not keep. On POSIX the order is
observe-kill-reap. Where `os.waitid` exists — Linux, and macOS from Python 3.13; CPython
does not build it on macOS before then, and the supported floor is 3.11.4 — `_exit_observed`
uses `os.waitid(P_PID, pid, WEXITED | WNOWAIT)` to see the exit without collecting it,
`os.killpg(process.pid, SIGKILL)` kills the group while the leader's pid is still held (so
it cannot have been reused), and `process.wait()` reaps last. Where it is missing, or if
something already reaped the child (`ECHILD`), the fallback is `Popen.wait` then the same
`killpg`: still safe from pid reuse, because POSIX forbids `fork` from returning a pid that
matches an existing process *group* id, and a group with live members is exactly the case
where the kill matters. `tests/test_scripts.py` exercises the fallback on every interpreter
by deleting `os.waitid` with `monkeypatch`. The group id *is* `process.pid` because
`start_new_session=True` makes the child a session leader; a `getpgid` lookup would fail
after the reap, exactly when the kill matters. `ProcessLookupError` and `PermissionError`
(macOS, when the only member left is the zombie leader) are both "already gone". On Windows
the kill is `taskkill /T /F` wrapped in `except OSError`, then `process.kill()`;
`taskkill /T` walks the tree from the parent, so after the parent has exited on its own
there is no tree to walk, and a background process outlives a normal exit there — the docs
say so. One honest limit everywhere: the kill is a group kill, so a script that calls
`os.setsid()` leaves the group and survives it on every POSIX platform; only under `bwrap`
do `--unshare-pid` and `--die-with-parent` still take it down with the sandbox, and a stock
Ubuntu runner may not have a working `bwrap`.

### Script output is read from files, capped, and a cut is never silent

Captured into memory, a script printing gigabytes inside the timeout would take the harness
down. The capture files are read back through the descriptors the harness opened *before*
the process started — `Popen` shares the open-file description with the child, so the inode
is fixed at spawn time — never by re-opening the path, because the script owns the scratch
directory and could put a symlink (to smuggle in another file's content) or a FIFO (whose
open would block forever) at that path.

### `run_script` never raises, and an unrunnable script is never `RunResult.error`

A missing script, a path outside `scripts/`, an unmapped extension, an interpreter that is
not on `PATH` or will not start — each is text the model reads; that reading is the eval
signal. Every refusal carries what the model needs for its next call to be right: the
bundled scripts when the name was wrong, the allowed extensions when the interpreter was.

### The sandbox decision is made once per run and appears on the report

`preflight` runs in `run_evals` after discovery and before any case: `required` without a
backend, and a missing interpreter for a discovered script, raise `ScriptSetupError` (exit 2)
before any money is spent. The backend is *executed*, not merely found on `PATH`. Preflight
and the "bundles N scripts; execution is off" notes both iterate every *discovered* skill,
including one `--tag` or `--case` filters out or that has no cases — a fail-closed check
that quietly skipped some skills would not be one. Baseline bundles are not preflighted; an
interpreter is looked up again at call time, and a miss there is a refusal, not an error.
The probe also fails closed on a temporary-directory path containing a double quote, which a
`sandbox-exec` profile cannot express safely.

### The sandbox hides other skill-lens temporary directories, and re-allows the bundle explicitly

The macOS profile allows by default, denies `network*` and `file-write*`, re-allows writes
under the workspace and scratch (and `file-write-data` on `/dev/null`), denies `file-read*`
under the resolved system temporary directory, then re-allows reads under the workspace,
scratch and the bundle; `bwrap` mounts an empty `tmpfs` over the temporary directory and
binds the same three back in (the bundle read-only). The bundle must be re-allowed because a
`--baseline previous` bundle is extracted *under* the temporary directory. The temp-dir
denial is a `subpath`, not a regex: an escaped path in a regex literal would silently stop
matching on any metacharacter — a fail-open the blanket `subpath` cannot have. Reads
elsewhere are allowed; the docs say so rather than pretend otherwise.

### A previous baseline carries its own bundle

A previous baseline carries its own bundle, extracted with `git archive <sha> -- .` from the
skill directory (verified: that form gives subtree-relative paths; `<sha>:./` gives an empty
archive), filtered to the three directories, with `tarfile`'s `data` filter — which is why
`requires-python` is `>=3.11.4`, the release that added `filter=`. The commit is the one
that last edited `SKILL.md` at the previous version — the version is the authority — so a
bundle-only commit after it is invisible, and a very large historical `assets/` can hit the
10-second git timeout, which comes back as `BaselineUnavailable`
(`cannot archive commit …`), a note, never an error. A commit with no bundle gives
`bundle_root=None` — never the candidate's.

### Baseline bundle directories are deleted when the run ends, however it ends

`_BaselineStore.cleanup()` runs in a `finally` around execution. `--keep-workspace` does not
keep them: they are an input, and the commit they came from is in the report already.

### Bundle tools require a `workspace:` block, and their names are reserved in every workspace case

The workspace is the script's working directory and, with its scratch directory, the only
host area the sandbox lets it write to; a name that is sometimes free is a name nobody can
rely on. The tools are registered in `mode: offered` too — an agent that declines the skill
has no reason to call them, and one that triggers it needs them exactly as a `loaded` case
does.

### The workspace preamble is unchanged

The workspace preamble is unchanged — byte-identical in both arms, naming no skill. The
bundle tools describe themselves; "run `scripts/count.py`" comes from `SKILL.md`, which is
the thing under measurement.

### A script's temporary files never enter the workspace

`TMPDIR` points at a per-call scratch directory outside it, deleted afterwards, so
`list_files`, `file-produced` and the judge's artifacts see only what the agent and the
script deliberately produced. A script writes to disk directly, so the workspace caps cannot
refuse it beforehand: the directory is measured after the call, an overrun becomes a
`warning:` line on the tool result, and every later `write_file` is refused by the existing
projection. Nothing bounds what a script can *leave* on disk — a sparse file has any
apparent size for almost no blocks — so the bound that matters is on the way back in: every
reader refuses a file over `max_file_bytes` before opening it (next).

### Only a regular file or a directory is ever resolved, and reads are capped

Part 1's `Workspace` assumed its only writer was `Workspace.write`, which creates regular
files; a bundled script can create anything. `Workspace._inspect` and `SkillBundle._inspect`
share two helpers in `workspace.py`: `resolve_under`, which turns a `Path.resolve()` failure
into `PathRefused` (3.11 and 3.12 raise `RuntimeError` on a symlink loop; 3.13 stops
resolving and the stat that follows raises `ELOOP` — both must become one refusal), and
`stat_regular`, which refuses an existing target that is neither `S_ISREG` nor `S_ISDIR`.
The check is `os.stat` on the resolved path (symlinks already followed), **never an open**:
opening a FIFO blocks until a writer connects, which no reader in the harness is, so a FIFO
a script planted under the name an assertion reads would hang the evaluator, the judge, and
`read_file` inside the agent loop where no timeout applies. `Workspace.read` and
`SkillBundle.read` then refuse `st_size > max_file_bytes` before reading a byte, naming the
cap the way `write` does. The agent's `read_file` sees the configured value, because the
orchestrator created that workspace with the repository's limits; the evaluators and the
judge rebuild `Workspace(root=…)` from the result's path with default limits, so their
ceiling is the 1 MB default even where `max_file_bytes` is raised, and the bundle always
uses the default — `docs/configuration.md` says so. A sparse file with an apparent size of
50 GB used to be `read_text()`ed whole before any budget applied, a `MemoryError` escaping
the assertion evaluator. `file-produced` is unaffected by the cap: it asks whether the file
exists. Every FIFO test runs the read in a thread with a join timeout, so a regression fails
the suite rather than hanging it.

### The author's path and the run's target are judged separately

`AssertionEvaluator` calls `check_relative_path` on the `file:` first and raises
`InvalidAssertionValue` for a path that could never name a workspace file — empty, absolute,
`..` — because that is a mistake in the eval file. A `PathRefused` raised *afterwards* by
`Workspace.resolve` or `read` (a symlink the script planted that resolves outside, a FIFO, a
loop, an over-size file) is a **failed** `CheckResult` carrying the refusal text as
evidence, never an exception: it is the skill's doing, and an exception would turn an eval
failure into exit 2 and, under `--concurrency`, cancel every queued case. The judge draws
the same line — a refused artifact is rendered as `NOT_PRODUCED`, except an over-size one,
which `Workspace.read` raises as `FileTooLarge` (a `PathRefused` subclass) and the judge
renders as `TOO_LARGE` (`(too large to read)`), because the file exists and "not produced"
would misdescribe the skill's output to the judge; it is a harness-authored sentinel, so
like the others it never consumes the artifact content budget — and `runners/tools.py` turns
every one of these into a tool-result string.

### `scripts=` reaches a runner only when execution is on

`_run_one` passes the keyword only when the runtime is set, so a third-party runner written
against a version without bundled scripts keeps working until the day someone turns scripts
on — at which point the `TypeError` names `run()` as the method that cannot take the keyword
and escapes `run_evals` as an uncaught traceback (exit 1), rather than the scripts silently
never running. Both bundled adapters, PydanticAI and LangChain, take it and register the
same six built-in tools under the same conditions.

## Failure context and the run matrix

### Output is expanded only under non-passing candidate outcomes

`failure_context` returns `None` for a passed outcome, a baseline outcome or an outcome with
no result, and every reporter renders nothing on `None`. Fifty green cases stay fifty lines,
and a baseline outcome — which is not the verdict — is never expanded.

### A cut is never silent

`FailureContext.cut` is the exact number of characters removed and `cut_note` states it,
with the flag that lifts the cap. A truncated excerpt that looked complete would be worse
than none.

### The three reporters render one `FailureContext`

Console, Markdown and JUnit call the same helper and may differ only in markup. Two excerpts
computed separately would drift the first time one of them changed.

### A `--case` matching nothing fails the gate

The fourth zero-cases cause, beside no skills, no cases and `--tag`: `case_filtered_skills`
on `RunReport` records the skills the filter emptied, and `evaluate_gate` names the flag. A
typo in a filter is not a pass.

### `--case` has no config key

A filter chooses which cases one invocation runs. A config file that permanently narrowed
the suite would let a green run measure less than the repository declares. `full_output` and
`keep_workspace` are rendering knobs with no such failure mode, which is why they may live
in the file.

### Every candidate `(skill, case, runner)` outcome counts toward the gate, and none counts twice

The orchestrator has always run a `skill × case × runner` matrix and every reporter keys on
`CaseOutcome.runner`; naming more than one runner on the CLI or in the config came later.
The one new rule is that a name given twice is refused rather than collapsed —
`cli._resolve_runners` and `Config.default_runner`'s validator both enforce it — because
under `--repeat` and `--baseline` a duplicate would weight one framework's vote double.
`--runner` replaces the configured list; it never appends.

### `init` never creates an `evals/` directory beside existing `*.eval.yaml` files

Discovery prefers `evals/` when it exists, so creating it would hide the files already there
from every later run — silently, with nothing red. `scaffold_target` writes beside
`SKILL.md` in that layout.

### Batch `init` never overwrites

A skill with any eval file is skipped and named; `--force` in batch mode is a user error.
Rewriting every suite in a repository must never be one flag away.

### The unfilled-scaffold scan covers mapping keys as well as values

`workspace: files:` is keyed by filename; an unfilled filename would otherwise seed a file
literally named after the placeholder.

### `examples/greeting` stays at `1.1.0` or later, with `1.0.0` in history

`--baseline previous` resolves an earlier *declared version* from git, so the shipped
comparative example only works because the bump is real and the earlier version is on
`main`.

## Mock tools

### An imported mock is the server's schema verbatim, and `returns` is never invented

`mcp-import` copies `name` and `inputSchema` byte-for-byte into `input_schema:` and writes
the `TODO(skill-lens)` sentinel for `returns` (and for a missing `description`), so a pasted
block cannot run until the author has said what the tool returns. The listing says nothing
about return values; a generated one would be exactly the silent drift the import exists to
remove. The server's `outputSchema`, when declared, rides along as a comment.

### `parameters` and `input_schema` are exclusive, and `input_schema` is validated at load time

Both set, a schema that fails `check_schema`, or a top-level type other than `object` is an
authoring error (exit 2) in `cases/loader.py`, before any case runs — the same treatment an
assertion's `json_schema` gets.

### The shorthand is closed; a declared schema is open

`build_mock_tool` adds `additionalProperties: false` and marks every key required only when
it derived the schema from `parameters:`, where the author wrote every key. A declared
`input_schema` is deep copied and passed as written, because fidelity to the server it
stands in for is its reason to exist.

### A tool name is what providers accept

A tool name is what providers accept: `^[A-Za-z0-9_-]{1,64}$`, the rule OpenAI and Anthropic
enforce, in place of `isidentifier()`. MCP tool names are routinely hyphenated and a mock
must answer to the name the skill's prose and the live server use; the import keeps the
name, and a name outside the rule is an error naming the tool, never a rewrite.
`skill_tool_name` — the offered-skill tool — is the one place a name *is* rewritten, and it
is total over the same rule: anything outside `[A-Za-z0-9_]` becomes `_` (the hyphen
included, because the recorded cassettes register `order-support` as `order_support`), a
leading digit gets `skill_`, and the result is cut to 64 characters after the prefix. A
Python identifier was not enough: `café` is one, and every provider rejects it.

### `mcp-import` never touches the network

`SOURCE` is a file or `-`; a live `--server` is deliberately deferred (see the design spec).

### `EvalCase.tools` holds only `ToolSpec`

A `ref:` is resolved by `cases/loader.py` on the raw case mapping, before
`EvalCase.model_validate`: `ToolSpec` forbids unknown keys and requires a name, so a
reference is not a ToolSpec and must never become one half-built. Resolving there is what
keeps every runner, evaluator, reporter and product preflight unchanged — the product
runners serve (or, under `cli`, refuse) a referenced tool exactly as an inline one, and the
duplicate-name, built-in-name, offered-skill and trajectory checks all read the resolved
name.

### A `ref:` may set `returns:` and nothing else

The library owns the contract (name, description, schema); the case owns the scenario.
Overriding the contract per case would reintroduce the drift the library exists to remove;
`ToolRef` (`extra="forbid"`) is what refuses it.

### An unresolvable `ref:` is an authoring error at load time

An unresolvable `ref:` is an authoring error at load time, exit 2, before any case runs: no
`tool_libraries:` key (the message says to add one), an unknown name (the message lists the
names the imports declare), a missing, unreadable or malformed library. Never an errored or
failed case.

### A name two imported libraries declare is refused naming both files

A name two imported libraries declare is refused naming both files — never resolved by
position — and so are one file declaring a name twice and one file imported twice (listed
twice, or a directory plus a file inside it). A runner named twice is refused rather than
de-duplicated for the same reason.

### Library paths are relative to the eval file, never the working directory, and never absolute

Library paths are relative to the eval file, never the working directory, and never absolute
(`is_absolute()`, `drive` or `root`, the three checks `check_relative_path` makes — but `..`
is allowed, because the library sits above the skill by design). The same eval file resolves
identically under discovery, `--evals`, `list` and from any checkout.

### A library is checked as an eval file is

A library is checked as an eval file is: the sentinel (keys and values), the tool-name rule,
unknown keys at the tool and at the top level, schema validity — each refusal naming the
library file and the tool's position, because that is the file to fix; the case loader then
prefixes the eval file that imported it.

### A library is a top-level `tools:` list and nothing else

A library is a top-level `tools:` list and nothing else — the block `mcp-import` prints, so
`mcp-import tools.json > shared-tools/server.yaml` is the whole import step.

### The resolver never mutates parsed YAML

An anchored `tools:` list aliased into two cases resolves in both; the resolver returns a
new mapping with a new list.

### `call_args` matches structurally and coerces nothing

`structural_match` in `matching.py` walks the entry against the argument dict the runner
recorded — never a serialised string, so key order and whitespace cannot matter. `contains`
ignores keys it does not name at every level; `equals` requires exactly the named keys; a
list matches element by element at equal length; a scalar must be equal — and a bool only
ever equals a bool, because Python's `True == 1` would otherwise let `limit: 1` pass on a
call that sent `true`. `"1"` never equals `1`. No runner changed: every adapter and both
trace parsers already fill `ToolCall.arguments`, and a payload one could not parse sits
under `_raw`, which never matches a structural check and appears in the evidence.

### A tool that was never called fails `call_args`, under `every: true` too

"At least one call matched" and "every call matched" are both false over zero calls; a pass
there would be the vacuous pass this project refuses everywhere else. `every` exists because
the issue's own example needs it: a skill told to fetch only active threads, that fetches
everything first and re-fetches filtered, passes the default and fails `every`.

### One subject per entry

Exactly one of `contains` / `equals`; `contains: {}` is refused because `{}` is a subset of
every dict — `called:` spelled longer — while `equals: {}` is kept because "called with no
arguments" is something a call can fail. These are shape rules, so they live in a
`model_validator` on `CallArgsSpec` and a programmatic `EvalCase` gets them too; the
loader's existing `ValidationError` wrapping names the file and case. The declared-`tool`
rule is the runner's, in `check_trajectory_names` beside `called` / `forbidden` / `order`,
built-ins included where a `workspace:` exists — see the invariant on trajectory names
above.

### Ids are positional, `call_args[{index}]`

Ids are positional, `call_args[{index}]`, because two entries may name one tool, and derived
from the case so both arms pair. A failing check's evidence renders the arguments every call
carried as sorted JSON and cuts at `_ARGUMENTS_LIMIT` with the removed count stated — a
`write_file` call can carry a document — while the failure excerpt keeps the full calls.

### `returns:` takes three shapes, and the shape says which rule applies

A skill whose instructions loop over a tool — fetch item A, follow its parent link, fetch B,
stop when there is none — cannot be exercised by a mock that hands back one fixed value.
`returns:` therefore takes three shapes, and the shape says which rule applies: a string
answers every call; a list of strings is a **sequence**, consumed in the order the calls
arrive; a list of `when:`/`value:` mappings is a **lookup**, answered by the first entry
whose `when:` keys all equal the call's arguments. A list that mixes the two, or an empty
list, is refused by `ToolSpec` itself (`_check_returns_shape`, before Pydantic's union would
report both branches), so an eval file can never be half one thing and half the other.
`ToolRef.returns` takes the same three shapes: the case still owns the scenario and nothing
else.

### A sequence repeats its last entry once used up

The last entry is the steady state a skill that keeps calling should see — the item with no
parent, the job that is done — and `trajectory.max_calls` is the check for a loop that
should have ended. A visible "no more entries" message would instead teach the model
something about the harness. The counter lives in the built tool (a locked closure in
`build_mock_tool`), never on the runner, so every build — each arm, each attempt, each work
item under `--concurrency` — starts from the top, and a framework running several calls from
one model turn in parallel still hands out each entry exactly once. Which parallel call gets
which entry is the framework's choice; where the argument, not the position, decides, the
docs say to use a lookup.

### A retried attempt gets fresh tools

Both keyed adapters build the agent *inside* the retried callable (`_run_with_retries` /
`_invoke` take a builder, not an agent). An agent reused across attempts would hand the
second attempt's first call the sequence's second entry, and a transient 429 would silently
change what the skill was shown.

### A lookup entry that could never answer a call is an authoring error

A lookup entry that could never answer a call is an authoring error (exit 2), caught by
`check_tool_returns` in `cases/checks.py` at load time for an inline tool, a library tool
and a `ref:` override alike — the ref's `returns:` is checked against the contract the
library declared, because the resolver copies the raw value and `_validate_tools` reads the
resolved tool. Three refusals: a `when:` key the tool's closed schema can never carry (the
`parameters:` shorthand, or an `input_schema` with `additionalProperties: false` and listed
`properties`; an open schema may key on anything), an empty `when:` (the fallback spelled
confusingly — drop the key), and an entry an earlier entry already answers (a fallback above
it, the same `when:`, or a `when:` it only narrows), since the first match wins. All three
are checks that could never fire, the vacuous case this project refuses everywhere.

### A call no lookup entry answers gets a message, never an exception

The skill asked for an argument the author did not script — an eval signal — so the model
reads `NO_RESPONSE_SCRIPTED` (the tool's name and the arguments as sorted JSON,
`default=str` so an unencodable value cannot make a mock raise) and the transcript shows it.
An entry with no `when:` is the fallback for authors who want a real error payload instead.

### A `when:` matches by the `call_args.contains` rule, through the one matcher

`ToolResponse.matches` is `matching.structural_match(when, arguments, exact=False)`: a
subset at every level, a list element by element at equal length, `"1"` never `1`, and a
bool only ever equal to a bool — the same function `evaluators/trajectory.py` runs, so an
author learns one rule for naming arguments and the two can never drift. The loader's
reachability check uses the same `matches`, so "unreachable" means exactly what the runtime
would do.

## Product runners

### The product sees `SKILL.md` verbatim (its text as written; line endings are normalised)

`Skill.markdown` is the file as the author wrote it, never a re-rendering from the parsed
fields; only the loader and the baseline resolver set it. The loader reads it with
`read_text(encoding="utf-8")`, which is text mode: a `\r\n` in the file becomes `\n` in
`Skill.markdown`, so the delivered file is the file's text verbatim, not its bytes -- a
newline-preserving read would push `\r\n` into the framework runners' system prompts too, so
the loader stays as it is. Products honour frontmatter keys skill-lens does not model
(`allowed-tools`, `disable-model-invocation`, `license`); a re-rendering would change the
product's behaviour and the eval would measure the re-rendering. `deliver_skill` writes it
with `newline=""` so no further translation changes a byte on the way out, and copies
`scripts/`, `references/` and `assets/` beside it — those three and nothing else, so an eval
file beside `SKILL.md` never reaches the product. The `--baseline none` skill has an empty
`markdown`, so no directory is written for it: the rule is keyed on emptiness, not on which
arm is running, the same rule `BASELINE_PREAMBLE` uses, so a runner never has to know which
arm it is serving.

### The prompt is the task verbatim, and the baseline-none arm never sees the skill's name

The product owns its system prompt; anything skill-lens added would be part of what
`--min-delta` measures. `mode: loaded` invokes the skill through the product's own spelling
(`/<name> <task>` for both presets, `invoke` for `cli`); `mode: offered` sends the bare
task. The baseline arm, having nothing to invoke, gets the bare task in both modes.

### `skill_triggered` comes only from the product's own load signal

Copilot's `skill` tool request (`toolRequests[]` named `skill`, `arguments.skill`) or its
`skill.invoked` event, Claude Code's `Skill` tool call. Copilot emits `skill.invoked` for a
slash invocation only — the probe recorded it from `/ping`, loaded mode — while a skill the
model chose itself is a `skill` tool request with no event, on 1.0.37 and 1.0.86-2 alike;
reading the event alone failed every positive offered case under `--runner copilot` (issue #50).
Inferring it from the output would let a model that guessed the answer read as a triggered
skill. A product with no such signal — `cli` — makes `mode: offered` an authoring error
under that runner, never a silent `false`, which would pass every negative control.
`ProductRunner.run` still returns `None` rather than `False` for a generic product, so the
runner never claims what it cannot observe.

### A limit the product cannot measure fails, it never passes

`RunResult.usage_note` is to tokens what `cost_note` is to cost: `0 <= max_tokens` is always
true, so `BudgetEvaluator` records a `max_tokens` under a non-empty `usage_note` as a
failing *not evaluated* check carrying the note, exactly as it already did for
`max_cost_usd` under `cost_note`, and excludes it from `score`'s divisor. Copilot bills per
premium request (its billing unit: one counted request to a model, not a token count), so
its cost is `0.0` with a `cost_note` naming the count and `max_cost_usd` fails as not
evaluated; Claude Code reports `total_cost_usd` at list price (the provider's published
per-token price), a real and comparable number, so it has none.

### A truncated trace is `RunResult.error`, never a partial parse

The final event is the last line, and losing it loses the output. `invoke` compares the
captured size against `max_output_bytes` and `read_trace` turns an over-size capture into an
error naming the key, so the fix is one config line. The other way round: a complete trace
carrying the product's own error message (`session.error`, `is_error`) is the best
explanation there is and wins over the exit code; only an incomplete trace, or one with no
error of its own, gets the exit code and the stderr tail instead.

### Naming a product runner is the trust decision, and the report says so

The product runs with permission prompts disabled (`--allow-all-tools`,
`--dangerously-skip-permissions`) because it cannot run non-interactively otherwise, and
with the full environment (`os.environ.copy()`) because it needs its own auth — the opposite
of `scripts.script_environment`'s allowlist, on purpose: here the product is the harness,
not the subject. No skill-lens sandbox applies; bundled scripts are reachable through the
product's own tools whatever `allow_scripts` says, which governs only `run_script` — gating
the runner on it would also switch on `run_script` for every framework runner in the same
matrix. `TRUST_NOTE` is fixed harness text on `ProductStatus.trust`, on the model rather
than in each reporter, so the three reporters cannot drift and the JSON carries the sentence
a human reads.

### Preflight spends nothing

`ProductRunner.preflight` finds the executable on `PATH` and, for the two presets,
*executes* its `--version` (a `copilot` that cannot start is exit 2 up front, not thirty
errored cases — the same rule as the sandbox probe; `cli` has no version command, so only
the `PATH` lookup applies to it), checks every skill name is one directory entry, and
refuses `tools:` under any product and `trajectory:` or `mode: offered` under `cli`, all
before the first case. It checks no `trajectory:` name against anything: under a product the
names are the product's own tools, which skill-lens cannot enumerate (a translation table
would be a third moving target), so that rule belongs to the framework runners' preflight —
see the invariant above. The orchestrator hands it only the candidate-arm `(skill, case)`
pairs planned for that runner, once each: compatibility is a property of `(case, runner)`,
so a case `--tag` or `--case` filtered out is not its concern, and neither arm nor repeat
changes the answer. `deliver_skill` re-checks the skill name at run time because under
`--baseline previous` it comes from a historical `SKILL.md` that never passed preflight;
that failure is `RunResult.error` for the arm it concerns, never a raise.

### `process.py` is the one implementation

`process.py` is the one implementation of the process-group start, the timed wait, the kill
after every exit, and the capped read through the harness's own handle. `scripts.py` and
`runners/product.py` spawn under opposite trust models but must agree on those mechanics,
and one implementation is what stops two callers drifting. It imports nothing from the rest
of the project.

### `--model` with nothing to read it is a user error, and so is `--judge-model`

`--runner fake --model x` was once silently ignored; with `--runner copilot` that silence is
a mistake that is easy to miss, because the flag looks honoured while the product runs its
own default. `cli.py` refuses `--model` unless a keyed runner is named or a keyed judge with
no `judge_model` of its own will fall back to it, and refuses `--judge-model` unless the
judge is keyed. A product's model is set with `[runners.<name>] args`, which never reaches
the wrong runner.

### `tools:` under a product that cannot take the MCP bridge is an authoring error

`tools:` under a product that cannot take the MCP bridge is an authoring error, not a
silently emptier run: `cli` has no known flag, and a preset whose `command` names another
executable is not known to take the preset's. Ignoring the block would make
`trajectory: called:` fail for a reason that says nothing about the skill and `forbidden:`
pass vacuously. Under the two presets the block is served; see [the MCP bridge](#a-cases-tools-reach-a-preset-product-through-a-stdio-mcp-server-skill-lens-ships-and-the-product-starts-it).

### `ProductRunner.run` never raises for a product failure

A timeout, a non-zero exit, a product-reported failure, a truncated trace, a prompt over
`MAX_PROMPT_BYTES`, an executable missing at run time and a `ProductSetupError` from
delivery are all `RunResult.error`. `ProductSetupError` propagates only from `preflight`,
where `cli.py` turns it into exit 2.

### `command` replaces the argv; `args` appends; presets forbid `skills_dir` and `invoke`

Two keys with two meanings: drop an isolation flag with `command`, add a model with `args`.
`command` must contain exactly one element equal to `{prompt}`, not in the executable slot,
substituted as a whole argv element and never through a shell; `args` must not contain it. A
preset is the verified spelling for its product; a repository that needs a different skill
directory or invocation is describing a different product and says so with `cli`.
`[runners.<name>]` holds no token: the product reads its own.

### A product judge's verdict is the first balanced JSON object in the reply, validated as `JudgeOutput`; anything else is `JudgeVerdict.error`

A product has no structured-output mode, so `judges/product.py` sends the shared prompt
(`judges/prompt.py`'s rules and request, the same text the framework judges send) as one
user turn closed by a line asking for one JSON object and nothing else, then reads the reply
with `extract_json_object`: one pass over the text, a stack of unmatched `{` positions
outside JSON strings, the earliest opening brace that closes wins — so an outer object beats
an inner one that closed first, and a `}` inside quoted evidence never ends the object
early. The object is validated by `_RawVerdict`, a strict local view of `JudgeOutput`
(`extra="forbid"`, `checks` required, `title="JudgeOutput"` so the error names the shape
callers asked for) — local so that the shared `JudgeOutput` the framework judges bind as
structured output stays lenient and its generated JSON schema, and the cassettes recorded
against it, are unchanged. No object, a cut-off object, the wrong shape, an empty object or
a key beside `checks` is `JudgeVerdict(error="JudgeOutputInvalid: ...")` naming the actual
mismatch, one attempt, which `JudgeEvaluator` reports as an **errored** case: an unreadable
verdict is an infra signal, not a low score, and the error says so here rather than
surfacing several layers away as a mismatched id set. A product failure — timeout, non-zero
exit, truncated output, an executable gone since preflight — is `JudgeVerdict.error` the
same way, through the runner's own `read_trace`; the judge never raises. An over-size prompt
is checked in the judge before the product starts, also `JudgeVerdict.error`, but never
reaches `read_trace`. The judge's working directory is a fresh empty temporary directory
removed in a `finally`, and holds no skill: the judge grades text and must not discover the
skill under test. `Product.judge_args` is appended only when the product judges —
`("--tools", "")` for `claude-code` and `("--available-tools=skill-lens-none",)` for
`copilot`, each verified against the product to leave the model no tool, built-in or MCP;
Copilot ignores an empty `--available-tools` list, so the list names one tool that does not
exist, as a single `=` element so the variadic option can never swallow what follows it —
after the table's `args`, so a repository's model flag still applies, and only while
`command` still names the preset's executable, since a wrapper is not known to accept the
flag. `Product.tool_flag` names the product's own tool-selection flag (`--tools`,
`--available-tools`); both products *accumulate* a repeated one rather than taking the last
(verified on both), so a table `args` or `command` entry equal to it or starting with
`<flag>=` would hand the judge tools back behind `judge_args`, and
`ProductJudge.preflight()` raises `ProductSetupError` (exit 2) naming the entry before any
case runs. `judge_temperature` is not consulted: no product exposes it. Tokens, cost, cost
note and model come from the trace, so `cli` reports none and Copilot reports a per-request
note, as under the runner. `ProductJudge.preflight()` finds the executable and runs the
preset's `--version` with `role="judge"` in the message. `cli.py` demands a key and a
`judge_model` only for the two keyed judges in `_KEYED_JUDGES`; a product judge is built
from its table with neither, and `--judge-model` with a product judge is refused as a user
error.

### A case's `tools:` reach a preset product through a stdio MCP server skill-lens ships, and the product starts it

The framework adapters register mock tools as callables inside the agent loop they drive; a
product owns its loop, and what it can take is an MCP server named in a config file.
`mcp_bridge.py` is that server, started by the product as
`python -m skill_lens.mcp_bridge <spec>` — `sys.executable`, so the package is importable
with no environment of its own — and `runners/mcp.py` is the runner's side: per invocation,
`write_bridge` puts the spec (name, description, the schema `build_mock_tool` registers,
`returns`) and the product's config into a fresh directory of its own, never the working
directory, which the product can list and a `file-produced` assertion can read; the config
travels as the last argv element (`--mcp-config=<file>` for Claude Code, beside the preset's
`--strict-mcp-config`; `--additional-mcp-config=@<file>` for Copilot), one element with `=`
so Claude Code's variadic option can never swallow what follows. The directory is deleted in
the run's `finally`, kept by nothing. The bridge is started with `-P`, so the product's
working directory — the case's workspace — never enters its import path, and it imports only
`matching` from the project, because the fewer things a child the product starts needs, the
fewer ways that start can fail: a `when:` must match by the one rule every runner uses, and
`matching` itself imports nothing from the project. It answers `returns:` in all three
shapes by the rules `runners/tools.py` applies — one value; a sequence in call order, its
counter in the server process, so every invocation starts from the top; a `when:` lookup, a
call no entry answers getting the `NO_RESPONSE_SCRIPTED` wording the runner writes into the
spec — and it writes nothing but JSON to stdout, because stdout is the protocol. No
dependency was added: four JSON-RPC methods do not need an SDK.

### No new opt-in

A mock returns canned text and executes nothing — it adds a fixed answer to a tool the skill
may call, not a capability the product lacked — so a case with `tools:` runs under a product
the way it runs under a framework runner, in both modes and both arms. Naming the product
runner remains the trust decision, and `TRUST_NOTE` is unchanged.

### The trace names the tool the product's way; the result names it the case's way

The model sees `mcp__skill-lens__<name>` under Claude Code and `skill-lens-<name>` under
Copilot (each verified by reading what the product sends the model), and the trace reports
the call so. `restore_tool_names` maps each declared tool back, by its exact product
spelling and nothing looser, so `trajectory: called`/`forbidden`/`order` — which the loader
already restricts to declared names — read identically under every runner; every other call
keeps the product's name, per the decision not to normalise tool names across products. The
transcript keeps the product's spelling. Tool calls are still what the model requested, read
from the trace; the bridge's record is not the source of the trajectory.

### A product that ran but never asked the bridge for its tools is `errored`, never a failed `called:`

The server appends a `list` event to a record file on every `tools/list`; `Bridge.connected`
reads it after the run. It is product-independent — Claude Code's `init` event and Copilot's
`session.mcp_servers_loaded` both report server status, but the record needs neither parser
to change. A trace error wins over the connection check, because a product that failed is
the better explanation.

### Preflight proves the bridge starts and refuses what would hide it, before any quota is spent

When a planned candidate case declares `tools:`, `probe_bridge` runs the module once with
`--check` under `sys.executable` — executed, not merely found, like the version probe and
the sandbox probe — and a `BridgeSetupError` is a `ProductSetupError`, exit 2. The same
preflight refuses `--available-tools` in `[runners.copilot]` (verified: it keeps only the
tools it names, MCP included), mirroring the judge's refusal of the flag; Claude Code's
`--tools` governs the built-in set only (verified: the MCP tool stays listed under
`--tools ""`) and is not refused. `Config.product` drops `mcp` along with the version probe
and `judge_args` when `command` names another executable, so `tools:` under such a table is
refused with a message naming the flag the wrapper is not known to take. The product judge
is built from the same `Product` but never writes a bridge: it grades with no tools.

### `ProductRunner.run` still never raises

A bridge directory that cannot be written is `OSError` → `RunResult.error`; a runner reached
with `tools:` and no `mcp` (a caller that skipped preflight) returns an errored result
rather than running the case without its tools.
