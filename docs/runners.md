# Runners

The default runner is `fake` (offline, scripted, free). To evaluate a skill with a
real agent, install the extra and pick a model:

```bash
uv sync --extra pydantic-ai
export OPENAI_API_KEY=...
uv run skill-eval run ./skills --runner pydantic-ai --model openai:gpt-4o-mini
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
only printed for a case that fails — and a declared `max_cost_usd` that cannot be priced
always fails its case (see below), so the note is always printed alongside it on the console,
never silently on a passing case. An aggregate line ("some costs not priced" / "Total cost:
not priced") also appears in the console totals whenever any outcome's pricing degraded,
pointing you at the JSON for the per-case detail.
An unpriceable cost limit is **skipped rather than silently passed**, so a case with an
unpriced cost limit and no other budget checks will fail, because nothing was actually
verified. If other budget limits are declared alongside (tokens, latency), they are still
evaluated normally and contribute to `score` — but the case's `passed` verdict still requires
every declared limit to hold, and a skipped cost limit never holds. **A budget block whose
priced limits all hold still fails the case if it also declares an unpriceable
`max_cost_usd`** — the skipped check counts as a failure of that one check, even though it
does not lower `score` below what the priced checks alone would give it. If you adopt
`skill-eval` against a provider `genai-prices` cannot price, omit `max_cost_usd` from the
budget block for that provider rather than expecting it to be silently ignored.

If you are upgrading from a version of `skill-eval` where this budget block previously passed
some other way, note the change: a repo that runs an unpriced model with a `budget:` block
mixing a priced limit (e.g. `max_tokens`) and `max_cost_usd` will now see those cases turn red
— they always failed to verify the cost limit; only the reporting of `passed` has caught up
with `score`.

## Comparative runs and the baseline arm

Under `--baseline` (see [Comparative evals](comparative-evals.md)), the runner is called
twice per case: once with the candidate skill, once with the baseline skill. Both calls go
through the same `Runner.run(skill, case)` seam — there is no separate "baseline mode" a
runner has to implement.

A skill with **both `description` and `instructions` empty** — which is exactly what
`--baseline none` constructs — gets a neutral system prompt instead of the normal
`# {skill.name}` header:

```
You are a helpful assistant.
```

This keeps the skill's name out of a prompt that is explicitly meant to measure what the
skill's own text contributes. The rule is keyed on the skill being empty, not on which arm is
running, so a runner never has to know — or be trusted to know — which arm it is serving.
