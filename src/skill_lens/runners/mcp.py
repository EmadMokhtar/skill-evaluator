"""Hand a case's mock tools to an agent product through a stdio MCP server.

The framework adapters register a case's `tools:` as callables inside the
agent loop they drive. A product owns its loop; what it can take instead is
an MCP server named in a config file, passed on its command line. The server
is `skill_lens.mcp_bridge`; this module is the runner's side of it: how each
product spells the flag, the config entry and a tool's name (`McpSupport`),
the files one invocation writes and reads back (`Bridge`), the mapping from
the product's spelling of a tool back to the name the case declared, and the
once-per-run probe that proves the server starts under this interpreter.

The tools are `build_mock_tool`'s -- the same schemas the framework runners
register, so a `parameters:` shorthand is closed and an `input_schema` is
passed verbatim under every runner. Imports no agent framework.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skill_lens import __version__
from skill_lens.mcp_bridge import SERVER_NAME
from skill_lens.models import ToolCall, ToolSpec
from skill_lens.runners.tools import build_mock_tool

BRIDGE_PREFIX = "skill-lens-mcp-"
BRIDGE_MODULE = "skill_lens.mcp_bridge"
PROBE_TIMEOUT_SECONDS = 30.0


class BridgeSetupError(Exception):
    """The bridge cannot serve here; raised by `probe_bridge` for preflight to
    turn into a `ProductSetupError`."""


@dataclass(frozen=True)
class McpSupport:
    """How one product takes a stdio MCP server and spells its tools.

    `config_arg` is the one argv element that hands the product the config
    file, with `{path}` for the file -- one element with `=` on purpose, so a
    variadic option (Claude Code's `--mcp-config <configs...>`) can never
    swallow an argument after it. `tool_name` is how the product names a
    tool from that server to the model (`{server}`, `{tool}`), which is what
    the trace reports and what `restore_tool_names` maps back. `server_extra`
    holds the keys the product's config entry needs beside `command` and
    `args`. `hiding_flags` are the product's own tool-selection flags that
    hide MCP tools from the model, which preflight refuses in a table when a
    case declares `tools:`.
    """

    config_arg: str
    tool_name: str
    server_extra: tuple[tuple[str, Any], ...] = ()
    hiding_flags: tuple[str, ...] = ()

    def product_name(self, tool: str) -> str:
        return self.tool_name.format(server=SERVER_NAME, tool=tool)


# Verified against copilot 1.0.37: `--additional-mcp-config=@<file>` reads
# the file (`@` is the product's file-path marker), the entry needs
# `"type": "local"` and `"tools": ["*"]`, the model sees the tool as
# `<server>-<tool>` (`skill-lens-lookup_order`) with the schema verbatim,
# and `--available-tools` hides MCP tools along with the built-ins.
COPILOT_MCP = McpSupport(
    config_arg="--additional-mcp-config=@{path}",
    tool_name="{server}-{tool}",
    server_extra=(("type", "local"), ("tools", ["*"])),
    hiding_flags=("--available-tools",),
)

# Verified against Claude Code 2.1.274: `--mcp-config=<file>` beside the
# preset's `--strict-mcp-config` makes the bridge the only MCP server, the
# `init` event lists it as connected and the tool as `mcp__<server>__<tool>`
# (`mcp__skill-lens__lookup_order`), and `--tools` governs the built-in set
# only -- the MCP tool stays listed under `--tools ""`, and under `--tools
# Bash,Read` (no `ToolSearch`) the model is sent its schema directly and
# calls it, so no `--tools` spelling hides the mocks.
CLAUDE_CODE_MCP = McpSupport(
    config_arg="--mcp-config={path}",
    tool_name="mcp__{server}__{tool}",
)


def bridge_command() -> list[str]:
    """The command a product runs to start the server: this interpreter, `-P`,
    `-m` the module. `sys.executable` is what is running skill-lens right now,
    so the package is importable from it without any environment of its own.
    `-P` keeps the child's working directory off `sys.path`: the product
    starts the server in the case's workspace, where a seeded `json.py` or
    `skill_lens/` would otherwise shadow the import and kill the bridge."""
    if not sys.executable:
        raise BridgeSetupError(
            "this Python has no sys.executable, so a product cannot start the MCP bridge"
        )
    return [sys.executable, "-P", "-m", BRIDGE_MODULE]


def bridge_spec(tools: Iterable[ToolSpec], record: Path | None) -> dict[str, Any]:
    """The spec the server reads: every tool's name, description, the schema
    `build_mock_tool` would register, and `returns`."""
    entries = []
    for spec in tools:
        tool = build_mock_tool(spec)
        entries.append(
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.json_schema,
                "returns": spec.returns,
            }
        )
    return {"version": __version__, "record": str(record) if record else "", "tools": entries}


def bridge_config(support: McpSupport, spec_path: Path) -> dict[str, Any]:
    """The product's MCP config: one server, this interpreter, the spec."""
    command = bridge_command()
    entry: dict[str, Any] = {"command": command[0], "args": [*command[1:], str(spec_path)]}
    entry.update(dict(support.server_extra))
    return {"mcpServers": {SERVER_NAME: entry}}


@dataclass(frozen=True)
class Bridge:
    """The files one product invocation's mock tools live in, under a fresh
    directory of their own -- never the working directory, which the product
    can list and a `file-produced` assertion can read."""

    directory: Path
    config: Path
    record: Path
    names: tuple[str, ...]
    support: McpSupport

    def argv(self) -> tuple[str, ...]:
        """The argv element that hands the product the config, appended last."""
        return (self.support.config_arg.format(path=self.config),)

    def connected(self) -> bool:
        """Did the product ask the server for its tools? A `list` event in the
        record is the product-independent sign that it connected; without
        one the model never had the tools, whatever the trace says."""
        try:
            lines = self.record.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("event") == "list":
                return True
        return False

    def cleanup(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)


def write_bridge(tools: list[ToolSpec], support: McpSupport) -> Bridge:
    """Write the spec and the product's config into a fresh directory.

    Raises `OSError` for a directory or file that cannot be written, which
    `ProductRunner.run` reports as `RunResult.error`, and `BridgeSetupError`
    when there is no interpreter to name.
    """
    directory = Path(tempfile.mkdtemp(prefix=BRIDGE_PREFIX)).resolve()
    try:
        spec_path = directory / "tools.json"
        record = directory / "calls.jsonl"
        config_path = directory / "mcp.json"
        spec_path.write_text(json.dumps(bridge_spec(tools, record)), encoding="utf-8")
        config_path.write_text(json.dumps(bridge_config(support, spec_path)), encoding="utf-8")
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return Bridge(
        directory=directory,
        config=config_path,
        record=record,
        names=tuple(spec.name for spec in tools),
        support=support,
    )


def restore_tool_names(bridge: Bridge, calls: Iterable[ToolCall]) -> list[ToolCall]:
    """The case's own names back on the calls the trace reported under the
    product's spelling, so a `trajectory:` reads the same under every runner.

    Only the declared tools are mapped, each by its exact product spelling;
    every other call keeps the product's name (`Bash`, `bash`), as the docs
    say a trajectory under a product names the product's tools.
    """
    declared = {bridge.support.product_name(name): name for name in bridge.names}
    return [
        call.model_copy(update={"name": declared[call.name]}) if call.name in declared else call
        for call in calls
    ]


def probe_bridge() -> None:
    """Start the server once under this interpreter, `--check`, before any
    case runs: executed, not merely found, like the version probe. Raises
    `BridgeSetupError` naming what went wrong."""
    command = bridge_command()
    directory = Path(tempfile.mkdtemp(prefix=BRIDGE_PREFIX)).resolve()
    try:
        spec_path = directory / "tools.json"
        spec_path.write_text(
            json.dumps(bridge_spec([ToolSpec(name="probe", returns="ok")], None)),
            encoding="utf-8",
        )
        try:
            completed = subprocess.run(  # noqa: S603 - this interpreter, fixed argv, no shell
                [*command, "--check", str(spec_path)],
                capture_output=True,
                timeout=PROBE_TIMEOUT_SECONDS,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BridgeSetupError(f"the MCP bridge could not start: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip().splitlines()
            first = detail[0] if detail else f"exited with code {completed.returncode}"
            raise BridgeSetupError(f"the MCP bridge does not start under {command[0]}: {first}")
    except OSError as exc:
        raise BridgeSetupError(f"cannot write the MCP bridge's files: {exc}") from exc
    finally:
        shutil.rmtree(directory, ignore_errors=True)
