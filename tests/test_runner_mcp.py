"""The runner's side of the MCP bridge: the files it writes, the spellings each
product needs, the name mapping, and the once-per-run probe."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from skill_lens import __version__
from skill_lens.mcp_bridge import SERVER_NAME
from skill_lens.models import ToolCall, ToolResponse, ToolSpec
from skill_lens.runners import mcp
from skill_lens.runners.mcp import (
    CLAUDE_CODE_MCP,
    COPILOT_MCP,
    BridgeSetupError,
    McpSupport,
    bridge_config,
    bridge_spec,
    probe_bridge,
    restore_tool_names,
    write_bridge,
)

TOOLS = [
    ToolSpec(
        name="lookup_order",
        description="Look up an order.",
        parameters={"order_id": "string"},
        returns='{"status": "shipped"}',
    ),
    ToolSpec(
        name="az_rest_write",
        input_schema={"type": "object", "properties": {"body": {}}},
        returns="written",
    ),
]


# --- the presets' spellings, as verified against each product ---


def test_the_presets_are_the_verified_spellings():
    assert COPILOT_MCP.config_arg == "--additional-mcp-config=@{path}"
    assert COPILOT_MCP.tool_name == "{server}-{tool}"
    assert dict(COPILOT_MCP.server_extra) == {"type": "local", "tools": ["*"]}
    assert COPILOT_MCP.hiding_flags == ("--available-tools",)
    assert CLAUDE_CODE_MCP.config_arg == "--mcp-config={path}"
    assert CLAUDE_CODE_MCP.tool_name == "mcp__{server}__{tool}"
    assert CLAUDE_CODE_MCP.server_extra == ()
    assert CLAUDE_CODE_MCP.hiding_flags == ()


def test_the_config_arg_is_one_element_so_a_variadic_option_swallows_nothing():
    # Claude Code's `--mcp-config <configs...>` takes several values; a
    # two-element spelling would let it eat whatever came after.
    for support in (COPILOT_MCP, CLAUDE_CODE_MCP):
        assert "=" in support.config_arg and support.config_arg.count("{path}") == 1


def test_product_name_spells_a_tool_the_way_the_product_does():
    assert COPILOT_MCP.product_name("lookup_order") == "skill-lens-lookup_order"
    assert CLAUDE_CODE_MCP.product_name("lookup_order") == "mcp__skill-lens__lookup_order"


# --- the spec and the config ---


def test_bridge_spec_carries_the_schemas_build_mock_tool_registers(tmp_path):
    spec = bridge_spec(TOOLS, tmp_path / "calls.jsonl")
    assert spec["version"] == __version__
    assert spec["record"] == str(tmp_path / "calls.jsonl")
    shorthand, declared = spec["tools"]
    # The shorthand is closed; a declared schema is passed as written.
    assert shorthand == {
        "name": "lookup_order",
        "description": "Look up an order.",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
        "returns": '{"status": "shipped"}',
    }
    assert declared["input_schema"] == {"type": "object", "properties": {"body": {}}}
    assert declared["returns"] == "written"
    assert spec["no_match"] == "no response is scripted for {name} with arguments {arguments}"
    assert bridge_spec([], None)["record"] == ""


def test_bridge_spec_writes_every_returns_shape_as_plain_json():
    tools = [
        ToolSpec(name="seq", returns=["first", "second"]),
        ToolSpec(
            name="lookup",
            parameters={"id": "string"},
            returns=[
                ToolResponse(when={"id": "A"}, value="a"),
                ToolResponse(value="fallback"),
            ],
        ),
    ]
    seq, lookup = bridge_spec(tools, None)["tools"]
    assert seq["returns"] == ["first", "second"]
    assert lookup["returns"] == [
        {"when": {"id": "A"}, "value": "a"},
        {"when": None, "value": "fallback"},
    ]
    json.dumps(bridge_spec(tools, None))  # nothing the server cannot read


def test_bridge_config_names_this_interpreter_and_the_products_extra_keys(tmp_path):
    spec_path = tmp_path / "tools.json"
    claude = bridge_config(CLAUDE_CODE_MCP, spec_path)
    assert claude == {
        "mcpServers": {
            SERVER_NAME: {
                "command": sys.executable,
                "args": ["-P", "-m", "skill_lens.mcp_bridge", str(spec_path)],
            }
        }
    }
    copilot = bridge_config(COPILOT_MCP, spec_path)
    assert copilot["mcpServers"][SERVER_NAME] == {
        "command": sys.executable,
        "args": ["-P", "-m", "skill_lens.mcp_bridge", str(spec_path)],
        "type": "local",
        "tools": ["*"],
    }


def test_an_interpreter_with_no_executable_is_a_setup_error(monkeypatch):
    monkeypatch.setattr(sys, "executable", "")
    with pytest.raises(BridgeSetupError, match="no sys.executable"):
        bridge_config(CLAUDE_CODE_MCP, Path("x"))
    with pytest.raises(BridgeSetupError, match="no sys.executable"):
        probe_bridge()


def test_write_bridge_puts_everything_in_a_fresh_directory_and_cleanup_removes_it():
    bridge = write_bridge(TOOLS, COPILOT_MCP)
    try:
        assert bridge.directory.is_dir()
        assert bridge.config.parent == bridge.directory
        assert bridge.record.parent == bridge.directory
        assert bridge.names == ("lookup_order", "az_rest_write")
        assert bridge.support is COPILOT_MCP
        config = json.loads(bridge.config.read_text(encoding="utf-8"))
        spec_path = Path(config["mcpServers"][SERVER_NAME]["args"][-1])
        assert spec_path.parent == bridge.directory
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        assert spec["record"] == str(bridge.record)
        assert [tool["name"] for tool in spec["tools"]] == ["lookup_order", "az_rest_write"]
        assert bridge.argv() == (f"--additional-mcp-config=@{bridge.config}",)
        assert not bridge.record.exists()  # nothing has connected yet
    finally:
        bridge.cleanup()
    assert not bridge.directory.exists()
    bridge.cleanup()  # a second cleanup is harmless


def test_write_bridge_removes_the_directory_when_a_file_cannot_be_written(monkeypatch):
    created: list[Path] = []
    real_mkdtemp = mcp.tempfile.mkdtemp

    def mkdtemp(prefix: str) -> str:
        path = real_mkdtemp(prefix=prefix)
        created.append(Path(path))
        return path

    monkeypatch.setattr(mcp.tempfile, "mkdtemp", mkdtemp)
    monkeypatch.setattr(mcp, "bridge_config", lambda *_: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(OSError, match="full"):
        write_bridge(TOOLS, CLAUDE_CODE_MCP)
    (directory,) = created
    assert not directory.exists()


# --- connected ---


def test_connected_reads_the_servers_list_event_and_nothing_else(tmp_path):
    bridge = write_bridge(TOOLS, CLAUDE_CODE_MCP)
    try:
        assert bridge.connected() is False  # no record at all
        bridge.record.write_text("garbage\n", encoding="utf-8")
        assert bridge.connected() is False
        bridge.record.write_text(
            json.dumps({"event": "call", "name": "lookup_order", "arguments": {}}) + "\n",
            encoding="utf-8",
        )
        assert bridge.connected() is False  # a call with no list is not a connection
        with bridge.record.open("a", encoding="utf-8") as handle:
            handle.write("not json\n" + json.dumps({"event": "list"}) + "\n")
        assert bridge.connected() is True
    finally:
        bridge.cleanup()


# --- the name mapping ---


def test_restore_tool_names_maps_only_the_declared_tools_by_exact_spelling():
    bridge = write_bridge(TOOLS, CLAUDE_CODE_MCP)
    try:
        calls = [
            ToolCall(name="Skill", arguments={"skill": "ping"}),
            ToolCall(name="ToolSearch", arguments={"query": "select:mcp__skill-lens__lookup"}),
            ToolCall(name="mcp__skill-lens__lookup_order", arguments={"order_id": "A-17"}),
            ToolCall(name="mcp__skill-lens__az_rest_write", arguments={}),
            ToolCall(name="mcp__other__lookup_order", arguments={}),
            ToolCall(name="lookup_order", arguments={}),
        ]
        restored = restore_tool_names(bridge, calls)
        assert [call.name for call in restored] == [
            "Skill",
            "ToolSearch",
            "lookup_order",
            "az_rest_write",
            "mcp__other__lookup_order",
            "lookup_order",
        ]
        assert restored[2].arguments == {"order_id": "A-17"}
        assert calls[2].name == "mcp__skill-lens__lookup_order"  # the input is untouched
    finally:
        bridge.cleanup()


def test_restore_tool_names_under_copilot_strips_the_server_prefix():
    bridge = write_bridge(TOOLS, COPILOT_MCP)
    try:
        calls = [ToolCall(name="skill-lens-lookup_order"), ToolCall(name="bash")]
        assert [c.name for c in restore_tool_names(bridge, calls)] == ["lookup_order", "bash"]
    finally:
        bridge.cleanup()


# --- the probe ---


def test_probe_bridge_starts_the_server_under_this_interpreter():
    probe_bridge()  # no raise


def test_probe_bridge_names_a_module_that_does_not_start(monkeypatch):
    monkeypatch.setattr(mcp, "BRIDGE_MODULE", "skill_lens.no_such_bridge")
    with pytest.raises(BridgeSetupError, match="does not start under"):
        probe_bridge()


def test_probe_bridge_names_a_command_that_cannot_run(monkeypatch):
    monkeypatch.setattr(sys, "executable", "/no/such/python-xyz")
    with pytest.raises(BridgeSetupError, match="could not start"):
        probe_bridge()


def test_mcp_support_is_a_plain_value():
    support = McpSupport(config_arg="--x={path}", tool_name="{tool}")
    assert support.product_name("t") == "t"
    with pytest.raises(AttributeError):
        support.config_arg = "y"  # type: ignore[misc]
