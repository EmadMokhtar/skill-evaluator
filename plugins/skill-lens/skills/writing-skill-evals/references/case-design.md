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
| Tool-using skill | `order` for a required sequence; `max_calls` against loops; `call_args` when correctness is in the arguments (a filter, an id, a path) — `called` alone cannot tell a filtered query from an unfiltered one; a case where the tool returns an error string |
| Loop-until / branch-on-result skill (walk a parent chain, poll until done, act on each of several ids) | one case whose mock answers per call — a `returns:` list in call order, or `when:`/`value:` entries keyed by argument — so the whole loop runs; `max_calls` for the stop condition; `call_args` with `every: true` for the id each call must carry; a `contains` per item it should have reached |
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

Never phrase an entry against a mock tool's `returns:`. The judge sees the task, `expected`,
the response and the files `artifacts:` names — never what a tool returned — so "does not
invent any detail not present in the mocked data" is unverifiable as written: a careful
judge fails it as ambiguous and a lenient one passes it unread. skill-lens refuses such a
line at load time. Phrase the check against the response ("names Alex Chen as the
reviewer"), or put the data in a `workspace:` file, name it under `artifacts:`, and phrase
the check against that file.

## Assertions that age badly

- A regex pinning phrasing the skill never promised. Check the structure the skill
  committed to, nothing more.
- `equals` on anything a model generates freely.
- Asserting on a number the mock tool returns — that tests the fixture, not the skill.
- A budget set at the current spend. Leave headroom, or every prompt improvement is a
  red case.
