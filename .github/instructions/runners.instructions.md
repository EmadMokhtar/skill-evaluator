---
applyTo: "src/skill_eval/runners/**"
---

# Reviewing runner code

This directory is the framework boundary. Everything the core sees is a plain `RunResult`.

- **Never raise for a provider failure.** Timeouts, rate limits, auth errors and malformed
  responses become `RunResult(error=...)`. Raising turns an infra problem into an
  unhandled crash and loses the errored/failed distinction the gate depends on.
- **Only `pydantic_ai.py` may import an agent framework.** `tools.py` builds
  framework-neutral `MockTool`s — a name, a JSON schema and a callable — and the adapter
  wraps them. A test asserts the string `pydantic_ai` does not appear in `tools.py`.
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
