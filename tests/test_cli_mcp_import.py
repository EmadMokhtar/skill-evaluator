"""`skill-lens mcp-import` prints mock-tool YAML for a saved tools/list listing."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from skill_lens.cli import app
from skill_lens.mcp_import import HEADER, RETURNS_PLACEHOLDER
from skill_lens.yaml_loading import safe_load

runner = CliRunner()

LISTING = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
        "tools": [
            {
                "name": "get_pull_request",
                "description": "Get a pull request",
                "inputSchema": {"type": "object", "properties": {"owner": {"type": "string"}}},
            },
            {
                "name": "list-issues",
                "description": "List issues",
                "inputSchema": {"type": "object"},
            },
        ]
    },
}


def _listing(tmp_path, payload=LISTING):
    path = tmp_path / "tools.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_prints_the_block_for_a_file(tmp_path):
    result = runner.invoke(app, ["mcp-import", str(_listing(tmp_path))])
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(HEADER)
    loaded = safe_load(result.stdout)
    assert [tool["name"] for tool in loaded["tools"]] == ["get_pull_request", "list-issues"]
    assert loaded["tools"][0]["returns"] == RETURNS_PLACEHOLDER


def test_stdout_holds_nothing_but_the_block(tmp_path):
    # `> tools.yaml` must capture exactly the block: no banner, no trailer.
    result = runner.invoke(app, ["mcp-import", str(_listing(tmp_path))])
    assert result.stdout.endswith("returns: TODO(skill-lens) the JSON this tool returns\n")
    assert result.stderr == ""


def test_reads_stdin_for_a_dash():
    result = runner.invoke(app, ["mcp-import", "-"], input=json.dumps(LISTING))
    assert result.exit_code == 0, result.output
    assert "name: list-issues" in result.stdout


def test_tool_filters_and_repeats(tmp_path):
    result = runner.invoke(app, ["mcp-import", str(_listing(tmp_path)), "--tool", "list-issues"])
    assert result.exit_code == 0, result.output
    names = [tool["name"] for tool in safe_load(result.stdout)["tools"]]
    assert names == ["list-issues"]


def test_an_unknown_tool_is_a_user_error_on_stderr(tmp_path):
    result = runner.invoke(app, ["mcp-import", str(_listing(tmp_path)), "--tool", "nope"])
    assert result.exit_code == 2
    assert "no tool named 'nope'" in result.stderr
    assert "get_pull_request" in result.stderr
    assert result.stdout == ""


def test_a_missing_file_is_a_user_error(tmp_path):
    result = runner.invoke(app, ["mcp-import", str(tmp_path / "missing.json")])
    assert result.exit_code == 2
    assert "cannot read" in result.stderr
    assert result.stdout == ""


def test_invalid_json_is_a_user_error(tmp_path):
    path = tmp_path / "tools.json"
    path.write_text("{", encoding="utf-8")
    result = runner.invoke(app, ["mcp-import", str(path)])
    assert result.exit_code == 2
    assert "not valid JSON" in result.stderr


def test_an_unknown_shape_is_a_user_error(tmp_path):
    result = runner.invoke(app, ["mcp-import", str(_listing(tmp_path, {"jsonrpc": "2.0"}))])
    assert result.exit_code == 2
    assert "expected a JSON-RPC response" in result.stderr


def test_a_name_no_provider_accepts_is_a_user_error(tmp_path):
    bad = {"tools": [{"name": "a.b", "inputSchema": {"type": "object"}}]}
    result = runner.invoke(app, ["mcp-import", str(_listing(tmp_path, bad))])
    assert result.exit_code == 2
    assert "tool 'a.b' cannot be a mock" in result.stderr


def test_stdin_errors_name_stdin():
    result = runner.invoke(app, ["mcp-import", "-"], input="{")
    assert result.exit_code == 2
    assert "<stdin>: not valid JSON" in result.stderr
