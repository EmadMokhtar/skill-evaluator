"""The built-in file tools.

The rule these tests exist to protect: a built-in tool NEVER raises. A model
that calls one wrongly is producing an eval signal, and an exception would
surface that as an infra error and mark the case errored instead of scoring it.
"""

from __future__ import annotations

import pytest

from skill_lens.runners.tools import BUILTIN_TOOL_NAMES, build_workspace_tools
from skill_lens.workspace import Workspace, WorkspaceLimits


def _tools(tmp_path, **limits):
    workspace = Workspace(
        root=tmp_path.resolve(),
        limits=WorkspaceLimits(**limits) if limits else WorkspaceLimits(),
    )
    return workspace, {tool.name: tool for tool in build_workspace_tools(workspace)}


def test_the_three_tools_are_built_under_their_declared_names(tmp_path):
    _, tools = _tools(tmp_path)
    assert set(tools) == set(BUILTIN_TOOL_NAMES)


def test_list_files_reports_an_empty_directory(tmp_path):
    _, tools = _tools(tmp_path)
    assert tools["list_files"].call() == "(empty)"


def test_list_files_is_sorted_relative_and_recursive(tmp_path):
    workspace, tools = _tools(tmp_path)
    workspace.write("b.txt", "b")
    workspace.write("nested/a.txt", "a")
    assert tools["list_files"].call() == "b.txt\nnested/a.txt"


def test_write_then_read_round_trips(tmp_path):
    _, tools = _tools(tmp_path)
    confirmation = tools["write_file"].call(path="report.md", content="hello")
    assert "report.md" in confirmation
    assert tools["read_file"].call(path="report.md") == "hello"


def test_reading_a_missing_file_returns_a_message(tmp_path):
    _, tools = _tools(tmp_path)
    assert tools["read_file"].call(path="nope.md").startswith("refused:")


def test_reading_a_directory_returns_a_message(tmp_path):
    workspace, tools = _tools(tmp_path)
    (workspace.root / "sub").mkdir()
    assert tools["read_file"].call(path="sub").startswith("refused:")


def test_reading_a_non_utf8_file_returns_a_message(tmp_path):
    workspace, tools = _tools(tmp_path)
    (workspace.root / "blob.dat").write_bytes(b"\xff\xfe\x00")
    assert "UTF-8" in tools["read_file"].call(path="blob.dat")


def test_escaping_the_root_returns_a_message_rather_than_raising(tmp_path):
    _, tools = _tools(tmp_path)
    assert tools["read_file"].call(path="../../etc/passwd").startswith("refused:")
    assert tools["write_file"].call(path="/etc/passwd", content="x").startswith("refused:")


def test_a_write_over_the_cap_returns_a_message_naming_the_cap(tmp_path):
    _, tools = _tools(tmp_path, max_file_bytes=8)
    message = tools["write_file"].call(path="big.txt", content="x" * 9)
    assert "max_file_bytes" in message


@pytest.mark.parametrize("name", BUILTIN_TOOL_NAMES)
@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"path": ""},
        {"path": None},
        {"path": 7},
        {"path": "a.txt", "hallucinated": "argument"},
        {"path": ["a.txt"], "content": {"not": "a string"}},
        {"content": "no path at all"},
    ],
)
def test_no_tool_ever_raises_whatever_the_model_sends(tmp_path, name, arguments):
    # The existing invariant for mock tools, extended to the real ones: a
    # model hallucinating an argument must not raise, or an eval signal would
    # surface as an infra error.
    _, tools = _tools(tmp_path)
    assert isinstance(tools[name].call(**arguments), str)


def test_every_builtin_declares_a_closed_schema(tmp_path):
    _, tools = _tools(tmp_path)
    for name in BUILTIN_TOOL_NAMES:
        schema = tools[name].json_schema
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


def test_two_toolsets_do_not_share_schema_objects(tmp_path):
    # `_empty_schema()` is built per call for exactly this reason: mutating
    # one tool's schema must never reach another's.
    _, first = _tools(tmp_path)
    _, second = _tools(tmp_path)
    assert first["list_files"].json_schema is not second["list_files"].json_schema
