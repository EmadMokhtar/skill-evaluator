# skill-lens `mcp-import` — Design

**Date:** 2026-09-17
**Status:** Approved (design), pending implementation plan
**Issue:** [#43](https://github.com/EmadMokhtar/skill-evaluator/issues/43) — mock tools must
be hand-transcribed; no import from a live/cached MCP server's `tools/list`

## 1. Scope

A skill that calls a real MCP server (Model Context Protocol — the JSON-RPC protocol an
agent uses to discover and call tools a separate server exposes) is evaluated today
against a mock the author transcribed by hand from the skill's prose: `name`,
`description` and a flat `parameters:` map. Two things go wrong. The mock can drift from
what the server actually declares, so a green run says nothing about the live server; and
the flat `parameters:` map cannot express what a real `inputSchema` (the JSON Schema each
MCP tool declares for its arguments) routinely does — optional arguments, enums, arrays,
nested objects.

This change ships as **one pull request** (`feat: import mock tools from an MCP server's
tools/list listing`):

- **`skill-lens mcp-import SOURCE [--tool NAME]...`** — reads a saved `tools/list` response
  (a JSON file, or `-` for stdin) and prints a `tools:` YAML block ready to paste under a
  case. Offline only; nothing here touches the network.
- **`ToolSpec.input_schema`** — a new, optional field holding a JSON Schema object that
  reaches the agent verbatim, so a mock can carry the server's real schema. It is exclusive
  with the existing `parameters:` shorthand, which is unchanged.
- **`ToolSpec.name` accepts what providers accept** — `^[A-Za-z0-9_-]{1,64}$`, the rule
  both OpenAI and Anthropic enforce on tool names — in place of Python's `isidentifier()`.
  MCP tool names are routinely hyphenated (`get-pull-request`), and an imported mock must
  keep the name the live server answers to.

### Explicitly deferred

| Deferred | Why |
| --- | --- |
| Connecting to a live MCP server (`--server`) | Needs the `mcp` client SDK, a transport (stdio command or HTTP URL), auth headers, process spawning, and a network-bearing CLI command that must fail closed — roughly three times the work and a new attack surface for a tool that already fences scripts behind `--allow-scripts`. The issue itself says the offline variant "would remove most of the manual-transcription risk". The command's `SOURCE` argument is the seam a later `--server` plugs into. |
| An `init`-time option | The output is pasteable; `init` gets one comment line pointing at `mcp-import`. A second scaffold shape is two templates to keep in step. |
| Inventing a `returns:` value from `outputSchema` | A generated example would be exactly the silent drift the issue complains about. The output schema is rendered as a comment above `returns:` so the author shapes the value against it. |
| Rewriting a tool name that fails the provider rule | The mock's job is to answer to the name the skill's prose and the live server use. A rewritten name would let the harness manufacture a failure. A name outside the rule is an import error naming the tool. |
| A `--output PATH` flag | Stdout redirection is what the issue asks for and is enough. |
| Checking an imported name against the six reserved built-in tool names | Whether `read_file` collides depends on the case it is pasted into (a `workspace:` block), which the import cannot see. The case loader already makes that check in context. |

## 2. Decisions

| Decision | Why |
| --- | --- |
| **Offline only.** `SOURCE` is a file or `-`. | Zero new dependencies; a pure function over text; every test runs with no network. Capturing the JSON is a one-off with any MCP client (`npx @modelcontextprotocol/inspector`, Claude Code's `/mcp` panel). |
| **Three accepted shapes**: the JSON-RPC envelope (`{"jsonrpc": ..., "result": {"tools": [...]}}`), the bare result (`{"tools": [...]}`), the bare array (`[...]`). | These are the three things people actually save. Anything else — including an envelope carrying an `error` member — is exit 2 with a message naming the accepted shapes. |
| **`input_schema` is the server's `inputSchema` verbatim.** No `additionalProperties: false` injected, nothing reordered, nothing dropped. | Fidelity to the live server is the whole point. `build_mock_tool` injects `additionalProperties: false` only for the `parameters:` shorthand, where the author wrote every key. |
| **`returns:` is always the `TODO(skill-lens)` sentinel.** | The listing says nothing about what a call returns. The sentinel makes the loader refuse the file until the author fills it (the existing unfilled-scaffold rule), so an imported block can never run half-written. |
| **A missing or empty `description` becomes the sentinel too.** | A mock with no description gives the model nothing to choose it by. Same rule, same reason. |
| **A missing or non-object `inputSchema` is exit 2 naming the tool.** | The MCP specification requires it; a listing without one is not a `tools/list` response, and guessing `{"type": "object"}` would hide a malformed capture. |
| **`--tool` filters by exact name; an unknown name is exit 2 listing the names found.** | The author's next step is to pick from that list. |
| **Rendering goes through `yaml.safe_dump(sort_keys=False, allow_unicode=True)` per tool**, with the header and the output-schema comment written as plain lines. | PyYAML quotes any scalar that would resolve to another type (`yes`, `on`, `1.20`), so the output survives `yaml_loading.safe_load` and the strict-bool loader. Declaration order is preserved. Output is deterministic and a test pins it. |
| **`parameters:` and `input_schema:` are exclusive**, checked in the loader with a message naming file, case and tool. | Two schemas for one tool is a contradiction, not a merge. |
| **`input_schema` must pass `Draft202012Validator.check_schema` and declare `type: object`**, checked in the loader beside the existing `json_schema` assertion check. | An unknown assertion kind is caught at load time before any money is spent; a malformed tool schema gets the same treatment. Every provider requires a tool schema to be an object. |
| **The name rule is `^[A-Za-z0-9_-]{1,64}$`**, in the model, replacing `isidentifier()`. | The rule both providers enforce. Every ASCII identifier up to 64 characters still passes. A non-ASCII or over-long name now fails at load time; it never worked against a real provider, and only a `FakeRunner`-only suite could have carried one. `skill_tool_name` (the offered-skill tool) is unchanged. |
| **`ImportedTool` is a frozen dataclass holding a real `ToolSpec`** plus the optional output schema. | The name rule and the `extra="forbid"` config fire at import time through the model itself, so the import and the loader can never disagree about what a valid mock is. `models.py` stays the home of every Pydantic model; `runners/tools.py` already sets the precedent for a plain dataclass beside one. |
| **`McpImportError` is a user error, exit 2.** | The CI contract: gate pass 0, gate fail 1, user/authoring error 2. It is not added to `cli._AUTHORING_ERRORS`, which belongs to `run`. |
| **A listing carrying a non-empty `nextCursor` is refused.** | It is one page of several; importing it would be a silent cut, and `--tool` would list only that page's names. |

## 3. `mcp_import.py`

A new module, `src/skill_lens/mcp_import.py`, with no IO. Two public functions and one
exception:

```python
class McpImportError(ValueError):
    """A listing that cannot be imported: bad JSON, an unknown shape, a tool the
    provider would refuse. A user error, so the CLI exits 2."""

@dataclass(frozen=True)
class ImportedTool:
    spec: ToolSpec                       # name, description, input_schema, returns=sentinel
    output_schema: dict[str, Any] | None # the server's outputSchema, when declared

def parse_tools_list(text: str, *, source: str) -> list[ImportedTool]: ...
def render_tool_mocks(tools: list[ImportedTool], *, only: Sequence[str] = ()) -> str: ...
```

**`parse_tools_list`.**

1. `json.loads(text)`; a `JSONDecodeError` becomes `McpImportError` naming `source` and
   the decoder's position message.
2. Unwrap the shape. A dict with an `error` member is refused, quoting the error's
   `message`. A dict with `result` unwraps to it; a dict with `tools` uses that list; a
   list is used as-is. Anything else — including a `tools` value that is not a list — is
   refused with a message naming the three accepted shapes.
3. For each entry, in listing order: it must be a dict with a string `name`; `inputSchema`
   must be present and a dict — otherwise refuse, naming the tool (or its index when it
   has no usable name). `description` is used when it is a non-empty string, otherwise the
   sentinel. `outputSchema` is kept when it is a dict, ignored otherwise. Every other key
   (`title`, `annotations`, `_meta`) is ignored.
4. Build `ToolSpec(name=..., description=..., input_schema=..., returns=<sentinel>)`. A
   Pydantic `ValidationError` — in practice the name rule — becomes `McpImportError`
   naming the tool and the rule.
5. The `inputSchema` dict is deep-copied into the spec so the parsed JSON and the spec
   share no state.

The sentinel is `cases.loader.UNFILLED_SENTINEL` (`TODO(skill-lens)`), followed by a short
hint as in `scaffold.py`: `TODO(skill-lens) what this tool does`, `TODO(skill-lens) the
JSON this tool returns`.

**`render_tool_mocks`.**

- `only` non-empty: keep the listed names, in the order the listing had them. A name in
  `only` that no tool carries is `McpImportError` listing every name the listing does
  carry, sorted, one per line.
- Output, exactly:

  ```yaml
  # Written by `skill-lens mcp-import`. Replace every TODO(skill-lens); until you do,
  # skill-lens refuses to run the file rather than pass a case that checks nothing.
  tools:
    - name: get_pull_request
      description: Get details of a specific pull request
      input_schema:
        type: object
        properties:
          owner:
            type: string
            description: Repository owner
          pull_number:
            type: integer
        required:
        - owner
        - pull_number
      # The server declares this output schema; shape `returns` to match it:
      #   {"type": "object", "properties": {"number": {"type": "integer"}}}
      returns: TODO(skill-lens) the JSON this tool returns
  ```

  Each tool is `yaml.safe_dump([spec.model_dump(exclude_none=True, exclude_defaults=True)],
  sort_keys=False, allow_unicode=True, default_flow_style=False)` with `name` first,
  indented two spaces under `tools:`. The output-schema comment is inserted before the
  `returns:` line; the output schema itself is `json.dumps(..., sort_keys=True)` on one
  line, so the comment is a single grep-able line however large the schema is. When no
  output schema was declared, no comment is written.
- An empty listing renders the header and `tools: []`.

Everything is deterministic: same text in, same text out.

## 4. `cli.py`

```python
@app.command("mcp-import")
def mcp_import(
    source: Annotated[Path, typer.Argument(help="A saved tools/list JSON response, or - for stdin.")],
    tool: Annotated[list[str] | None, typer.Option("--tool", help="Import only this tool; repeatable.")] = None,
) -> None:
    """Print mock-tool YAML for the tools an MCP server's tools/list listing declares."""
```

- `source == Path("-")` reads `sys.stdin`; otherwise `source.read_text(encoding="utf-8")`.
  An `OSError` or `UnicodeDecodeError` is exit 2 naming the file.
- `parse_tools_list` then `render_tool_mocks`; `McpImportError` is exit 2 with its message.
- `typer.echo(text, nl=False)` — the renderer already ends with a newline; nothing else is
  printed to stdout, so `> tools.yaml` captures exactly the block.
- Exit 0 otherwise.

`scaffold.py` gains one comment line in the tools example of `_TEMPLATE`:

```
  #    For a tool a real MCP server exposes, `skill-lens mcp-import tools.json`
  #    writes this block from the server's own schema.
```

## 5. `models.py` and `cases/loader.py`

**`ToolSpec`:**

```python
TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    parameters: dict[str, ToolParamType] = Field(default_factory=dict)
    input_schema: dict[str, Any] | None = None
    returns: str = ""

    @field_validator("name")
    @classmethod
    def _must_be_a_provider_tool_name(cls, value: str) -> str:
        if not TOOL_NAME_PATTERN.fullmatch(value):
            raise ValueError(
                f"tool name must match {TOOL_NAME_PATTERN.pattern} (what providers "
                f"accept), got {value!r}"
            )
        return value
```

`input_schema` is a mapping so an eval file's YAML lands in it directly. The docstring
says why it exists: a mock that must match a real server's declared schema, which the
flat `parameters:` map cannot express.

**Loader checks**, in the per-case validation pass beside the assertion `json_schema`
check, each a `CaseParseError` naming `{path}`, the case and the tool:

- `parameters` and `input_schema` both set → "declares both parameters and input_schema;
  choose one".
- `input_schema` fails `Draft202012Validator.check_schema` → "has an invalid input_schema:
  {exc.message}".
- `input_schema.get("type") != "object"` → "input_schema must declare type: object; a
  tool's arguments are always an object".

The existing checks — reserved built-in names, collision with the offered-skill tool,
trajectory names declared in `tools:` — are untouched and apply to an imported mock like
any other.

## 6. `runners/tools.py`

```python
def build_mock_tool(spec: ToolSpec) -> AgentTool:
    if spec.input_schema is not None:
        json_schema = copy.deepcopy(spec.input_schema)
    else:
        properties = {name: {"type": type_name} for name, type_name in spec.parameters.items()}
        json_schema = {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }
    ...
```

The deep copy keeps an adapter from mutating the case. The docstring records the
asymmetry: the shorthand is closed (`additionalProperties: false`) because the author
wrote every key; a declared schema is passed verbatim because fidelity to the server is
its reason to exist. Neither adapter changes — both already hand `json_schema` to the
framework untouched (`Tool.from_schema(json_schema=...)`, `StructuredTool.from_function(
args_schema=...)`), and `call` still accepts any arguments.

## 7. Documentation

| Page | Change |
| --- | --- |
| `docs/cli.md` | A `mcp-import` section: the three accepted shapes, `--tool`, stdin, what is copied and what is a sentinel, exit codes, how to capture a listing. |
| `docs/eval-files.md` | The `tools:` row links to the new material; the name rule; `input_schema` beside `parameters`, exclusive, verbatim, and where `mcp-import` writes it. |
| `docs/runners.md` | The "Declaring tools" section shows an `input_schema` mock and states the `additionalProperties` asymmetry. |
| `skills/writing-skill-evals/references/eval-file-syntax.md` | The same `tools:` reference the bundled skill hands to an agent. |
| `ARCHITECTURE.md` | `mcp_import.py` in the module map; two invariants (§8). |
| `CLAUDE.md` | The condensed form of the same two invariants; the `mcp-import` line in the "What this is" paragraph. |
| `README.md` | Landing page only: one sentence in "A case is a few lines of YAML" pointing at `mcp-import`. Reference prose stays in `docs/`. |

`tests/test_docs.py` already requires every command and every `--flag` to appear in
`docs/cli.md`; the new command and `--tool` fall under it without a new test.

## 8. Invariants this change adds

- **An imported mock is the server's schema verbatim, and `returns` is never invented.**
  `mcp-import` copies `name` and `inputSchema` byte-for-byte and writes the
  `TODO(skill-lens)` sentinel for `returns` (and for a missing `description`), so an
  imported block cannot run until the author has said what the tool returns. Inventing a
  value would be exactly the silent drift the import exists to remove.
- **`parameters` and `input_schema` are exclusive, and `input_schema` is validated at load
  time.** Both set, a malformed schema, or a top-level type other than `object` is an
  authoring error (exit 2) before any case runs.
- **The shorthand is closed; a declared schema is open.** `build_mock_tool` adds
  `additionalProperties: false` only when it derived the schema from `parameters:`. A
  verbatim schema is passed as declared.
- **A tool name is what providers accept**: `^[A-Za-z0-9_-]{1,64}$`. Import keeps the
  name; a name outside the rule is an import error, never a rewrite.
- **`mcp-import` never touches the network.** `SOURCE` is a file or stdin.

## 9. Testing

All offline, all deterministic, all in the zero-cost tier.

**`tests/test_mcp_import.py`** — the pure module:

- the envelope, the bare result and the bare array parse to the same tools;
- an envelope with `error` refuses, quoting the message; a dict without `result` or
  `tools`, a string, a number, a `tools` that is not a list each refuse naming the shapes;
- invalid JSON refuses naming the source;
- a tool without `inputSchema`, with a non-dict `inputSchema`, without a string `name`,
  refuses naming the tool or its index;
- `get-pull-request` is kept verbatim; `a.b` and a 65-character name refuse naming the
  rule;
- a missing, empty or non-string description becomes the sentinel; a present one is
  copied;
- `returns` is the sentinel on every tool;
- `outputSchema` renders as a single comment line above `returns:`; absent, no comment;
- the rendered `input_schema` block round-trips through `yaml_loading.safe_load` to a
  dict equal to the input, with a nested object, an array, an `enum` and a `required` list;
- `only=` filters and preserves listing order; an unknown name refuses listing the names
  found;
- a description of `yes` and a property named `on` survive the strict-bool loader;
- the exact output for one fixture is pinned;
- **round trip**: the rendered YAML, sentinels replaced, written beside a `SKILL.md`,
  loads through `load_cases_for_skill`, and `build_mock_tool(case.tools[0]).json_schema`
  equals the fixture's `inputSchema`.

**`tests/test_cli_mcp_import.py`** via `CliRunner` (beside `test_cli_init.py`):

- a file argument prints the block and exits 0; `-` reads stdin;
- a missing file, invalid JSON, an unknown `--tool` each exit 2 with the message on
  **stderr** and nothing on stdout; the success path writes nothing to stderr.

**`tests/test_case_loader.py`** and **`tests/test_models.py`**:

- `parameters` plus `input_schema` is a `CaseParseError` naming the tool;
- a non-object `input_schema` and a malformed one (`{"type": 5}`) are `CaseParseError`s;
- an `input_schema` mock is accepted and its trajectory names resolve as before;
- `get-pull-request` is a valid `ToolSpec.name`; `a.b`, `café` and a 65-character name
  are not.

**`tests/test_tools.py`**: `build_mock_tool` returns a deep copy of `input_schema` when
set, with no `additionalProperties` injected, and the derived closed schema otherwise;
the mock still accepts any arguments.

**`tests/test_scaffold.py`**: the rendered scaffold mentions `mcp-import`.

## 10. Release shape

One PR, `feat: import mock tools from an MCP server's tools/list listing`, `Closes #43`.
A minor bump under `cz bump`. Not a breaking change: every eval file that loaded before
still loads unless it named a tool no provider would register.
