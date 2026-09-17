"""Grade a rubric through an agent product's own CLI.

The framework judges ask a model for a structured `JudgeOutput`. A product
has no structured-output mode, so the same prompt (`judges/prompt.py`) goes
in as text with one closing line asking for the JSON object only, and the
first balanced object in the reply is validated as `JudgeOutput`. Everything
that is not a valid verdict -- prose, a cut-off object, the wrong shape -- is
`JudgeVerdict.error`, one attempt: an unreadable verdict is an infra signal,
not a low score.

The judge runs in an empty directory with no skill delivered: it grades
text, and must not discover the skill under test.
"""

from __future__ import annotations

from skill_lens.judges.prompt import SYSTEM_PROMPT, render_request
from skill_lens.models import JudgeRequest

CLOSING_INSTRUCTION = (
    "Reply with one JSON object and nothing else -- no prose before or after it, "
    'no code fence: {"checks": [{"id": "<check id>", "passed": <true or false>, '
    '"evidence": "<the words from the response that decide it>"}, ...]}, '
    "one entry per check id, `passed` true or false."
)


def judge_prompt(request: JudgeRequest) -> str:
    """The shared judge prompt as one user turn, closed by the JSON-only line.

    The product's system prompt is its own; the grading rules travel in the
    prompt text instead.
    """
    return f"{SYSTEM_PROMPT}\n\n{render_request(request)}\n\n{CLOSING_INSTRUCTION}"


def extract_json_object(text: str) -> str | None:
    """The first balanced `{ ... }` in `text`, or None.

    A product answers in prose, often around a code fence; the verdict is
    the object inside. Braces inside JSON strings are skipped by tracking
    string state and escapes, so evidence quoting a `}` does not end the
    object early.

    One pass over `text`, not one scan per candidate `{`: a stack holds the
    positions of unmatched `{` seen outside a string. Each `}` outside a
    string pops the most recent one, completing an object that runs from
    the popped position to the current index -- a `}` with nothing to pop
    is stray and ignored. Every completion is a candidate; the one with the
    earliest start wins, because an outer object (pushed first, so popped
    last) can complete after an inner one already has, and "first balanced
    object" means the earliest-opening brace, not the first to close.
    """
    stack: list[int] = []
    best: tuple[int, int] | None = None
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            stack.append(index)
        elif char == "}" and stack:
            start = stack.pop()
            if best is None or start < best[0]:
                best = (start, index)
    if best is None:
        return None
    return text[best[0] : best[1] + 1]
