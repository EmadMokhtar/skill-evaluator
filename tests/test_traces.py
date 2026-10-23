# tests/test_traces.py
"""The two product trace parsers, against scrubbed recordings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skill_lens.models import HookRun, ToolCall
from skill_lens.runners.traces import parse_claude_code, parse_copilot, parse_lines

FIXTURES = Path(__file__).parent / "fixtures" / "products"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- parse_lines ---


def test_parse_lines_skips_blank_and_non_json_lines():
    text = 'Warning: something\n\n{"type":"a"}\n[1,2]\n{"type":"b"}\n'
    assert parse_lines(text) == [{"type": "a"}, {"type": "b"}]


# --- Claude Code ---


def test_claude_code_output_tool_calls_and_skill_load():
    trace = parse_claude_code(_fixture("claude-code-trigger.jsonl"))
    assert trace.error is None
    assert trace.output == "PONG-7731"
    assert trace.tool_calls == [
        ToolCall(name="Skill", arguments={"skill": "ping"}),
        ToolCall(name="Bash", arguments={"command": "ls"}),
    ]
    assert trace.invoked_skills == frozenset({"ping"})


def test_claude_code_tokens_include_cache_reads_and_writes():
    trace = parse_claude_code(_fixture("claude-code-trigger.jsonl"))
    assert trace.input_tokens == 4 + 100 + 360
    assert trace.output_tokens == 19
    assert trace.usage_note == ""


def test_claude_code_cost_is_the_products_own_figure_with_no_note():
    trace = parse_claude_code(_fixture("claude-code-trigger.jsonl"))
    assert trace.cost_usd == pytest.approx(0.273076)
    assert trace.cost_note == ""


def test_claude_code_model_comes_from_the_init_event():
    assert parse_claude_code(_fixture("claude-code-trigger.jsonl")).model == "claude-opus-5[1m]"


def test_claude_code_transcript_keeps_every_event():
    trace = parse_claude_code(_fixture("claude-code-trigger.jsonl"))
    assert [event["type"] for event in trace.transcript] == [
        "system",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
        "result",
    ]


def test_claude_code_negative_control_loads_no_skill():
    trace = parse_claude_code(_fixture("claude-code-negative.jsonl"))
    assert trace.output == "4"
    assert trace.tool_calls == []
    assert trace.invoked_skills == frozenset()


def test_claude_code_error_result_is_the_products_own_explanation():
    trace = parse_claude_code(_fixture("claude-code-error.jsonl"))
    assert trace.error == "claude-code: API Error: 401 authentication_error"
    assert trace.model == "claude-opus-5[1m]"


def test_claude_code_without_a_result_event_is_an_error():
    lines = _fixture("claude-code-trigger.jsonl").splitlines()[:-1]
    trace = parse_claude_code("\n".join(lines))
    assert trace.error == "no result event in the claude-code trace"
    assert trace.complete is False


def test_claude_code_without_a_result_event_says_its_spend_is_unknown():
    # Cost and usage live on the `result` event only: without it the 0.0 and
    # the zero tokens are not measurements, and the notes say so.
    lines = _fixture("claude-code-trigger.jsonl").splitlines()[:-1]
    trace = parse_claude_code("\n".join(lines))
    assert trace.cost_usd == 0.0
    assert trace.cost_note == "the claude-code trace ended before reporting cost"
    assert trace.usage_note == "the claude-code trace ended before reporting token usage"


def test_claude_code_notes_name_how_a_stopped_run_ended():
    # The runner knows why the trace stopped (a timeout, a cut); the parser
    # only knows that it did.
    lines = _fixture("claude-code-trigger.jsonl").splitlines()[:-1]
    trace = parse_claude_code("\n".join(lines), "claude-code timed out")
    assert trace.cost_note == "claude-code timed out before reporting cost"
    assert trace.usage_note == "claude-code timed out before reporting token usage"


@pytest.mark.parametrize("cost", [None, "0.04", True])
def test_claude_code_without_a_usable_cost_says_cost_was_not_reported(cost):
    # A result with no number for `total_cost_usd` must not read as a free run,
    # nor let `max_cost_usd` pass on a 0.0 nobody measured.
    lines = _fixture("claude-code-trigger.jsonl").splitlines()
    result = json.loads(lines[-1])
    if cost is None:
        del result["total_cost_usd"]
    else:
        result["total_cost_usd"] = cost
    lines[-1] = json.dumps(result)
    trace = parse_claude_code("\n".join(lines))
    assert trace.cost_usd == 0.0
    assert trace.cost_note == "claude-code did not report cost"
    assert trace.error is None


def test_claude_code_marks_a_trace_with_a_result_event_complete():
    assert parse_claude_code(_fixture("claude-code-trigger.jsonl")).complete is True
    assert parse_claude_code(_fixture("claude-code-error.jsonl")).complete is True


def test_claude_code_without_usage_says_tokens_were_not_reported():
    lines = _fixture("claude-code-trigger.jsonl").splitlines()
    result = json.loads(lines[-1])
    del result["usage"]
    lines[-1] = json.dumps(result)
    trace = parse_claude_code("\n".join(lines))
    assert trace.input_tokens == 0
    assert trace.output_tokens == 0
    assert trace.usage_note == "claude-code did not report token usage"
    assert trace.error is None


# The hook fixture is built from the `hook_response` schema Claude Code 2.1.289
# declares for its SDK, not recorded: `exit_code` is absent when the hook had
# none (a cancelled one), and `hook_progress` is a partial read of a hook still
# running.


def test_claude_code_hooks_are_the_hook_response_events_in_order():
    trace = parse_claude_code(_fixture("claude-code-hooks.jsonl"))
    assert trace.error is None
    assert trace.hooks == [
        HookRun(
            name="UserPromptSubmit",
            event="UserPromptSubmit",
            outcome="success",
            exit_code=0,
            stdout="[plan] no task_plan.md yet\n",
        ),
        HookRun(name="PreToolUse:Write", event="PreToolUse", outcome="success", exit_code=0),
        HookRun(
            name="PostToolUse:Write",
            event="PostToolUse",
            outcome="error",
            exit_code=127,
            stderr="check-complete.sh: line 4: jq: command not found\n",
        ),
        HookRun(name="Stop", event="Stop", outcome="cancelled", exit_code=None),
    ]


def test_claude_code_hook_events_stay_in_the_transcript():
    trace = parse_claude_code(_fixture("claude-code-hooks.jsonl"))
    subtypes = [event.get("subtype") for event in trace.transcript if event["type"] == "system"]
    assert subtypes.count("hook_started") == 4
    assert subtypes.count("hook_progress") == 1
    assert subtypes.count("hook_response") == 4


def test_claude_code_hooks_do_not_change_what_the_result_event_reports():
    trace = parse_claude_code(_fixture("claude-code-hooks.jsonl"))
    assert trace.output == "Plan written."
    assert trace.tool_calls == [
        ToolCall(name="Write", arguments={"file_path": "task_plan.md", "content": "# Plan"})
    ]
    assert trace.input_tokens == 3 + 100 + 210
    assert trace.output_tokens == 13
    assert trace.cost_usd == pytest.approx(0.0512)


def test_claude_code_a_trace_without_hook_events_has_no_hooks():
    assert parse_claude_code(_fixture("claude-code-trigger.jsonl")).hooks == []


def test_claude_code_hooks_seen_before_a_cut_are_still_parsed():
    # The parser reports what the trace said; the runner decides that an
    # incomplete trace is an error and keeps none of it.
    lines = _fixture("claude-code-hooks.jsonl").splitlines()
    trace = parse_claude_code("\n".join(lines[:4]))
    assert trace.complete is False
    assert [hook.name for hook in trace.hooks] == ["UserPromptSubmit"]


@pytest.mark.parametrize(
    "broken",
    [
        {"hook_name": 7},
        {"hook_event": None},
        {"outcome": ["error"]},
    ],
)
def test_claude_code_a_hook_response_without_a_string_name_event_or_outcome_is_skipped(broken):
    event = {
        "type": "system",
        "subtype": "hook_response",
        "hook_name": "Stop",
        "hook_event": "Stop",
        "outcome": "success",
        **broken,
    }
    result = {"type": "result", "subtype": "success", "is_error": False, "result": "ok"}
    trace = parse_claude_code(json.dumps(event) + "\n" + json.dumps(result))
    assert trace.hooks == []
    assert trace.error is None


def test_claude_code_a_hook_responses_odd_streams_and_exit_code_do_not_raise():
    event = {
        "type": "system",
        "subtype": "hook_response",
        "hook_name": "Stop",
        "hook_event": "Stop",
        "outcome": "error",
        "stdout": None,
        "stderr": 3,
        "exit_code": True,
    }
    result = {"type": "result", "subtype": "success", "is_error": False, "result": "ok"}
    trace = parse_claude_code(json.dumps(event) + "\n" + json.dumps(result))
    assert trace.hooks == [HookRun(name="Stop", event="Stop", outcome="error", exit_code=None)]


# --- Copilot ---


def test_copilot_output_is_the_last_non_empty_assistant_message():
    trace = parse_copilot(_fixture("copilot-trigger.jsonl"))
    assert trace.error is None
    assert trace.output == "PONG-7731"


def test_copilot_tool_calls_are_the_models_requests_in_order():
    trace = parse_copilot(_fixture("copilot-trigger.jsonl"))
    assert trace.tool_calls == [
        ToolCall(name="bash", arguments={"command": "ls", "intent": "list files"})
    ]


def test_copilot_slash_invocation_is_the_skill_invoked_event():
    # `/ping ...` (mode: loaded) is what this recording invoked; the event is
    # the slash invocation's, and offered mode never produces it.
    assert parse_copilot(_fixture("copilot-trigger.jsonl")).invoked_skills == frozenset({"ping"})


def test_copilot_offered_skill_load_is_the_skill_tool_request():
    # A skill the model chose itself is a `skill` tool request, on Copilot CLI
    # 1.0.37 and 1.0.86-2 alike; no `skill.invoked` event is emitted for it
    # (issue #50). Same rule as Claude Code's `Skill` tool call.
    text = _fixture("copilot-offered-trigger.jsonl")
    assert "skill.invoked" not in text
    trace = parse_copilot(text)
    assert trace.error is None
    assert trace.output == "PONG-7731"
    assert trace.tool_calls == [ToolCall(name="skill", arguments={"skill": "ping"})]
    assert trace.invoked_skills == frozenset({"ping"})


def test_copilot_skill_tool_request_names_the_skill_it_asked_for():
    lines = (
        '{"type":"assistant.message","data":{"content":"","toolRequests":'
        '[{"toolCallId":"c","name":"skill","arguments":{"skill":"other"},"type":"function"}]}}\n'
        '{"type":"result","exitCode":0,"usage":{"premiumRequests":1}}'
    )
    assert parse_copilot(lines).invoked_skills == frozenset({"other"})


def test_copilot_skill_tool_request_without_a_string_skill_is_a_call_but_no_load():
    lines = (
        '{"type":"assistant.message","data":{"content":"","toolRequests":'
        '[{"toolCallId":"c1","name":"skill","arguments":{},"type":"function"},'
        '{"toolCallId":"c2","name":"skill","arguments":{"skill":5},"type":"function"}]}}\n'
        '{"type":"result","exitCode":0,"usage":{"premiumRequests":1}}'
    )
    trace = parse_copilot(lines)
    assert [call.name for call in trace.tool_calls] == ["skill", "skill"]
    assert trace.invoked_skills == frozenset()


def test_copilot_tokens_come_from_the_shutdown_metrics():
    trace = parse_copilot(_fixture("copilot-trigger.jsonl"))
    assert trace.input_tokens == 1000 + 200 + 100
    assert trace.output_tokens == 40
    assert trace.usage_note == ""
    assert trace.model == "gpt-5.4"


def test_copilot_without_shutdown_metrics_says_tokens_were_not_reported():
    trace = parse_copilot(_fixture("copilot-no-shutdown.jsonl"))
    assert trace.error is None
    assert trace.input_tokens == 0 and trace.output_tokens == 0
    assert trace.usage_note == "copilot did not report token usage"
    assert trace.model == "gpt-5.4"  # falls back to session.tools_updated


def test_copilot_cost_is_zero_with_a_premium_request_note():
    trace = parse_copilot(_fixture("copilot-trigger.jsonl"))
    assert trace.cost_usd == 0.0
    assert trace.cost_note == (
        "copilot bills per premium request, not per token; 1 premium request(s)"
    )


def test_copilot_transcript_drops_ephemeral_events():
    trace = parse_copilot(_fixture("copilot-trigger.jsonl"))
    assert "session.skills_loaded" not in [event["type"] for event in trace.transcript]
    assert trace.transcript[0]["type"] == "user.message"


def test_copilot_session_error_is_the_error_even_with_a_result_line():
    trace = parse_copilot(_fixture("copilot-error.jsonl"))
    assert trace.error == "copilot: 402 You have exceeded your monthly quota (Request ID: REDACTED)"
    assert trace.model == "gpt-4.1"


def test_copilot_non_zero_exit_without_a_session_error_is_named():
    lines = [
        line
        for line in _fixture("copilot-error.jsonl").splitlines()
        if '"session.error"' not in line
    ]
    assert parse_copilot("\n".join(lines)).error == "copilot exited with code 1"


def test_copilot_without_a_result_event_is_an_error():
    lines = _fixture("copilot-trigger.jsonl").splitlines()[:-1]
    trace = parse_copilot("\n".join(lines))
    assert trace.error == "no result event in the copilot trace"
    assert trace.complete is False


def test_copilot_without_a_result_event_claims_no_premium_request_count():
    # The count lives on the `result` event: without it, "0 premium
    # request(s)" would be a figure the trace never reported.
    lines = _fixture("copilot-trigger.jsonl").splitlines()[:-1]
    trace = parse_copilot("\n".join(lines))
    assert trace.cost_note == (
        "copilot bills per premium request, not per token; "
        "the copilot trace ended before reporting premium requests"
    )
    assert trace.usage_note == ""  # the shutdown metrics came before the cut


def test_copilot_notes_name_how_a_stopped_run_ended():
    # Cut before `session.shutdown` too: neither figure was reported.
    lines = _fixture("copilot-trigger.jsonl").splitlines()[:3]
    trace = parse_copilot("\n".join(lines), "copilot timed out")
    assert trace.cost_note == (
        "copilot bills per premium request, not per token; "
        "copilot timed out before reporting premium requests"
    )
    assert trace.usage_note == "copilot timed out before reporting token usage"


def test_copilot_marks_a_trace_with_a_result_event_complete():
    assert parse_copilot(_fixture("copilot-trigger.jsonl")).complete is True
    assert parse_copilot(_fixture("copilot-error.jsonl")).complete is True


def test_copilot_non_dict_arguments_are_preserved_raw():
    line = (
        '{"type":"assistant.message","data":{"content":"","toolRequests":'
        '[{"toolCallId":"c","name":"bash","arguments":"not a dict","type":"function"}]}}\n'
        '{"type":"result","exitCode":0,"usage":{"premiumRequests":0}}'
    )
    assert parse_copilot(line).tool_calls == [
        ToolCall(name="bash", arguments={"_raw": "not a dict"})
    ]


def test_copilot_result_with_null_usage_does_not_raise():
    trace = parse_copilot('{"type":"result","exitCode":0,"usage":null}')
    assert trace.error is None
    assert trace.complete is True
    assert trace.cost_note == (
        "copilot bills per premium request, not per token; 0 premium request(s)"
    )


def test_copilot_non_list_tool_requests_does_not_raise():
    line = (
        '{"type":"assistant.message","data":{"content":"","toolRequests":5}}\n'
        '{"type":"result","exitCode":0,"usage":{"premiumRequests":0}}'
    )
    assert parse_copilot(line).tool_calls == []


def test_claude_code_non_list_content_does_not_raise():
    lines = (
        '{"type":"assistant","message":{"model":"m","content":true},"session_id":"s"}\n'
        '{"type":"result","subtype":"success","is_error":false,"result":"ok",'
        '"session_id":"s","total_cost_usd":0.0}'
    )
    assert parse_claude_code(lines).tool_calls == []
