"""The first real adapter, exercised offline with a scripted model."""

import sys
from pathlib import Path

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from skill_lens.models import EvalCase, Skill, ToolSpec
from skill_lens.runners.base import Runner
from skill_lens.runners.prompting import BASELINE_PREAMBLE, OFFERED_PREAMBLE
from skill_lens.runners.pydantic_ai import PydanticAIRunner
from skill_lens.runners.tools import (
    BUILTIN_TOOL_NAMES,
    BUNDLE_TOOL_NAMES,
    WORKSPACE_TOOL_NAMES,
    skill_tool_name,
)
from skill_lens.scripts import SandboxStatus, ScriptPolicy, ScriptRuntime
from skill_lens.workspace import Workspace

SKILL = Skill(
    name="order-support",
    description="Handle refund requests",
    instructions="Always look up the order first.",
    path=Path("."),
)

# Not a fixed point of a naive `name.replace("-", "_")`: digits, a space and
# punctuation all need `skill_tool_name`'s escaping, unlike "order-support"
# above which a hand-rolled replace would also get right by accident.
WEIRD_SKILL = Skill(
    name="123 weird name!",
    description="Do something odd",
    instructions="Weird instructions.",
    path=Path("."),
)


def scripted(*responses: ModelResponse) -> FunctionModel:
    """A model that replays `responses` in order, then repeats the last one."""
    calls = {"n": 0}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        index = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[index]

    return FunctionModel(reply)


def text(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])


def tool_call(name: str, args) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args)])


def case(**kwargs) -> EvalCase:
    kwargs.setdefault("name", "c")
    kwargs.setdefault("task", "refund order 1234")
    return EvalCase(**kwargs)


def test_the_final_text_becomes_the_output():
    runner = PydanticAIRunner(model=scripted(text("Order 1234 was delivered.")))
    result = runner.run(SKILL, case())
    assert result.output == "Order 1234 was delivered."
    assert result.errored is False


def test_the_runner_registers_its_name():
    assert PydanticAIRunner(model=scripted(text("x"))).name == "pydantic-ai"


def test_declared_tools_are_offered_to_the_model():
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        seen["tools"] = sorted(tool.name for tool in info.function_tools)
        return text("done")

    runner = PydanticAIRunner(model=FunctionModel(reply))
    runner.run(
        SKILL,
        case(tools=[ToolSpec(name="lookup_order"), ToolSpec(name="issue_refund")]),
    )
    assert seen["tools"] == ["issue_refund", "lookup_order"]


def test_the_skill_instructions_reach_the_model():
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        seen["instructions"] = messages[0].instructions or ""
        return text("done")

    PydanticAIRunner(model=FunctionModel(reply)).run(SKILL, case())
    assert "Always look up the order first." in seen["instructions"]
    assert "order-support" in seen["instructions"]


def test_tool_calls_are_captured_in_order():
    runner = PydanticAIRunner(
        model=scripted(
            tool_call("lookup_order", {"order_id": "1234"}),
            tool_call("check_policy", {}),
            text("Refund declined."),
        )
    )
    result = runner.run(
        SKILL,
        case(
            tools=[
                ToolSpec(name="lookup_order", parameters={"order_id": "string"}),
                ToolSpec(name="check_policy"),
            ]
        ),
    )
    assert [call.name for call in result.tool_calls] == ["lookup_order", "check_policy"]
    assert result.tool_calls[0].arguments == {"order_id": "1234"}


def test_json_string_arguments_are_normalised_to_a_dict():
    # Real providers hand back a JSON string; FunctionModel hands back a dict.
    # Both must land in RunResult as a dict.
    runner = PydanticAIRunner(
        model=scripted(tool_call("lookup_order", '{"order_id": "1234"}'), text("done"))
    )
    result = runner.run(
        SKILL, case(tools=[ToolSpec(name="lookup_order", parameters={"order_id": "string"})])
    )
    assert result.tool_calls[0].arguments == {"order_id": "1234"}


def test_unparseable_arguments_are_preserved_rather_than_dropped():
    runner = PydanticAIRunner(model=scripted(tool_call("ping", "not json at all"), text("done")))
    result = runner.run(SKILL, case(tools=[ToolSpec(name="ping")]))
    assert result.tool_calls[0].arguments == {"_raw": "not json at all"}


def test_the_canned_return_value_is_handed_back_to_the_model():
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        for message in messages:
            for part in getattr(message, "parts", []):
                if type(part).__name__ == "ToolReturnPart":
                    seen["content"] = part.content
        if "content" not in seen:
            return tool_call("lookup_order", {})
        return text("done")

    runner = PydanticAIRunner(model=FunctionModel(reply))
    runner.run(SKILL, case(tools=[ToolSpec(name="lookup_order", returns='{"status": "shipped"}')]))
    assert seen["content"] == '{"status": "shipped"}'


def test_a_sequence_of_return_values_is_handed_back_in_call_order():
    seen = []

    def reply(messages, info: AgentInfo) -> ModelResponse:
        for message in messages:
            for part in getattr(message, "parts", []):
                if type(part).__name__ == "ToolReturnPart" and part.content not in seen:
                    seen.append(part.content)
        if len(seen) < 3:
            return tool_call("get_work_item", {"id": "x"})
        return text("done")

    runner = PydanticAIRunner(model=FunctionModel(reply))
    runner.run(
        SKILL,
        case(tools=[ToolSpec(name="get_work_item", returns=['{"id": "A"}', '{"id": "B"}', "end"])]),
    )
    assert seen == ['{"id": "A"}', '{"id": "B"}', "end"]


def test_a_retry_starts_a_sequence_of_return_values_from_the_top():
    # A retried attempt is a fresh conversation, so its mocks must be fresh
    # too: an agent reused across attempts would hand the second attempt's
    # first call the sequence's second entry.
    turns = {"n": 0}
    seen = []

    def reply(messages, info: AgentInfo) -> ModelResponse:
        turn = turns["n"]
        turns["n"] += 1
        if turn in (0, 2):  # the first model turn of each attempt
            return tool_call("poll", {})
        if turn == 1:  # attempt 1 fails after the tool ran once
            raise ModelHTTPError(status_code=429, model_name="scripted", body=None)
        for message in messages:
            for part in getattr(message, "parts", []):
                if type(part).__name__ == "ToolReturnPart":
                    seen.append(part.content)
        return text("done")

    runner = PydanticAIRunner(
        model=FunctionModel(reply), retries=1, retry_backoff_seconds=0.01, sleep=lambda _: None
    )
    result = runner.run(SKILL, case(tools=[ToolSpec(name="poll", returns=["one", "two"])]))
    assert result.errored is False
    assert seen == ["one"]


def test_usage_latency_and_transcript_are_captured():
    runner = PydanticAIRunner(model=scripted(text("done")))
    result = runner.run(SKILL, case())
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.tokens == result.input_tokens + result.output_tokens
    assert result.latency_ms >= 0
    assert len(result.transcript) >= 2
    assert isinstance(result.transcript[0], dict)


def test_an_unpriced_model_reports_zero_cost_with_a_note():
    result = PydanticAIRunner(model=scripted(text("done"))).run(SKILL, case())
    assert result.cost_usd == 0.0
    assert "no price data" in result.cost_note


def test_a_provider_failure_is_reported_not_raised():
    def explode(messages, info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(status_code=500, model_name="scripted", body=None)

    result = PydanticAIRunner(model=FunctionModel(explode), retries=0).run(SKILL, case())
    assert result.errored is True
    assert "500" in result.error
    assert result.output == ""


def test_a_transient_failure_is_retried_and_can_succeed():
    attempts = {"n": 0}

    def flaky(messages, info: AgentInfo) -> ModelResponse:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ModelHTTPError(status_code=429, model_name="scripted", body=None)
        return text("recovered")

    slept = []
    runner = PydanticAIRunner(
        model=FunctionModel(flaky), retries=2, retry_backoff_seconds=0.01, sleep=slept.append
    )
    result = runner.run(SKILL, case())
    assert result.output == "recovered"
    assert result.errored is False
    assert slept == [0.01]


def test_backoff_grows_exponentially_between_attempts():
    def always_429(messages, info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(status_code=429, model_name="scripted", body=None)

    slept = []
    runner = PydanticAIRunner(
        model=FunctionModel(always_429), retries=3, retry_backoff_seconds=1.0, sleep=slept.append
    )
    result = runner.run(SKILL, case())
    assert slept == [1.0, 2.0, 4.0]
    assert result.errored is True


def test_a_permanent_failure_is_not_retried():
    attempts = {"n": 0}

    def unauthorized(messages, info: AgentInfo) -> ModelResponse:
        attempts["n"] += 1
        raise ModelHTTPError(status_code=401, model_name="scripted", body=None)

    slept = []
    runner = PydanticAIRunner(
        model=FunctionModel(unauthorized), retries=3, retry_backoff_seconds=0.01, sleep=slept.append
    )
    result = runner.run(SKILL, case())
    assert attempts["n"] == 1
    assert slept == []
    assert result.errored is True


def test_the_runner_declares_that_it_needs_a_key():
    assert PydanticAIRunner.needs_api_key is True


def test_a_model_omitting_a_required_tool_argument_does_not_error_the_case():
    # build_mock_tool marks every declared parameter "required" with
    # additionalProperties: false in the JSON schema shown to the model. That
    # schema is descriptive only: PydanticAI's `Tool.from_schema` does not
    # generate a validator from it, so a call that omits a required argument
    # (or sends an undeclared one) is executed exactly like any other call --
    # no ModelRetry prompt back to the model, no exception out of run_sync.
    # A model choosing to omit an argument is therefore captured faithfully
    # in the trajectory rather than being turned into an infra error.
    runner = PydanticAIRunner(
        model=scripted(tool_call("lookup_order", {}), text("done")),
    )
    result = runner.run(
        SKILL,
        case(tools=[ToolSpec(name="lookup_order", parameters={"order_id": "string"})]),
    )
    assert result.errored is False
    assert result.tool_calls[0].name == "lookup_order"
    assert result.tool_calls[0].arguments == {}
    assert result.output == "done"
    # Pin the actual claim: no retry-prompt part anywhere in the transcript.
    # If PydanticAI HAD rejected the call, a RetryPromptPart (serialised with
    # part_kind == "retry-prompt") would show up in a request message's parts.
    retry_parts = [
        part
        for message in result.transcript
        for part in message.get("parts", [])
        if part.get("part_kind") == "retry-prompt"
    ]
    assert retry_parts == []


def test_a_failure_while_capturing_the_result_is_reported_not_raised(monkeypatch):
    # The never-raise contract covers our own capture code, not just the
    # provider. A real provider can emit message shapes FunctionModel never
    # does, and a serialisation failure must not crash the run.
    import skill_lens.runners.pydantic_ai as adapter

    def boom(messages):
        raise RuntimeError("transcript exploded")

    monkeypatch.setattr(adapter, "_transcript", boom)
    result = PydanticAIRunner(model=scripted(text("done"))).run(SKILL, case())
    assert result.errored is True
    assert "transcript exploded" in result.error


def test_a_numeric_temperature_is_sent_to_the_model():
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        seen["settings"] = info.model_settings
        return text("done")

    PydanticAIRunner(model=FunctionModel(reply), temperature=0.7).run(SKILL, case())
    assert seen["settings"] == {"temperature": 0.7}


def test_an_unset_temperature_sends_no_temperature_at_all():
    # Reasoning models reject any temperature but 1, so "unset" must mean no
    # model_settings at all rather than an omitted key inside one.
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        seen["settings"] = info.model_settings
        return text("done")

    PydanticAIRunner(model=FunctionModel(reply), temperature="unset").run(SKILL, case())
    assert seen["settings"] is None


def test_a_5xx_failure_is_retried_and_can_succeed():
    attempts = {"n": 0}

    def flaky(messages, info: AgentInfo) -> ModelResponse:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ModelHTTPError(status_code=503, model_name="scripted", body=None)
        return text("recovered")

    slept = []
    runner = PydanticAIRunner(
        model=FunctionModel(flaky), retries=2, retry_backoff_seconds=0.01, sleep=slept.append
    )
    result = runner.run(SKILL, case())
    assert result.output == "recovered"
    assert result.errored is False
    assert slept == [0.01]


def test_a_missing_optional_extra_propagates_rather_than_becoming_an_errored_case(monkeypatch):
    # RunnerDependencyError means the caller's environment is missing the
    # optional extra -- it's a user/setup error the CLI turns into a clean
    # exit 2 (see _AUTHORING_ERRORS in cli.py), not something the runner
    # should swallow into RunResult.error like a provider failure.
    import skill_lens.runners.pydantic_ai as adapter

    def explode() -> None:
        raise adapter.RunnerDependencyError("the 'pydantic-ai' runner needs its optional extra")

    monkeypatch.setattr(adapter, "_require_pydantic_ai", explode)
    runner = PydanticAIRunner(model=scripted(text("done")))
    with pytest.raises(adapter.RunnerDependencyError):
        runner.run(SKILL, case())


def test_pydantic_ai_runner_satisfies_the_runner_protocol():
    """Runner is @runtime_checkable; this guards the seam the whole design rests
    on (Runner.run(skill, case) -> RunResult) against signature drift."""
    assert isinstance(PydanticAIRunner(model=scripted(text("x"))), Runner)


def offered_case(**kwargs) -> EvalCase:
    kwargs.setdefault("name", "c")
    kwargs.setdefault("task", "refund order 1234")
    kwargs["mode"] = "offered"
    return EvalCase(**kwargs)


def test_a_loaded_run_reports_no_triggering_decision():
    # None is "not a triggering run", which is distinct from "did not trigger".
    result = PydanticAIRunner(model=scripted(text("done"))).run(SKILL, case())
    assert result.skill_triggered is None


def test_an_offered_skill_is_registered_as_a_tool_named_after_it():
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        seen["tools"] = sorted(tool.name for tool in info.function_tools)
        return text("done")

    PydanticAIRunner(model=FunctionModel(reply)).run(
        SKILL, offered_case(tools=[ToolSpec(name="lookup_order")])
    )
    assert seen["tools"] == ["lookup_order", "order_support"]


def test_an_offered_skill_is_not_forced_into_the_system_prompt():
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        seen["instructions"] = messages[0].instructions or ""
        return text("done")

    PydanticAIRunner(model=FunctionModel(reply)).run(SKILL, offered_case())
    assert "Always look up the order first." not in seen["instructions"]
    # Pin the preamble exactly: anything appended beyond OFFERED_PREAMBLE --
    # even something as small as a hint about what the skill does -- would
    # turn the trigger rate into a measurement of the prompt, not the skill.
    assert seen["instructions"] == OFFERED_PREAMBLE
    assert SKILL.description not in seen["instructions"]


def test_an_offered_skill_that_is_declined_reports_false():
    result = PydanticAIRunner(model=scripted(text("done"))).run(SKILL, offered_case())
    assert result.skill_triggered is False


def test_an_offered_skill_that_is_chosen_reports_true():
    runner = PydanticAIRunner(model=scripted(tool_call("order_support", {}), text("done")))
    result = runner.run(SKILL, offered_case())
    assert result.skill_triggered is True


def test_choosing_the_skill_delivers_its_instructions_to_the_model():
    # Offered mode has to be honest: an agent that picks the skill must
    # actually receive it, or every later assertion in the case is fiction.
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        for message in messages:
            for part in getattr(message, "parts", []):
                if (
                    type(part).__name__ == "ToolReturnPart"
                    and getattr(part, "tool_name", None) == "order_support"
                ):
                    seen["returned"] = str(getattr(part, "content", ""))
        if "returned" in seen:
            return text("done")
        return tool_call("order_support", {})

    PydanticAIRunner(model=FunctionModel(reply)).run(SKILL, offered_case())
    assert "Always look up the order first." in seen["returned"]


def test_the_offered_tool_call_appears_in_the_trajectory():
    # It counts toward max_calls like any other call: the message history stays
    # the authoritative record of what the model asked for.
    runner = PydanticAIRunner(model=scripted(tool_call("order_support", {}), text("done")))
    result = runner.run(SKILL, offered_case())
    assert [call.name for call in result.tool_calls] == ["order_support"]


def test_registration_and_detection_agree_on_a_name_replace_would_get_wrong():
    # skill_tool_name is the single source of truth: the runner registers the
    # offered tool under it AND detects the trigger by comparing against it.
    # "order-support" is a fixed point of a naive `name.replace("-", "_")`, so
    # a test built only on it can't tell the real derivation apart from a
    # hand-rolled stand-in. A digit-leading, space-and-punctuation name can.
    expected_name = skill_tool_name(WEIRD_SKILL.name)
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        seen.setdefault("tools", sorted(tool.name for tool in info.function_tools))
        if "called" not in seen:
            seen["called"] = True
            return tool_call(expected_name, {})
        return text("done")

    result = PydanticAIRunner(model=FunctionModel(reply)).run(WEIRD_SKILL, offered_case())

    assert expected_name in seen["tools"]
    assert result.skill_triggered is True


EMPTY_SKILL = Skill(
    name="order-support",
    description="",
    instructions="",
    variant="baseline",
    path=Path("."),
)


def test_a_skill_with_nothing_to_say_gets_a_neutral_preamble():
    seen = {}

    def reply(messages, info: AgentInfo):
        seen["instructions"] = messages[0].instructions or ""
        return text("done")

    PydanticAIRunner(model=FunctionModel(reply)).run(EMPTY_SKILL, case())
    assert seen["instructions"] == BASELINE_PREAMBLE


def test_a_baseline_prompt_never_leaks_the_skill_name():
    # The whole point of the baseline arm: if its prompt names the skill, the
    # delta measures the leak rather than the skill.
    seen = {}

    def reply(messages, info: AgentInfo):
        seen["instructions"] = messages[0].instructions or ""
        return text("done")

    PydanticAIRunner(model=FunctionModel(reply)).run(EMPTY_SKILL, case())
    assert "order-support" not in seen["instructions"]


def test_a_baseline_resolved_from_git_still_gets_its_own_prompt():
    # `--baseline previous` has real content, so it is prompted normally --
    # the neutral preamble keys on emptiness, not on the arm.
    previous = Skill(
        name="order-support",
        description="Handle refunds",
        instructions="Old instructions.",
        variant="baseline",
        path=Path("."),
    )
    seen = {}

    def reply(messages, info: AgentInfo):
        seen["instructions"] = messages[0].instructions or ""
        return text("done")

    PydanticAIRunner(model=FunctionModel(reply)).run(previous, case())
    assert "Old instructions." in seen["instructions"]


def test_the_builtin_tools_are_registered_when_a_workspace_is_given(tmp_path):
    seen: dict[str, list[str]] = {}

    def reply(messages, info: AgentInfo):
        seen["tools"] = [tool.name for tool in info.function_tools]
        return text("done")

    runner = PydanticAIRunner(model=FunctionModel(reply))
    workspace = Workspace(root=tmp_path.resolve())
    runner.run(SKILL, case(), workspace=workspace)
    assert set(WORKSPACE_TOOL_NAMES) <= set(seen["tools"])


def test_no_builtin_tools_without_a_workspace():
    seen: dict[str, list[str]] = {}

    def reply(messages, info: AgentInfo):
        seen["tools"] = [tool.name for tool in info.function_tools]
        return text("done")

    runner = PydanticAIRunner(model=FunctionModel(reply))
    runner.run(SKILL, case())
    assert not set(BUILTIN_TOOL_NAMES) & set(seen["tools"])


def test_a_model_writing_a_file_lands_it_in_the_workspace(tmp_path):
    runner = PydanticAIRunner(
        model=scripted(
            tool_call("write_file", {"path": "report.md", "content": "north 120"}),
            text("done"),
        )
    )
    workspace = Workspace(root=tmp_path.resolve())
    result = runner.run(SKILL, case(), workspace=workspace)
    assert result.error is None
    assert workspace.read("report.md") == "north 120"
    assert [call.name for call in result.tool_calls] == ["write_file"]


def test_a_model_writing_outside_the_root_is_refused_not_errored(tmp_path):
    # The refusal is an eval signal. A raised exception would mark the case
    # errored and hide it.
    runner = PydanticAIRunner(
        model=scripted(
            tool_call("write_file", {"path": "../escape.txt", "content": "x"}),
            text("done"),
        )
    )
    workspace = Workspace(root=tmp_path.resolve())
    result = runner.run(SKILL, case(), workspace=workspace)
    assert result.error is None
    assert workspace.listing() == []


RUNTIME = ScriptRuntime(
    policy=ScriptPolicy(sandbox="off", interpreters={"py": (sys.executable,)}),
    sandbox=SandboxStatus(backend="none", detail="test"),
)


def _bundled_skill(tmp_path):
    root = tmp_path / "skill"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "hello.py").write_text("print('hi from script')", encoding="utf-8")
    return SKILL.model_copy(update={"path": root, "bundle_root": root.resolve()})


def _seen_tools(skill, workspace, scripts, **case_kwargs):
    seen: dict[str, list[str]] = {}

    def reply(messages, info: AgentInfo):
        seen["tools"] = [tool.name for tool in info.function_tools]
        return text("done")

    PydanticAIRunner(model=FunctionModel(reply)).run(
        skill, case(**case_kwargs), workspace=workspace, scripts=scripts
    )
    return set(seen["tools"])


def test_bundle_tools_are_registered_with_a_bundle_and_a_workspace(tmp_path):
    workspace = Workspace(root=(tmp_path / "ws").resolve())
    workspace.root.mkdir()
    tools = _seen_tools(_bundled_skill(tmp_path), workspace, RUNTIME)
    assert set(BUNDLE_TOOL_NAMES) <= tools


def test_no_run_script_without_a_runtime(tmp_path):
    workspace = Workspace(root=(tmp_path / "ws").resolve())
    workspace.root.mkdir()
    tools = _seen_tools(_bundled_skill(tmp_path), workspace, None)
    assert {"list_skill_files", "read_skill_file"} <= tools
    assert "run_script" not in tools


def test_no_bundle_tools_without_a_workspace(tmp_path):
    tools = _seen_tools(_bundled_skill(tmp_path), None, RUNTIME)
    assert not set(BUNDLE_TOOL_NAMES) & tools


def test_no_bundle_tools_for_a_skill_without_a_bundle(tmp_path):
    workspace = Workspace(root=(tmp_path / "ws").resolve())
    workspace.root.mkdir()
    tools = _seen_tools(SKILL, workspace, RUNTIME)
    assert not set(BUNDLE_TOOL_NAMES) & tools


def test_bundle_tools_are_registered_in_offered_mode_too(tmp_path):
    # An agent that triggers the skill needs the bundle exactly as a loaded
    # case does; registering the tools says nothing about what the skill is,
    # so the trigger rate still measures the skill's description.
    workspace = Workspace(root=(tmp_path / "ws").resolve())
    workspace.root.mkdir()
    tools = _seen_tools(_bundled_skill(tmp_path), workspace, RUNTIME, mode="offered")
    assert set(BUNDLE_TOOL_NAMES) <= tools
    assert skill_tool_name(SKILL.name) in tools


def test_a_model_running_a_script_gets_its_output_back(tmp_path):
    runner = PydanticAIRunner(
        model=scripted(tool_call("run_script", {"path": "scripts/hello.py"}), text("done"))
    )
    workspace = Workspace(root=(tmp_path / "ws").resolve())
    workspace.root.mkdir()
    result = runner.run(_bundled_skill(tmp_path), case(), workspace=workspace, scripts=RUNTIME)
    assert result.error is None
    assert result.tool_calls[0].name == "run_script"
    # The transcript is pydantic-ai's serialised message list; its exact shape
    # is the adapter's business. What matters is that the script's output
    # reached the model at all.
    assert "hi from script" in str(result.transcript)


def test_a_model_running_a_missing_script_is_refused_not_errored(tmp_path):
    # run_script never raises: a bad path comes back as a tool result the
    # model reads, never an exception that would mark the whole run errored.
    runner = PydanticAIRunner(
        model=scripted(tool_call("run_script", {"path": "scripts/nope.py"}), text("done"))
    )
    workspace = Workspace(root=(tmp_path / "ws").resolve())
    workspace.root.mkdir()
    result = runner.run(_bundled_skill(tmp_path), case(), workspace=workspace, scripts=RUNTIME)
    assert result.error is None
    assert "refused" in str(result.transcript)


def test_preflight_refuses_a_trajectory_naming_a_tool_the_case_does_not_declare():
    from skill_lens.models import TrajectorySpec
    from skill_lens.runners.preflight import UndeclaredTool

    case_ = EvalCase(name="c", task="t", trajectory=TrajectorySpec(called=["Bash"]))
    runner = PydanticAIRunner(model=scripted(text("x")))
    with pytest.raises(UndeclaredTool, match=r"runner pydantic-ai: case 'c' of skill 's'"):
        runner.preflight([], {"s": [case_]})
    assert runner.preflight([], {"s": [EvalCase(name="c", task="t")]}) is None
