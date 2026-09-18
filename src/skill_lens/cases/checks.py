"""Checks every eval-authoring file kind shares.

An eval file and a tool library are both the author's input, and the same
mistakes are refused in both: a scaffold placeholder left in place, and a
mock tool whose declared schema no provider would register. `cases/loader.py`
and `cases/tool_libraries.py` both import from here and this module imports
neither, so the loader can import the library module without a cycle.
"""

from __future__ import annotations

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from skill_lens.models import ToolSpec

# The placeholder `skill-lens init` and `skill-lens mcp-import` write into
# every field the author has to fill in. Living here rather than in
# scaffold.py makes it the *loader's* guarantee: a hand-written stub is
# refused exactly like a generated one.
UNFILLED_SENTINEL = "TODO(skill-lens)"


def find_unfilled(raw: object, trail: str = "", seen: frozenset[int] = frozenset()) -> str | None:
    """The trail ("tools[0].returns") of the first scaffold placeholder in
    `raw`, or None when there is none.

    Mapping keys are checked as well as values: `workspace.files` is keyed by
    filename, and a key hit reports the mapping's own trail. `seen` holds the
    `id()` of every dict/list currently being walked, so a self-referential
    YAML anchor (a node that contains itself) is skipped instead of recursing
    forever -- still a malformed file, but one that must exit cleanly rather
    than crash with a RecursionError.
    """
    if isinstance(raw, str):
        return trail if UNFILLED_SENTINEL in raw else None
    if isinstance(raw, dict):
        if id(raw) in seen:
            return None
        seen = seen | {id(raw)}
        for key, value in raw.items():
            found = find_unfilled(key, trail, seen)
            if found is None:
                found = find_unfilled(value, f"{trail}.{key}" if trail else str(key), seen)
            if found is not None:
                return found
        return None
    if isinstance(raw, list):
        if id(raw) in seen:
            return None
        seen = seen | {id(raw)}
        for position, value in enumerate(raw):
            found = find_unfilled(value, f"{trail}[{position}]", seen)
            if found is not None:
                return found
    return None


def check_tool_schema(tool: ToolSpec) -> None:
    """Raise ValueError unless the tool's declared schema is one a provider
    would register.

    A tool declares its arguments one of two ways -- the `parameters:`
    shorthand or a full `input_schema:` -- never both, and a declared schema
    has to be valid JSON Schema whose top level is an object. All three
    mistakes are the author's; the caller prefixes the message with where the
    tool was declared, so the same text serves a case and a library.
    """
    if tool.input_schema is None:
        return
    if tool.parameters:
        raise ValueError("declares both parameters and input_schema; choose one.")
    try:
        Draft202012Validator.check_schema(tool.input_schema)
    except SchemaError as exc:
        raise ValueError(f"has an invalid input_schema: {exc.message}") from exc
    if tool.input_schema.get("type") != "object":
        raise ValueError(
            "input_schema must declare type: object; a tool's arguments are always an object."
        )
