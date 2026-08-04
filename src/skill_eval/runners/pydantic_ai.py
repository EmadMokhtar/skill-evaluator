"""The PydanticAI adapter — the only module that imports an agent framework.

Everything the core sees is a plain `RunResult`. Provider failures are reported
through `RunResult.error`, never raised, so the orchestrator can tell an infra
problem (errored) apart from a low score (failed).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from skill_eval.models import EvalCase, RunResult, Skill, ToolCall
from skill_eval.runners.pricing import calculate_cost, provider_of
from skill_eval.runners.tools import build_mock_tool, build_skill_tool, skill_tool_name

DEFAULT_MODEL = "openai:gpt-4o-mini"

# Statuses worth another attempt: rate limits, request timeouts, conflicts and
# anything the provider blames on itself. A 401 or 404 will never fix itself.
_TRANSIENT_STATUSES = {408, 409, 429}

# In offered mode the agent must be able to *decline* the skill, so the system
# prompt says nothing about what the skill does -- only that tools exist and
# describe themselves. Anything more would be a nudge, and a nudged trigger
# rate measures the prompt rather than the skill.
OFFERED_PREAMBLE = (
    "You are a helpful assistant. Some capabilities are available to you as tools. "
    "Read their descriptions and use one when it genuinely fits the request. "
    "If none fits, just answer directly."
)

# A skill with no description and no instructions has nothing to say. Emitting
# the usual `# {name}` header anyway would put the skill's name into a baseline
# run's prompt, and the delta would then measure that leak rather than the
# skill. The rule keys on emptiness, not on the arm, so no runner has to know
# which arm it is serving -- a runner that *could* branch on the arm could cheat.
BASELINE_PREAMBLE = "You are a helpful assistant."


class RunnerDependencyError(Exception):
    """Raised when the optional extra providing this runner is not installed."""


def _require_pydantic_ai() -> None:
    try:
        import pydantic_ai  # noqa: F401
    except ImportError as exc:
        raise RunnerDependencyError(
            "the 'pydantic-ai' optional extra is required for this runner or judge: "
            "pip install 'skill-eval[pydantic-ai]'"
        ) from exc


def _system_prompt(skill: Skill) -> str:
    """The skill, as the agent sees it: identity first, then its instructions."""
    if not skill.description and not skill.instructions:
        return BASELINE_PREAMBLE
    header = f"# {skill.name}"
    if skill.description:
        header = f"{header}\n\n{skill.description}"
    return f"{header}\n\n{skill.instructions}".strip()


def _arguments(args: Any) -> dict[str, Any]:
    """Normalise tool-call arguments to a dict.

    Real providers send a JSON string; in-process models send a dict. An
    unparseable payload is preserved verbatim so a capture problem can never
    masquerade as a model problem.
    """
    if isinstance(args, dict):
        return args
    if not args:
        return {}
    try:
        parsed = json.loads(args)
    except (TypeError, ValueError):
        return {"_raw": str(args)}
    return parsed if isinstance(parsed, dict) else {"_raw": str(args)}


def _tool_calls(messages: list[Any]) -> list[ToolCall]:
    """Read the trajectory out of the message history, in order.

    The message history is authoritative: it records what the model asked for,
    including calls whose execution then failed.
    """
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    calls: list[ToolCall] = []
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                calls.append(ToolCall(name=part.tool_name, arguments=_arguments(part.args)))
    return calls


def _transcript(messages: list[Any]) -> list[dict[str, Any]]:
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    return ModelMessagesTypeAdapter.dump_python(messages, mode="json")


def _model_name(messages: list[Any], fallback: str) -> str:
    """The model the provider actually served, which may be a dated snapshot."""
    from pydantic_ai.messages import ModelResponse

    for message in reversed(messages):
        if isinstance(message, ModelResponse) and message.model_name:
            return message.model_name
    return fallback


def _is_transient(exc: Exception) -> bool:
    from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError

    if isinstance(exc, ModelHTTPError):
        return exc.status_code in _TRANSIENT_STATUSES or exc.status_code >= 500
    if isinstance(exc, ModelAPIError):
        return True
    return isinstance(exc, (TimeoutError, ConnectionError))


class PydanticAIRunner:
    """Runs a case through a real agent, behind the framework-agnostic protocol."""

    name = "pydantic-ai"
    needs_api_key = True

    def __init__(
        self,
        model: Any = DEFAULT_MODEL,
        temperature: float | str = 0.0,
        retries: int = 2,
        retry_backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._retries = retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._sleep = sleep

    def _model_settings(self) -> Any:
        """Reasoning models reject any temperature but 1, so 'unset' sends none."""
        from pydantic_ai.settings import ModelSettings

        if self._temperature == "unset":
            return None
        return ModelSettings(temperature=float(self._temperature))

    def _build_agent(self, skill: Skill, case: EvalCase) -> Any:
        from pydantic_ai import Agent, Tool

        mocks = [build_mock_tool(spec) for spec in case.tools]
        if case.mode == "offered":
            mocks.append(build_skill_tool(skill))
            instructions = OFFERED_PREAMBLE
        else:
            instructions = _system_prompt(skill)
        tools = [
            Tool.from_schema(
                mock.call,
                name=mock.name,
                description=mock.description,
                json_schema=mock.json_schema,
            )
            for mock in mocks
        ]
        return Agent(self._model, instructions=instructions, tools=tools)

    def _run_with_retries(self, agent: Any, task: str) -> Any:
        settings = self._model_settings()
        delay = self._retry_backoff_seconds
        attempt = 0
        while True:
            try:
                return agent.run_sync(task, model_settings=settings)
            except Exception as exc:
                if attempt >= self._retries or not _is_transient(exc):
                    raise
                self._sleep(delay)
                delay *= 2
                attempt += 1

    def run(self, skill: Skill, case: EvalCase) -> RunResult:
        _require_pydantic_ai()
        configured = self._model if isinstance(self._model, str) else ""
        offered = skill_tool_name(skill.name) if case.mode == "offered" else None
        started = time.monotonic()
        try:
            agent = self._build_agent(skill, case)
            result = self._run_with_retries(agent, case.task)
            messages = result.all_messages()
            usage = result.usage
            model_name = _model_name(messages, configured)
            cost_usd, cost_note = calculate_cost(usage, model_name, provider_of(configured))
            tool_calls = _tool_calls(messages)
            run_result = RunResult(
                output=result.output if isinstance(result.output, str) else str(result.output),
                tool_calls=tool_calls,
                transcript=_transcript(messages),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                latency_ms=int((time.monotonic() - started) * 1000),
                cost_usd=cost_usd,
                cost_note=cost_note,
                model=model_name,
                skill_triggered=(
                    None if offered is None else any(call.name == offered for call in tool_calls)
                ),
            )
        except Exception as exc:
            return RunResult(
                error=f"{type(exc).__name__}: {exc}",
                latency_ms=int((time.monotonic() - started) * 1000),
                model=configured,
            )

        return run_result
