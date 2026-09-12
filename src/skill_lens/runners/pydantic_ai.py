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

from skill_lens.bundle import SkillBundle
from skill_lens.models import EvalCase, RunResult, Skill, ToolCall
from skill_lens.runners.base import RunnerDependencyError
from skill_lens.runners.pricing import calculate_cost, provider_of
from skill_lens.runners.prompting import instructions
from skill_lens.runners.retry import run_with_retries, transient_status
from skill_lens.runners.tools import (
    build_bundle_tools,
    build_mock_tool,
    build_skill_tool,
    build_workspace_tools,
    skill_tool_name,
)
from skill_lens.scripts import ScriptRuntime
from skill_lens.workspace import Workspace

DEFAULT_MODEL = "openai:gpt-4o-mini"


def _require_pydantic_ai() -> None:
    try:
        import pydantic_ai  # noqa: F401
    except ImportError as exc:
        raise RunnerDependencyError(
            "the 'pydantic-ai' optional extra is required for this runner or judge: "
            "pip install 'skill-lens[pydantic-ai]'"
        ) from exc


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
        return transient_status(exc.status_code)
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

    def _build_agent(
        self,
        skill: Skill,
        case: EvalCase,
        workspace: Workspace | None,
        scripts: ScriptRuntime | None,
    ) -> Any:
        from pydantic_ai import Agent, Tool

        built = [build_mock_tool(spec) for spec in case.tools]
        if case.mode == "offered":
            built.append(build_skill_tool(skill))
        if workspace is not None:
            built.extend(build_workspace_tools(workspace))
            # The bundle tools need the workspace: it is the script's working
            # directory and the sandbox's only writable area. Registered in
            # offered mode too -- an agent that declines the skill has no
            # reason to call them, and one that triggers it needs them exactly
            # as a loaded case does.
            if skill.bundle_root is not None:
                built.extend(build_bundle_tools(SkillBundle(skill.bundle_root), workspace, scripts))
        tools = [
            Tool.from_schema(
                agent_tool.call,
                name=agent_tool.name,
                description=agent_tool.description,
                json_schema=agent_tool.json_schema,
            )
            for agent_tool in built
        ]
        return Agent(
            self._model,
            instructions=instructions(skill, case, workspace is not None),
            tools=tools,
        )

    def _run_with_retries(self, agent: Any, task: str) -> Any:
        settings = self._model_settings()
        return run_with_retries(
            lambda: agent.run_sync(task, model_settings=settings),
            _is_transient,
            self._retries,
            self._retry_backoff_seconds,
            self._sleep,
        )

    def run(
        self,
        skill: Skill,
        case: EvalCase,
        workspace: Workspace | None = None,
        scripts: ScriptRuntime | None = None,
    ) -> RunResult:
        _require_pydantic_ai()
        configured = self._model if isinstance(self._model, str) else ""
        offered = skill_tool_name(skill.name) if case.mode == "offered" else None
        started = time.monotonic()
        try:
            agent = self._build_agent(skill, case, workspace, scripts)
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
