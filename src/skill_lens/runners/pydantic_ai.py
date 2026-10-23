"""The PydanticAI adapter — the only module that imports an agent framework.

Everything the core sees is a plain `RunResult`. Provider failures are reported
through `RunResult.error`, never raised, so the orchestrator can tell an infra
problem (errored) apart from a low score (failed).
"""

from __future__ import annotations

import functools
import inspect
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from skill_lens.bundle import SkillBundle
from skill_lens.models import EvalCase, RunResult, Skill, ToolCall
from skill_lens.runners.base import RunnerDependencyError
from skill_lens.runners.limits import no_usage_notes, stop_note, token_limit
from skill_lens.runners.preflight import UnsupportedBaseURL, check_trajectory_names
from skill_lens.runners.pricing import calculate_cost, provider_of, unpriced_model
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


def resolve_model(model: Any, base_url: str) -> Any:
    """The model PydanticAI will run: `model` as given, or, with a `base_url`,
    the same `provider:name` served from that endpoint.

    With no `base_url` the string goes to `infer_model` untouched, so the
    provider reads its own environment variable (`OPENAI_BASE_URL`,
    `OLLAMA_BASE_URL`) or its default exactly as before this argument
    existed. With one, the provider is constructed here with `base_url=`,
    which is why a provider whose constructor has no such parameter
    (`deepseek`, `azure`, `openrouter`, ...) is `UnsupportedBaseURL` rather
    than a URL that is silently dropped -- and why a model *object* is too:
    it already carries a provider, and there is nothing to point elsewhere.
    No network is touched: a provider builds an HTTP client, nothing more.
    """
    if not base_url:
        # Before any framework import: this branch must behave exactly as
        # the code did before `base_url` existed, missing extra included.
        return model
    from pydantic_ai.models import infer_model
    from pydantic_ai.providers import infer_provider_class

    if not isinstance(model, str):
        raise UnsupportedBaseURL(
            f"base_url {base_url!r} cannot apply to a model object "
            f"({type(model).__name__}); it takes a provider:model string"
        )

    def provider(name: str) -> Any:
        cls = infer_provider_class(name)
        if "base_url" not in inspect.signature(cls.__init__).parameters:
            raise UnsupportedBaseURL(
                f"base_url {base_url!r} cannot apply to model {model!r}: the {name!r} "
                f"provider ({cls.__name__}) takes no endpoint; unset base_url and use "
                f"the provider's own environment variable instead"
            )
        return cls(base_url=base_url)

    return infer_model(model, provider_factory=provider)


@functools.cache
def _meter_class() -> type:
    """A model wrapper that records every response its model returns.

    Built on first use, because the framework is imported lazily. PydanticAI
    raises `UsageLimitExceeded` *before* it appends the response that crossed
    the limit to the message history, so the history alone would under-count
    a stopped run by exactly that response. Every response passes through
    `request`, which makes `responses` the complete record of what the
    attempt was billed for -- on an attempt that raised as much as on one
    that finished.
    """
    from pydantic_ai.models.wrapper import WrapperModel

    class Meter(WrapperModel):
        def __init__(self, wrapped: Any) -> None:
            super().__init__(wrapped)
            self.responses: list[Any] = []

        async def request(self, messages: Any, model_settings: Any, parameters: Any) -> Any:
            response = await super().request(messages, model_settings, parameters)
            self.responses.append(response)
            return response

    return Meter


@dataclass
class _Attempt:
    """One attempt at a run: its metered model, and the history it built.

    `history` is PydanticAI's own record, captured with
    `capture_run_messages`, so an attempt that raised still has the turns it
    got through.
    """

    meter: Any
    history: list[Any] = field(default_factory=list)


def _spend(attempt: _Attempt | None, runner: str, configured: str) -> dict[str, Any]:
    """What one attempt's responses were billed for, as `RunResult` fields.

    No response means nothing was billed: a measured zero, so it is not
    priced and gets no note. Responses that carried no usage at all are no
    measurement, and say so (`no_usage_notes`).
    """
    from pydantic_ai.usage import RunUsage

    responses = attempt.meter.responses if attempt is not None else []
    if not responses:
        return {"model": configured}
    usage = RunUsage()
    for response in responses:
        usage.incr(response.usage)
    model_name = _model_name(responses, configured)
    if usage.input_tokens + usage.output_tokens == 0:
        return {"model": model_name, **no_usage_notes(runner)}
    cost_usd, cost_note = calculate_cost(usage, model_name, provider_of(configured))
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cost_usd": cost_usd,
        "cost_note": cost_note,
        "model": model_name,
    }


def _retry_cost(attempts: list[_Attempt], runner: str, configured: str) -> tuple[float, str]:
    """What the attempts a retry discarded cost, and the first note saying
    part of that could not be priced."""
    cost, note = 0.0, ""
    for attempt in attempts:
        spend = _spend(attempt, runner, configured)
        cost += spend.get("cost_usd", 0.0)
        note = note or spend.get("cost_note", "")
    return cost, note


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


def _responses(messages: list[Any]) -> list[Any]:
    from pydantic_ai.messages import ModelResponse

    return [message for message in messages if isinstance(message, ModelResponse)]


def _text(response: Any) -> str:
    """The text a response carried: what a stopped run had said when it stopped."""
    from pydantic_ai.messages import TextPart

    return "".join(part.content for part in response.parts if isinstance(part, TextPart))


def _stopped_history(attempt: _Attempt) -> list[Any]:
    """The history of an attempt PydanticAI stopped at its token limit.

    PydanticAI raises `UsageLimitExceeded` before it appends the response
    that crossed the limit, so that response is taken from the meter, where
    every response is recorded as it arrives -- appended only when the
    history does not already end with it.
    """
    messages = list(attempt.history)
    crossing = attempt.meter.responses[-1]
    if not messages or messages[-1] is not crossing:
        messages.append(crossing)
    return messages


def _is_usage_limit(exc: Exception) -> bool:
    from pydantic_ai.exceptions import UsageLimitExceeded

    return isinstance(exc, UsageLimitExceeded)


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
        base_url: str = "",
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._retries = retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._sleep = sleep
        self._base_url = base_url

    def unpriced(self) -> str:
        """Why this runner's model has no price, or ""; see `unpriced_model`."""
        return unpriced_model(self._model)

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
        # Metered, so an attempt that raises still says what it spent; the
        # agent's `model` is the meter, which is how `run` reads it back.
        return Agent(
            _meter_class()(resolve_model(self._model, self._base_url)),
            instructions=instructions(skill, case, workspace is not None),
            tools=tools,
        )

    def preflight(self, skills: list[Skill], cases_by_skill: dict[str, list[EvalCase]]) -> None:
        """Refuse, before any spend, a `trajectory:` naming a tool this runner
        cannot offer, and a `base_url` its provider cannot take.

        This runner offers a case its mock tools and, with a workspace, the
        built-ins -- so the first check is `check_trajectory_names`. The
        second resolves the model once, which builds a client and nothing
        more, so an `UnsupportedBaseURL` is raised here rather than from the
        first case as an errored run.
        """
        check_trajectory_names(self.name, cases_by_skill)
        if not self._base_url:
            return
        _require_pydantic_ai()
        try:
            resolve_model(self._model, self._base_url)
        except UnsupportedBaseURL as exc:
            raise UnsupportedBaseURL(f"runner {self.name}: {exc}") from exc
        except Exception as exc:
            # The framework's own refusal -- an unknown provider prefix, a
            # model id with no prefix -- has no RunResult to land in here, so
            # it becomes the setup error rather than a traceback.
            raise UnsupportedBaseURL(
                f"runner {self.name}: base_url {self._base_url!r} cannot apply to model "
                f"{self._model!r}: {type(exc).__name__}: {exc}"
            ) from exc

    def run(
        self,
        skill: Skill,
        case: EvalCase,
        workspace: Workspace | None = None,
        scripts: ScriptRuntime | None = None,
    ) -> RunResult:
        """Run the case, building a fresh agent for every attempt.

        A retry is a new conversation, and its tools must be new too: a mock
        whose `returns:` is consumed in call order keeps its counter in the
        built tool, so an agent reused across attempts would hand the second
        attempt's first call the sequence's second entry.

        The case's `max_tokens` becomes PydanticAI's `total_tokens_limit`,
        checked after every response, so a runaway case stops as soon as it
        crosses its budget. Every other limit keeps PydanticAI's default,
        `request_limit=50` included -- the same limits a call with no
        `usage_limits` gets. A stopped attempt is returned as far as it got,
        with `stop_note` set, and is never retried.

        Every figure on the result is the last attempt's, so `budget:`
        measures one run of the skill; what the attempts before it cost is
        `retry_cost_usd`.
        """
        _require_pydantic_ai()
        from pydantic_ai import capture_run_messages
        from pydantic_ai.usage import UsageLimits

        configured = self._model if isinstance(self._model, str) else ""
        offered = skill_tool_name(skill.name) if case.mode == "offered" else None
        settings = self._model_settings()
        limit = token_limit(case)
        limits = UsageLimits(total_tokens_limit=limit)
        attempts: list[_Attempt] = []

        def attempt() -> Any:
            agent = self._build_agent(skill, case, workspace, scripts)
            record = _Attempt(meter=agent.model)
            attempts.append(record)
            with capture_run_messages() as history:
                record.history = history
                return agent.run_sync(case.task, model_settings=settings, usage_limits=limits)

        def triggered(tool_calls: list[ToolCall]) -> bool | None:
            return None if offered is None else any(call.name == offered for call in tool_calls)

        started = time.monotonic()
        try:
            result = run_with_retries(
                attempt, _is_transient, self._retries, self._retry_backoff_seconds, self._sleep
            )
            retry_cost, retry_note = _retry_cost(attempts[:-1], self.name, configured)
            messages = result.all_messages()
            usage = result.usage
            model_name = _model_name(messages, configured)
            if usage.input_tokens + usage.output_tokens == 0 and _responses(messages):
                priced: dict[str, Any] = no_usage_notes(self.name)
            else:
                cost_usd, cost_note = calculate_cost(usage, model_name, provider_of(configured))
                priced = {"cost_usd": cost_usd, "cost_note": cost_note}
            priced["cost_note"] = priced["cost_note"] or retry_note
            tool_calls = _tool_calls(messages)
            return RunResult(
                output=result.output if isinstance(result.output, str) else str(result.output),
                tool_calls=tool_calls,
                transcript=_transcript(messages),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                latency_ms=int((time.monotonic() - started) * 1000),
                model=model_name,
                skill_triggered=triggered(tool_calls),
                retry_cost_usd=retry_cost,
                **priced,
            )
        except Exception as exc:
            last = attempts[-1] if attempts else None
            retry_cost, retry_note = _retry_cost(attempts[:-1], self.name, configured)
            spend = _spend(last, self.name, configured)
            spend["cost_note"] = spend.get("cost_note", "") or retry_note
            latency_ms = int((time.monotonic() - started) * 1000)
            tokens = spend.get("input_tokens", 0) + spend.get("output_tokens", 0)
            if last is not None and limit is not None and tokens > limit and _is_usage_limit(exc):
                messages = _stopped_history(last)
                tool_calls = _tool_calls(messages)
                return RunResult(
                    output=_text(last.meter.responses[-1]),
                    tool_calls=tool_calls,
                    transcript=_transcript(messages),
                    latency_ms=latency_ms,
                    skill_triggered=triggered(tool_calls),
                    stop_note=stop_note(self.name),
                    retry_cost_usd=retry_cost,
                    **spend,
                )
            # A run that failed twenty turns in was billed for what it got
            # back, so the attempt's spend stays on the errored result.
            return RunResult(
                error=f"{type(exc).__name__}: {exc}",
                latency_ms=latency_ms,
                retry_cost_usd=retry_cost,
                **spend,
            )
