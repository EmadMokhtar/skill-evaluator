"""Build framework-neutral mock tools from a case's tool declarations.

Nothing here knows about any agent framework: a MockTool is a name, a JSON
schema and a callable, which every adapter can register in its own way.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from skill_eval.models import Skill, ToolSpec


@dataclass(frozen=True)
class MockTool:
    """A tool the agent may call. Calling it has no side effects."""

    name: str
    description: str
    json_schema: dict[str, Any]
    call: Callable[..., str]


def build_mock_tool(spec: ToolSpec) -> MockTool:
    """Turn a declared ToolSpec into a callable plus its JSON schema.

    Parameter types are already constrained by `ToolSpec`, so an unsupported
    type is rejected by the case loader as an authoring error long before it
    reaches here.
    """
    properties = {name: {"type": type_name} for name, type_name in spec.parameters.items()}
    returns = spec.returns

    def call(**_arguments: Any) -> str:
        """Return the canned value, whatever the model passed in."""
        return returns

    return MockTool(
        name=spec.name,
        description=spec.description,
        json_schema={
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
        call=call,
    )


def _empty_schema() -> dict[str, Any]:
    """A fresh, parameter-free JSON schema.

    Built inline per call -- like `build_mock_tool` already does for its own
    schema -- so no two built tools ever share a mutable `properties` dict or
    `required` list. A dict literal, however it was copied, does not protect
    against mutating what's *inside* it.
    """
    return {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }


def skill_tool_name(skill_name: str) -> str:
    """The identifier a skill is offered under: 'order-support' -> 'order_support'.

    Deterministic, because both the runner (which registers the tool) and the
    case loader (which rejects a case tool that would collide with it) have to
    agree on the answer without talking to each other.

    Must be total: every possible input has to yield a valid Python
    identifier. `char.isalnum()` / `char.isdigit()` are not safe tests for
    this -- both return True for Unicode "Other Number" (No) characters
    (superscripts, circled digits, vulgar fractions) that are nonetheless
    illegal in an identifier in any position. Asking Python directly avoids
    that trap: `f"a{char}".isidentifier()` is exactly "valid in a non-leading
    position", and `char.isidentifier()` is exactly "valid in the leading
    position".

    Distinct names can collapse to the same identifier (e.g. "a-b", "a_b" and
    "a b" all become "a_b"). That's an accepted, deliberate tradeoff: only one
    skill is offered as a tool per run, so there's never a same-run collision
    to resolve.
    """
    cleaned = "".join(char if f"a{char}".isidentifier() else "_" for char in skill_name)
    if not cleaned.strip("_"):
        return "skill"
    if not cleaned[0].isidentifier():
        cleaned = f"skill_{cleaned}"
    return cleaned


def build_skill_tool(skill: Skill) -> MockTool:
    """The skill itself, offered as a tool the agent may decline to use.

    Calling it returns the skill's instructions, so an offered run only has the
    skill once the agent chose it -- and the rest of the run proceeds
    realistically with it loaded, rather than the agent acting on instructions
    it never received.
    """
    instructions = skill.instructions

    def call(**_arguments: Any) -> str:
        return instructions

    return MockTool(
        name=skill_tool_name(skill.name),
        description=skill.description,
        json_schema=_empty_schema(),
        call=call,
    )
