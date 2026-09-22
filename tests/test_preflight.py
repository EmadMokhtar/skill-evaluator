"""Check for the key -- and for a trajectory the runner can serve -- before spending."""

import pytest

from skill_lens.models import CallArgsSpec, EvalCase, ToolSpec, TrajectorySpec, WorkspaceSpec
from skill_lens.runners.preflight import (
    MissingAPIKey,
    UndeclaredTool,
    check_api_key,
    check_trajectory_names,
)


def test_a_present_key_passes():
    check_api_key("openai:gpt-4o-mini", {"OPENAI_API_KEY": "sk-test"})


def test_a_missing_key_is_reported_with_the_variable_name():
    with pytest.raises(MissingAPIKey) as exc:
        check_api_key("openai:gpt-4o-mini", {})
    assert "OPENAI_API_KEY" in str(exc.value)


def test_an_empty_key_counts_as_missing():
    with pytest.raises(MissingAPIKey):
        check_api_key("anthropic:claude-sonnet-4-6", {"ANTHROPIC_API_KEY": ""})


def test_the_message_never_suggests_putting_secrets_in_config():
    with pytest.raises(MissingAPIKey) as exc:
        check_api_key("openai:gpt-4o-mini", {})
    message = str(exc.value)
    assert "skill-lens.toml" in message
    assert "never" in message.lower()


def test_an_unknown_provider_is_not_blocked():
    # Better to attempt the run and report the provider's own error than to
    # refuse a provider we simply have not catalogued.
    check_api_key("some-new-provider:model", {})


def test_a_model_without_a_provider_prefix_is_not_blocked():
    check_api_key("gpt-4o-mini", {})


def test_a_google_model_is_checked_rather_than_silently_skipped():
    # The provider prefix here must match what pydantic-ai actually accepts.
    # A stale prefix makes check_api_key a no-op and defeats the whole point.
    with pytest.raises(MissingAPIKey):
        check_api_key("google:gemini-1.5-flash", {})


# --- trajectory names: a check the runner makes, because only it knows its tools


def _case(**kwargs) -> EvalCase:
    kwargs.setdefault("name", "order lookup")
    kwargs.setdefault("task", "t")
    return EvalCase(**kwargs)


@pytest.mark.parametrize("field", ["called", "forbidden", "order"])
def test_an_undeclared_trajectory_name_is_refused_naming_everything(field):
    # A typo in `called:` can never pass. It is a mistake in the file, not a
    # signal about the skill, so it aborts before any case runs.
    case = _case(
        tools=[ToolSpec(name="lookup_order")],
        trajectory=TrajectorySpec(**{field: ["lookup_ordr"]}),
    )
    with pytest.raises(UndeclaredTool) as exc:
        check_trajectory_names("fake", {"pdf": [case]})
    message = str(exc.value)
    assert message.startswith("runner fake: ")
    assert "case 'order lookup' of skill 'pdf'" in message
    assert f"trajectory.{field} names 'lookup_ordr'" in message
    assert "not declared in this case's tools" in message


def test_a_call_args_entry_naming_an_undeclared_tool_is_refused_too():
    case = _case(
        tools=[ToolSpec(name="lookup_order")],
        trajectory=TrajectorySpec(
            call_args=[CallArgsSpec(tool="lookup_ordr", contains={"order_id": "1234"})]
        ),
    )
    with pytest.raises(UndeclaredTool, match=r"trajectory\.call_args names 'lookup_ordr'"):
        check_trajectory_names("fake", {"pdf": [case]})


def test_declared_names_pass():
    case = _case(
        tools=[ToolSpec(name="lookup_order"), ToolSpec(name="issue_refund")],
        trajectory=TrajectorySpec(
            called=["lookup_order"], forbidden=["issue_refund"], order=["lookup_order"]
        ),
    )
    assert check_trajectory_names("fake", {"pdf": [case]}) is None


def test_a_case_without_a_trajectory_is_not_inspected():
    assert check_trajectory_names("fake", {"pdf": [_case()]}) is None


@pytest.mark.parametrize("builtin", ["write_file", "run_script"])
def test_a_builtin_is_declared_only_where_a_workspace_exists(builtin):
    with_workspace = _case(
        workspace=WorkspaceSpec(files={}), trajectory=TrajectorySpec(called=[builtin])
    )
    assert check_trajectory_names("fake", {"pdf": [with_workspace]}) is None

    without = _case(trajectory=TrajectorySpec(called=[builtin]))
    with pytest.raises(UndeclaredTool, match="'workspace:' block"):
        check_trajectory_names("fake", {"pdf": [without]})


def test_the_runner_name_in_the_message_is_the_one_given():
    case = _case(trajectory=TrajectorySpec(called=["nope"]))
    with pytest.raises(UndeclaredTool, match=r"^runner langchain: "):
        check_trajectory_names("langchain", {"pdf": [case]})
