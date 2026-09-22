"""Checks every eval-authoring file kind shares.

An eval file and a tool library are both the author's input, and the same
mistakes are refused in both: a scaffold placeholder left in place, a mock
tool whose declared schema no provider would register, and a `returns:`
lookup with an entry that could never answer a call. `cases/loader.py` and
`cases/tool_libraries.py` both import from here and this module imports
neither, so the loader can import the library module without a cycle.
"""

from __future__ import annotations

import re

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from skill_lens.models import ToolResponse, ToolSpec

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


# The harness's own words for a mock tool's `returns:` -- data the agent saw
# and the judge never does. A rubric line naming it is unverifiable as
# written: the judge grades the task, the `expected` text, the response and
# the files `judge.artifacts` names, and nothing else. Each alternative
# requires a second word, so a rubric about a testing skill ("proposes a mock
# for the HTTP client") or about the response ("names the tool it would use",
# "mentions no tool calls") is not caught. `\b` on both sides keeps "mockup"
# and "toolset" out.
_HIDDEN_DATA_NOUNS = r"(?:data|responses?|results?|returns?|values?|outputs?|tools?)"
_HIDDEN_DATA_REFERENCE = re.compile(
    r"\b(?:"
    rf"mock(?:ed|s)?\s+{_HIDDEN_DATA_NOUNS}"
    r"|(?:tools?|mocks?)(?:'s?)?\s+"
    r"(?:returned|returns?(?:\s+values?)?|responses?|results?|outputs?|data|values?)"
    r"|returned\s+by\s+(?:the|a|any|each|every)\s+(?:tool|mock)"
    r")\b",
    re.IGNORECASE,
)


def find_hidden_data_reference(text: str) -> str | None:
    """The phrase in a rubric line that names a case's mock tool data, as the
    author wrote it, or None when the line names none.

    The judge is never shown what a mock tool returned, so a check phrased
    against it ("does not invent any detail not present in the mocked data")
    can only be passed by a judge that ignores its own "fail when ambiguous"
    rule. Refusing it at load time is what stops a lenient judge from
    passing a check no judge could verify. The vocabulary is fixed and
    documented, so an author can predict a refusal; it is a heuristic, and
    rewording is the escape hatch.
    """
    match = _HIDDEN_DATA_REFERENCE.search(text)
    return match.group(0) if match else None


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


def _closed_keys(tool: ToolSpec) -> frozenset[str] | None:
    """The argument names a call to `tool` can carry, or None when the
    schema is open and any name might arrive.

    The `parameters:` shorthand is closed by construction. A declared
    `input_schema` is closed only when it says `additionalProperties: false`
    and lists its properties; otherwise the server it stands in for accepts
    names it does not list, and so may the author's `when:`.
    """
    if tool.input_schema is None:
        return frozenset(tool.parameters)
    properties = tool.input_schema.get("properties")
    if tool.input_schema.get("additionalProperties") is False and isinstance(properties, dict):
        return frozenset(str(key) for key in properties)
    return None


def _lookup_entries(tool: ToolSpec) -> list[ToolResponse] | None:
    """The tool's `returns:` as lookup entries, or None for the other two
    shapes. `ToolSpec` already guarantees a list holds one kind of entry."""
    if isinstance(tool.returns, list) and tool.returns:
        return [entry for entry in tool.returns if isinstance(entry, ToolResponse)] or None
    return None


def check_tool_returns(tool: ToolSpec) -> None:
    """Raise ValueError unless every entry of a `returns:` lookup could
    answer some call.

    Only the lookup shape has anything to check. A `when:` key the tool's
    closed schema can never carry would never match, so it is a mistake
    rather than a scenario; an empty `when:` is the fallback spelled
    confusingly, so the author is told to drop the key; and an entry that an
    earlier entry already answers -- a fallback above it, the same `when:`,
    or a `when:` it only narrows -- can never be reached, because the first
    match wins. The caller prefixes the message with where the tool was
    declared, so the same text serves a case and a library.
    """
    entries = _lookup_entries(tool)
    if entries is None:
        return
    allowed = _closed_keys(tool)
    for position, entry in enumerate(entries):
        if entry.when is not None and not entry.when:
            raise ValueError(
                f"returns[{position}].when is empty, which matches every call; drop the "
                f"when: key to declare a fallback."
            )
        if allowed is not None:
            for key in entry.when or {}:
                if key not in allowed:
                    declares = ", ".join(sorted(allowed)) or "no parameters"
                    raise ValueError(
                        f"returns[{position}].when names {key!r}, which a call to this "
                        f"tool can never carry (it declares {declares}); the entry "
                        f"could never match."
                    )
        for earlier, other in enumerate(entries[:position]):
            if other.matches(entry.when or {}):
                raise ValueError(
                    f"returns[{position}] can never be reached: returns[{earlier}] "
                    f"already answers every call it would match, and the first match "
                    f"wins."
                )


def check_tool(tool: ToolSpec) -> None:
    """Every load-time rule for one mock tool: its schema, then its returns."""
    check_tool_schema(tool)
    check_tool_returns(tool)
