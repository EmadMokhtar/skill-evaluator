"""The LangChain adapter -- the third module that imports an agent framework.

Everything the core sees is a plain `RunResult`. Provider failures are reported
through `RunResult.error`, never raised, so the orchestrator can tell an infra
problem (errored) apart from a low score (failed). The prompt rules and the
retry loop come from `runners/prompting.py` and `runners/retry.py`, which is
what keeps this adapter and the PydanticAI one measuring the same thing.

Framework imports are inside functions so `cli.py` can import this module --
to register the runner -- without the `langchain` extra installed.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from skill_lens.bundle import SkillBundle
from skill_lens.models import EvalCase, RunResult, Skill, ToolCall
from skill_lens.runners.base import RunnerDependencyError
from skill_lens.runners.preflight import UnsupportedBaseURL, check_trajectory_names
from skill_lens.runners.pricing import calculate_cost, provider_of
from skill_lens.runners.prompting import instructions
from skill_lens.runners.retry import run_with_retries, transient_status
from skill_lens.runners.tools import (
    AgentTool,
    build_bundle_tools,
    build_mock_tool,
    build_skill_tool,
    build_workspace_tools,
    skill_tool_name,
)
from skill_lens.scripts import ScriptRuntime
from skill_lens.workspace import Workspace

DEFAULT_MODEL = "openai:gpt-4o-mini"

# The SDKs LangChain wraps (`openai`, `anthropic`) name their network errors
# `APIConnectionError` / `APITimeoutError`. Those carry no status code and do
# not subclass the builtins, so the class name is the one provider-neutral
# signal left; matching a suffix rather than importing either SDK keeps the
# rule identical for a provider package that is not installed.
_TRANSIENT_NAME_SUFFIXES = ("ConnectionError", "TimeoutError")


def _require_langchain() -> None:
    try:
        import langchain  # noqa: F401
    except ImportError as exc:
        raise RunnerDependencyError(
            "the 'langchain' optional extra is required for this runner or judge: "
            "pip install 'skill-lens[langchain]'"
        ) from exc


def _is_transient(exc: Exception) -> bool:
    """Duck-typed, because LangChain does not normalise provider exceptions.

    An exception carrying an integer `status_code` -- both the `openai` and the
    `anthropic` SDK put one on their HTTP errors -- is judged by the shared
    status policy; the builtin timeout and connection errors, and the SDKs'
    own, are transient; nothing else is.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return transient_status(status)
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return type(exc).__name__.endswith(_TRANSIENT_NAME_SUFFIXES)


def _chat_model(model: Any, temperature: float | str, base_url: str = "") -> Any:
    """A `provider:model` string becomes a chat model; an instance is used as-is.

    Reasoning models reject any temperature but 1, so 'unset' sends none.

    A `base_url` is handed to the chat model's constructor as `base_url=`,
    which `ChatOpenAI` and `ChatAnthropic` take; with none, nothing is
    passed and the provider reads its own environment variable
    (`OPENAI_BASE_URL`) or its default, exactly as before this argument
    existed. A model *instance* already carries its client, so a `base_url`
    beside one is `UnsupportedBaseURL` rather than a URL silently dropped.
    """
    if not isinstance(model, str):
        if base_url:
            raise UnsupportedBaseURL(
                f"base_url {base_url!r} cannot apply to a model object "
                f"({type(model).__name__}); it takes a provider:model string"
            )
        return model
    import langchain.chat_models

    kwargs: dict[str, Any] = {}
    if temperature != "unset":
        kwargs["temperature"] = float(temperature)
    if base_url:
        kwargs["base_url"] = base_url
    # Looked up on the module at call time so the one framework call that
    # could refuse a `base_url` can be stood in for offline.
    return langchain.chat_models.init_chat_model(model, **kwargs)


def check_base_url(seat: str, model: Any, temperature: float | str, base_url: str) -> None:
    """Raise `UnsupportedBaseURL` unless `base_url` can be applied to `model`.

    Called from preflight, before any spend. Building the chat model is the
    check itself -- constructing a client touches no network -- and any
    refusal the constructor makes is reported under the seat (`runner
    langchain`, `judge langchain`) that would otherwise have surfaced it from
    the first case as an errored run. Without a `base_url` there is nothing
    to check, and the model is not built here.
    """
    if not base_url:
        return
    _require_langchain()
    try:
        _chat_model(model, temperature, base_url)
    except UnsupportedBaseURL as exc:
        raise UnsupportedBaseURL(f"{seat}: {exc}") from exc
    except Exception as exc:
        raise UnsupportedBaseURL(
            f"{seat}: base_url {base_url!r} cannot apply to model {model!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def _structured_tools(built: list[AgentTool]) -> list[Any]:
    """Wrap framework-neutral tools for LangChain.

    A dict `args_schema` is passed to the model as-is and the model's
    arguments are handed to the callable unvalidated, which is what every
    `AgentTool` expects: a call never raises, whatever the model sent.
    """
    from langchain_core.tools import StructuredTool

    return [
        StructuredTool.from_function(
            func=tool.call,
            name=tool.name,
            description=tool.description,
            args_schema=tool.json_schema,
        )
        for tool in built
    ]


def _ai_messages(messages: list[Any]) -> list[Any]:
    from langchain_core.messages import AIMessage

    return [message for message in messages if isinstance(message, AIMessage)]


def _tool_calls(messages: list[Any]) -> list[ToolCall]:
    """Read the trajectory out of the message history, in order.

    The history is authoritative: it records what the model asked for,
    including calls whose execution then failed. LangChain has already parsed
    the arguments into a dict, so no normalisation is needed.
    """
    return [
        ToolCall(name=str(call["name"]), arguments=dict(call["args"]))
        for message in _ai_messages(messages)
        for call in message.tool_calls
    ]


def _usage(messages: list[Any]) -> tuple[int, int]:
    """(input_tokens, output_tokens) summed over every model turn.

    LangChain reports usage per response rather than per run; a response
    without `usage_metadata` contributes nothing.
    """
    input_tokens = output_tokens = 0
    for message in _ai_messages(messages):
        usage = message.usage_metadata or {}
        input_tokens += int(usage.get("input_tokens", 0))
        output_tokens += int(usage.get("output_tokens", 0))
    return input_tokens, output_tokens


def _model_name(messages: list[Any], fallback: str) -> str:
    """The model the provider actually served, which may be a dated snapshot.

    Provider integrations do not agree on the key: `langchain-openai` writes
    `model_name`, and `langchain-anthropic` has written `model` (its API's own
    field) in releases the extra's floor still admits. Both are read, in that
    order, so an Anthropic run is priced and reported by what was served
    rather than falling back to the configured string.
    """
    for message in reversed(_ai_messages(messages)):
        metadata = message.response_metadata
        name = metadata.get("model_name") or metadata.get("model")
        if name:
            return str(name)
    return fallback


def _output(messages: list[Any]) -> str:
    replies = _ai_messages(messages)
    return replies[-1].text if replies else ""


def _transcript(messages: list[Any]) -> list[dict[str, Any]]:
    return [message.model_dump(mode="json") for message in messages]


def _cost(
    input_tokens: int, output_tokens: int, model_name: str, configured: str
) -> tuple[float, str]:
    """Price a run. `genai_prices.Usage` is the shape `calculate_cost` prices.

    Built here rather than in `pricing.py` so that module stays free of an
    import only the extras provide; the fallback note mirrors its own.
    """
    try:
        from genai_prices import Usage
    except ImportError:  # pragma: no cover - the extra declares genai-prices
        return 0.0, "genai-prices is not installed; cost not calculated"
    usage = Usage(input_tokens=input_tokens, output_tokens=output_tokens)
    return calculate_cost(usage, model_name, provider_of(configured))


class LangChainRunner:
    """Runs a case through LangChain's prebuilt agent, behind the protocol."""

    name = "langchain"
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

    def _build_agent(
        self,
        skill: Skill,
        case: EvalCase,
        workspace: Workspace | None,
        scripts: ScriptRuntime | None,
    ) -> Any:
        from langchain.agents import create_agent

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
        return create_agent(
            _chat_model(self._model, self._temperature, self._base_url),
            tools=_structured_tools(built),
            system_prompt=instructions(skill, case, workspace is not None),
        )

    def _invoke(self, build_agent: Callable[[], Any], task: str) -> list[Any]:
        """Build a fresh agent for every attempt, then invoke it.

        A retry is a new conversation, and its tools must be new too: a mock
        whose `returns:` is consumed in call order keeps its counter in the
        built tool, so an agent reused across attempts would hand the second
        attempt's first call the sequence's second entry.
        """
        from langchain_core.messages import HumanMessage

        state = run_with_retries(
            lambda: build_agent().invoke({"messages": [HumanMessage(task)]}),
            _is_transient,
            self._retries,
            self._retry_backoff_seconds,
            self._sleep,
        )
        return list(state["messages"])

    def preflight(self, skills: list[Skill], cases_by_skill: dict[str, list[EvalCase]]) -> None:
        """Refuse, before any spend, a `trajectory:` naming a tool this runner
        cannot offer, and a `base_url` its chat model cannot take.

        This runner offers a case its mock tools and, with a workspace, the
        built-ins -- so the first check is `check_trajectory_names`; the
        second is `check_base_url`.
        """
        check_trajectory_names(self.name, cases_by_skill)
        check_base_url(f"runner {self.name}", self._model, self._temperature, self._base_url)

    def run(
        self,
        skill: Skill,
        case: EvalCase,
        workspace: Workspace | None = None,
        scripts: ScriptRuntime | None = None,
    ) -> RunResult:
        _require_langchain()
        configured = self._model if isinstance(self._model, str) else ""
        offered = skill_tool_name(skill.name) if case.mode == "offered" else None
        started = time.monotonic()
        try:
            messages = self._invoke(
                lambda: self._build_agent(skill, case, workspace, scripts), case.task
            )
            input_tokens, output_tokens = _usage(messages)
            model_name = _model_name(messages, configured)
            cost_usd, cost_note = _cost(input_tokens, output_tokens, model_name, configured)
            tool_calls = _tool_calls(messages)
            return RunResult(
                output=_output(messages),
                tool_calls=tool_calls,
                transcript=_transcript(messages),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
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
