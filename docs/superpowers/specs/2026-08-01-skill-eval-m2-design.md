# skill-eval M2 — Design

**Date:** 2026-08-01
**Status:** Approved (design), pending implementation plan
**Parent:** `2026-07-30-skill-eval-design.md` (§9 M2)

## 1. Scope

M2 replaces "the pipeline works" with "the pipeline runs a real agent". It adds:

- **`PydanticAIRunner`** — the first real adapter, provider-flexible, registry name `pydantic-ai`.
- **Case-declared mock tools** — the eval case defines the tools the agent may call.
- **`TrajectoryEvaluator`** — scores which tools were called, which were avoided, in what
  order, and how many times.
- **`BudgetEvaluator`** — turns captured tokens/cost/latency into a gate rather than a
  decoration.
- **Cost & latency capture**, retries with backoff, and an API-key preflight check.
- **The cassette test tier** (§7) plus the live-integration marker.
- **Real `examples/` skills** whose assertions describe real model behavior.

### Explicitly deferred

| Deferred | Milestone | Why |
| --- | --- | --- |
| Real-execution tools (files, running skill scripts) | M6 | Mock tools give trajectory evals full value with no execution risk and stable cassettes. |
| Skill-triggering mode (`offered` vs `loaded`) | M3 | A second runner mode doubles the cassette surface; M2 is already carrying the first adapter, two evaluators, and a new test tier. |
| Baseline / repeat / delta | M4 | Needs its own report and gating shape. |
| Orchestrator concurrency | M5 | A thread pool makes cassette replay ordering nondeterministic — do not build it in the milestone that stands the cassettes up. |

## 2. Decisions

1. **Tools come from the eval case, as mocks.** The case declares each tool's name,
   description, parameters, and canned return value. Calling one records a `ToolCall` and
   returns the canned value; nothing executes. The trajectory is therefore genuinely the
   model's choice, with no side effects and no cassette instability.
2. **Trajectory checks live in their own typed `trajectory:` block**, not in the flat
   `assertions:` list. `AssertionSpec.value` is a `str` and cannot carry the list that
   `order` and `forbidden` need; more importantly, `assertions:` stays about `result.output`
   and `trajectory:` about `result.tool_calls`, so each evaluator has exactly one input and
   `extra="forbid"` still protects both.
3. **Efficiency is a first-class goal**, per the four categories in OpenAI's eval guide
   (outcome / process / style / efficiency). `trajectory.max_calls` catches looping;
   a `budget:` block caps tokens, cost, and latency.
4. **Usage is captured at full fidelity.** `RunResult` splits `input_tokens` /
   `output_tokens` (pricing depends on the split) and takes `cost_usd` from PydanticAI's
   `genai-prices` integration rather than a hand-maintained price table.
5. **`Runner.run` takes the case, not the task string.** A runner that must build the
   environment a case declares cannot be handed only that case's prompt.
6. **Retries and preflight now; concurrency later.** A 429 in CI must not read as a skill
   failure.
7. **Each test tier is aimed at what it can actually prove** (§7), which changes what CI's
   dogfood step runs.

## 3. The eval-case surface

```yaml
cases:
  - name: looks up the order before promising anything
    task: I want a refund for order 1234
    tags: [refund]

    tools:
      - name: lookup_order
        description: Look up an order by its id
        parameters:
          order_id: string
        returns: '{"id": "1234", "status": "delivered", "days_since_delivery": 45}'
      - name: issue_refund
        description: Issue a refund for an order
        parameters:
          order_id: string
          amount: number
        returns: '{"ok": true}'

    trajectory:
      called: [lookup_order]
      forbidden: [issue_refund]
      order: [lookup_order]
      max_calls: 4

    budget:
      max_tokens: 4000
      max_cost_usd: 0.01
      max_latency_ms: 30000

    assertions:
      - kind: contains
        value: "1234"
```

**`ToolSpec`** — `name` (a valid Python identifier), `description`, `parameters` (a mapping
of parameter name to one of `string` / `integer` / `number` / `boolean`), `returns` (a
string handed back to the model verbatim; use a YAML block scalar for JSON). All parameters
are required; optional parameters and nested schemas are deferred until a case needs them.

**`TrajectorySpec`** — `called`, `forbidden`, `order`, `max_calls`. All optional.

**`BudgetSpec`** — `max_tokens`, `max_cost_usd`, `max_latency_ms`. All optional.

Every one of these carries `extra="forbid"`, and an unknown tool parameter type is an
**authoring error** that aborts the run (exit 2), consistent with the existing invariant
that a mistake in the user's YAML is never scored as a skill failure.

## 4. `PydanticAIRunner`

```
run(skill, case) -> RunResult
  system prompt := skill.instructions, headed by the skill's name and description
  tools         := one function per case.tools entry, built by runners/tools.py
  settings      := temperature 0 by default (determinism, stable cassette bodies)
  agent.run_sync(case.task)  with retry/backoff
```

Mock-tool construction lives in `runners/tools.py`, not in the adapter: turning a `ToolSpec`
into a callable with a typed signature is framework-independent, and M6's real-execution
toolset will register through the same seam.

`temperature` is nullable and omitted from the request when unset, because the GPT-5-family
and o-series reasoning models reject any temperature other than 1 — a hardcoded 0 would make
the tool unusable on exactly the models a user is most likely to reach for next.

**Trajectory capture** reads `ToolCallPart`s out of `result.all_messages()` in order, rather
than recording inside the mock tool bodies — the message history is the authoritative record
of what the model asked for, including calls whose execution failed. Arguments arrive as
either a dict or a JSON string; both normalize to a dict, and an unparseable payload is kept
verbatim so a capture problem never masquerades as a model problem.

**Transcript** is the serialized message list, stored as plain JSON dicts. No PydanticAI type
crosses into the core — the adapter is the only module that imports the framework.

**Errors never raise.** Transient failures (HTTP 408/409/429/5xx, timeouts, connection
errors) are retried with bounded exponential backoff (`retries`, `retry_backoff_seconds`
from config; defaults 2 and 1.0s). Once retries are exhausted — or for a non-transient
failure — the runner returns `RunResult(error=...)` and the orchestrator marks the case
`errored`, not `failed`.

**Cost lookup degrades.** An unpriced or unknown model leaves `cost_usd` at `0.0` and sets a
note on the result. A missing price is never allowed to error a run.

**Preflight.** Before any spend, the CLI checks that the environment variable implied by the
model's provider prefix is set (`openai:` → `OPENAI_API_KEY`, `anthropic:` →
`ANTHROPIC_API_KEY`, and so on) and exits 2 with a precise message if not. An unrecognized
prefix skips the check rather than blocking a provider we do not know about. **Keys are read
from the environment only, never from `skill-eval.toml`.**

## 5. Model changes

Additive, so nothing in M0+M1 breaks:

- `RunResult` — `input_tokens`, `output_tokens`, `model`, `cost_note`. `tokens` becomes the
  computed sum of the two, keeping every existing reader working.
- `EvalCase` — `tools: list[ToolSpec]`, `trajectory: TrajectorySpec | None`,
  `budget: BudgetSpec | None`.
- `Runner` protocol — `run(skill, case)`. `FakeRunner` keeps its scripted behavior, still
  keyed on `case.task`, and still returns a deep copy.

## 6. Evaluators

**`TrajectoryEvaluator`** (`name = "trajectory"`) scores `result.tool_calls`:

- `called` — each listed name appears at least once.
- `forbidden` — no listed name appears.
- `order` — the listed names appear as a **relative subsequence**: other calls may be
  interleaved, but the listed ones must not appear out of sequence. ("Order" is otherwise
  ambiguous; this is the reading that survives a model taking a reasonable extra step.)
- `max_calls` — the total number of tool calls is at most N.

**`BudgetEvaluator`** (`name = "budget"`) compares `result` usage against `case.budget`.
A blown budget is `failed` — an eval signal about an inefficient skill — never `errored`.

Both follow the `AssertionEvaluator` precedent: score is the fraction of individual checks
that held, the verdict passes only when all hold, and an absent block is a vacuous pass with
an explanatory detail string.

The orchestrator's default evaluator list becomes
`[AssertionEvaluator(), TrajectoryEvaluator(), BudgetEvaluator()]`.

## 7. Testing

Each tier is pointed at what only it can prove:

**Tier 1 — pipeline (every PR, zero cost, no HTTP).** Loader and evaluator unit tests as
today, plus adapter-mapping tests driven by PydanticAI's `FunctionModel`: a scripted
in-process model that emits chosen `ToolCallPart`s so we can assert the adapter translates
messages into `ToolCall`s, sums usage, and converts failures into `RunResult.error` — all
without a socket.

**Tier 2 — cassettes (every PR, real fidelity, zero cost).** `pytest-recording` + `vcrpy`,
cassettes committed under `tests/cassettes/`, **replay-only by default** so an unmatched
request fails rather than silently reaching the network. Requests match on method, host,
path, and body; `Authorization`, `api-key`, `x-api-key`, and organization headers are
scrubbed on record. At least one full loop — runner → trajectory → budget → gating — runs
against recorded traffic. A test whose cassette is absent **skips with a clear message**
rather than erroring, so a fresh clone without recordings is not a red build.

**Tier 3 — live integration (opt-in, real money).** `@pytest.mark.integration`, deselected
by default and skipped without an API key, running the real `examples/` suites.

Recording for M2 is done locally against OpenAI (`openai:gpt-4o-mini` as the default model).
The automated "refresh cassettes" workflow lands with the rest of the CI work in M5.

**CI changes.** The dogfood step `skill-eval run ./examples` cannot survive examples that
assert real model behavior, so it becomes `skill-eval list ./examples` — still zero-cost,
still exercising real discovery, YAML parsing, and tool/trajectory/budget schema validation
on real files. The full run path is covered every PR by the cassette test instead.

## 8. Examples

- `examples/greeting` — assertions rewritten for a real model. Its current assertions match
  `FakeRunner`'s echo output and are documented in-file as wrong for real runners; that is
  the first thing an adopter copies.
- `examples/order-support` — a new skill with mock `lookup_order` / `check_policy` /
  `issue_refund` tools, exercising `called`, `forbidden`, `order`, `max_calls`, and a budget.
  The skill's rule ("never promise a refund before checking the order and the policy") is
  exactly the kind of behavior a trajectory check can verify and an output assertion cannot.

## 9. Config additions

A project opting into real runs writes:

```toml
default_runner = "pydantic-ai"   # built-in default stays "fake"
model = "openai:gpt-4o-mini"
temperature = 0.0                # omit or set to null on reasoning models
retries = 2
retry_backoff_seconds = 1.0
```

`--runner` and `--model` override the config; the config overrides the built-in default.
Secrets remain environment-only.

`gpt-4o-mini` is the default model because it is cheap, supports tool calling, and honours
`temperature = 0` — which the newer reasoning models do not. The built-in `default_runner`
stays `fake`, so upgrading to M2 never starts spending money on its own; reaching a provider
is always an explicit `--runner pydantic-ai` or a config edit.

## 10. Invariants this milestone must not break

- `errored` ≠ `failed`, and runners never raise for provider failures.
- A run executing zero cases fails the gate.
- Authoring errors abort the run and exit 2 — now including malformed `tools:`,
  `trajectory:`, and `budget:` blocks.
- Exit codes: pass `0`, gate fail `1`, user/authoring error `2`.
- `extra="forbid"` on every user-authored model.
- All file IO pins `encoding="utf-8"`; YAML goes through `yaml_loading.safe_load`.
- `skill_eval` (underscore) never appears in user-facing output; the runner registers as
  `pydantic-ai`.
- No agent-framework type appears outside `runners/pydantic_ai.py`.
