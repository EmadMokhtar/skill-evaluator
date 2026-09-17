"""Read an agent product's machine-readable trace into one shape.

Two pure parsers -- Copilot CLI's `--output-format json` and Claude Code's
`--output-format stream-json` -- each turning captured stdout into a `Trace`.
Neither raises for content: a product may print a warning to stdout, a run may
end in the product's own error event, and a trace may be cut short. All of
that is data (`Trace.error`), because the runner must never raise for a
product failure.

Tool calls are what the model *requested* (`toolRequests`, `tool_use`), not
what executed: a refused or failed call was still the model's choice, the
same rule the framework runners apply to their message histories.

Imports no agent framework -- this is JSON, not an SDK.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from skill_lens.models import ToolCall


@dataclass(frozen=True)
class Trace:
    """What one product run reported. Every field defaults so a failure can be
    a `Trace(error=...)` with nothing else known."""

    output: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    cost_usd: float = 0.0
    cost_note: str = ""
    usage_note: str = ""
    invoked_skills: frozenset[str] = frozenset()
    # True when the product's final event (`result`) was seen. A trace can be
    # complete and still carry an error (the product reported one); a trace
    # that is not complete was cut short, and the exit code is then the
    # better explanation.
    complete: bool = False
    error: str | None = None


def parse_lines(text: str) -> list[dict[str, Any]]:
    """Every line that is a JSON object, in order; anything else is skipped.

    A product may print a warning before its first event; skipping it is the
    only way to read the events behind it.
    """
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def _arguments(args: Any) -> dict[str, Any]:
    """A tool call's arguments as a dict; anything else is preserved under `_raw`
    so a capture problem never masquerades as a model problem."""
    if isinstance(args, dict):
        return args
    if args in (None, ""):
        return {}
    return {"_raw": str(args)}


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def parse_copilot(text: str) -> Trace:
    """Copilot CLI: `assistant.message`, `skill.invoked`, `session.shutdown`,
    `session.error`, `result`."""
    events = parse_lines(text)
    output = ""
    tool_calls: list[ToolCall] = []
    invoked: set[str] = set()
    model = ""
    input_tokens = 0
    output_tokens = 0
    usage_seen = False
    error: str | None = None
    result: dict[str, Any] | None = None
    for event in events:
        kind = event.get("type")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if kind == "assistant.message":
            content = data.get("content")
            if isinstance(content, str) and content.strip():
                output = content
            requests = data.get("toolRequests")
            for request in requests if isinstance(requests, list) else []:
                if isinstance(request, dict) and isinstance(request.get("name"), str):
                    tool_calls.append(
                        ToolCall(
                            name=request["name"], arguments=_arguments(request.get("arguments"))
                        )
                    )
        elif kind == "skill.invoked" and isinstance(data.get("name"), str):
            invoked.add(data["name"])
        elif kind == "session.tools_updated" and isinstance(data.get("model"), str):
            model = data["model"]
        elif kind == "session.shutdown":
            if isinstance(data.get("currentModel"), str):
                model = data["currentModel"]
            metrics = data.get("modelMetrics")
            if isinstance(metrics, dict):
                for per_model in metrics.values():
                    usage = per_model.get("usage") if isinstance(per_model, dict) else None
                    if not isinstance(usage, dict):
                        continue
                    usage_seen = True
                    input_tokens += (
                        _int(usage.get("inputTokens"))
                        + _int(usage.get("cacheReadTokens"))
                        + _int(usage.get("cacheWriteTokens"))
                    )
                    output_tokens += _int(usage.get("outputTokens"))
        elif kind == "session.error" and isinstance(data.get("message"), str):
            error = f"copilot: {data['message']}"
        elif kind == "result":
            result = event
    if result is None:
        error = error or "no result event in the copilot trace"
    elif error is None and _int(result.get("exitCode")) != 0:
        error = f"copilot exited with code {result.get('exitCode')}"
    result_usage = result.get("usage") if result else None
    premium = _int(result_usage.get("premiumRequests")) if isinstance(result_usage, dict) else 0
    return Trace(
        output=output,
        tool_calls=tool_calls,
        transcript=[event for event in events if event.get("ephemeral") is not True],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
        cost_usd=0.0,
        cost_note=(
            f"copilot bills per premium request, not per token; {premium} premium request(s)"
        ),
        usage_note="" if usage_seen else "copilot did not report token usage",
        invoked_skills=frozenset(invoked),
        complete=result is not None,
        error=error,
    )


def parse_claude_code(text: str) -> Trace:
    """Claude Code: `system`/`init`, `assistant` messages, `result`."""
    events = parse_lines(text)
    tool_calls: list[ToolCall] = []
    invoked: set[str] = set()
    model = ""
    result: dict[str, Any] | None = None
    for event in events:
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            if isinstance(event.get("model"), str):
                model = event["model"]
        elif kind == "assistant":
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            content = message.get("content")
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name")
                if not isinstance(name, str):
                    continue
                arguments = _arguments(block.get("input"))
                tool_calls.append(ToolCall(name=name, arguments=arguments))
                if name == "Skill" and isinstance(arguments.get("skill"), str):
                    invoked.add(arguments["skill"])
        elif kind == "result":
            result = event
    if result is None:
        return Trace(
            tool_calls=tool_calls,
            transcript=events,
            model=model,
            invoked_skills=frozenset(invoked),
            error="no result event in the claude-code trace",
        )
    usage_seen = isinstance(result.get("usage"), dict)
    usage = result.get("usage") if usage_seen else {}
    output = result.get("result") if isinstance(result.get("result"), str) else ""
    failed = result.get("is_error") is True or result.get("subtype") != "success"
    cost = result.get("total_cost_usd")
    return Trace(
        output=output,
        tool_calls=tool_calls,
        transcript=events,
        input_tokens=(
            _int(usage.get("input_tokens"))
            + _int(usage.get("cache_creation_input_tokens"))
            + _int(usage.get("cache_read_input_tokens"))
        ),
        output_tokens=_int(usage.get("output_tokens")),
        model=model,
        cost_usd=float(cost)
        if isinstance(cost, (int, float)) and not isinstance(cost, bool)
        else 0.0,
        usage_note="" if usage_seen else "claude-code did not report token usage",
        invoked_skills=frozenset(invoked),
        complete=True,
        error=f"claude-code: {output or result.get('subtype')}" if failed else None,
    )
