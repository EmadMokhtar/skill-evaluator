"""Render a JudgeRequest into prompt text.

Pure functions, no framework, no IO — so the wording that decides how strict a
judge is can be tested without spending anything.
"""

from __future__ import annotations

from skill_eval.models import JudgeRequest

SYSTEM_PROMPT = """\
You grade one AI assistant response against a fixed list of checks.

Rules:
- Return exactly one verdict per check id you are given. Never invent, merge,
  drop, rename or reorder an id.
- Judge only what the response actually says. Do not give credit for intent,
  effort, or what a reasonable assistant would probably have meant.
- `evidence` must quote the part of the response that decides the check. A
  verdict you cannot evidence is a fail, not a pass.
- When a check is ambiguous about the response in front of you, fail it and say
  so in the evidence.
"""


def render_request(request: JudgeRequest) -> str:
    """Lay a request out as prompt text, in the order a grader needs it."""
    parts = ["## Task given to the assistant", request.task]
    if request.expected:
        parts += ["## What a good response looks like", request.expected]
    parts += [
        "## The assistant's response",
        request.output if request.output else "(the assistant produced no output)",
    ]
    checks = "\n".join(f"{check.id}: {check.text}" for check in request.checks)
    parts.append(f"## Checks\n{checks}")
    return "\n\n".join(parts)
