"""Turn an MCP server's `tools/list` listing into mock-tool YAML.

An MCP (Model Context Protocol) server answers a `tools/list` request with
the tools it exposes: each one's name, description and the JSON Schema of
its arguments. Copying that into a case's `tools:` block by hand is where a
mock drifts from the server it stands in for; this module copies it instead.

Pure: text in, text out. `cli.py` does the IO, and nothing here touches the
network -- the listing is whatever the author saved from an MCP client.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import ValidationError

from skill_lens.cases.loader import UNFILLED_SENTINEL
from skill_lens.models import ToolSpec

# The placeholders an imported block carries. Both start with the loader's
# sentinel, so a pasted block refuses to run until the author fills them --
# the same rule `skill-lens init` relies on.
DESCRIPTION_PLACEHOLDER = f"{UNFILLED_SENTINEL} what this tool does"
RETURNS_PLACEHOLDER = f"{UNFILLED_SENTINEL} the JSON this tool returns"

HEADER = (
    "# Written by `skill-lens mcp-import`. Replace every TODO(skill-lens); until you do,\n"
    "# skill-lens refuses to run the file rather than pass a case that checks nothing.\n"
)
_OUTPUT_SCHEMA_NOTE = "# The server declares this output schema; shape `returns` to match it:"

_ACCEPTED_SHAPES = (
    'a JSON-RPC response ({"result": {"tools": [...]}}), its result ({"tools": [...]}), '
    "or the bare tools array ([...])"
)

# `yaml.safe_dump` folds long plain scalars at 80 columns by default; a folded
# description is valid YAML but reads badly in a file meant to be edited.
_NO_WRAP = 1_000_000


class McpImportError(ValueError):
    """A listing that cannot be imported: bad JSON, an unknown shape, or a
    tool no provider would register. A user error, so the CLI exits 2."""


@dataclass(frozen=True)
class ImportedTool:
    """One tool from the listing: the mock it becomes, and the output schema
    the server declared (if any) for the author to shape `returns` against."""

    spec: ToolSpec
    output_schema: dict[str, Any] | None = None


def parse_tools_list(text: str, *, source: str) -> list[ImportedTool]:
    """Parse a saved `tools/list` response into the mocks it describes.

    `source` names the file (or `<stdin>`) in every error message.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise McpImportError(f"{source}: not valid JSON: {exc}") from exc
    entries = _unwrap(data, source)
    return [_import_tool(entry, index, source) for index, entry in enumerate(entries)]


def _unwrap(data: object, source: str) -> list[object]:
    """Find the tools array inside any of the three shapes people save."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "error" in data:
            error = data["error"]
            message = error.get("message", error) if isinstance(error, dict) else error
            raise McpImportError(
                f"{source}: the server answered tools/list with an error: {message}"
            )
        if "result" in data:
            data = data["result"]
        if isinstance(data, dict):
            # A truthy nextCursor means more tools exist on a later page; a
            # page-one capture would otherwise import a silent subset, and
            # --tool would only ever be able to list that page's names. None
            # or "" means "last page" and is accepted below as usual.
            cursor = data.get("nextCursor")
            if cursor:
                raise McpImportError(
                    f"{source}: the listing carries nextCursor {cursor!r}, so it is "
                    f"one page of several; capture every page (or the tool list as "
                    f"one array) and import that"
                )
            if isinstance(data.get("tools"), list):
                return data["tools"]
    raise McpImportError(f"{source}: expected {_ACCEPTED_SHAPES}")


def _import_tool(entry: object, index: int, source: str) -> ImportedTool:
    if not isinstance(entry, dict):
        raise McpImportError(f"{source}: tool #{index + 1} is not an object")
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise McpImportError(f"{source}: tool #{index + 1} has no name")
    label = f"tool {name!r}"
    input_schema = entry.get("inputSchema")
    if not isinstance(input_schema, dict):
        raise McpImportError(
            f"{source}: {label} has no inputSchema object; an MCP tools/list entry "
            f"always carries one"
        )
    # The same two checks the case loader makes on a pasted block. Refusing
    # here says so before the author fills in a placeholder, instead of at
    # the first `skill-lens run`.
    try:
        Draft202012Validator.check_schema(input_schema)
    except SchemaError as exc:
        raise McpImportError(
            f"{source}: {label} has an invalid inputSchema: {exc.message}"
        ) from exc
    if input_schema.get("type") != "object":
        raise McpImportError(
            f"{source}: {label} input_schema must declare type: object; a tool's "
            f"arguments are always an object"
        )
    description = entry.get("description")
    if not isinstance(description, str) or not description.strip():
        description = DESCRIPTION_PLACEHOLDER
    try:
        spec = ToolSpec(
            name=name,
            description=description,
            input_schema=copy.deepcopy(input_schema),
            returns=RETURNS_PLACEHOLDER,
        )
    except ValidationError as exc:
        reasons = "; ".join(error["msg"] for error in exc.errors())
        raise McpImportError(f"{source}: {label} cannot be a mock: {reasons}") from exc
    output_schema = entry.get("outputSchema")
    return ImportedTool(
        spec=spec,
        output_schema=copy.deepcopy(output_schema) if isinstance(output_schema, dict) else None,
    )


def render_tool_mocks(tools: Sequence[ImportedTool], *, only: Sequence[str] = ()) -> str:
    """Render the mocks as a `tools:` block ready to paste under a case.

    `only` keeps the named tools, in the order the listing had them. A name the
    listing does not carry is refused with every name it does, because the
    author's next step is to pick from that list.
    """
    if only:
        found = [tool.spec.name for tool in tools]
        missing = [name for name in only if name not in found]
        if missing:
            wanted = ", ".join(repr(name) for name in missing)
            declared = "\n".join(f"  {name}" for name in sorted(found)) or "  (none)"
            raise McpImportError(f"no tool named {wanted} in the listing; it declares:\n{declared}")
        keep = set(only)
        tools = [tool for tool in tools if tool.spec.name in keep]
    if not tools:
        return f"{HEADER}tools: []\n"
    return HEADER + "tools:\n" + "".join(_render_tool(tool) for tool in tools)


def _dump(value: object) -> str:
    # Insertion order preserved; PyYAML quotes any scalar that would resolve
    # to another type (`yes`, `on`, `1.20`), so the block survives the strict
    # loader unchanged.
    return yaml.safe_dump(
        value, sort_keys=False, allow_unicode=True, default_flow_style=False, width=_NO_WRAP
    )


def _render_tool(tool: ImportedTool) -> str:
    """One list item: name, description, schema, the output-schema note, `returns`."""
    spec = tool.spec
    lines = _dump(
        {"name": spec.name, "description": spec.description, "input_schema": spec.input_schema}
    ).splitlines()
    if tool.output_schema is not None:
        lines.append(_OUTPUT_SCHEMA_NOTE)
        # One line however large, so the note is grep-able and never splits a
        # schema across comment lines an author might half-delete.
        lines.append(f"#   {json.dumps(tool.output_schema, sort_keys=True)}")
    lines.extend(_dump({"returns": spec.returns}).splitlines())
    out = []
    for index, line in enumerate(lines):
        if not line:
            out.append("\n")  # a blank line inside a quoted multi-line scalar
            continue
        out.append(f"{'  - ' if index == 0 else '    '}{line}\n")
    return "".join(out)
