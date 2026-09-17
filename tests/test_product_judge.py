"""ProductJudge: the existing judge prompt through a product, the verdict read back."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from skill_lens.judges.product import (
    CLOSING_INSTRUCTION,
    ProductJudge,
    extract_json_object,
    judge_prompt,
)
from skill_lens.judges.prompt import SYSTEM_PROMPT, render_request
from skill_lens.models import CheckResult, JudgeRequest, RubricCheck
from skill_lens.runners.product import PRESETS, Product, ProductSetupError
from skill_lens.runners.traces import parse_claude_code, parse_copilot

FAKE = Path(__file__).parent / "fake_product.py"
FIXTURES = Path(__file__).parent / "fixtures" / "products"

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


# --- ProductJudge ---


def _product(parse=parse_claude_code, **overrides) -> Product:
    fields = dict(
        name="claude-code",
        argv=(sys.executable, str(FAKE), "-p", "{prompt}"),
        skills_dir=".claude/skills",
        invoke="/{name} {task}",
        parse=parse,
        version_command=(sys.executable, str(FAKE), "--version"),
        judge_args=("--tools", ""),
    )
    fields.update(overrides)
    return Product(**fields)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_PRODUCT_RECORD", str(record))
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "claude-code-verdict.jsonl"))
    monkeypatch.setenv("FAKE_PRODUCT_SKILLS_DIR", ".claude/skills")
    monkeypatch.delenv("FAKE_PRODUCT_MODE", raising=False)
    return lambda: json.loads(record.read_text(encoding="utf-8"))


REQUEST = JudgeRequest(
    task="greet Ada",
    output="Hello, Ada.",
    checks=[
        RubricCheck(id="c1", text="greets by name"),
        RubricCheck(id="c2", text="asks a question"),
    ],
)


def test_the_presets_grade_with_the_verified_extra_args():
    assert PRESETS["claude-code"].judge_args == ("--tools", "")
    assert PRESETS["copilot"].judge_args == ()


def test_a_verdict_is_read_from_the_products_answer(fake):
    verdict = ProductJudge(_product()).judge(REQUEST)
    assert verdict.error is None
    assert verdict.checks == [
        CheckResult(id="c1", passed=True, evidence="Hello, Ada."),
        CheckResult(id="c2", passed=False, evidence="no name given"),
    ]
    assert verdict.input_tokens == 903 and verdict.output_tokens == 60
    assert verdict.cost_usd == pytest.approx(0.0123)
    assert verdict.model == "claude-opus-5[1m]"


def test_the_judge_sends_the_shared_prompt_with_the_extra_args_and_no_skill(fake):
    ProductJudge(_product()).judge(REQUEST)
    seen = fake()
    assert seen["prompt"] == judge_prompt(REQUEST)
    assert seen["argv"][-2:] == ["--tools", ""]
    assert seen["skill_files"] == []
    assert not Path(seen["cwd"]).exists()  # the judge's directory is gone


def test_a_verdict_inside_prose_and_a_fence_is_still_read(fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "copilot-verdict.jsonl"))
    verdict = ProductJudge(_product(name="copilot", parse=parse_copilot, judge_args=())).judge(
        REQUEST
    )
    assert verdict.error is None
    assert verdict.checks == [CheckResult(id="c1", passed=True, evidence="Hello, Ada.")]
    assert verdict.cost_usd == 0.0
    assert "premium request" in verdict.cost_note


def test_a_generic_product_is_graded_from_stdout(fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "copilot-verdict.jsonl"))
    product = _product(name="cli", parse=None, version_command=None, judge_args=())
    verdict = ProductJudge(product).judge(REQUEST)
    # stdout is the whole JSONL trace; the first balanced object in it is the
    # first event, not a verdict -- so this is an invalid verdict, honestly.
    assert verdict.error is not None
    assert verdict.error.startswith("JudgeOutputInvalid:")


def test_no_object_in_the_reply_is_an_invalid_verdict(fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "claude-code-negative.jsonl"))
    verdict = ProductJudge(_product()).judge(REQUEST)
    assert verdict.error == "JudgeOutputInvalid: no JSON object in the response"


def test_the_wrong_shape_is_an_invalid_verdict(fake, tmp_path, monkeypatch):
    trace = (FIXTURES / "claude-code-verdict.jsonl").read_text(encoding="utf-8")
    bad = trace.replace('\\"passed\\": true', '\\"passed\\": \\"maybe\\"')
    (tmp_path / "bad.jsonl").write_text(bad, encoding="utf-8")
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(tmp_path / "bad.jsonl"))
    verdict = ProductJudge(_product()).judge(REQUEST)
    assert verdict.error is not None and verdict.error.startswith("JudgeOutputInvalid:")


def test_a_product_failure_is_the_verdicts_error(fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "exit3")
    verdict = ProductJudge(_product()).judge(REQUEST)
    assert verdict.error == "claude-code exited with code 3: boom"


def test_a_timeout_is_the_verdicts_error(fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "sleep")
    verdict = ProductJudge(_product(timeout_seconds=0.5)).judge(REQUEST)
    assert verdict.error == "claude-code timed out after 0.5s"


def test_an_oversized_prompt_is_refused(fake):
    request = JudgeRequest(
        task="t", output="x" * (100 * 1024), checks=[RubricCheck(id="c", text="t")]
    )
    verdict = ProductJudge(_product()).judge(request)
    assert verdict.error is not None
    assert "a product judge sends at most 102400 bytes" in verdict.error


def test_the_judge_never_raises_for_a_missing_executable(fake):
    product = _product(argv=("/nonexistent/product", "-p", "{prompt}"))
    verdict = ProductJudge(product).judge(REQUEST)
    assert verdict.error is not None and verdict.error.startswith("cannot start")


def test_preflight_checks_the_executable_and_records_the_product():
    status = ProductJudge(_product()).preflight()
    assert status.name == "claude-code"
    assert status.version == "fake 1.2.3"
    with pytest.raises(
        ProductSetupError, match=r"judge claude-code: 'no-such-thing' is not on PATH"
    ):
        ProductJudge(_product(argv=("no-such-thing", "{prompt}"), version_command=None)).preflight()


def test_the_judge_name_is_the_products():
    judge = ProductJudge(_product(name="copilot"))
    assert judge.name == "copilot"
    assert judge.needs_api_key is False
