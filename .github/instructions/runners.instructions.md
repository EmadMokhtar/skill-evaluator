---
applyTo: "src/skill_eval/runners/**,src/skill_eval/judges/**"
---

# Reviewing runner and judge code

These two directories are the framework boundary. Everything the core sees is a plain
`RunResult` or `JudgeVerdict`. The two share a contract, so the rules below apply to both
unless one is named.

- **Never raise for a provider failure.** Timeouts, rate limits, auth errors and malformed
  responses become `RunResult(error=...)` or `JudgeVerdict(error=...)`. Raising turns an
  infra problem into an unhandled crash and loses the errored/failed distinction the gate
  depends on.
- **The whole capture path belongs inside the guard.** Reading messages, summing usage,
  pricing and constructing the result must all sit inside the `try`, not just the model
  call — a serialisation failure on a real provider's message shape must report errored,
  not crash the run. This was a real bug once.
- **Only `runners/pydantic_ai.py` and `judges/pydantic_ai.py` may import an agent
  framework.** `runners/tools.py` builds framework-neutral `MockTool`s — a name, a JSON
  schema and a callable — and the adapters wrap them.
  `tests/test_framework_isolation.py` enforces the boundary across the whole package.
- **A judge reports per-check verdicts only** — never an overall verdict or a blended
  score. skill-eval derives `passed` and `score` from the per-check results, because an
  unsupported PASS hidden inside a single number is invisible. `JudgeOutput` is deliberately
  narrower than `JudgeVerdict` so the model cannot assert its own cost or outcome.
- **`FakeJudge` unscripted returns an error, not a pass.** That is what makes
  `judge = "fake"` safe as the built-in default: a rubric with no configured judge is
  errored, never a quiet green.
- **`judges/prompt.py` is pure and deterministic.** Same input, byte-identical output, every
  process — cassettes match on the request body, so any instability makes them unmatchable.
  No `hash()`, no randomness, no time.
- **Mock tools accept any arguments.** A model hallucinating an argument is an eval signal
  about the skill; rejecting it would surface as an infra error instead.
- **Nothing executes in a mock tool.** It records the call and returns its canned value, so
  the trajectory is genuinely the model's choice and a run has no side effects.
- **Pricing never fails a run.** An unpriced model yields `cost_usd = 0.0` plus a
  `cost_note`. Flag any code path where a pricing lookup can raise.
- **`RunResult.tokens` is derived** from the input/output split. Flag any attempt to set it.
- **`FakeRunner.run` returns `model_copy(deep=True)`** so a caller cannot corrupt scripted
  state.
- **Cassettes are replay-only and secret-free.** Recording is a deliberate, key-bearing act.
  A missing cassette skips; a mismatched request fails rather than reaching the network.
  Flag any credential or account-identifying header that could reach a recorded file.
- A runner that spends money sets `needs_api_key = True` so the preflight check runs before
  any request.
