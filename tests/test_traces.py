# tests/test_traces.py
"""The two product trace parsers, against scrubbed recordings."""

from __future__ import annotations

from pathlib import Path

import pytest

from skill_lens.models import ToolCall
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


def test_claude_code_marks_a_trace_with_a_result_event_complete():
    assert parse_claude_code(_fixture("claude-code-trigger.jsonl")).complete is True
    assert parse_claude_code(_fixture("claude-code-error.jsonl")).complete is True


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


def test_copilot_skill_load_is_the_skill_invoked_event():
    assert parse_copilot(_fixture("copilot-trigger.jsonl")).invoked_skills == frozenset({"ping"})


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
