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


_EMPTY_SCHEMA = {
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
    """
    cleaned = "".join(char if char.isalnum() else "_" for char in skill_name)
    if not cleaned.strip("_"):
        return "skill"
    if cleaned[0].isdigit():
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
        json_schema=dict(_EMPTY_SCHEMA),
        call=call,
    )
