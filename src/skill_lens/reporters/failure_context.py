"""What a non-passing case actually did: its output and its tool calls.

`contains('1234') did not hold` says that a case failed, not why. This module
computes the excerpt that answers "why" -- and it is the *only* place that
does, so the console, Markdown and JUnit reporters can never disagree on what
was shown. They own the markup; this owns the text.

The excerpt exists only for non-passing candidate outcomes. Fifty green cases
must not print fifty transcripts, and a baseline outcome is not the verdict --
the delta block is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from skill_lens.models import CaseOutcome, ToolCall

# Characters of agent output shown under a failing case by default. Enough to
# read what the agent said; small enough to keep a CI log readable when a
# suite has many red cases. `--full-output` lifts it.
OUTPUT_LIMIT = 500

# Characters per tool-call argument value. A `write_file` call carries the
# whole document; the reader needs the file name, not the document.
ARGUMENT_LIMIT = 80

# Tool calls listed before the rest are counted rather than shown.
TOOL_CALL_LIMIT = 20


@dataclass(frozen=True)
class FailureContext:
    """The excerpt. `output` is already cut, has normalised line endings

    (CRLF/CR folded to LF), and carries no trailing newline; `cut` says how
    much was cut, counted on the raw output before that normalisation.
    """

    output: str
    cut: int
    tool_calls: list[str]
    more_calls: int


def _argument(value: object) -> str:
    # `default=str` because arguments come from a provider as JSON but a
    # library caller may hand a runner anything; a reporter must never raise.
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > ARGUMENT_LIMIT:
        return text[:ARGUMENT_LIMIT] + "…"
    return text


def format_tool_call(call: ToolCall) -> str:
    """`name(arg=<json>, ...)`, arguments in the order the model sent them."""
    arguments = ", ".join(f"{name}={_argument(value)}" for name, value in call.arguments.items())
    return f"{call.name}({arguments})"


def cut_note(context: FailureContext) -> str:
    """Never a silent cut: the exact count, and how to see the rest."""
    return f"… ({context.cut:,} more characters; --full-output prints them)"


def more_calls_note(context: FailureContext) -> str:
    return f"… +{context.more_calls} more calls"


def failure_context(outcome: CaseOutcome, *, limit: int | None) -> FailureContext | None:
    """The excerpt for `outcome`, or None when nothing should be expanded.

    `limit=None` means no cap on the output (the `full_output` path).
    """
    if outcome.status == "passed" or outcome.arm != "candidate" or outcome.result is None:
        return None
    output = outcome.result.output
    cut = 0
    if limit is not None and len(output) > limit:
        cut = len(output) - limit
        output = output[:limit]
    # Line endings are markup, not content: a trailing newline would render as a
    # blank line in every reporter, and an output that is only line breaks said
    # nothing. Normalised after the cut so `cut` stays a count over the raw text.
    output = output.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    calls = outcome.result.tool_calls
    return FailureContext(
        output=output,
        cut=cut,
        tool_calls=[format_tool_call(call) for call in calls[:TOOL_CALL_LIMIT]],
        more_calls=max(0, len(calls) - TOOL_CALL_LIMIT),
    )
