# skill-lens per-call mock returns — Design

**Date:** 2026-09-21
**Status:** Implemented
**Issue:** [#41](https://github.com/EmadMokhtar/skill-evaluator/issues/41) — a mocked tool's
`returns:` is a single fixed string regardless of the arguments it's called with

## 1. Scope

A case's `tools:` block declares one static `returns:` per tool. A skill whose instructions
loop over a tool — fetch work item A, follow its parent link, fetch B, stop when there is no
parent — or branch on what a call returned cannot be exercised end to end: every call gets
the same payload, so the "walk until no parent" branch never runs. Authors either skip that
logic or approximate it with several near-duplicate cases that each cover one call.

This change ships as **one pull request** (`feat: let a mock tool answer differently per
call`):

- `returns:` keeps its string form and gains two list forms. A **list of strings** is a
  sequence consumed in call order, the last entry repeating once the list is used up. A
  **list of `when:`/`value:` mappings** is a lookup answered by the first entry whose
  `when:` keys all equal the call's arguments; an entry with no `when:` is the fallback.
- A `ref:` may set `returns:` in any of the three shapes. The library still owns the
  contract; the case still owns only the scenario.
- Load-time checks refuse an entry that could never fire, in a case, a library and a ref
  override alike.
- Both keyed adapters build the agent inside the retried callable, so a retried attempt's
  mocks start from the top.
- Documentation, the shipped `writing-skill-evals` skill, and a sixth `order-support`
  example case that answers by argument.

### Explicitly deferred

- **Per-call side effects** (a `write_file` the mock performs). A mock records and answers;
  the workspace tools are where side effects live.
- **Pattern matching in `when:`** (regex, ranges, `any`). Equality on a subset of the
  arguments covers the issue's cases; a richer matcher is a separate design if one is ever
  asked for.
- **Product runners.** They refuse `tools:` entirely (the product's own tools are the
  environment), so nothing here reaches them.

## 2. Decisions

| Decision | Why |
| --- | --- |
| The shape says which rule applies; a list mixing strings and mappings is refused | Two rules in one list would need a third rule to say which entry is which. `ToolSpec` refuses the mix in a `mode="before"` validator so the message is one sentence, not Pydantic's report of both union branches. |
| A sequence repeats its last entry rather than reporting exhaustion | The last entry is the steady state a skill that keeps calling should see (the item with no parent, the job that is done). A "no more entries" message would teach the model something about the harness; `trajectory.max_calls` is the check for a loop that should have stopped, and the issue asked for exactly this. |
| A lookup answers the first match, and an entry with no `when:` is the fallback | First-match is the rule authors already know from every router; a fallback spelled as "no condition" needs no new keyword. |
| A call no entry answers gets a fixed message, never an exception | A mock tool never raises (the invariant every adapter relies on). The unscripted argument is an eval signal: the transcript shows what the skill asked for. |
| An unreachable entry is an authoring error | An entry an earlier entry already answers is a check that could never fire — the vacuous case this project refuses everywhere. Detected with `ToolResponse.matches` itself, so "unreachable" means what the runtime would do. |
| A `when:` key the closed schema can never carry is an authoring error; an open schema may key on anything | The `parameters:` shorthand and an `input_schema` with `additionalProperties: false` describe every key a call can have. An open schema stands in for a server that accepts keys it does not list, and the author may know one. |
| `when:` matches by the `call_args.contains` rule, through one matcher | `trajectory.call_args` (#40) landed on `main` while this was in review with the same structural rule (subset at every level, bool only equals bool, `"1"` is not `1`). One function, `matching.structural_match`, now serves both, so an author learns one rule for naming arguments and the two cannot drift. |
| The sequence counter lives in the built tool, locked | Runners hold no mutable state touched by `run`. A closure per `build_mock_tool` means each arm, attempt and work item starts from the top; the lock is for a framework that runs one turn's parallel calls in threads. |
| Both adapters build the agent per attempt | The retry loop starts a fresh conversation; an agent reused across attempts would hand the retry's first call the sequence's second entry, and a transient 429 would silently change what the skill was shown. |
| The resolver copies a ref's raw `returns:`, not the validated model | The resolved mapping stays plain data for `EvalCase.model_validate`, and `_validate_tools` then checks a lookup's `when:` keys against the contract the library declared. |

## 3. File format

```yaml
tools:
  - name: get_work_item
    parameters:
      id: string
    returns:                                # by call order; the last entry repeats
      - '{"id": "A", "parent": "B"}'
      - '{"id": "B", "parent": null}'
  - name: lookup_order
    parameters:
      order_id: string
    returns:                                # by argument; first match wins
      - when: {order_id: "1234"}
        value: '{"id": "1234", "days_since_delivery": 45}'
      - when: {order_id: "5678"}
        value: '{"id": "5678", "days_since_delivery": 3}'
      - value: '{"error": "not found"}'     # no when: -- the fallback
```

Refused at load time (exit 2): `returns: []`; `returns: [a, {value: b}]`; a `when:` key
outside a closed schema; `when: {}`; an entry after a fallback, one repeating an earlier
`when:`, or one that only narrows an earlier `when:`.

## 4. `models.py`

- `ToolResponse` (`extra="forbid"`): `when: dict[str, Any] | None`, `value: str`
  (required), `matches(arguments) -> bool`.
- `ToolReturns = str | list[str] | list[ToolResponse]`; `ToolSpec.returns: ToolReturns =
  ""`; `ToolRef.returns: ToolReturns | None = None`; both run `_check_returns_shape` in a
  `mode="before"` validator (empty list, mixed list).
- `matches` delegates to `matching.structural_match(when, arguments, exact=False)` — the module `evaluators/trajectory.py` also uses for `call_args`; it imports nothing from the project.

## 5. `cases/checks.py`

`check_tool_returns(tool)` raises `ValueError` for the three lookup mistakes, naming
`returns[i]` (and `.when`); `_closed_keys(tool)` decides whether the schema is closed.
`check_tool(tool)` runs the schema check then this one; both loaders call it.

## 6. `runners/tools.py`

`_canned(spec)` returns the callable for the shape: `fixed`, `in_order` (locked counter,
`min(calls, len - 1)`), or `lookup` (first `matches`, else `NO_RESPONSE_SCRIPTED` with the
arguments as sorted JSON, `default=str`). `build_mock_tool` is otherwise unchanged.

## 7. Adapters

`PydanticAIRunner._run_with_retries` and `LangChainRunner._invoke` take a builder
(`Callable[[], Any]`) rather than an agent and call it inside the retried lambda. The
request bodies the cassettes replay are unchanged — the same tools are built, once per
attempt.

## 8. Documentation

`docs/eval-files.md` (a new "Answering differently per call" subsection under Mock tools),
`docs/runners.md` (the per-attempt rebuild and the parallel-call order), `ARCHITECTURE.md`
(a "Per-call mock returns" invariants subsection, the `ToolSpec` model row, the module map,
the "Adding a runner" extension text), `CLAUDE.md`, the shipped skill's
`eval-file-syntax.md` and `case-design.md`, and the sixth `order-support` example case
(the listing in `docs/cli.md` and `README.md` now shows `6 case(s)`).

## 9. Testing

Model shapes and `matches` (`tests/test_models.py`); the three callables, the last-entry
repeat, per-build counters, the lock under a thread pool, the no-match message
(`tests/test_tools.py`); every load-time refusal with its message (`tests/test_checks.py`);
YAML round trips, placeholder trails inside a list, ref overrides checked against the
library contract (`tests/test_case_loader.py`, `tests/test_tool_libraries.py`); the sequence
through each adapter and a retry that starts from the top
(`tests/test_pydantic_ai_runner.py`, `tests/test_langchain_runner.py`); the example case
(`tests/test_examples.py`).
