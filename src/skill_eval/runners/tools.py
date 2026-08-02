"""Build framework-neutral mock tools from a case's tool declarations.

Nothing here knows about any agent framework: a MockTool is a name, a JSON
schema and a callable, which every adapter can register in its own way.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from skill_eval.models import ToolSpec


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
