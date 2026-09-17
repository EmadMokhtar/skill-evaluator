"""Grade a rubric through an agent product's own CLI.

The framework judges ask a model for a structured `JudgeOutput`. A product
has no structured-output mode, so the same prompt (`judges/prompt.py`) goes
in as text with one closing line asking for the JSON object only, and the
first balanced object in the reply is validated against a strict copy of
`JudgeOutput` (`_RawVerdict`, below). Everything that is not a valid
verdict -- prose, a cut-off object, the wrong shape -- is
`JudgeVerdict(error="JudgeOutputInvalid: ...")` naming the actual mismatch,
one attempt: an unreadable verdict is an infra signal, not a low score, and
its error should say so rather than surface several layers away as a
`JudgeEvaluator` complaint about a mismatched id set.

The judge runs in an empty directory with no skill delivered: it grades
text, and must not discover the skill under test.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

from pydantic import ConfigDict, ValidationError

from skill_lens.judges.prompt import SYSTEM_PROMPT, render_request
from skill_lens.models import CheckResult, JudgeOutput, JudgeRequest, JudgeVerdict, ProductStatus
from skill_lens.runners.product import (
    MAX_PROMPT_BYTES,
    TRUST_NOTE,
    Product,
    find_executable,
    invoke,
    probe_version,
    read_trace,
)

JUDGE_PREFIX = "skill-lens-judge-"


class _RawVerdict(JudgeOutput):
    """`JudgeOutput`, strict, for reading a product's free-text reply only.

    The framework judges bind `JudgeOutput` as the model's own structured
    output, which a provider enforces server-side -- `JudgeOutput` itself
    stays lenient so that path is untouched (a stricter shared model would
    also change the JSON schema pydantic-ai and langchain generate for that
    binding, breaking their recorded cassettes). A product's reply has no
    such guarantee, so this judge validates it against a strict copy instead.

    This is not about stopping a vacuous pass -- `JudgeEvaluator` already
    errors a verdict whose check ids do not match the rubric's, so an empty
    or extra-keyed reply was never going to score as a pass. It is about
    which error a caller sees: this judge's contract is that a reply of the
    wrong shape is `JudgeVerdict(error="JudgeOutputInvalid: ...")` naming the
    real cause, not a `JudgeEvaluator` message about a mismatched id set
    several layers away from the object that was actually wrong. `checks`
    has no default here (unlike on `JudgeOutput`) so `{}` is that wrong shape
    too, and `title="JudgeOutput"` keeps the `ValidationError` text naming
    the shape callers actually asked for, not this private subclass.
    """

    model_config = ConfigDict(extra="forbid", title="JudgeOutput")

    checks: list[CheckResult]


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


class AmbiguousReply(Exception):
    """Raised by `extract_json_object` when the reply contains two or more
    top-level JSON objects -- e.g. a graded response quoting one verdict
    inside an injection attempt, followed by the model's real one. Picking
    either silently would let quoted text in the response masquerade as
    the verdict, so the caller is handed the count and must treat the
    reply as unreadable rather than choose."""

    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(f"{count} top-level JSON objects in the response")


def extract_json_object(text: str) -> str | None:
    """The first balanced `{ ... }` in `text`, or None.

    A product answers in prose, often around a code fence; the verdict is
    the object inside. Braces inside JSON strings are skipped by tracking
    string state and escapes, so evidence quoting a `}` does not end the
    object early; a quote seen outside any object (prose, not JSON) is not
    string state at all, so it cannot swallow a later `{` that starts the
    real object.

    One pass over `text`, not one scan per candidate `{`: a stack holds the
    positions of unmatched `{` seen outside a string. Each `}` outside a
    string pops the most recent one, completing an object that runs from
    the popped position to the current index -- a `}` with nothing to pop
    is stray and ignored. Every completion is a candidate; the one with the
    earliest start wins, because an outer object (pushed first, so popped
    last) can complete after an inner one already has, and "first balanced
    object" means the earliest-opening brace, not the first to close.

    A completion that empties the stack closes a *top-level* span -- one
    not nested inside another still-open one. Only a top-level span that
    parses as a JSON object counts: prose braces such as `{name}` are not
    objects and must neither be returned in place of the verdict nor make
    the reply look ambiguous. Two or more counted spans mean the reply holds
    two separate JSON objects (siblings, not one nested in the other), which
    is ambiguous: `AmbiguousReply` is raised naming the count instead of
    silently returning whichever opened first. Exactly one counted span is
    the verdict. None at all (an outer object that never closes, say) falls
    back to the earliest-opening completion when that parses as an object,
    so a nested object inside a broken outer one is still found; prose
    braces alone yield None. Parsing happens only at top-level completions,
    whose spans never overlap, plus at most once for the fallback, so the
    pass stays linear.
    """
    stack: list[int] = []
    best: tuple[int, int] | None = None
    best_object: tuple[int, int] | None = None
    top_level_completions = 0
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
        if char == '"' and stack:
            in_string = True
        elif char == "{":
            stack.append(index)
        elif char == "}" and stack:
            start = stack.pop()
            if best is None or start < best[0]:
                best = (start, index)
            if not stack and _is_json_object(text[start : index + 1]):
                top_level_completions += 1
                if best_object is None:
                    best_object = (start, index)
    if top_level_completions > 1:
        raise AmbiguousReply(top_level_completions)
    if best_object is not None:
        return text[best_object[0] : best_object[1] + 1]
    if best is None:
        return None
    # No top-level object closed. The earliest completion is a nested one
    # inside an outer object that never closed; it is the verdict only if
    # it is itself a JSON object, otherwise the reply holds none.
    span = text[best[0] : best[1] + 1]
    return span if _is_json_object(span) else None


def _is_json_object(span: str) -> bool:
    try:
        return isinstance(json.loads(span), dict)
    except ValueError:
        return False


class ProductJudge:
    """Grades a rubric with a product, behind the framework-agnostic protocol."""

    needs_api_key = False

    def __init__(self, product: Product) -> None:
        self._product = replace(product, argv=(*product.argv, *product.judge_args))
        self.name = product.name

    def judge(self, request: JudgeRequest) -> JudgeVerdict:
        product = self._product
        prompt = judge_prompt(request)
        size = len(prompt.encode("utf-8"))
        if size > MAX_PROMPT_BYTES:
            return JudgeVerdict(
                error=(
                    f"prompt is {size} bytes; a product judge sends at most "
                    f"{MAX_PROMPT_BYTES} bytes as one argument"
                )
            )
        try:
            cwd = Path(tempfile.mkdtemp(prefix=JUDGE_PREFIX)).resolve()
        except OSError as exc:
            return JudgeVerdict(error=f"{type(exc).__name__}: {exc}")
        try:
            trace = read_trace(product, invoke(product, prompt, cwd))
        finally:
            shutil.rmtree(cwd, ignore_errors=True)
        spend = {
            "input_tokens": trace.input_tokens,
            "output_tokens": trace.output_tokens,
            "cost_usd": trace.cost_usd,
            "cost_note": trace.cost_note,
            "model": trace.model,
        }
        if trace.error is not None:
            return JudgeVerdict(error=trace.error, model=trace.model)
        try:
            raw = extract_json_object(trace.output)
        except AmbiguousReply as exc:
            return JudgeVerdict(
                error=(
                    f"JudgeOutputInvalid: {exc.count} JSON objects in the response; "
                    "expected exactly one"
                ),
                **spend,
            )
        if raw is None:
            return JudgeVerdict(error="JudgeOutputInvalid: no JSON object in the response", **spend)
        try:
            output = _RawVerdict.model_validate_json(raw)
        except ValidationError as exc:
            return JudgeVerdict(error=f"JudgeOutputInvalid: {exc}", **spend)
        return JudgeVerdict(checks=list(output.checks), **spend)

    def preflight(self) -> ProductStatus:
        """The executable is on PATH and starts, before any case runs.

        A judge has no cases to inspect; the orchestrator calls this with no
        arguments and puts the status beside the runners'.
        """
        product = self._product
        executable = find_executable(product, "judge")
        version = probe_version(product, executable, "judge")
        return ProductStatus(
            name=product.name, executable=executable, version=version, trust=TRUST_NOTE
        )
