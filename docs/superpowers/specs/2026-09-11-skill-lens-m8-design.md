# skill-lens M8 — Design

**Date:** 2026-09-11
**Status:** Approved (design), pending implementation plan
**Parent:** `2026-07-30-skill-eval-design.md` (§9 M8)

## 1. Scope

The parent spec defines M8 as "**(optional)** LangChain adapter: only if it slots cleanly
behind `Runner`; enables cross-framework matrix. Droppable." Reading the two existing
adapters settles the condition: `runners/pydantic_ai.py` is 254 lines and
`judges/pydantic_ai.py` is 95, and every input they consume is already framework-neutral —
`AgentTool` (name, JSON schema, callable) for tools, `render_request` and `JudgeOutput`
for the judge, `calculate_cost` for pricing, `RunResult` and `JudgeVerdict` for results.
A second framework is a translation layer over the same pieces, so M8 is not droppable
on feasibility grounds, and this milestone ships it as **one spec and two pull requests**.

**Part 1 — the adapter** (`feat: run cases and grade rubrics through LangChain`):

- **`LangChainRunner`** (`--runner langchain`): LangChain 1.x's prebuilt tool-calling
  agent (`langchain.agents.create_agent`, built on LangGraph, LangChain's graph-based
  agent runtime) behind the unchanged `Runner` protocol. Mock tools, the offered-mode
  skill tool and the workspace tools are the same `AgentTool`s the PydanticAI runner
  uses; trajectory, tokens, served model name, cost and `skill_triggered` are captured
  the same way and land in the same `RunResult`.
- **`LangChainJudge`** (`judge = "langchain"`): the same rubric, prompt and per-check
  verdict contract, so the `[langchain]` extra is self-sufficient — a repository that
  installs only it can both run and grade.
- **Two framework-neutral helpers extracted from the PydanticAI adapter**, so the rules
  both adapters must agree on exist once: `runners/prompting.py` (the three preambles and
  the system-prompt builder) and `runners/retry.py` (the transient-retry loop).
- **A `[langchain]` optional extra**, and Anthropic added to both extras so a matrix run
  can name one `--model` that every runner accepts.
- Zero-cost fake-model tests for both adapters, three cassette tests, one live-tier test,
  and the framework-isolation guard extended to four modules.

**Part 2 — the matrix** (`feat: run every case through more than one runner`):

- **`--runner` becomes repeatable** and `default_runner` accepts a string or a list, so
  one invocation runs every case through every named framework and produces one report.
- **The action's `runner` input accepts a comma-separated list.**
- Nothing in the orchestrator, the reporters or the gate changes: they have keyed on
  `CaseOutcome.runner` since M1 and M5. Part 2 is the CLI, the config model, the action,
  their tests and their docs.

### Explicitly deferred

| Deferred | Why |
| --- | --- |
| A per-runner gate threshold (`per_runner_min`) | Joins the deferred gating knobs from M4 and M5 (per-skill `min_delta`, `--baseline-ref`, both-arms-fail flagging). A case that fails under one framework fails the gate; that is the matrix's purpose. |
| A judge list (`judge = ["pydantic-ai", "langchain"]`) | One judge grades every runner's output, or the comparison between runners is not fair. The judge is a measuring instrument, not a subject. |
| Provider packages beyond OpenAI and Anthropic | `pip install langchain-<provider>` or `pydantic-ai-slim[<provider>]`; documented, not bundled. Every bundled SDK is weight for users who never name it. |
| LangGraph features (checkpointers, middleware, streaming) | The runner measures a skill, not a graph. A case has one turn. |
| A LangChain-native judge prompt | The judge prompt is a project contract shared by every judge; a second prompt would make `judge = "langchain"` measure the prompt, not the framework. |
| Normalising provider-prefix spellings across frameworks (`google-gla:` vs `google_genai:`) | The model string is passed to each framework unchanged. A translation table would be a third thing to keep in step with two moving targets; the docs list the spellings that differ. |
| A top-level `runners` field in the JSON report | Every outcome already carries `runner`; nothing consumes an aggregate list. The JSON report is a contract and every field added to it is permanent. |

## 2. Decisions

| Decision | Why |
| --- | --- |
| **`create_agent`**, not `bind_tools` plus a hand-written loop. | The matrix exists to answer "does the skill work under LangChain's agent". A loop written here would compare two harnesses written here. |
| The **prompt rules move to `runners/prompting.py`**; the retry loop to `runners/retry.py`. | The arm-identical and never-names-the-skill invariants are what `--min-delta` measures against. Two copies could drift; one function has one test. Neither helper imports a framework, so both sit outside the isolation allowlist. |
| **Duck-typed transient detection** for LangChain: an exception with a `status_code` attribute is transient on 408/409/429/5xx; the builtin `TimeoutError` / `ConnectionError` are transient; so is any exception whose class name ends in `ConnectionError` or `TimeoutError`; nothing else is. | LangChain does not normalise provider exceptions. The `openai` and `anthropic` SDKs both expose `status_code` on HTTP errors, but their network errors (`APIConnectionError`, `APITimeoutError`) carry no status and do not subclass the builtins, so the class name is the one provider-neutral signal left. Matching attributes and names rather than SDK types keeps the rule identical for a provider package that is not installed. |
| **A LangChain judge reports a malformed structured output as `errored`**, through the same retry loop, as non-transient (one attempt). | PydanticAI retries a malformed output internally; LangChain hands it back as `parsing_error`. An unreadable verdict is an infra signal, not a low score — the existing `errored` ≠ `failed` rule. |
| **`with_structured_output(..., include_raw=True)`** in the judge. | Without `include_raw` LangChain returns only the parsed object; tokens, cost and the served model name would be unreadable, and `EvalScore.cost_usd` (judge overhead in the report) depends on them. |
| **`genai-prices` is declared by the `[langchain]` extra.** | `calculate_cost` imports it and today it arrives only as a transitive dependency of `pydantic-ai`. A `[langchain]`-only install would otherwise stamp every case with a truthful-but-useless "not installed" note. |
| **Anthropic goes into both extras.** | A matrix run passes one `--model` to every runner; `--model anthropic:…` erroring on one arm only is a confusing failure for the feature Part 2 exists to enable. `check_api_key` already maps `anthropic:`, and `genai-prices` prices Anthropic models. |
| **A duplicate `--runner` is a user error**, not de-duplicated. | The same `(skill, case)` would enter the pass rate twice, weighting one framework's vote double under `--repeat` and `--baseline`. |
| **An empty `default_runner` list is a config error**, checked at load time. | Zero runners means zero cases run, which the gate would fail — but as "nothing ran", far from the cause. `ConfigError` names the field. |
| **`--runner` replaces the config list wholesale**; it never appends. | Every other flag replaces its key. An appending flag would make "run only LangChain this once" impossible from a repository whose file names both. |
| **The action's `runner` input splits on commas** rather than adding a second input. | One Markdown summary and one JUnit file for the whole matrix. A GitHub `strategy.matrix` over runner values works today without any change and stays documented as the alternative; it yields one report per job. |
| **`RunnerDependencyError` moves to `runners/base.py`**; `runners/pydantic_ai.py` re-exports it. | Two adapters raise it; it is a protocol-level concept now. The re-export keeps every existing import working. |
| **The model string reaches each framework unchanged.** | `openai:` and `anthropic:` are spelled identically in PydanticAI and in LangChain's `init_chat_model`; where spellings differ the docs say so and the user picks the one the framework understands. |

## 3. `runners/prompting.py` and `runners/retry.py`

Both are extracted from `runners/pydantic_ai.py` with no change in behaviour.

**`prompting.py`** holds `OFFERED_PREAMBLE`, `BASELINE_PREAMBLE`, `WORKSPACE_PREAMBLE`,
`system_prompt(skill)` (identity first, then instructions; `BASELINE_PREAMBLE` when both
`description` and `instructions` are empty) and `instructions(skill, case, has_workspace)`
(offered preamble in `offered` mode, otherwise the skill prompt; the workspace preamble
appended byte-identically whenever a workspace exists). The PydanticAI module imports
`instructions` and nothing else from it; the tests and the cassette test that named the
preambles through `runners/pydantic_ai.py` import them from `runners/prompting.py` instead,
so no re-export is needed and ruff's unused-import rule stays clean.

**`retry.py`** holds `TRANSIENT_STATUSES = {408, 409, 429}`, `transient_status(status_code)`
(true for those three and for any 5xx — the one place the status policy is spelled) and

```python
def run_with_retries(call, is_transient, retries, backoff_seconds, sleep) -> Any
```

which calls `call()` and, on an exception `is_transient` accepts, sleeps `backoff_seconds`
(doubling each time) and tries again up to `retries` more times; any other exception, or
the last attempt, re-raises. Each adapter supplies its own `is_transient`. The PydanticAI
runner and judge switch to it in Part 1; the sleep is injectable, as today, so the tests
never wait.

## 4. `runners/langchain.py`

**Class `LangChainRunner`**, `name = "langchain"`, `needs_api_key = True`. The constructor
is the PydanticAI runner's: `model` (a `provider:model` string, or a `BaseChatModel`
instance — tests pass a scripted one), `temperature` (float or `"unset"`), `retries`,
`retry_backoff_seconds`, `sleep`.

**Building the agent.**

1. `_require_langchain()` imports `langchain` and raises `RunnerDependencyError` naming
   `pip install 'skill-lens[langchain]'` when it is missing.
2. The `AgentTool` list is built exactly as today — `build_mock_tool` for each declared
   tool, `build_skill_tool` in `offered` mode, `build_workspace_tools` when a workspace
   exists — and each becomes `StructuredTool.from_function(func=tool.call, name=tool.name,
   description=tool.description, args_schema=tool.json_schema)`. LangChain accepts a JSON
   schema dictionary as `args_schema` and passes the model's arguments as keyword
   arguments, which `AgentTool.call` already accepts in any shape (mock tools accept any
   arguments; that invariant is the callable's, not the framework's).
3. A string `model` becomes `init_chat_model(model, temperature=…)`, the keyword omitted
   under `"unset"`; an instance is used as-is.
4. `create_agent(model, tools=tools, system_prompt=instructions(skill, case, workspace is
   not None))`.

**Running.** `agent.invoke({"messages": [HumanMessage(case.task)]})` inside
`run_with_retries` with the duck-typed `_is_transient` from §2. Latency is measured around
build plus run, as today.

**Reading the result.** `result["messages"]` is authoritative, as `all_messages()` is
for PydanticAI: it records what the model asked for, including calls whose execution
then failed.

- `tool_calls`: every `AIMessage.tool_calls` entry in message order, `ToolCall(name,
  arguments=args)`; `args` is already a dictionary, so `_arguments` is not needed.
- `input_tokens` / `output_tokens` (`_usage`): summed over every `AIMessage.usage_metadata`.
  LangChain reports usage per response, not per run; a message without
  `usage_metadata` contributes zero.
- `model`: `response_metadata["model_name"]` of the last `AIMessage` that has one,
  falling back to the configured string — the served model may be a dated snapshot.
- `output`: the last `AIMessage`'s text content (`.text`), or `""` when there is none.
- `transcript`: `[message.model_dump(mode="json") for message in messages]`.
- `cost_usd` / `cost_note`: `calculate_cost(genai_prices.Usage(input_tokens=…,
  output_tokens=…), model, provider_of(configured))`. The `Usage` type is the one
  `genai-prices` prices; building it here keeps `pricing.py` framework-free.
- `skill_triggered`: `None` unless `offered`, else whether any call names the skill tool.

**Errors.** Any exception from build or run becomes `RunResult(error=f"{type(exc).__name__}:
{exc}", latency_ms=…, model=configured)`; `run` never raises for a provider failure.

## 5. `judges/langchain.py`

**Class `LangChainJudge`**, `name = "langchain"`, `needs_api_key = True`, the PydanticAI
judge's constructor.

**Grading.** The model (string → `init_chat_model`, instance as-is) is wrapped with
`.with_structured_output(JudgeOutput, include_raw=True)` and invoked with
`[SystemMessage(SYSTEM_PROMPT), HumanMessage(render_request(request))]` inside
`run_with_retries`. The result is `{"raw": AIMessage, "parsed": JudgeOutput | None,
"parsing_error": Exception | None}`.

**Verdict.** `parsed` present → `JudgeVerdict(checks=list(parsed.checks), input_tokens,
output_tokens, cost_usd, cost_note, model)`, usage and model name read from `raw` with the
same two helpers the runner uses (`_usage`, `_model_name`), imported from
`runners/langchain.py` exactly as `judges/pydantic_ai.py` imports from its runner.
`parsing_error` set, or `parsed` is `None` → `JudgeVerdict(error="JudgeOutputInvalid:
<error>", model=…)`. A provider exception → `JudgeVerdict(error=f"{type(exc).__name__}:
{exc}")`. The judge never raises.

Everything downstream is untouched: `JudgeEvaluator` derives `passed` and `score` from
the per-check verdicts, and a check that passes without evidence is still recorded as a
failure whichever framework produced it.

## 6. Packaging and the framework boundary

```toml
[project.optional-dependencies]
pydantic-ai = ["pydantic-ai-slim[openai,anthropic]>=2.22"]
langchain = [
    "langchain>=1.0",
    "langchain-openai>=1.0",
    "langchain-anthropic>=1.0",
    "genai-prices>=0.1",
]
```

CI already runs `uv sync --all-extras --dev`, so the new extra is installed there with no
workflow change; Dependabot's `uv` ecosystem picks up the new lockfile entries.

**`tests/test_framework_isolation.py`** gains a second pattern (`langchain`,
`langchain_*`, `langgraph`) and two allowed paths (`runners/langchain.py`,
`judges/langchain.py`). The existing "every allowed adapter actually exists" guard covers
the new entries. The condensed invariant in `CLAUDE.md` and its explanation in
`ARCHITECTURE.md` change from "the two adapter modules" to "the four".

**`cli.py`** registers `_RUNNERS["langchain"]` and `_JUDGES["langchain"]`. The existing
`RunnerDependencyError` handler (exit 2 with the install hint) needs no change.

**`action.yml`**: the `install-spec` default stays `skill-lens[pydantic-ai]==<version>`;
`docs/ci.md` shows `skill-lens[pydantic-ai,langchain]==<version>` for a matrix job. The
version-pattern guard in `tests/test_release_config.py` is untouched.

## 7. The matrix — `cli.py`, `config.py`, `action.yml`

**CLI.** `--runner` is `list[str] | None`. Resolution: the flag list when given, else
`settings.default_runner` normalised to a list. Two user errors (exit 2): a name not in
`_RUNNERS` — the message names which — and a name given twice. Every runner with
`needs_api_key` is built with the same `model`, `temperature`, `retries` and
`retry_backoff_seconds`; `_require_a_model` and `check_api_key` run once when any of them
needs a key. The `Plan:` line becomes

```
Plan: up to {arms} arm(s) x {repeat} repeat(s) x {runners} runner(s) x {cases} case(s) = N runs
```

so the ceiling it promises stays a ceiling, and a two-runner run says before the first
request that it will spend twice. `run_evals(skills, runners, …)` receives the whole
list; it has taken `list[Runner]` since M1.

**Config.** `default_runner: str | list[str] = "fake"`, with a validator that rejects an
empty list and a duplicate, each as `ConfigError` naming the field. `extra="forbid"`
unchanged.

**Action.** The `runner` input is split on commas into one `--runner` per entry inside
the existing bash `add` block; a single value behaves exactly as today.

**Reporters and gate — no change, stated.** `CaseOutcome.runner` exists per outcome; the
JUnit reporter already suffixes `(runner)` to a test name only when the matrix has more
than one runner; console and Markdown already print `(runner)` on every case line;
`comparison.py` already pairs candidate and baseline per `(skill, case, runner)`, so the
delta block is per framework and the aggregate delta spans the matrix. Every candidate
outcome counts toward the pass rate: a case failing under either framework fails the
gate, `per_skill_min` is per skill across runners, and `--min-delta` is over the whole
matrix.

## 8. Documentation

| Change | Page |
| --- | --- |
| `--runner` repeatable; the matrix cost line | `docs/cli.md` |
| `default_runner` string-or-list; its two validation errors | `docs/configuration.md` |
| The LangChain runner and judge: what each extra bundles, provider-prefix spellings per framework, how tokens and the model name are read, the parsing-error rule | `docs/runners.md` |
| Gate semantics over a matrix (unchanged, stated) | `docs/gating.md` |
| Matrix job example, the `runner:` list input, the `strategy.matrix` alternative | `docs/ci.md` |
| `runners/prompting.py`, `runners/retry.py`, both adapters; the four-module boundary | `ARCHITECTURE.md` and the condensed invariant in `CLAUDE.md` |
| One install line for `[langchain]` | `docs/index.md`, `docs/getting-started.md`, `README.md` (landing-page rule: one line each) |
| M8 row → shipped; a "What M8 shipped" section | `docs/roadmap.md` |

`test_every_cli_option_is_documented` and `test_every_config_field_is_documented` catch
an omission on the first two rows. Nothing new in `nav:`.

## 9. Testing

**Zero-cost tier (CI, no network).** `tests/langchain_fakes.py` holds one scripted chat
model: a `BaseChatModel` subclass whose `_generate` returns the next scripted `AIMessage`
and whose `bind_tools` returns `self`. The one double serves both adapters — `create_agent`
calls `bind_tools`, and LangChain's default `with_structured_output` binds a tool named
after the schema and parses that tool call's arguments, so a scripted `AIMessage` carrying
a `JudgeOutput` tool call is a scripted verdict. It is the counterpart of `FunctionModel`
in `tests/test_pydantic_ai_runner.py`.

- **`tests/test_langchain_runner.py`** mirrors the PydanticAI file case for case: text
  output; a tool call recorded with its arguments and the mock tool actually invoked; the
  offered-mode skill tool present and `skill_triggered` both ways; a provider exception
  becomes `RunResult.error`, never a raise; a 429 is retried and a 401 is not; tokens
  summed across two model turns; the served model name read from `response_metadata` and
  the fallback to the configured string; the dependency message names `[langchain]`.
- **`tests/test_langchain_judge.py`**: a scripted verdict yields the checks; usage, cost
  and model name are read from `raw`; a `parsing_error` becomes `JudgeVerdict.error`
  without a retry; a provider exception becomes `JudgeVerdict.error`.
- **`tests/test_prompting.py`** and **`tests/test_retry.py`** cover the extracted helpers
  once; the arm-identical and never-names-the-skill tests move there from the PydanticAI
  file, and `test_pydantic_ai_runner.py` keeps only what is PydanticAI-specific.

**Cassette tier.** Three recordings: the whole loop through `LangChainRunner`, the judge
grading a rubric with evidence, and the offered-skill trigger. The `vcr_config` is
unchanged — LangChain's OpenAI client is the same `openai` SDK over `httpx`, so the header
scrub and body matching apply as they are. Recording needs an `OPENAI_API_KEY` and is a
deliberate act by a person; until the files exist the three tests skip, as a missing
cassette does today, and CI stays green. The refresh workflow's `--record-mode=rewrite`
then covers them for life.

**Live tier.** One `@pytest.mark.integration` twin of
`test_the_examples_pass_against_a_real_provider` that runs `--runner langchain`; opt-in
and key-bearing as today.

**Part 2.** `tests/test_cli.py`: the repeatable flag; a duplicate rejected; an unknown name
named; a config list honoured; the flag replacing the list; the `Plan:` line multiplying
by runner count; a two-runner run over `FakeRunner` twice under distinct names producing
`2 × cases` outcomes each stamped with its runner. `tests/test_config.py`: an empty list
and a duplicate rejected with the field named; a plain string still accepted.
`tests/test_action.py`: the comma split. `tests/test_framework_isolation.py`: the second
pattern and the two paths.

**Guards that must keep passing:** `test_naming`, `test_docs`, `test_supply_chain`,
`test_release_config`, `test_security_checks`.

## 10. Invariants this milestone must not break

1. **No agent-framework type appears outside the four adapter modules.** `prompting.py`
   and `retry.py` import none; that is why they can be shared.
2. **Runners and judges never raise for provider failures.** A LangChain provider error
   is `RunResult.error` / `JudgeVerdict.error`; a malformed judge output is
   `JudgeVerdict.error`.
3. **The workspace preamble is byte-identical in both arms and never names the skill;
   the baseline arm never receives the skill's name.** Now enforced by one function both
   adapters call.
4. **Judge spend never enters `RunResult`.** The LangChain judge's usage lives on
   `EvalScore.cost_usd`, as the PydanticAI judge's does.
5. **Cost lookup degrades, never raises.** The `[langchain]` extra declares `genai-prices`
   so the note is informative when it appears, and `calculate_cost` still returns `0.0`
   plus a note for anything it cannot price.
6. **Every `(skill, case, runner)` candidate outcome counts toward the gate.** A duplicate
   runner is refused so no outcome counts twice.
7. **`--runner` replaces `default_runner`; it never appends.**
8. **Cassettes are replay-only and secret-free.** The new recordings are made with a key
   by a person, scrubbed by the same `vcr_config`, and never fetched by CI.
9. **`skill_lens` never appears in user-facing output.** New error messages say
   `skill-lens[langchain]`.

## 11. Release shape

Two pull requests, each squash-merged, each `feat:`, each bumping the minor version:

1. `feat: run cases and grade rubrics through LangChain` — Part 1. Additive: a new runner
   name, a new judge name, a new extra, two new modules, two extracted helpers. No exit
   code, JSON field or config key changes.
2. `feat: run every case through more than one runner` — Part 2. Additive: `--runner`
   accepts more than one value (one value behaves as before), `default_runner` accepts a
   list (a string behaves as before), the action's `runner` input accepts a comma
   (a single value behaves as before). The `Plan:` line gains a factor; console text is
   not a contract.

The cassette recordings for Part 1 land in the same pull request when a key is at hand
before it merges, or in a follow-up `test:` pull request produced by running the three
tests with `--record-mode=once`; either way CI is green throughout, because a missing
cassette skips.
