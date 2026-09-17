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
    'no code fence: {"checks": [{"id": "<check id>", "passed": true, '
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
    object early. An unbalanced run from one `{` is abandoned and the scan
    restarts at the next.
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
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
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        start = text.find("{", start + 1)
    return None
