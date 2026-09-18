"""Tool libraries: mock tools declared once and imported by many eval files."""

from pathlib import Path

import pytest

from skill_lens.cases.checks import UNFILLED_SENTINEL
from skill_lens.cases.tool_libraries import (
    EMPTY_LIBRARY,
    ToolLibrary,
    ToolLibraryError,
    load_tool_libraries,
    parse_tool_library,
)
from skill_lens.models import ToolSpec

LIBRARY = """# the order API
tools:
  - name: lookup_order
    description: Look up an order by its id
    parameters:
      order_id: string
    returns: '{"id": "0000"}'
  - name: issue_refund
    description: Issue a refund for an order
    parameters:
      order_id: string
    returns: '{"ok": true}'
"""


def _write(directory: Path, body: str, name: str = "lib.yaml") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


# --- parse_tool_library ------------------------------------------------------


def test_a_library_parses_to_tool_specs_in_file_order(tmp_path):
    specs = parse_tool_library(_write(tmp_path, LIBRARY))
    assert [s.name for s in specs] == ["lookup_order", "issue_refund"]
    assert specs[0].parameters == {"order_id": "string"}
    assert specs[0].returns == '{"id": "0000"}'


def test_an_empty_tools_list_is_a_library_with_nothing_in_it(tmp_path):
    assert parse_tool_library(_write(tmp_path, "tools: []\n")) == []


def test_a_missing_file_is_refused_naming_it(tmp_path):
    with pytest.raises(ToolLibraryError, match="cannot read tool library .*missing.yaml"):
        parse_tool_library(tmp_path / "missing.yaml")


def test_invalid_yaml_is_refused_naming_the_file(tmp_path):
    with pytest.raises(ToolLibraryError, match="invalid YAML in tool library .*lib.yaml"):
        parse_tool_library(_write(tmp_path, "tools: [unclosed\n"))


@pytest.mark.parametrize(
    "body",
    ["- just\n- a list\n", "cases: []\n", "tools: {not: a list}\n", "just a string\n", ""],
    ids=["list", "no-tools-key", "tools-not-a-list", "scalar", "empty"],
)
def test_anything_but_a_top_level_tools_list_is_refused(tmp_path, body):
    with pytest.raises(ToolLibraryError, match="expected a top-level 'tools' list"):
        parse_tool_library(_write(tmp_path, body))


def test_a_second_top_level_key_is_refused_naming_it(tmp_path):
    with pytest.raises(ToolLibraryError, match="expected only a top-level 'tools' list.*'cases'"):
        parse_tool_library(_write(tmp_path, "tools: []\ncases: []\n"))


def test_a_placeholder_value_is_refused_naming_the_tool_and_the_field(tmp_path):
    body = f"tools:\n  - name: t\n    returns: {UNFILLED_SENTINEL} fill me\n"
    with pytest.raises(ToolLibraryError) as info:
        parse_tool_library(_write(tmp_path, body))
    assert "tool #1 still has the scaffold placeholder" in str(info.value)
    assert "at returns." in str(info.value)


def test_a_placeholder_key_is_refused_too(tmp_path):
    body = f"tools:\n  - name: t\n    parameters:\n      '{UNFILLED_SENTINEL}': string\n"
    with pytest.raises(ToolLibraryError, match=r"tool #1 still has the scaffold placeholder"):
        parse_tool_library(_write(tmp_path, body))


def test_a_tool_the_case_loader_would_refuse_is_refused_here_naming_its_position(tmp_path):
    with pytest.raises(ToolLibraryError, match=r"tool #2 invalid \(name\)"):
        parse_tool_library(_write(tmp_path, "tools:\n  - name: ok\n  - name: 'not ok'\n"))
    with pytest.raises(ToolLibraryError, match=r"tool #1 invalid \(colour\)"):
        parse_tool_library(_write(tmp_path, "tools:\n  - name: ok\n    colour: blue\n"))


def test_a_tool_that_is_not_a_mapping_is_refused_naming_its_position(tmp_path):
    with pytest.raises(ToolLibraryError, match=r"tool #1 invalid"):
        parse_tool_library(_write(tmp_path, "tools:\n  - just a string\n"))


def test_the_schema_checks_apply_naming_the_tool(tmp_path):
    both = "tools:\n  - name: t\n    parameters: {q: string}\n    input_schema: {type: object}\n"
    with pytest.raises(ToolLibraryError, match="tool #1 't' declares both parameters and"):
        parse_tool_library(_write(tmp_path, both))
    with pytest.raises(ToolLibraryError, match="tool #1 't' input_schema must declare type"):
        parse_tool_library(
            _write(tmp_path, "tools:\n  - name: t\n    input_schema: {type: string}\n")
        )
    with pytest.raises(ToolLibraryError, match="tool #1 't' has an invalid input_schema"):
        parse_tool_library(_write(tmp_path, "tools:\n  - name: t\n    input_schema: {type: 5}\n"))


def test_a_name_declared_twice_in_one_file_is_refused_naming_both_positions(tmp_path):
    body = "tools:\n  - name: t\n  - name: other\n  - name: t\n"
    with pytest.raises(ToolLibraryError, match=r"declares tool 't' twice \(tools #1 and #3\)"):
        parse_tool_library(_write(tmp_path, body))


def test_a_description_of_yes_survives_the_strict_bool_loader(tmp_path):
    (spec,) = parse_tool_library(_write(tmp_path, "tools:\n  - name: t\n    description: yes\n"))
    assert spec.description == "yes"


# --- load_tool_libraries -----------------------------------------------------


def test_a_file_entry_is_imported_relative_to_the_given_directory(tmp_path):
    _write(tmp_path / "shared", LIBRARY, "api.yaml")
    evals = tmp_path / "skills" / "a" / "evals"
    evals.mkdir(parents=True)
    library = load_tool_libraries(["../../../shared/api.yaml"], relative_to=evals)
    assert library.declared is True
    assert set(library.specs) == {"lookup_order", "issue_refund"}
    assert library.sources["lookup_order"] == (tmp_path / "shared" / "api.yaml").resolve()


def test_a_directory_entry_imports_its_yaml_files_sorted_and_nothing_else(tmp_path):
    shared = tmp_path / "shared"
    _write(shared, "tools:\n  - name: b_tool\n", "b.yml")
    _write(shared, "tools:\n  - name: a_tool\n", "a.yaml")
    _write(shared, "tools:\n  - name: ignored\n", "notes.txt")
    _write(shared / "nested", "tools:\n  - name: deeper\n", "c.yaml")
    library = load_tool_libraries(["shared"], relative_to=tmp_path)
    assert list(library.specs) == ["a_tool", "b_tool"]


def test_an_empty_directory_is_refused(tmp_path):
    (tmp_path / "shared").mkdir()
    with pytest.raises(
        ToolLibraryError,
        match=r"tool_libraries\[0\] 'shared' names a directory with no YAML files",
    ):
        load_tool_libraries(["shared"], relative_to=tmp_path)


def test_a_missing_path_is_refused_saying_where_it_looked(tmp_path):
    with pytest.raises(
        ToolLibraryError, match=r"tool_libraries\[0\] 'nope.yaml' does not exist \(looked at"
    ):
        load_tool_libraries(["nope.yaml"], relative_to=tmp_path)


def test_an_absolute_path_is_refused(tmp_path):
    # The check mirrors workspace.check_relative_path (absolute, drive or
    # root); the Windows spellings only trip on Windows, and that function's
    # own tests cover them.
    with pytest.raises(ToolLibraryError, match=r"tool_libraries\[0\] .* is not a relative path"):
        load_tool_libraries(["/etc/tools.yaml"], relative_to=tmp_path)


@pytest.mark.parametrize("entries", ["shared/api.yaml", {"path": "x"}, 3])
def test_a_value_that_is_not_a_list_is_refused(tmp_path, entries):
    with pytest.raises(ToolLibraryError, match="tool_libraries must be a list of paths"):
        load_tool_libraries(entries, relative_to=tmp_path)


@pytest.mark.parametrize("entry", [3, None, "", "   ", ["nested"]])
def test_an_entry_that_is_not_a_path_is_refused_naming_its_position(tmp_path, entry):
    _write(tmp_path, LIBRARY)
    with pytest.raises(ToolLibraryError, match=r"tool_libraries\[1\] must be a path"):
        load_tool_libraries(["lib.yaml", entry], relative_to=tmp_path)


def test_the_same_file_listed_twice_is_refused(tmp_path):
    _write(tmp_path, LIBRARY)
    with pytest.raises(
        ToolLibraryError,
        match=r"tool_libraries\[1\] 'lib.yaml' imports .*lib.yaml again; "
        r"tool_libraries\[0\] already did",
    ):
        load_tool_libraries(["lib.yaml", "lib.yaml"], relative_to=tmp_path)


def test_a_directory_plus_a_file_inside_it_is_refused(tmp_path):
    _write(tmp_path / "shared", LIBRARY, "api.yaml")
    with pytest.raises(ToolLibraryError, match=r"tool_libraries\[1\] .* imports .* again"):
        load_tool_libraries(["shared", "shared/api.yaml"], relative_to=tmp_path)


def test_a_name_two_files_declare_is_refused_naming_both(tmp_path):
    _write(tmp_path, "tools:\n  - name: lookup_order\n", "one.yaml")
    _write(tmp_path, "tools:\n  - name: lookup_order\n", "two.yaml")
    with pytest.raises(ToolLibraryError) as info:
        load_tool_libraries(["one.yaml", "two.yaml"], relative_to=tmp_path)
    message = str(info.value)
    assert "tool 'lookup_order' is declared by both" in message
    assert "one.yaml" in message and "two.yaml" in message


def test_a_library_error_names_the_library_file(tmp_path):
    _write(tmp_path, "tools: [unclosed\n")
    with pytest.raises(ToolLibraryError, match="invalid YAML in tool library .*lib.yaml"):
        load_tool_libraries(["lib.yaml"], relative_to=tmp_path)


def test_an_empty_import_list_is_declared_but_empty(tmp_path):
    library = load_tool_libraries([], relative_to=tmp_path)
    assert library.declared is True
    assert library.specs == {}


# --- ToolLibrary.resolve -----------------------------------------------------


def test_resolve_returns_the_named_tool():
    library = ToolLibrary(specs={"t": ToolSpec(name="t")}, sources={"t": Path("x")}, declared=True)
    assert library.resolve("t").name == "t"


def test_resolve_without_a_tool_libraries_key_says_to_add_one():
    with pytest.raises(
        ToolLibraryError,
        match="references tool 'x' but the file declares no tool_libraries:",
    ):
        EMPTY_LIBRARY.resolve("x")


def test_resolve_of_an_unknown_name_lists_the_declared_names_sorted():
    library = ToolLibrary(
        specs={"zeta": ToolSpec(name="zeta"), "alpha": ToolSpec(name="alpha")},
        sources={"zeta": Path("x"), "alpha": Path("x")},
        declared=True,
    )
    with pytest.raises(
        ToolLibraryError,
        match="which no imported library declares; the imports declare: alpha, zeta",
    ):
        library.resolve("x")


def test_resolve_against_empty_imports_says_nothing_is_declared():
    library = ToolLibrary(specs={}, sources={}, declared=True)
    with pytest.raises(ToolLibraryError, match="the imports declare: nothing"):
        library.resolve("x")
