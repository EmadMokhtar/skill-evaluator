# skill-lens MCP bridge — Design

**Date:** 2026-09-21
**Status:** Approved (design), implemented in the same pull request
**Issue:** [#53](https://github.com/EmadMokhtar/skill-evaluator/issues/53) — `tools:` is a
preflight authoring error under every product runner

## 1. Scope

The M9 design deferred one thing a product runner could not serve: a case's `tools:` block.
The framework adapters register a mock tool as a callable inside the agent loop they drive;
a product owns its loop, and skill-lens cannot reach into it. The M9 decision was to refuse
`tools:` in preflight rather than run a case without its tools — a `trajectory: called:`
would then fail for a reason that says nothing about the skill, and `forbidden:` would pass
vacuously. Until this change, a suite that mocks a real MCP server's tools (imported with
`mcp-import`, shared through `tool_libraries:`) had to run those cases through a framework
runner and a provider key or a self-hosted model, even in a repository that has only a
Copilot or Claude Code seat.

This change ships as **one pull request** (`feat: serve mock tools to product runners
through a stdio MCP server`):

- **`mcp_bridge.py`**, a stdio MCP server in the package: `python -m skill_lens.mcp_bridge
  <spec>` reads a JSON spec listing the case's tools and answers `initialize`, `ping`,
  `tools/list` and `tools/call` over JSON-RPC 2.0, one message per line. A call returns
  `returns` by the rules `runners/tools.py` applies — one value; a sequence in call order;
  a `when:` lookup through `matching.structural_match`, the no-match wording carried in the
  spec — whatever the arguments, exactly what a mock does under every other runner. Every list and call is appended to a record file the spec names. `--check` drives
  the handlers in-process and exits.
- **`runners/mcp.py`**, the runner's side: `McpSupport` (each product's config flag, tool
  spelling, config-entry keys and hiding flags), `write_bridge` (the spec and the product's
  config in a fresh directory), `Bridge.connected` (the record's `list` event),
  `restore_tool_names` (the product's spelling back to the case's) and `probe_bridge` (the
  once-per-run `--check`).
- **`Product.mcp`**, set on the two presets (`COPILOT_MCP`, `CLAUDE_CODE_MCP`) and `None`
  for `cli` and for a preset whose `command` names another executable. `ProductRunner.run`
  writes the bridge when the case declares `tools:`, appends the config flag last, checks
  the connection afterwards and maps the names back; `preflight` refuses `tools:` where
  `mcp` is `None`, refuses a hiding flag in the table, and probes the bridge once.
- **No new dependency, no new flag, no new config key.** Four JSON-RPC methods do not need
  an SDK; a mock adds no capability the product lacked; the bridge's argv element is
  derived from the preset.

### Explicitly deferred

| Deferred | Why |
| --- | --- |
| `tools:` under `cli` | The generic product has no known flag that takes an MCP config, and no parser that would record the calls. A `[runners.cli] mcp_config` template is a design of its own; the issue names the two presets. `tools:` stays a preflight authoring error under `cli`, with a message pointing at the presets. |
| Keeping the bridge running across cases (one server per run) | The product starts its MCP servers itself, per session, from the config it is handed — there is no product-side handle to a server skill-lens already runs. One bridge per invocation is the only shape the products offer, and a Python start costs tens of milliseconds against a model round trip. It also gives every arm and every repetition its own record, which the connection check relies on. |
| Reading the trajectory from the bridge's record instead of the trace | The project's rule is that tool calls are what the model *requested*, read from the trace; the record is what executed. Keeping the trace as the source keeps the rule and the parsers unchanged. The record answers one question only: did the product connect. |
| Suppressing Claude Code's `ToolSearch` call for a deferred MCP schema | It is the product's own loading mechanic, which is what a product runner exists to measure. It counts toward `max_calls`; the docs say so. |
| Hiding the user's own MCP servers under Copilot | `--additional-mcp-config` augments `~/.copilot/mcp-config.json`; Copilot has no flag that drops user-configured servers wholesale. The M9 hermetic recipe (`COPILOT_HOME`) covers it, and the built-in GitHub server is the product as shipped, per the M9 decision. Claude Code's preset already carries `--strict-mcp-config`, so the bridge is its only MCP server. |
| A `ProductStatus` field saying the bridge served tools | The trust decision is unchanged and the sentence on the report with it; the docs describe the bridge. A report field that only ever said "yes" when a case declared `tools:` would repeat the eval file. |
| Refusing `--excluded-tools` naming a mock under Copilot | A repository excluding its own mock by name is not a trap anyone falls into by accident; `--available-tools` is, because it hides everything it does not name. |

## 2. Decisions

| Decision | Why |
| --- | --- |
| **The product starts the bridge; skill-lens only writes the config.** | That is how both products take an MCP server: a config naming a command. Starting the server ourselves would leave no way to tell the product about it. |
| **`sys.executable -P -m skill_lens.mcp_bridge`, and the module imports only `matching` from the project.** | The interpreter running skill-lens can import the package with no environment of its own, and the product inherits our environment anyway. The fewer things a child the product starts needs, the fewer ways that start can fail; `matching` imports nothing from the project itself, and a `when:` must match by the one rule every runner uses (#60). `-P` keeps the product's working directory — the case's workspace — off the child's import path. |
| **The config flag is one argv element with `=`, appended last.** | Claude Code's `--mcp-config <configs...>` is variadic; a two-element spelling after a table's `args` could swallow whatever came next, and nothing may come after the bridge. Verified: `--mcp-config=<file>` and `--additional-mcp-config=@<file>` (Copilot's `@` marks a file path) both work. |
| **The bridge's files live in a fresh directory of their own, never the working directory.** | The product can list its working directory, `list_files` under a workspace case lists it, and a `file-produced` assertion reads it. A config file there would be an input the case never declared. Deleted in the run's `finally`; `--keep-workspace` does not keep it — it is an input, not an output. |
| **The spec carries the schema `build_mock_tool` registers.** | One rule for every runner: a `parameters:` shorthand is closed, an `input_schema` is passed verbatim. The server does not rebuild anything. |
| **No opt-in flag.** | A mock returns canned text and executes nothing. `--allow-scripts` exists because a bundled script is unvetted code; a mock is a fixed answer the author wrote. Naming the product runner is the trust decision, and `TRUST_NOTE` stays as it is. |
| **The trace's spelling is mapped back to the case's name, for declared tools only, by exact match.** | The loader restricts `trajectory: called`/`forbidden`/`order` to declared names, so under a product they could name nothing before this change. Mapping `mcp__skill-lens__<name>` (Claude Code) and `skill-lens-<name>` (Copilot) back — each verified by reading what the product sends the model — is what makes a trajectory read identically under every runner. Every other call keeps the product's name, per the M9 decision not to normalise tool names across products. The transcript keeps the product's spelling. |
| **A product that ran but never listed the bridge's tools is `errored`, never a failed `called:`.** | `errored` ≠ `failed`: the model had no tools, which says nothing about the skill. The record's `list` event is product-independent — Claude Code's `init` and Copilot's `session.mcp_servers_loaded` both report server status, but neither parser had to change. A trace error wins over the check: a product that failed is the better explanation. |
| **Preflight starts the bridge once with `--check` when a planned case declares `tools:`.** | Executed, not merely found, like the version probe and the sandbox probe. A `sys.executable` that cannot import the bridge (a frozen build) is exit 2 up front, not one errored case per work item, before any quota is spent. |
| **Preflight refuses `--available-tools` in `[runners.copilot]` when a case declares `tools:`; Claude Code's `--tools` is not refused.** | Verified against `copilot` 1.0.37: the flag keeps only the tools it names, MCP included, so the model would run without the mocks and every `called:` would fail as an eval signal. Verified against Claude Code 2.1.274: `--tools ""` still lists the MCP tool in `init`, so the flag governs the built-in set only. Mirrors the judge's refusal of the same flag, with the opposite concern. |
| **A `command` naming another executable drops `mcp` along with the version probe and `judge_args`.** | A wrapper is not known to take the flag; running it would either fail or silently run without the tools. The refusal message names the flag the wrapper is not known to take. |
| **The product judge never gets a bridge.** | It grades text with no tools at all; `judge_args` is what keeps it from acting. The same `Product` value serves both seats, and only `ProductRunner.run` writes a bridge. |
| **`ProductRunner.run` still never raises.** | A bridge directory that cannot be written is `OSError` → `RunResult.error`; a runner reached with `tools:` and no `mcp` (a caller that skipped preflight) returns an errored result rather than running the case without its tools. |

## 3. Wire formats

**The spec** (`tools.json`, written by the runner, read by the server):

```json
{"version": "0.11.0", "record": "/tmp/skill-lens-mcp-abc/calls.jsonl",
 "tools": [{"name": "lookup_order", "description": "Look up an order by its id",
            "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}},
                             "required": ["order_id"], "additionalProperties": false},
            "returns": "{\"id\": \"1234\", \"status\": \"delivered\"}"}]}
```

**The product's config** (`mcp.json`): one server named `skill-lens`, `command` this
interpreter, `args` `["-m", "skill_lens.mcp_bridge", "<spec>"]`; Copilot's entry also
carries `"type": "local"` and `"tools": ["*"]`.

**The record** (`calls.jsonl`, appended by the server): `{"event": "list"}` on every
`tools/list`; `{"event": "call", "name": ..., "arguments": {...}}` on every answered call.

**On the wire:** `initialize` echoes the client's protocol revision when it is one of
`2024-11-05`, `2025-03-26`, `2025-06-18` (the tool shapes are identical across them) and
offers the newest otherwise; `tools/list` returns `{name, description, inputSchema}` in
declaration order; `tools/call` returns `{"content": [{"type": "text", "text": returns}]}`;
an unknown tool is `-32602`, an unknown method `-32601`, a line that is not JSON `-32700`
with a null id; a notification gets no reply; a JSON array is answered per element. The
server exits when stdin closes.

## 4. Verified against the products

| Product | Flag | Config entry | Tool as the model sees it | Server status in the trace |
| --- | --- | --- | --- | --- |
| Copilot CLI 1.0.37 | `--additional-mcp-config=@<file>` | `type: local`, `command`, `args`, `tools: ["*"]` | `skill-lens-lookup_order`, schema verbatim (read from the `Tools:` list in `--log-level all`) | `session.mcp_servers_loaded` → `{name: skill-lens, status: connected}` |
| Claude Code 2.1.274 | `--mcp-config=<file>` with `--strict-mcp-config` | `command`, `args` | `mcp__skill-lens__lookup_order`; requested as such, with the arguments the model chose; the schema fetched first through a `ToolSearch` call | `init` → `mcp_servers: [{name: skill-lens, status: connected, source: dynamic}]`, `tools: [..., mcp__skill-lens__lookup_order]` |

`copilot --available-tools=<names>` hides MCP tools along with the built-ins (M9 part 2's
verification); `claude --tools ""` does not (the MCP tool stays in `init`'s `tools`). One
half is unrecorded: Copilot's quota was exhausted (402) during verification, so no
`toolRequests` entry for an MCP tool was captured; the runner assumes the request carries
the function name the model was sent (`skill-lens-<name>`), as the `skill` tool request
already does, and `tests/fixtures/products/copilot-mcp.jsonl` is written on that basis. An
end-to-end `skill-lens run ./examples/order-support --runner claude-code --case "refuses a
refund"` served `lookup_order` and `issue_refund`, saw `lookup_order(order_id="1234")`
mapped back in the report, passed the assertion, trajectory and judge, and left no
directory or process behind.

## 5. Tests

- `tests/test_mcp_bridge.py` — the handlers (each method, the record, an unwritable record),
  the transport (one line per request, notifications, a non-JSON line, a batch, JSON-only
  stdout), the entry point, the module as a real subprocess over pipes, and the import rule.
- `tests/test_runner_mcp.py` — the presets' verified spellings, the one-element `=` rule,
  the spec's schemas, the config per product, `write_bridge`'s directory and cleanup,
  `connected`, `restore_tool_names` (declared only, exact match, input untouched), the
  probe (passes; a module that does not start; a command that cannot run; no
  `sys.executable`).
- `tests/fake_product.py` acts as an MCP client when handed a config (both spellings),
  records what it saw, and can be told to ignore the config.
- `tests/test_product_runner.py` — a bridged run under each preset's spellings, the bridge
  kept out of a workspace, a product that never connects (errored), a product failure
  winning over the check, a case without tools (no bridge), a product with no `mcp`.
- `tests/test_product_preflight.py` — the refusal messages (`cli`, a wrapped preset),
  acceptance plus the probe (once per run), the probe skipped without a tools case, a probe
  failure as `ProductSetupError`, the hiding-flag refusal and its Claude Code non-refusal.
- `tests/test_config.py` — `mcp` dropped with the version probe under a wrapping `command`,
  kept under the preset's executable, `None` for `cli`.
- `tests/test_cli.py` — a wrapped product still exits 2 naming the reason; a `claude` shim
  on `PATH` runs a bridged case end to end through the CLI and the JSON report.

## 6. Documentation

`docs/runners.md` (the measurement table and a new *Mock tools under a product* section),
`docs/eval-files.md` (the mock-tools section and the runner table), `docs/configuration.md`
(`command`, the hiding-flag refusal), `docs/security.md` (the bridge under the product's
trust), `docs/roadmap.md` (*What the MCP bridge shipped*), `ARCHITECTURE.md` (module map,
the isolation paragraph, the M9 decision, a new invariants section) and `CLAUDE.md`.
