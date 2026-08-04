# Designing the cases

## Deriving cases from the skill

Read the instructions and list every claim. Claims look like: "always call X first",
"never do Y after 30 days", "answer in one short sentence", "ask before writing files".
Each claim gets at least one case; the interesting ones get two, one either side of the
line.

The frontmatter `description` is the claim that triggering tests: it is the only text an
agent sees when deciding whether to reach for the skill.

## A minimum suite

1. **The common case** — the prompt this skill exists for, with an assertion on something
   every good answer contains.
2. **The edge** — the prompt that reaches the policy line, with `trajectory` if getting
   there requires a tool call.
3. **The other side of the edge** — the near-identical prompt where the answer flips. A
   skill that refuses everything passes case 2 alone.
4. **The triggering pair** — `mode: offered`, positive and negative.

## Patterns by skill archetype

| Archetype | What to check |
| --- | --- |
| Policy skill (refunds, approvals) | `trajectory` proving it looked before deciding; `forbidden` on the destructive tool; both sides of the policy line |
| Tool-using skill | `order` for a required sequence; `max_calls` against loops; a case where the tool returns an error string |
| Formatting skill | `regex` — but only on structure the skill actually promised; a judge for "reads plainly" |
| Knowledge skill | `contains` on the fact; `not_contains` on the plausible wrong answer; a judge for reasoning |

## Writing rubric entries

Each entry is checked independently and must be evidenced by quoting the output. That
makes the test for a good entry mechanical: **could you point at the sentence that proves
it?**

- Good: "The reply names order 1234." "The reply states the return window has closed."
- Bad: "The reply is helpful." "The response is well structured." Nothing can be quoted
  as proof, and an unsupported pass is an LLM judge's characteristic failure mode.

Split compound entries. "Names the order and explains the policy" hides which half failed.

## Assertions that age badly

- A regex pinning phrasing the skill never promised. Check the structure the skill
  committed to, nothing more.
- `equals` on anything a model generates freely.
- Asserting on a number the mock tool returns — that tests the fixture, not the skill.
- A budget set at the current spend. Leave headroom, or every prompt improvement is a
  red case.
