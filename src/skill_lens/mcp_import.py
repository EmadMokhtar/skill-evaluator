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
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from skill_lens.cases.loader import UNFILLED_SENTINEL
from skill_lens.models import ToolSpec

# The placeholders an imported block carries. Both start with the loader's
# sentinel, so a pasted block refuses to run until the author fills them --
# the same rule `skill-lens init` relies on.
DESCRIPTION_PLACEHOLDER = f"{UNFILLED_SENTINEL} what this tool does"
RETURNS_PLACEHOLDER = f"{UNFILLED_SENTINEL} the JSON this tool returns"

_ACCEPTED_SHAPES = (
    'a JSON-RPC response ({"result": {"tools": [...]}}), its result ({"tools": [...]}), '
    "or the bare tools array ([...])"
)


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
        if isinstance(data, dict) and isinstance(data.get("tools"), list):
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
