# skill-lens `trajectory.call_args` — Design

**Date:** 2026-09-21
**Status:** Approved (design), implemented in the same pull request
**Issue:** [#40](https://github.com/EmadMokhtar/skill-evaluator/issues/40) — trajectory
assertions can't check *what* a tool was called with, only *that* it was called

## 1. Scope

`trajectory:` scores which mocked tools ran (`called`, `forbidden`), in what order
(`order`) and how many times (`max_calls`), but says nothing about the arguments a call
carried. A skill told to fetch only unresolved review threads by passing `status: active`
to an MCP tool (Model Context Protocol — the JSON-RPC protocol an agent uses to discover and
call tools a separate server exposes) is indistinguishable, to `called:`, from one that
fetched everything and filtered afterwards, or never filtered at all. The output text and
an LLM judge see the difference only indirectly, and noisily.

This change ships as **one pull request** (`feat: assert on the arguments a tool was called
with`):

- **`trajectory.call_args:`**, a list. Each entry names one declared `tool` and one expected
  argument shape: `contains:` (a structural subset) or `equals:` (the whole argument
  dict). An optional `every: true` requires every call to that tool to match rather than
  at least one.
- **`CallArgsSpec`** in `models.py`, `extra="forbid"`, with a `model_validator` owning the
  shape rules; **`TrajectorySpec.call_args`** defaults to an empty list, so every existing
  eval file is unchanged.
- **One check per entry**, `call_args[{index}]`, with evidence — the same per-check model
  `called` / `forbidden` / `order` already use, so comparative runs pair the check across
  arms and low-signal detection covers it.
- **No runner changes.** PydanticAI, LangChain and both product trace parsers already fill
  `ToolCall.arguments`.

### Explicitly deferred

| Deferred | Why |
| --- | --- |
| `index:` — the *n*th call to a tool | Brittle: one retry shifts every index. "At least one" and "every" cover the motivating cases without naming positions. |
| List containment (`[bug]` matching `[bug, urgent]`) | One rule — same length, element by element — is easier to state and to read a failure against. Containment can be added as a further mode if a case needs it. |
| Regex or JSON Schema on arguments | A `json_schema:` entry is the natural next step; nothing in the issue needs it. |
| A negative form (`not_contains`) | `every: true` with the wanted arguments already refuses the unwanted call in every case raised; a negative check on an absent tool would also reopen the vacuous-pass question `forbidden` settled. |
| Matching arguments of the six built-in workspace and bundle tools by a shorthand | They are ordinary tools to `call_args`; the existing built-in-name rule (a `workspace:` block must exist) applies unchanged. |

## 2. Decisions

| Decision | Why |
| --- | --- |
| **`call_args` lives under `trajectory:`**, not as a sibling block. | It is a statement about the trajectory — what a call carried — and it shares the trajectory's declared-tool rule, evaluator, and per-check ids. |
| **Matching is structural, against the recorded argument dict, never against a serialised string.** | Key order, whitespace and quoting in a JSON rendering are not the model's choices. `_matches` walks the entry against the dict. |
| **`contains` is a subset at every level; lists match element by element at the same length; scalars must be equal.** | One rule that reads the same at every depth. `{query: {status: active}}` matches `{query: {status: active, limit: 10}}`; `[bug]` does not match `[bug, urgent]`. |
| **`equals` is the same comparison with exact key sets on mappings.** | The whole dict, so an extra key the author did not expect fails. `equals: {}` is "called with no arguments". |
| **Nothing is coerced, and a bool only ever equals a bool.** | `True == 1` in Python; an author who wrote `limit: 1` must not pass on a call that sent `true`. `"1"` never equals `1`. `yes` / `no` stay strings through `yaml_loading.safe_load`, as everywhere else. |
| **Default: at least one call matched. `every: true`: every call matched.** | The issue's own example needs `every`: a skill that fetched everything first and only then re-fetched with the filter passes "at least one" and fails "every". A tool the agent also calls for other things (`write_file`) needs the default. |
| **A tool that was never called fails, under `every` too.** | "Every call matched" over zero calls is a vacuous pass. |
| **Exactly one of `contains` / `equals`; `contains: {}` refused; `equals: {}` kept.** | Neither would pass any call — `called:` spelled longer. Both is two checks under one id. `{}` is a subset of every dict, so `contains: {}` could never fail. These are shape rules, so they are a `model_validator` on `CallArgsSpec`: an `EvalCase` built in code gets them too, and the loader's existing `ValidationError` wrapping names the file and case. |
| **`tool` must be declared in the case's `tools:`**, built-ins included where a `workspace:` block exists. | The same loop as `called` / `forbidden` / `order` in `cases/loader.py`, same message shape. A check on a tool the agent was never offered can never pass. |
| **Ids are positional, `call_args[{index}]`.** | Two entries may name one tool. Derived from the case, never the result, so both arms of a comparative run pair. |
| **Evidence renders the arguments and announces a cut.** | A pass names the matching call ("call 2 of 3 to X matched contains {…}"); a failure says "X was never called" or lists every call's arguments as sorted JSON, cut at `_ARGUMENTS_LIMIT` (800 characters) with the removed count stated. A `write_file` call can carry a document; the failure excerpt every reporter prints keeps the full calls. |
| **A `_raw` payload never matches.** | Runners keep an unparseable argument payload under `_raw`. It cannot satisfy a structural check, and the evidence shows it — a capture problem reads as a failed check with the payload visible, never as a pass. |

## 3. `models.py`

```python
class CallArgsSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    contains: dict[str, Any] | None = None
    equals: dict[str, Any] | None = None
    every: bool = False

    @model_validator(mode="after")
    def _one_subject(self) -> CallArgsSpec: ...   # exactly one; contains != {}


class TrajectorySpec(BaseModel):
    ...
    call_args: list[CallArgsSpec] = Field(default_factory=list)
```

## 4. `evaluators/trajectory.py`

- `_same_scalar(expected, actual)` — equality with the bool guard.
- `_matches(expected, actual, *, exact)` — the structural walk; `exact` is what `equals`
  adds.
- `_render(value)` — `json.dumps(sort_keys=True, ensure_ascii=False, default=str)`.
- `_cut(text)` — the announced truncation at `_ARGUMENTS_LIMIT`.
- `_call_args_check(index, entry, tool_calls) -> CheckResult` — never-called, then
  `every`, then at-least-one.
- `_checks` now takes the `ToolCall` list rather than the names, derives `called` from it,
  and appends one `call_args[{index}]` check per entry after the existing checks, so the
  order of ids in a report is stable: `called:*`, `forbidden:*`, `order`, `max_calls`,
  `skill_triggered`, `call_args[*]`.

## 5. `cases/loader.py`

One more pair in the declared-tool loop:

```python
("call_args", [entry.tool for entry in case.trajectory.call_args]),
```

The message reads `trajectory.call_args names 'x', which is not declared in this case's
tools.` with the built-in hint where it applies.

## 6. Documentation

| Page | Change |
| --- | --- |
| `docs/eval-files.md` | Field-table wording; a `trajectory.call_args` row in the per-check table. |
| `docs/runners.md` | `call_args` in the trajectory example; a "What a tool was called with" subsection stating the matching rules, `every`, the refusals and the evidence; the declared-name sentence extended. |
| `ARCHITECTURE.md` | Module-map row; the check-id list; a "Call arguments" invariants section. |
| `CLAUDE.md` | The "What this is" paragraph; two invariant bullets. |
| `skills/writing-skill-evals` | Syntax reference, "Choosing the check" row, the tool-using archetype, an auditing checklist item. |
| `examples/order-support` | The first case checks `lookup_order` was called with the customer's order id. |

## 7. Invariants this change adds

- **`call_args` matches structurally and coerces nothing.** Never a comparison of serialised
  strings; a bool only ever equals a bool; `"1"` never equals `1`; lists match at equal
  length, element by element; `contains` ignores unnamed keys, `equals` does not.
- **A tool that was never called fails `call_args`, under `every: true` too.**
- **One subject per entry**: exactly one of `contains` / `equals`; `contains: {}` refused;
  `equals: {}` kept. Enforced on the model, so a programmatic case gets it.
- **`tool` must be declared** — the loader's rule, beside `called` / `forbidden` / `order`.
- **Ids are positional `call_args[{index}]`**, evidence renders the arguments seen, and a
  cut is announced.
- **No runner changed**, and a `_raw` payload never matches.

## 8. Testing

All offline, `FakeRunner` tier:

- `tests/test_models.py` — the field and its defaults; unknown keys; neither / both /
  empty `contains` refused; `equals: {}` allowed.
- `tests/test_trajectory_evaluator.py` — at-least-one and `every`; never called under both;
  nested subset; missing nested key; list length and element matching; bool-vs-number and
  string-vs-number strictness; `equals` exactness; `equals: {}`; positional ids and the
  score fraction; id order after the other checks; `_raw`; the announced cut.
- `tests/test_case_loader.py` — undeclared `tool`; parses as written with `yes` kept a
  string; the model refusals name the file; built-ins only with a workspace.
- `tests/test_orchestrator.py` — the check reaches the report; wrong arguments are `failed`,
  never `errored`.
- `tests/test_examples.py` keeps loading the example; `tests/test_docs.py` and
  `mkdocs build --strict` keep the docs honest.

## 9. Release shape

One `feat:` commit → a minor bump. Additive: no existing eval file, report field or exit
code changes.
