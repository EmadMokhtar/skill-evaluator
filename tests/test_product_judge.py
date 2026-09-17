"""ProductJudge: the existing judge prompt through a product, the verdict read back."""

from __future__ import annotations

import time

import pytest

from skill_lens.judges.product import CLOSING_INSTRUCTION, extract_json_object, judge_prompt
from skill_lens.judges.prompt import SYSTEM_PROMPT, render_request
from skill_lens.models import JudgeRequest, RubricCheck

# --- extract_json_object ---


@pytest.mark.parametrize(
    "text, expected",
    [
        ('{"checks": []}', '{"checks": []}'),
        ('Sure!\n```json\n{"checks": []}\n```\nDone.', '{"checks": []}'),
        ('Here: {"a": {"b": 1}} and more', '{"a": {"b": 1}}'),
        ('{"evidence": "has a } inside"} trailing', '{"evidence": "has a } inside"}'),
        ('{"evidence": "escaped \\" quote }"}', '{"evidence": "escaped \\" quote }"}'),
        ("prose only", None),
        ("", None),
        ('{"unbalanced": 1', None),
        ('{ broken { "ok": 1 }', '{ "ok": 1 }'),  # only the inner object ever closes
    ],
)
def test_extract_json_object_finds_the_first_balanced_object(text, expected):
    assert extract_json_object(text) == expected


def test_extract_json_object_is_linear_on_a_run_of_stray_braces():
    unmatched = "{" * 20_000
    start = time.perf_counter()
    result = extract_json_object(unmatched)
    elapsed = time.perf_counter() - start
    assert result is None
    assert elapsed < 1

    start = time.perf_counter()
    result = extract_json_object(unmatched + "}")
    elapsed = time.perf_counter() - start
    assert result == "{}"
    assert elapsed < 1


# --- judge_prompt ---


def test_the_judge_prompt_is_the_shared_prompt_plus_the_closing_line():
    request = JudgeRequest(task="t", output="o", checks=[RubricCheck(id="c1", text="says hi")])
    text = judge_prompt(request)
    assert text.startswith(SYSTEM_PROMPT)
    assert render_request(request) in text
    assert text.endswith(CLOSING_INSTRUCTION)
    assert '"checks"' in CLOSING_INSTRUCTION and "no code fence" in CLOSING_INSTRUCTION
