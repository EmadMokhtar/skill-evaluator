"""The built-in file tools.

The rule these tests exist to protect: a built-in tool NEVER raises. A model
that calls one wrongly is producing an eval signal, and an exception would
surface that as an infra error and mark the case errored instead of scoring it.
"""

from __future__ import annotations

import sys

import pytest

from skill_lens.bundle import SkillBundle
from skill_lens.runners.tools import (
    BUILTIN_TOOL_NAMES,
    BUNDLE_TOOL_NAMES,
    WORKSPACE_TOOL_NAMES,
    build_bundle_tools,
    build_workspace_tools,
    render_script_result,
)
from skill_lens.scripts import SandboxStatus, ScriptPolicy, ScriptResult, ScriptRuntime
from skill_lens.workspace import Workspace, WorkspaceLimits

RUNTIME = ScriptRuntime(
    policy=ScriptPolicy(sandbox="off", interpreters={"py": (sys.executable,)}),
    sandbox=SandboxStatus(backend="none", detail="test"),
)


def _tools(tmp_path, **limits):
    workspace = Workspace(
        root=tmp_path.resolve(),
        limits=WorkspaceLimits(**limits) if limits else WorkspaceLimits(),
    )
    return workspace, {tool.name: tool for tool in build_workspace_tools(workspace)}


def test_the_three_workspace_tools_are_built_under_their_declared_names(tmp_path):
    _, tools = _tools(tmp_path)
    assert set(tools) == set(WORKSPACE_TOOL_NAMES)


def test_the_builtin_names_are_the_workspace_three_plus_the_bundle_three():
    assert BUILTIN_TOOL_NAMES == (
        "list_files",
        "read_file",
        "write_file",
        "list_skill_files",
        "read_skill_file",
        "run_script",
    )


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
    message = tools["read_file"].call(path="blob.dat")
    assert "UTF-8" in message
    # The refusal must blame the CONTENT, not the path the model asked for --
    # the path was fine, the bytes behind it were not.
    assert "content" in message


def test_escaping_the_root_returns_a_message_rather_than_raising(tmp_path):
    _, tools = _tools(tmp_path)
    assert tools["read_file"].call(path="../../etc/passwd").startswith("refused:")
    assert tools["write_file"].call(path="/etc/passwd", content="x").startswith("refused:")


def test_a_write_over_the_cap_returns_a_message_naming_the_cap(tmp_path):
    _, tools = _tools(tmp_path, max_file_bytes=8)
    message = tools["write_file"].call(path="big.txt", content="x" * 9)
    assert "max_file_bytes" in message


@pytest.mark.parametrize("name", WORKSPACE_TOOL_NAMES)
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


@pytest.mark.parametrize("name", ["read_file", "write_file"])
def test_a_lone_surrogate_is_refused_rather_than_raising(tmp_path, name):
    # A model emitting a malformed \uXXXX escape produces an unpaired UTF-16
    # surrogate. os.path.realpath raises UnicodeEncodeError on it -- an encode
    # error, which a UnicodeDecodeError-only catch misses entirely.
    _, tools = _tools(tmp_path)
    assert isinstance(tools[name].call(path="a\ud800b.txt", content="x"), str)


def test_a_lone_surrogate_in_content_is_refused_rather_than_raising(tmp_path):
    _, tools = _tools(tmp_path)
    message = tools["write_file"].call(path="a.txt", content="\ud800")
    assert isinstance(message, str)
    # Names the content as the problem: the path was valid.
    assert "content" in message and "encoded" in message


def test_a_nul_byte_in_a_path_is_refused_rather_than_raising(tmp_path):
    _, tools = _tools(tmp_path)
    assert tools["read_file"].call(path="a\x00b.txt").startswith("refused:")


def test_every_builtin_declares_a_closed_schema(tmp_path):
    _, tools = _tools(tmp_path)
    for name in WORKSPACE_TOOL_NAMES:
        schema = tools[name].json_schema
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


@pytest.mark.parametrize("name", WORKSPACE_TOOL_NAMES)
def test_two_toolsets_do_not_share_schema_objects(tmp_path, name):
    # Schemas are built per call for exactly this reason: under --concurrency N
    # an adapter mutating one toolset's schema in place must never reach
    # another's. A top-level `is not` alone would NOT catch this -- the nested
    # `required` list and property dicts have to be checked by identity too.
    _, first = _tools(tmp_path)
    _, second = _tools(tmp_path)
    one, two = first[name].json_schema, second[name].json_schema
    assert one is not two
    assert one["required"] is not two["required"]
    assert one["properties"] is not two["properties"]
    for key, value in one["properties"].items():
        assert value is not two["properties"][key]


def _bundle(tmp_path, with_script=True):
    # exist_ok: several tests build the same bundle twice under one tmp_path.
    root = tmp_path / "skill"
    (root / "references").mkdir(parents=True, exist_ok=True)
    (root / "references" / "style.md").write_text("# Style\n", encoding="utf-8")
    if with_script:
        (root / "scripts").mkdir(exist_ok=True)
        (root / "scripts" / "hello.py").write_text(
            "import sys; print('hello', *sys.argv[1:])", encoding="utf-8"
        )
    return SkillBundle(root.resolve())


def _bundle_tools(tmp_path, runtime=RUNTIME, with_script=True):
    workspace = Workspace(root=(tmp_path / "ws").resolve())
    workspace.root.mkdir(exist_ok=True)
    tools = build_bundle_tools(_bundle(tmp_path, with_script), workspace, runtime)
    return workspace, {tool.name: tool for tool in tools}


def test_the_read_tools_are_always_built_and_run_script_only_with_a_runtime(tmp_path):
    _, with_runtime = _bundle_tools(tmp_path)
    assert set(with_runtime) == set(BUNDLE_TOOL_NAMES)
    _, without = _bundle_tools(tmp_path, runtime=None)
    assert set(without) == {"list_skill_files", "read_skill_file"}


def test_run_script_is_absent_when_the_bundle_has_no_scripts(tmp_path):
    _, tools = _bundle_tools(tmp_path, with_script=False)
    assert "run_script" not in tools


def test_list_skill_files_lists_the_bundle(tmp_path):
    _, tools = _bundle_tools(tmp_path)
    assert tools["list_skill_files"].call() == "references/style.md\nscripts/hello.py"


def test_read_skill_file_reads_and_refuses(tmp_path):
    _, tools = _bundle_tools(tmp_path)
    assert tools["read_skill_file"].call(path="references/style.md") == "# Style\n"
    assert tools["read_skill_file"].call(path="SKILL.md").startswith("refused:")
    assert (
        tools["read_skill_file"]
        .call(path="references/nope.md")
        .startswith("refused: cannot read references/nope.md")
    )
    assert tools["read_skill_file"].call().startswith("refused:")
    assert tools["read_skill_file"].call(path=42).startswith("refused:")


def test_run_script_renders_exit_code_and_both_streams(tmp_path):
    _, tools = _bundle_tools(tmp_path)
    rendered = tools["run_script"].call(path="scripts/hello.py", args=["a", "b"])
    assert rendered == "exit code: 0\nstdout:\nhello a b\n\nstderr:\n(empty)"


def test_run_script_accepts_a_wrongly_shaped_args_without_raising(tmp_path):
    _, tools = _bundle_tools(tmp_path)
    assert "hello single" in tools["run_script"].call(path="scripts/hello.py", args="single")
    assert "hello\n" in tools["run_script"].call(path="scripts/hello.py", args=None)
    assert "hello\n" in tools["run_script"].call(path="scripts/hello.py")


def test_run_script_returns_a_refusal_for_a_bad_path(tmp_path):
    _, tools = _bundle_tools(tmp_path)
    assert tools["run_script"].call(path="references/style.md").startswith("refused:")
    assert tools["run_script"].call().startswith("refused:")


def test_the_descriptions_name_no_skill(tmp_path):
    _, tools = _bundle_tools(tmp_path)
    for tool in tools.values():
        assert "skill" in tool.description.lower()  # they say what they are for...
        assert "hello" not in tool.description  # ...without naming this skill's files


def test_run_script_schema_takes_a_path_and_optional_string_args(tmp_path):
    _, tools = _bundle_tools(tmp_path)
    schema = tools["run_script"].json_schema
    assert schema["required"] == ["path"]
    assert schema["properties"]["args"] == {"type": "array", "items": {"type": "string"}}
    assert schema["additionalProperties"] is False


def test_each_bundle_tool_gets_its_own_schema_object(tmp_path):
    workspace = _bundle_tools(tmp_path)[0]
    a = {t.name: t for t in build_bundle_tools(_bundle(tmp_path), workspace, RUNTIME)}
    b = {t.name: t for t in build_bundle_tools(_bundle(tmp_path), workspace, RUNTIME)}
    for name in a:
        assert a[name].json_schema is not b[name].json_schema


def test_render_script_result_states_a_timeout_and_a_warning():
    result = ScriptResult(timed_out=True, stdout="partial", workspace_warning="warning: too big")
    assert render_script_result(result, 30.0) == (
        "stopped after 30 s (script_timeout_seconds)\nstdout:\npartial\nstderr:\n(empty)\n"
        "warning: too big"
    )


def test_render_script_result_passes_a_refusal_through():
    assert render_script_result(ScriptResult(refused="refused: nope"), 30.0) == "refused: nope"
