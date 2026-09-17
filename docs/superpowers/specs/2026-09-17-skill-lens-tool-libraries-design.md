# skill-lens tool libraries — Design

**Date:** 2026-09-17
**Status:** Approved (design), pending implementation plan
**Issue:** [#42](https://github.com/EmadMokhtar/skill-evaluator/issues/42) — identical mock tool
definitions must be hand-copied into every skill's eval file

## 1. Scope

Several skills in one repository can front the same API surface — one MCP server (Model
Context Protocol, the JSON-RPC protocol an agent uses to discover and call tools a separate
server exposes) behind nine skills, each with its own eval file. Every one of those files
that mocks `get_pull_request` transcribes the same `name` / `description` / `parameters` /
`returns` block, because `skill-lens` loads each eval file on its own and a YAML anchor
cannot cross a file boundary. A fix to one copy — a `returns` shape corrected after the real
API turned out to answer differently — has no way to reach the others.

This change ships as **one pull request** (`feat: share mock tools across eval files
through tool libraries`):

- **A tool library** is a YAML file with one top-level `tools:` list — the same shape a
  case's `tools:` block has, and exactly what `skill-lens mcp-import` prints. It lives
  wherever the repository keeps it (`shared-tools/azure-devops-mcp.yaml`); skill-lens
  never discovers one by convention.
- **`tool_libraries:`**, a new top-level key in an eval file beside `cases:`, lists the
  libraries the file imports. Each entry is a path relative to the eval file — a library
  file, or a directory whose `.yaml` / `.yml` files are all imported.
- **`- ref: <name>`** in a case's `tools:` list pulls that tool from the imported
  libraries. It may carry a `returns:` of its own and nothing else: the library owns the
  tool's contract (name, description, schema), the case owns the scenario (what the call
  returns in this case).
- **`EvalCase.tools` still holds only `ToolSpec`.** The case loader resolves every `ref:`
  before the case is validated, so no runner, evaluator, reporter or product preflight ever
  sees a reference.

### Explicitly deferred

| Deferred | Why |
| --- | --- |
| Discovering a `tools/` or `shared-tools/` directory by convention (walking up from the skill, or beside the run's root path) | An upward walk is a hidden input to the run — the same reason `--case` has no config key. The run root is "a skill directory, or a directory of skills", so a fixed directory beside it has no stable meaning for a single-skill run. One explicit line per eval file names where its tools come from, works under `--evals`, under `skill-lens list` (which loads no config) and from any working directory. |
| A `tool_libraries` key in `skill-lens.toml` | Config is discovered upward from the working directory, not from the skill path; a run from the wrong directory would turn every `ref:` into an authoring error that blames the eval file. `list` would also have to grow config loading. A per-file import has no such failure mode, and one mechanism is enough. |
| Overriding `description`, `parameters` or `input_schema` on a `ref:` | Those are the server's contract; letting a case rewrite them reintroduces exactly the drift the library exists to remove. A case that needs a different contract declares the tool inline. |
| Glob patterns in `tool_libraries:` (`shared-tools/*.yaml`) | A directory entry imports every YAML file in it, which is what the glob would do. |
| Rejecting unknown top-level keys in an eval file | Documented today as ignored; a repository may carry a top-level `description:`. A misspelled `tool_libraries:` is not silent — the first `ref:` fails naming the missing key — so the strictness gains little for a compatibility risk. |
| A `mcp-import --library PATH` flag or an `init` scaffold for a library | `skill-lens mcp-import tools.json > shared-tools/server.yaml` already writes a library, because the import's output *is* the library format. |
| A `ref:` in a library file (a library importing another) | Two levels is a dependency graph with cycles to detect, for no case anyone has. |

## 2. Decisions

| Decision | Why |
| --- | --- |
| **The library format is a top-level `tools:` list and nothing else.** | It is the block an author already knows how to write, and the block `mcp-import` already prints — `skill-lens mcp-import tools.json > shared-tools/server.yaml` makes a library with no new command. A second top-level key is refused: a new file kind has no compatibility to keep. |
| **`tool_libraries:` is per eval file, relative to that file, never absolute.** | The eval file is the unit skill-lens loads, and its own location is the one path it can rely on: the same file loads identically under discovery, under `--evals`, under `list`, and from any working directory. An absolute path breaks on the next checkout, so it is an authoring error. `..` is allowed — the library sits above the skill by design — so `check_relative_path` (which forbids `..` for workspace containment) is not the check here. |
| **A directory entry imports every `.yaml` / `.yml` file in it, sorted, non-recursive.** | Mirrors `evals/`. An empty directory, or a path that does not exist, is an authoring error: nothing was imported and the author meant something to be. |
| **`ref:` resolves in the loader, before `EvalCase.model_validate`.** | `ToolSpec` has `extra="forbid"` and a required `name`; a `{ref: ...}` entry is not a `ToolSpec` and must never become one half-built. Resolving on the raw mapping keeps `EvalCase.tools: list[ToolSpec]` true everywhere downstream — every runner, both product presets' `tools:` refusal, the built-in-name and offered-skill collision checks, and the trajectory checks all run on resolved tools unchanged. |
| **A `ref:` may carry `returns:` and nothing else.** | Contract versus scenario. The order-support example shows why the override exists: one `lookup_order` contract, one case where the order was delivered 45 days ago and one where it was 3. Anything else beside `ref:` is refused naming the offending key. |
| **`ToolRef` lives in `models.py`, and `EvalCase` never holds one.** | `models.py` holds every data shape; `extra="forbid"` on the model is what refuses `{ref: x, description: y}`. Its docstring says the loader consumes it. |
| **The library's tools are checked exactly as inline tools are**: the scaffold sentinel, `ToolSpec` validation (the name rule, unknown keys), `parameters` / `input_schema` exclusivity, `check_schema`, top-level `type: object`. Each refusal names the library file and the tool's position. | A library is authoring input like an eval file. Checking at library load, not only after substitution, is what makes the message point at the file to fix. The substituted tool is checked again in the case's own pass — harmless, and it keeps the case loader's guarantees true for a programmatic caller. |
| **A name two imported files both declare is an error naming both files. So is one file declaring a name twice, and one file imported twice.** | Ambiguity is never resolved by position. A runner named twice is refused rather than de-duplicated for the same reason. |
| **An unresolvable `ref:` is `CaseParseError`, exit 2, before any case runs.** | No `tool_libraries:` at all, an unknown name (the message lists every name the imports declare, sorted), a missing or unreadable library — each is a mistake in the user's files, not a signal about the skill. |
| **The resolver never mutates the parsed YAML.** | A YAML anchor can alias one `tools:` list into several cases; substituting in place would resolve the shared list once and leave the aliases pointing at already-resolved entries — harmless today, a trap for the next change. The resolver returns a new case mapping with a new list. |
| **`ToolLibraryError` is raised by the library module; the case loader wraps it as `CaseParseError` prefixed with the eval file.** | The library module does not know which eval file imported it; the loader does. Every message therefore names both — the eval file to look at and the library file to fix — and `cli._AUTHORING_ERRORS` needs no new member. |
| **The sentinel walk and the tool-schema check move to `cases/checks.py`.** | Both now serve two file kinds. `cases/loader.py` imports the library module, so the library module cannot import the loader; a small shared module below both is the clean cut. `loader.py` keeps re-exporting `UNFILLED_SENTINEL` so `scaffold.py`, `cli.py`, `mcp_import.py` and the tests are untouched. |
| **The order-support example becomes the dogfood.** | Its four tool-bearing cases carry the same two contracts with three different `returns`. `examples/shared-tools/order-api.yaml` holds the contracts; the cases carry `ref:` plus their scenario. `skill-lens list ./examples` runs in CI as a self-check, and `tests/test_examples.py` pins that the example resolves. The cassette tests build their order-support cases programmatically, so no recording changes. |

## 3. File formats

**A tool library** (`examples/shared-tools/order-api.yaml`):

```yaml
# The order API two skills share. Each case supplies its own `returns:`.
tools:
  - name: lookup_order
    description: Look up an order by its id
    parameters:
      order_id: string
    returns: '{"id": "0000", "status": "delivered", "days_since_delivery": 0}'
  - name: issue_refund
    description: Issue a refund for an order
    parameters:
      order_id: string
    returns: '{"ok": true}'
```

Every entry is a `ToolSpec` — the same keys, the same rules — and `returns:` is the default
a case gets when it does not set one.

**An eval file importing it** (`examples/order-support/order-support.eval.yaml`):

```yaml
tool_libraries:
  - ../shared-tools/order-api.yaml      # relative to this file; a directory imports every YAML in it

cases:
  - name: refuses a refund outside the return window
    task: I want a refund for order 1234
    tools:
      - ref: lookup_order
        returns: '{"id": "1234", "status": "delivered", "days_since_delivery": 45}'
      - ref: issue_refund
    trajectory:
      called: [lookup_order]
      forbidden: [issue_refund]
```

A `ref:` and an inline tool may sit in the same list. After resolution the case is exactly
what it would have been had the author pasted the library's block, with `returns:` replaced
where the case set it — so `trajectory:`, the six reserved built-in names, and the
`mode: offered` collision rule all read the resolved name.

## 4. `models.py`

```python
class ToolRef(BaseModel):
    """A case's reference to a tool a library declares: `- ref: lookup_order`.

    The library owns the contract (name, description, schema); the case may
    set only `returns`, the scenario. The case loader resolves every ref into
    the `ToolSpec` it names before the case is validated, so `EvalCase.tools`
    never holds one and no runner ever sees one.
    """

    model_config = ConfigDict(extra="forbid")

    ref: str
    returns: str | None = None
```

`ToolSpec` and `EvalCase` are unchanged.

## 5. `cases/checks.py`

A new module holding what both file kinds check, importing only `models` and `jsonschema`:

```python
UNFILLED_SENTINEL = "TODO(skill-lens)"

def find_unfilled(raw: object, trail: str = "") -> str | None:
    """The trail ("tools[0].returns") of the first scaffold placeholder in `raw`,
    or None. Keys are walked as well as values; a self-referential anchor is
    skipped rather than recursed into forever."""

def check_tool_schema(tool: ToolSpec) -> None:
    """Raise ValueError unless the tool's declared schema is one a provider would
    register: never both `parameters` and `input_schema`; an `input_schema`
    that passes `Draft202012Validator.check_schema` and declares `type: object`."""
```

`find_unfilled` is today's `_reject_unfilled` walk with the raise taken out; the three
messages `check_tool_schema` raises are today's `_validate_tools` messages verbatim
("declares both parameters and input_schema; choose one.", "has an invalid input_schema:
{message}", "input_schema must declare type: object; a tool's arguments are always an
object."). The loader prefixes each with its `{path}: case {name!r} tool {name!r}`.

## 6. `cases/tool_libraries.py`

```python
class ToolLibraryError(Exception):
    """A library that cannot be imported: a missing file, a malformed one, a tool
    the loader would refuse inline, or a name declared twice. An authoring error;
    the case loader wraps it with the eval file that imported the library."""

@dataclass(frozen=True)
class ToolLibrary:
    specs: Mapping[str, ToolSpec]   # name -> the tool
    sources: Mapping[str, Path]     # name -> the file that declared it
    declared: bool                  # False when the eval file has no tool_libraries: key

    def resolve(self, ref: str) -> ToolSpec: ...

EMPTY_LIBRARY = ToolLibrary(specs={}, sources={}, declared=False)

def parse_tool_library(path: Path) -> list[ToolSpec]: ...
def load_tool_libraries(entries: Sequence[object], *, relative_to: Path) -> ToolLibrary: ...
```

**`parse_tool_library(path)`**, every refusal a `ToolLibraryError` naming `path`:

1. `read_text(encoding="utf-8")`; an `OSError` or `UnicodeDecodeError` → "cannot read".
2. `yaml_loading.safe_load`; a `YAMLError` → "invalid YAML in".
3. The document must be a mapping with a `tools` list. Any other top-level key is refused
   naming it: "expected only a top-level 'tools' list".
4. For each entry, in order: `find_unfilled(entry)` → "tool #N still has the scaffold
   placeholder TODO(skill-lens) at {trail}. Fill it in — an unfinished mock cannot stand in
   for anything." Then `ToolSpec.model_validate(entry)`; a `ValidationError` → "tool #N
   invalid ({fields}): {exc}". Then `check_tool_schema(spec)`; a `ValueError` → "tool #N
   {name!r} {message}".
5. A name already seen in this file → "declares tool {name!r} twice (tools #i and #j)".

**`load_tool_libraries(entries, relative_to)`**:

1. `entries` must be a list; each entry a non-empty string. Otherwise "tool_libraries must be
   a list of paths relative to the eval file" naming the position.
2. An entry that is absolute — `is_absolute()`, or has a `drive` or `root`, the three checks
   `check_relative_path` makes — is refused: "tool_libraries[N] {entry!r} is not a relative
   path; a library path is relative to the eval file so the suite loads from any checkout".
3. `(relative_to / entry).resolve()`. Missing → "tool_libraries[N] {entry!r} does not exist
   (looked at {resolved})". A directory expands to its sorted `.yaml` / `.yml` files;
   none → "names a directory with no YAML files in it". A file is used as written, whatever
   its suffix.
4. The same resolved file arriving twice — listed twice, or a directory plus a file inside
   it — is refused naming the entry and the file.
5. Each file is parsed; a name two files declare is refused naming both: "tool
   {name!r} is declared by both {a} and {b}; a ref can only mean one of them".
6. The result carries `declared=True` even when `entries` is empty: an empty
   `tool_libraries: []` and no key at all produce different messages on the first `ref:`.

**`ToolLibrary.resolve(ref)`** returns the spec, or raises `ToolLibraryError`: with
`declared=False`, "references tool {ref!r} but the file declares no tool_libraries:"; with
libraries and no match, "references tool {ref!r}, which no imported library declares; the
imports declare: a, b, c" (sorted; "nothing" when the imports are empty).

## 7. `cases/loader.py`

```python
TOOL_LIBRARIES_KEY = "tool_libraries"

def parse_cases_file(path, skill=None):
    ...
    data = safe_load(text) or {}
    if not isinstance(data, dict) or "cases" not in data: raise ...
    library = _load_tool_libraries(path, data)
    for index, raw in enumerate(raw_cases):
        _reject_unfilled(path, index, raw)
        raw = _resolve_tool_refs(path, index, raw, library)
        case = EvalCase.model_validate(raw)
        ... (the existing checks, unchanged)
```

- `_load_tool_libraries(path, data)`: `EMPTY_LIBRARY` when the key is absent; otherwise
  `load_tool_libraries(data[KEY], relative_to=path.parent)`, with a `ToolLibraryError`
  re-raised as `CaseParseError(f"{path}: {exc}")`.
- `_resolve_tool_refs(path, index, raw, library)`: returns `raw` untouched unless it is a
  mapping whose `tools` is a list. For each entry that is a mapping with a `ref` key:
  `ToolRef.model_validate(entry)`; a `ValidationError` → `CaseParseError` "{path}: case #N
  tool #M: a ref: entry may carry only returns: beside ref:; the library declares the
  tool's name, description and schema. Got: {fields}". Then `library.resolve(ref)`; a
  `ToolLibraryError` → `CaseParseError(f"{path}: case #N tool #M {exc}")`. The replacement is
  `copy.deepcopy(spec.model_dump())` with `returns` overwritten when the ref set it. Every
  other entry is kept as written. The function returns `{**raw, "tools": new_list}`.
- `_reject_unfilled` becomes a call to `find_unfilled` plus today's message; the sentinel in
  a ref's own `returns:` is caught here, at `tools[M].returns`, before resolution.
- `_validate_tools` calls `check_tool_schema` and prefixes the message; the three tests that
  match on those messages still pass.
- `UNFILLED_SENTINEL` is imported from `checks.py` and stays importable from `loader.py`.

Nothing changes in `load_cases_for_skill`, `discover_eval_paths`, the orchestrator, the CLI,
`init` or `mcp-import`. `_validate_cross_references` already refuses the same name twice in
one case — which now also covers a `ref:` beside an inline tool of the same name, and the
same `ref:` twice.

## 8. `scaffold.py` and the example

The `tools:` comment in `_TEMPLATE` gains two lines:

```
  #    A tool several skills share can live once in a library file that each
  #    eval file imports with `tool_libraries:` and names with `- ref: <name>`.
```

`examples/shared-tools/order-api.yaml` is added as in §3, and
`examples/order-support/order-support.eval.yaml` imports it, each of the four tool-bearing
cases replacing its inline block with `ref:` entries and its own `returns:`. The resolved
cases are field-for-field what the file declares today. `load_skills` finds `SKILL.md` files
only, so the new directory is not a skill and `init` in batch mode does not touch it.

## 9. Documentation

| Page | Change |
| --- | --- |
| `docs/eval-files.md` | The opening paragraph names `tool_libraries:` as the one other top-level key. A "Sharing tools across eval files" subsection under "Mock tools": the library format, `tool_libraries:` (relative to the file; file or directory), `ref:` with `returns:` only, what is refused (absolute path, missing file, unknown name, a name two files declare, any key but `returns:` beside `ref:`), and that the resolved case is what an inline block would have been. "Where eval files are found" gains how library paths resolve under `--evals`. |
| `docs/cli.md` | The `mcp-import` section says the output is a library: `> shared-tools/server.yaml`, then `tool_libraries:` in each eval file. |
| `docs/runners.md` | "Declaring tools and scoring the trajectory": one sentence — a `ref:` resolves before the runner is involved, so every runner sees a `ToolSpec`. |
| `skills/writing-skill-evals/references/eval-file-syntax.md` | The same `tool_libraries:` / `ref:` reference for the bundled skill, in its "Tools" section, not in the case-fields table (which a test pins to `EvalCase`'s fields). |
| `ARCHITECTURE.md` | `cases/checks.py` and `cases/tool_libraries.py` in the module map; the data-flow line; a "Tool libraries" invariants section. |
| `CLAUDE.md` | The condensed invariants; one sentence in "What this is". |
| `README.md` | One sentence beside the `mcp-import` one: a library shares a mock across skills. |

`tests/test_docs.py` pins every `EvalCase` field to `docs/eval-files.md`; no field changes.
`tests/test_shipped_skill.py` pins the bundled reference's case-fields table to `EvalCase`;
`tool_libraries` is a file key, not a case field, and is documented outside that table.

## 10. Invariants this change adds

- **`EvalCase.tools` holds only `ToolSpec`.** A `ref:` is resolved by the case loader on the
  raw mapping, before validation; no runner, evaluator, reporter or product preflight ever
  sees a reference, and the product runners' `tools:` refusal applies to a referenced tool
  exactly as to an inline one.
- **A `ref:` may set `returns:` and nothing else.** The library owns the contract, the case
  owns the scenario. Any other key beside `ref:` is an authoring error naming it.
- **An unresolvable `ref:` is an authoring error at load time** (exit 2): no
  `tool_libraries:` key, an unknown name (the message lists the names the imports declare),
  a missing, unreadable or malformed library. Never an errored or failed case.
- **A name two imported libraries declare is refused naming both files** — never resolved by
  position. One file declaring a name twice, and one file imported twice, are refused too.
- **Library paths are relative to the eval file, never to the working directory and never
  absolute.** The same eval file resolves identically under discovery, `--evals`, `list`, and
  from any directory.
- **A library is checked as an eval file is**: the `TODO(skill-lens)` sentinel (keys and
  values), the tool-name rule, unknown keys, `parameters` / `input_schema` exclusivity and
  schema validity — each refusal naming the library file and the tool's position.
- **A library is a top-level `tools:` list and nothing else** — the block `mcp-import`
  prints, so `mcp-import tools.json > shared-tools/server.yaml` is the whole import step.
- **The resolver never mutates parsed YAML.** An anchored `tools:` list shared by two cases
  resolves in both.

## 11. Testing

All offline, all deterministic, all in the zero-cost tier.

**`tests/test_tool_libraries.py`** — the library module:

- a well-formed library parses to `ToolSpec`s in file order, `returns` included;
- a missing file, invalid YAML, a non-mapping document, a mapping without `tools`, a `tools`
  that is not a list, and a second top-level key each refuse naming the file;
- a tool carrying the sentinel refuses naming the tool's position and the trail — in a
  value and in a mapping key;
- an entry failing `ToolSpec` (a bad name, an unknown key), `parameters` plus
  `input_schema`, a non-object `input_schema`, a malformed one — each refuses naming the
  tool's position;
- a name declared twice in one file refuses naming both positions;
- `load_tool_libraries`: a file entry; a directory entry imports its `.yaml` and `.yml`
  files sorted and ignores other suffixes; an empty directory, a missing path, an absolute
  path (POSIX and a drive-relative spelling), a non-list, a non-string entry each refuse
  naming the position; the same file listed twice, and a directory plus a file in it, refuse;
  a name two files declare refuses naming both files;
- `resolve`: `declared=False` says the file declares no `tool_libraries:`; an unknown name
  lists the declared names sorted; an empty import set says "nothing".

**`tests/test_case_loader.py`**:

- a `ref:` resolves to the library's tool, `returns` included; `returns:` on the ref replaces
  the library's; a ref beside an inline tool loads; the loaded case's tools are all
  `ToolSpec`;
- a ref carrying `description:` (or `name:`) refuses naming the key; a `ref:` with a
  sentinel `returns:` refuses at `tools[0].returns` before any library is consulted;
- an unknown ref with no `tool_libraries:` key, and with a key that does not declare it,
  refuse with the two messages, the eval file named in both;
- a library path resolves relative to the eval file, not the working directory: the eval
  file sits in `skills/a/evals/`, the library in `shared-tools/`, the entry is
  `../../shared-tools/x.yaml`, and the test's working directory is elsewhere (`conftest`
  already moves it); the same holds under `load_cases_for_skill(..., evals_path=...)`;
- a library error surfaces as `CaseParseError` naming the eval file and the library file;
- a ref twice in one case, and a ref beside an inline tool of the same name, hit the
  existing duplicate check; a ref'd tool named `read_file` in a workspace case hits the
  built-in collision; a ref'd tool satisfies `trajectory.called`; `mode: offered` refuses a
  ref'd tool colliding with the offered-skill name;
- two cases sharing one anchored `tools:` list both resolve;
- `_validate_tools`' three messages are unchanged (the existing tests).

**`tests/test_models.py`**: `ToolRef` refuses an extra key and requires `ref`.

**`tests/test_mcp_import.py`**: the import's rendered output, sentinels filled, written as a
library file and imported by an eval file through `ref:`, loads, and
`build_mock_tool(case.tools[0]).json_schema` equals the listing's `inputSchema`.

**`tests/test_examples.py`**: the order-support example resolves to four cases carrying
`lookup_order` / `issue_refund` `ToolSpec`s, with the per-case `returns` the file sets.

**`tests/test_scaffold.py`**: the rendered scaffold mentions `tool_libraries`.

**`tests/test_product_preflight.py`** (one case): a case whose only tool is a `ref:` is
refused under a product runner with the existing `tools:` message.

## 12. Release shape

One PR, `feat: share mock tools across eval files through tool libraries`, `Closes #42`. A
minor bump under `cz bump`. Not a breaking change: an eval file without `tool_libraries:`
loads byte-identically, and a case's `tools:` list that never uses `ref:` is untouched.
