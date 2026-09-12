"""A scripted chat model for the LangChain adapters' zero-cost tests.

The counterpart of PydanticAI's `FunctionModel`. langchain-core's own
`GenericFakeChatModel` cannot be used: `create_agent` calls `bind_tools`, and
`with_structured_output` refuses a model that has not overridden it -- the
generic fake overrides neither. This subclass answers each turn by calling
`reply(messages, turn)`, records what it was asked and which tools were
bound, and lets `bind_tools` return itself so both the agent loop and the
judge's structured output can be driven offline.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolCall
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

Reply = Callable[[list[BaseMessage], int], AIMessage]

# Every scripted reply carries usage so the adapter's token arithmetic is
# exercised; a real provider always sends it.
USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


class StatusError(Exception):
    """An exception shaped like the openai/anthropic SDKs' HTTP errors."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class FunctionChatModel(BaseChatModel):
    """Answers turn `n` with `reply(messages, n)`; `bind_tools` returns itself."""

    reply: Reply
    turns: list[list[BaseMessage]] = Field(default_factory=list)
    bound_tools: list[list[str]] = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        turn = len(self.turns)
        self.turns.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.reply(list(messages), turn))])

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self.bound_tools.append(
            [getattr(tool, "name", getattr(tool, "__name__", "")) for tool in tools]
        )
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted"


def scripted(*replies: AIMessage) -> FunctionChatModel:
    """A model that replays `replies` in order, then repeats the last one."""

    def reply(messages: list[BaseMessage], turn: int) -> AIMessage:
        return replies[min(turn, len(replies) - 1)]

    return FunctionChatModel(reply=reply)


def text(content: str, **metadata: Any) -> AIMessage:
    """A plain text reply. `model_name=` lands in `response_metadata`."""
    return AIMessage(content=content, usage_metadata=dict(USAGE), response_metadata=dict(metadata))


def tool_call(name: str, args: dict[str, Any], **metadata: Any) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[ToolCall(name=name, args=args, id=f"call-{name}")],
        usage_metadata=dict(USAGE),
        response_metadata=dict(metadata),
    )


def system_text(turn: list[BaseMessage]) -> str:
    """The system prompt the model saw on one turn ('' when there was none)."""
    return str(turn[0].content) if turn and turn[0].type == "system" else ""


def tool_results(turn: list[BaseMessage]) -> dict[str, str]:
    """Every tool result the model saw on one turn, by tool name."""
    return {str(m.name): str(m.content) for m in turn if m.type == "tool"}
