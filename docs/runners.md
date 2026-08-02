# Runners

The default runner is `fake` (offline, scripted, free). To evaluate a skill with a
real agent, install the extra and pick a model:

```bash
pip install 'skill-eval[pydantic-ai]'
export OPENAI_API_KEY=...
skill-eval run ./skills --runner pydantic-ai --model openai:gpt-4o-mini
```

API keys are read from the environment only — never from `skill-eval.toml`.
`skill-eval` checks for the key before making any request, so a missing key costs
nothing and exits 2.

## Declaring tools and scoring the trajectory

An eval case can declare the tools the agent may call. Nothing executes: a tool
records the call and returns its canned value, so the trajectory is the model's
own choice and the run has no side effects.

```yaml
cases:
  - name: checks the order before refusing
    task: I want a refund for order 1234
    tools:
      - name: lookup_order
        description: Look up an order by its id
        parameters:
          order_id: string
        returns: '{"id": "1234", "days_since_delivery": 45}'
      - name: issue_refund
        description: Issue a refund for an order
        parameters:
          order_id: string
        returns: '{"ok": true}'
    trajectory:
      called: [lookup_order]        # each of these ran
      forbidden: [issue_refund]     # none of these ran
      order: [lookup_order]         # ran in this relative order
      max_calls: 3                  # no looping
    budget:
      max_tokens: 2000
      max_cost_usd: 0.01
      max_latency_ms: 30000
    assertions:
      - kind: contains
        value: "1234"
```

`order` is a relative subsequence: unrelated calls may appear in between, but the
listed tools must not appear out of sequence.

Every tool name in `called`, `forbidden`, or `order` must be declared in that case's
`tools:` — including `forbidden`, since forbidding a tool the agent was never offered in
the first place is a check that can never fire. A name that isn't declared is an
authoring error (the run aborts, exit `2`), not a failing case, because a check that can
never pass tells you nothing about the skill.

## Budget limits and pricing

The `budget` block sets ceilings on tokens, cost, and latency. Pricing comes from
`genai-prices`, which carries data for widely-used models but not every provider — for
example, Groq and Mistral models have no pricing entry yet. When a model cannot be priced,
`cost_usd` degrades to `0.0` and a note is recorded explaining why; the note always appears
in the JSON report. On the console it surfaces as budget-evaluator failure detail, which is
only printed for a case that fails — so if the cost limit is the only budget check declared,
the skip fails the case and the note prints; if token or latency limits are declared
alongside and pass, the case passes and the note is silent on screen. Either way, an aggregate
line ("some costs not priced" / "Total cost: not priced") appears in the console totals
whenever any outcome's pricing degraded, pointing you at the JSON for the per-case detail.
An unpriceable cost limit is **skipped rather than silently passed**, so a case with an
unpriced cost limit and no other budget checks will fail,
because nothing was actually verified. If other budget limits are declared alongside (tokens,
latency), they are still evaluated normally, and only the cost limit is skipped. To adopt
`skill-eval` against a provider without pricing data, simply omit `max_cost_usd` from the budget
block for that provider.
