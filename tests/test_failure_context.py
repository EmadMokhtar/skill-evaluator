"""The excerpt a non-passing case shows. One helper, three reporters."""

from __future__ import annotations

from skill_lens.models import CaseOutcome, EvalScore, HookRun, RunResult, ToolCall
from skill_lens.reporters.failure_context import (
    ARGUMENT_LIMIT,
    HOOK_LIMIT,
    HOOK_STREAM_LIMIT,
    OUTPUT_LIMIT,
    TOOL_CALL_LIMIT,
    FailureContext,
    cut_note,
    failure_context,
    format_hook,
    format_tool_call,
    more_calls_note,
    more_hooks_note,
)

_SAID = "the agent said this"


def _outcome(status="failed", arm="candidate", result=None):
    return CaseOutcome(
        skill_name="pdf",
        case_name="extracts",
        runner="fake",
        status=status,
        scores=[EvalScore(evaluator="assertion", passed=status == "passed")],
        result=RunResult(output=_SAID) if result is None else result,
        arm=arm,
    )


def test_a_passing_case_has_no_context():
    assert failure_context(_outcome(status="passed"), limit=OUTPUT_LIMIT) is None


def test_a_baseline_outcome_has_no_context():
    assert failure_context(_outcome(arm="baseline"), limit=OUTPUT_LIMIT) is None


def test_an_outcome_without_a_result_has_no_context():
    outcome = CaseOutcome(
        skill_name="pdf", case_name="x", runner="fake", status="errored", scores=[], result=None
    )
    assert failure_context(outcome, limit=OUTPUT_LIMIT) is None


def test_a_failed_case_carries_its_output_uncut_when_under_the_limit():
    context = failure_context(_outcome(), limit=OUTPUT_LIMIT)
    assert context == FailureContext(output=_SAID, cut=0, tool_calls=[], more_calls=0)


def test_an_errored_case_carries_its_output_too():
    context = failure_context(_outcome(status="errored"), limit=OUTPUT_LIMIT)
    assert context is not None
    assert context.output == _SAID


def test_the_cut_is_exact_at_the_boundary():
    for length, expected_cut in [(499, 0), (500, 0), (501, 1), (2342, 1842)]:
        outcome = _outcome(result=RunResult(output="x" * length))
        context = failure_context(outcome, limit=500)
        assert context is not None
        assert context.cut == expected_cut, length
        assert len(context.output) == min(length, 500), length


def test_no_limit_cuts_nothing():
    outcome = _outcome(result=RunResult(output="x" * 5000))
    context = failure_context(outcome, limit=None)
    assert context is not None
    assert context.cut == 0
    assert len(context.output) == 5000


def test_the_cut_note_states_the_exact_count_and_the_way_to_see_it():
    context = FailureContext(output="", cut=1842, tool_calls=[], more_calls=0)
    assert cut_note(context) == "… (1,842 more characters; --full-output prints them)"


def test_tool_calls_render_in_order_with_json_arguments():
    outcome = _outcome(
        result=RunResult(
            output="",
            tool_calls=[
                ToolCall(name="lookup_order", arguments={"order_id": "1234"}),
                ToolCall(name="issue_refund", arguments={"order_id": "1234", "amount": 12.5}),
                ToolCall(name="list_files", arguments={}),
            ],
        )
    )
    context = failure_context(outcome, limit=OUTPUT_LIMIT)
    assert context is not None
    assert context.tool_calls == [
        'lookup_order(order_id="1234")',
        'issue_refund(order_id="1234", amount=12.5)',
        "list_files()",
    ]
    assert context.more_calls == 0


def test_a_long_argument_value_is_cut_with_a_marker():
    call = ToolCall(name="write_file", arguments={"path": "a.md", "content": "y" * 500})
    text = format_tool_call(call)
    # The JSON string is `"yyyy..."`; the opening quote counts toward the limit.
    assert text.startswith('write_file(path="a.md", content="' + "y" * (ARGUMENT_LIMIT - 1))
    assert text.endswith("…)")
    assert len(text) < 500


def test_non_ascii_arguments_stay_readable():
    call = ToolCall(name="greet", arguments={"name": "Zoë"})
    assert format_tool_call(call) == 'greet(name="Zoë")'


def test_an_unserialisable_argument_does_not_raise():
    call = ToolCall(name="odd", arguments={"when": object()})
    assert format_tool_call(call).startswith("odd(when=")


def test_calls_beyond_the_cap_are_counted_not_listed():
    calls = [ToolCall(name=f"t{i}", arguments={}) for i in range(TOOL_CALL_LIMIT + 5)]
    outcome = _outcome(result=RunResult(output="", tool_calls=calls))
    context = failure_context(outcome, limit=OUTPUT_LIMIT)
    assert context is not None
    assert len(context.tool_calls) == TOOL_CALL_LIMIT
    assert context.tool_calls[0] == "t0()"
    assert context.more_calls == 5
    assert more_calls_note(context) == "… +5 more calls"


def test_a_trailing_newline_is_stripped_from_the_excerpt():
    context = failure_context(_outcome(result=RunResult(output="hello\n\n")), limit=OUTPUT_LIMIT)
    assert context is not None
    assert context.output == "hello"
    assert context.cut == 0


def test_crlf_line_endings_are_normalised():
    context = failure_context(_outcome(result=RunResult(output="a\r\nb\rc")), limit=OUTPUT_LIMIT)
    assert context is not None
    assert context.output == "a\nb\nc"


def test_an_output_that_is_only_line_breaks_is_empty():
    context = failure_context(_outcome(result=RunResult(output="\r\n\n")), limit=OUTPUT_LIMIT)
    assert context is not None
    assert context.output == ""


def test_the_cut_is_counted_on_the_raw_output_before_normalisation():
    # 500 x's then a newline: the newline is the 501st raw character, so it is
    # cut (cut == 1) and never reaches the excerpt either way.
    context = failure_context(_outcome(result=RunResult(output="x" * 500 + "\n")), limit=500)
    assert context is not None
    assert context.cut == 1
    assert context.output == "x" * 500


# --- hooks ---


def _hooked(*hooks):
    return _outcome(result=RunResult(output="", hooks=list(hooks)))


def test_only_the_hooks_that_failed_are_shown():
    context = failure_context(
        _hooked(
            HookRun(name="UserPromptSubmit", event="UserPromptSubmit", outcome="success"),
            HookRun(
                name="PostToolUse:Write",
                event="PostToolUse",
                outcome="error",
                exit_code=127,
                stderr="check-complete.sh: line 4: jq: command not found\n",
            ),
            HookRun(name="Stop", event="Stop", outcome="cancelled"),
        ),
        limit=OUTPUT_LIMIT,
    )
    assert context is not None
    assert context.failed_hooks == [
        'PostToolUse:Write error (exit code 127): stderr="check-complete.sh: line 4: jq: '
        'command not found"',
        "Stop cancelled",
    ]
    assert context.more_hooks == 0


def test_a_case_whose_hooks_all_succeeded_shows_none():
    context = failure_context(
        _hooked(HookRun(name="Stop", event="Stop", outcome="success", stdout="done")),
        limit=OUTPUT_LIMIT,
    )
    assert context is not None
    assert context.failed_hooks == []


def test_a_failed_hook_shows_stderr_then_stdout_each_on_one_line():
    hook = HookRun(
        name="PreToolUse:Bash",
        event="PreToolUse",
        outcome="error",
        exit_code=2,
        stdout="plan\nsteps\n",
        stderr="blocked: no plan\n",
    )
    assert format_hook(hook) == (
        'PreToolUse:Bash error (exit code 2): stderr="blocked: no plan" stdout="plan\\nsteps"'
    )


def test_a_long_hook_stream_is_cut_with_a_marker():
    hook = HookRun(name="Stop", event="Stop", outcome="error", exit_code=1, stderr="e" * 1000)
    text = format_hook(hook)
    # The JSON string is `"eeee..."`; the opening quote counts toward the limit.
    assert text == 'Stop error (exit code 1): stderr="' + "e" * (HOOK_STREAM_LIMIT - 1) + "…"


def test_failed_hooks_beyond_the_cap_are_counted_not_listed():
    hooks = [
        HookRun(name=f"PostToolUse:T{i}", event="PostToolUse", outcome="error", exit_code=1)
        for i in range(HOOK_LIMIT + 3)
    ]
    context = failure_context(_hooked(*hooks), limit=OUTPUT_LIMIT)
    assert context is not None
    assert len(context.failed_hooks) == HOOK_LIMIT
    assert context.failed_hooks[0] == "PostToolUse:T0 error (exit code 1)"
    assert context.more_hooks == 3
    assert more_hooks_note(context) == "… +3 more failed hooks"
