# skill-lens M9 Part 2 — Product Judge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grade `judge:` rubrics through an agent product's own CLI (`judge = "copilot"`, `"claude-code"` or `"cli"`), so an engineer with a product seat but no provider API key can run every kind of eval case, not only assertion cases.

**Architecture:** `ProductJudge` (`judges/product.py`) behind the unchanged `Judge` protocol reuses Part 1's `Product`, `invoke` and `read_trace`: the existing judge prompt (`judges/prompt.py`) plus one closing line asking for the JSON object only is sent as the product's prompt, in an empty temporary working directory with no skill delivered; the first balanced JSON object in the response is validated as `JudgeOutput`. Anything else is `JudgeVerdict.error`. The same `[runners.<name>]` table configures runner and judge.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, `subprocess`, pytest. No agent framework.

**Spec:** `docs/superpowers/specs/2026-09-17-skill-lens-m9-design.md` §6 (Part 2). Depends on Part 1 (`docs/superpowers/plans/2026-09-17-skill-lens-m9-part1-product-runner.md`) being merged.

## Global Constraints

Everything in Part 1's Global Constraints, plus:

- **Judges never raise for a product failure**; they set `JudgeVerdict.error`. A malformed verdict is `JudgeVerdict.error`, one attempt, never a low score.
- **skill-lens derives `passed` and `score` from per-check verdicts**; the judge is never asked for a blended number. `JudgeEvaluator` is untouched.
- **Judge spend never enters `RunResult`**; it lives on `JudgeVerdict` → `EvalScore.cost_usd`.
- **The judge's working directory holds no skill.** It grades text; it must not discover the skill under test.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/skill_lens/judges/product.py` (new) | `extract_json_object`, `judge_prompt`, `ProductJudge` (with `preflight()`). |
| `src/skill_lens/runners/product.py` | `Product.judge_args` (extra argv for grading; the `claude-code` preset gets `("--tools", "")`). |
| `src/skill_lens/orchestrator.py` | Calls the judge's optional `preflight()`; its status joins `RunReport.products` (deduplicated). |
| `src/skill_lens/cli.py` | Judge factory; `judge = "copilot"` etc. |
| `tests/fixtures/products/claude-code-verdict.jsonl`, `copilot-verdict.jsonl` (new) | Traces whose answer is a verdict. |
| `tests/test_product_judge.py` (new); additions to `test_product_runner.py`, `test_orchestrator.py`, `test_cli.py`, `test_config.py`. |
| Docs: `docs/runners.md`, `docs/configuration.md`, `docs/cli.md`, `docs/roadmap.md`, `ARCHITECTURE.md`, `CLAUDE.md`, `examples/skill-lens.toml`. |

---

### Task 1: `extract_json_object` and the judge prompt

**Files:**
- Create: `src/skill_lens/judges/product.py` (pure parts only)
- Test: `tests/test_product_judge.py`

**Interfaces:**
- Consumes: `judges.prompt.SYSTEM_PROMPT`, `render_request`.
- Produces: `extract_json_object(text: str) -> str | None`; `judge_prompt(request: JudgeRequest) -> str`; `CLOSING_INSTRUCTION`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_product_judge.py
"""ProductJudge: the existing judge prompt through a product, the verdict read back."""

from __future__ import annotations

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
        ('{ broken { "ok": 1 }', '{ "ok": 1 }'),  # retries from the next opening brace
    ],
)
def test_extract_json_object_finds_the_first_balanced_object(text, expected):
    assert extract_json_object(text) == expected


# --- judge_prompt ---


def test_the_judge_prompt_is_the_shared_prompt_plus_the_closing_line():
    request = JudgeRequest(task="t", output="o", checks=[RubricCheck(id="c1", text="says hi")])
    text = judge_prompt(request)
    assert text.startswith(SYSTEM_PROMPT)
    assert render_request(request) in text
    assert text.endswith(CLOSING_INSTRUCTION)
    assert '"checks"' in CLOSING_INSTRUCTION and "no code fence" in CLOSING_INSTRUCTION
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_product_judge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'skill_lens.judges.product'`

- [ ] **Step 3: Write the pure parts**

```python
# src/skill_lens/judges/product.py
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
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_product_judge.py -v && uv run ruff check . && uv run ruff format .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/judges/product.py tests/test_product_judge.py
git commit -m "feat: extract a judge verdict from a product's free-text reply

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `ProductJudge`

**Files:**
- Modify: `src/skill_lens/runners/product.py` (`Product.judge_args`; the `claude-code` preset)
- Modify: `src/skill_lens/judges/product.py` (add `ProductJudge`)
- Create: `tests/fixtures/products/claude-code-verdict.jsonl`, `tests/fixtures/products/copilot-verdict.jsonl`
- Test: `tests/test_product_judge.py`, `tests/test_product_runner.py`

**Interfaces:**
- Consumes: `runners.product.Product`, `invoke`, `read_trace`, `MAX_PROMPT_BYTES`, `TRUST_NOTE`, `probe_version`, `ProductSetupError`; `models.JudgeOutput`, `JudgeVerdict`, `ProductStatus`.
- Produces: `Product.judge_args: tuple[str, ...] = ()`; `ProductJudge(product)` with `name`, `needs_api_key = False`, `judge(request) -> JudgeVerdict`, `preflight() -> ProductStatus`.

- [ ] **Step 1: Write the fixtures**

`tests/fixtures/products/claude-code-verdict.jsonl`:

```
{"type":"system","subtype":"init","cwd":"/judge","session_id":"j1","tools":[],"model":"claude-opus-5[1m]","permissionMode":"bypassPermissions","skills":[],"claude_code_version":"9.9.9"}
{"type":"assistant","message":{"model":"claude-opus-5","content":[{"type":"text","text":"{\"checks\": [{\"id\": \"c1\", \"passed\": true, \"evidence\": \"Hello, Ada.\"}, {\"id\": \"c2\", \"passed\": false, \"evidence\": \"no name given\"}]}"}],"usage":{"input_tokens":3,"cache_creation_input_tokens":0,"cache_read_input_tokens":900,"output_tokens":60}},"session_id":"j1"}
{"type":"result","subtype":"success","is_error":false,"num_turns":1,"result":"{\"checks\": [{\"id\": \"c1\", \"passed\": true, \"evidence\": \"Hello, Ada.\"}, {\"id\": \"c2\", \"passed\": false, \"evidence\": \"no name given\"}]}","session_id":"j1","total_cost_usd":0.0123,"duration_ms":1500,"duration_api_ms":1400,"stop_reason":"end_turn","usage":{"input_tokens":3,"cache_creation_input_tokens":0,"cache_read_input_tokens":900,"output_tokens":60}}
```

`tests/fixtures/products/copilot-verdict.jsonl`:

```
{"type":"session.tools_updated","data":{"model":"gpt-5.4"},"id":"e1","timestamp":"2026-09-17T11:00:00.000Z","ephemeral":true}
{"type":"user.message","data":{"content":"...","attachments":[]},"id":"e2","timestamp":"2026-09-17T11:00:00.100Z"}
{"type":"assistant.turn_start","data":{"turnId":"0"},"id":"e3","timestamp":"2026-09-17T11:00:00.200Z"}
{"type":"assistant.message","data":{"messageId":"m1","content":"Here is the verdict:\n```json\n{\"checks\": [{\"id\": \"c1\", \"passed\": true, \"evidence\": \"Hello, Ada.\"}]}\n```","toolRequests":[]},"id":"e4","timestamp":"2026-09-17T11:00:02.000Z"}
{"type":"assistant.turn_end","data":{"turnId":"0"},"id":"e5","timestamp":"2026-09-17T11:00:02.100Z"}
{"type":"result","timestamp":"2026-09-17T11:00:02.300Z","sessionId":"j2","exitCode":0,"usage":{"premiumRequests":1,"totalApiDurationMs":1800,"sessionDurationMs":2300,"codeChanges":{"linesAdded":0,"linesRemoved":0,"filesModified":[]}}}
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_product_judge.py`:

```python
import json
import sys
from pathlib import Path

from skill_lens.judges.product import ProductJudge
from skill_lens.models import CheckResult
from skill_lens.runners.product import PRESETS, Product, ProductSetupError
from skill_lens.runners.traces import parse_claude_code, parse_copilot

FAKE = Path(__file__).parent / "fake_product.py"
FIXTURES = Path(__file__).parent / "fixtures" / "products"


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
    checks=[RubricCheck(id="c1", text="greets by name"), RubricCheck(id="c2", text="asks a question")],
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
    verdict = ProductJudge(_product(name="copilot", parse=parse_copilot, judge_args=())).judge(REQUEST)
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
    request = JudgeRequest(task="t", output="x" * (100 * 1024), checks=[RubricCheck(id="c", text="t")])
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
    with pytest.raises(ProductSetupError, match=r"judge claude-code: 'no-such-thing' is not on PATH"):
        ProductJudge(_product(argv=("no-such-thing", "{prompt}"), version_command=None)).preflight()


def test_the_judge_name_is_the_products():
    judge = ProductJudge(_product(name="copilot"))
    assert judge.name == "copilot"
    assert judge.needs_api_key is False
```

Add to `tests/test_product_runner.py`'s `test_the_presets_are_the_verified_spellings`: `assert PRESETS["claude-code"].judge_args == ("--tools", "")` and `assert PRESETS["copilot"].judge_args == ()`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_product_judge.py tests/test_product_runner.py -v`
Expected: FAIL (`unexpected keyword argument 'judge_args'`, `ImportError: ProductJudge`).

- [ ] **Step 4: Add `judge_args` and the judge**

In `src/skill_lens/runners/product.py`, add to `Product` after `max_output_bytes`:

```python
    # Appended only when the product grades a rubric: what keeps it from
    # acting while it judges. Verified for claude-code (`--tools ""` disables
    # every tool); Copilot has no verified equivalent and gets none.
    judge_args: tuple[str, ...] = ()
```

and `judge_args=("--tools", ""),` on the `claude-code` preset. Also make the `preflight` executable-check message reusable: extract

```python
def find_executable(product: Product, role: str) -> str:
    """`shutil.which` on the product's executable, or `ProductSetupError` naming `role`."""
    executable = shutil.which(product.argv[0])
    if executable is None:
        raise ProductSetupError(
            f"{role} {product.name}: {product.argv[0]!r} is not on PATH; {_install_hint(product)}"
        )
    return executable
```

and have `ProductRunner.preflight` call `find_executable(product, "runner")` (its existing test `runner claude-code: ... is not on PATH` still matches).

In `src/skill_lens/judges/product.py`, add the imports and the class:

```python
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

from pydantic import ValidationError

from skill_lens.models import JudgeOutput, JudgeRequest, JudgeVerdict, ProductStatus
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
        raw = extract_json_object(trace.output)
        if raw is None:
            return JudgeVerdict(error="JudgeOutputInvalid: no JSON object in the response", **spend)
        try:
            output = JudgeOutput.model_validate_json(raw)
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
```

Keep the module's existing docstring and pure functions above the class.

- [ ] **Step 5: Run the tests and lint**

Run: `uv run pytest tests/test_product_judge.py tests/test_product_runner.py tests/test_product_preflight.py -v && uv run ruff check . && uv run ruff format .`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/skill_lens/judges/product.py src/skill_lens/runners/product.py tests/test_product_judge.py tests/test_product_runner.py tests/fixtures/products
git commit -m "feat: add the product judge

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The orchestrator and the command line know the judge

**Files:**
- Modify: `src/skill_lens/orchestrator.py` (`_preflight_runners`; `run_evals`)
- Modify: `src/skill_lens/cli.py` (`_JUDGES`; judge construction in `run`)
- Test: `tests/test_orchestrator.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `ProductJudge` (Task 2); Part 1's `_KEYED_RUNNERS` pattern, `Config.product`, `PRODUCT_NAMES`.
- Produces: `_KEYED_JUDGES`, `_JUDGE_NAMES`, `_build_judge(name, settings, model_name) -> Judge`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py`:

```python
def test_a_judges_preflight_status_joins_the_products(tmp_path):
    class ProbedJudge(FakeJudge):
        name = "probed-judge"

        def preflight(self):
            return ProductStatus(name="probed-judge", executable="/bin/j", version="2", trust="t")

    report = run_evals([_skill_with_cases(tmp_path)], [_runner()], judge=ProbedJudge())
    assert report.products == [
        ProductStatus(name="probed-judge", executable="/bin/j", version="2", trust="t")
    ]


def test_the_same_product_as_runner_and_judge_is_listed_once(tmp_path):
    status = ProductStatus(name="probed", executable="/bin/probed", version="1", trust="t")

    class SameJudge(FakeJudge):
        name = "probed"

        def preflight(self):
            return status

    report = run_evals([_skill_with_cases(tmp_path)], [_PreflightRunner()], judge=SameJudge())
    assert report.products == [status]
```

Append to `tests/test_cli.py`:

```python
JUDGED_CASES_YAML = """cases:
  - name: pongs
    task: Please ping.
    judge:
      rubric:
        - greets by name
        - asks a question
"""


def test_a_product_judge_grades_a_rubric(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(PRODUCT_FIXTURES / "claude-code-verdict.jsonl"))
    skill_dir = _make_skill(tmp_path, cases=JUDGED_CASES_YAML)
    config = _product_config(tmp_path, name="claude-code")
    with (tmp_path / "skill-lens.toml").open("a", encoding="utf-8") as handle:
        handle.write('judge = "claude-code"\n')
    # The same fake serves runner and judge here: the runner reads a verdict
    # trace as its output (fine -- assertions are not what this test checks)
    # and the judge reads the two-check verdict.
    result = runner.invoke(
        app, ["run", str(skill_dir), "--runner", "claude-code", "--config", str(config)]
    )
    assert result.exit_code == 1, result.output  # c2 failed in the fixture verdict
    assert "product claude-code" in result.output
    assert "judge" in result.output


def test_a_product_judge_needs_no_key_and_no_judge_model(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(PRODUCT_FIXTURES / "claude-code-verdict.jsonl"))
    skill_dir = _make_skill(tmp_path)  # no judge: block, so the judge is never called
    config = _product_config(tmp_path, name="claude-code")
    with (tmp_path / "skill-lens.toml").open("a", encoding="utf-8") as handle:
        handle.write('judge = "claude-code"\n')
    result = runner.invoke(app, ["run", str(skill_dir), "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert "OPENAI_API_KEY" not in result.output


def test_judge_model_with_a_product_judge_is_a_user_error(tmp_path):
    skill_dir = _make_skill(tmp_path)
    (tmp_path / "skill-lens.toml").write_text('judge = "copilot"\n', encoding="utf-8")
    result = runner.invoke(
        app, ["run", str(skill_dir), "--judge-model", "x", "--config", str(tmp_path / "skill-lens.toml")]
    )
    assert result.exit_code == 2
    assert "--judge-model is read by" in plain(result.output)


def test_a_product_judge_that_is_not_installed_is_exit_2_before_any_case(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    skill_dir = _make_skill(tmp_path)
    (tmp_path / "skill-lens.toml").write_text('judge = "copilot"\n', encoding="utf-8")
    result = runner.invoke(
        app, ["run", str(skill_dir), "--config", str(tmp_path / "skill-lens.toml")]
    )
    assert result.exit_code == 2
    assert "judge copilot: 'copilot' is not on PATH" in plain(result.output)
```

Note `_product_config` writes `skill-lens.toml` into `tmp_path`; the tests above append `judge = ...` to it. A TOML key after a `[runners.x]` table would belong to that table, so write the `judge` line **first**: change `_product_config` in Part 1's test file to accept `judge: str | None = None` and emit `judge = "..."` before the table. Update the two new tests to pass `judge="claude-code"` instead of appending.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator.py tests/test_cli.py -k "judge" -v`
Expected: FAIL (`unknown judge: claude-code`; products missing the judge).

- [ ] **Step 3: Wire it**

`src/skill_lens/orchestrator.py` — rename `_preflight_runners` to `_preflight_hooks(plan, runners, judge)` and, after the runner loop:

```python
    hook = getattr(judge, "preflight", None) if judge is not None else None
    if hook is not None:
        status = hook()
        if status is not None and status not in statuses:
            # The same product serving as runner and judge is one product.
            statuses.append(status)
    return statuses
```

Call it as `products = _preflight_hooks(plan, runners, judge)`. Update the docstring: "...and the judge's, which takes no arguments."

`src/skill_lens/cli.py`:

```python
from skill_lens.judges.base import Judge
from skill_lens.judges.product import ProductJudge

_KEYED_JUDGES = {"pydantic-ai": PydanticAIJudge, "langchain": LangChainJudge}
_JUDGE_NAMES: tuple[str, ...] = ("fake", *_KEYED_JUDGES, *PRODUCT_NAMES)


def _build_judge(name: str, settings: Config, model_name: str) -> Judge:
    """One judge by name, the way `_build_runner` builds a runner."""
    if name == "fake":
        return FakeJudge()
    if name in _KEYED_JUDGES:
        return _KEYED_JUDGES[name](
            model=model_name,
            temperature=settings.judge_temperature,
            retries=settings.retries,
            retry_backoff_seconds=settings.retry_backoff_seconds,
        )
    return ProductJudge(settings.product(name))
```

Delete `_JUDGES`. In `run`: `if judge_name not in _JUDGE_NAMES:` → `BadParameter`; `judge_needs_key = judge_name in _KEYED_JUDGES`; replace the `if judge_needs_key: ... active_judge = judge_class(...) else: active_judge = judge_class()` block with:

```python
        if judge_needs_key:
            _require_a_model("--judge-model", resolved_judge_model)
            check_api_key(resolved_judge_model, os.environ)
        active_judge = _build_judge(judge_name, settings, resolved_judge_model)
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_orchestrator.py tests/test_cli.py tests/test_cli_init.py -v && uv run ruff check . && uv run ruff format .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/orchestrator.py src/skill_lens/cli.py tests/test_orchestrator.py tests/test_cli.py
git commit -m "feat: grade rubrics through a product from the command line

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Documentation, the full suite, the pull request

**Files:**
- Modify: `docs/runners.md`, `docs/configuration.md`, `docs/cli.md`, `docs/roadmap.md`, `ARCHITECTURE.md`, `CLAUDE.md`, `examples/skill-lens.toml`

- [ ] **Step 1: Write the docs**

`docs/runners.md`, at the end of "Product runners":

```markdown
**Judging with a product.** `judge = "copilot"`, `"claude-code"` or `"cli"` grades every
`judge:` block through the same product, using the same `[runners.<name>]` table. The
shared judge prompt goes in as text with one closing line asking for the JSON verdict
only; the first JSON object in the reply is the verdict, one entry per check, evidence
required. The judge runs in an empty directory with no skill delivered, and Claude Code
grades with `--tools ""` (no tools); Copilot has no verified equivalent and keeps its
tools. A reply with no readable verdict is an **errored** case, never a low score —
the same rule as the framework judges. `judge_temperature` is not consulted: no product
exposes it. Judge spend is reported as judge overhead, as always.
```

`docs/configuration.md`: in "Judging", add: "`judge` also accepts `copilot`, `claude-code` and `cli`, each configured by its `[runners.<name>]` table; `judge_model` and `judge_temperature` are then not read (`--judge-model` is a user error)." In the product-runners section: "The same table serves the judge."

`docs/cli.md`: under `--judge-model`, extend: "...or a product judge; otherwise exit 2."

`docs/roadmap.md`: change the M9 row's status to `shipped` and add "## What M9 part 2 shipped" (three sentences: the product judge, the prompt, the verdict rule).

`ARCHITECTURE.md`: add the row `| `judges/product.py` | The product judge: the shared judge prompt as one text turn, the first balanced JSON object in the reply validated as `JudgeOutput`, everything else `JudgeVerdict.error`. Imports no agent framework. |`; note in the `orchestrator.py` row that the judge's `preflight()` is called too.

`CLAUDE.md`: status "**M9 complete**"; add the bullet "**A product judge's verdict is the first balanced JSON object in the reply, validated as `JudgeOutput`; anything else is `JudgeVerdict.error`.** The judge's working directory holds no skill; `judge_temperature` is not consulted."

`examples/skill-lens.toml`: beside the `judge = "pydantic-ai"` line, add the comment `# "copilot" / "claude-code" / "cli" grade through the product named in [runners.<name>].`

- [ ] **Step 2: Verify everything**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv sync --group docs && uv run mkdocs build --strict`
Expected: all PASS.

- [ ] **Step 3: Live check once**

Run: `uv run pytest tests/test_integration_live.py -m integration -k claude_code -v` after adding `judge=ProductJudge(PRESETS["claude-code"])` to Part 1's Claude Code live test (the `greeting` example has no `judge:` block, so also run `examples/order-support`, which has two, against `ProductRunner` + `ProductJudge` in a third live test mirroring the first; skip on a missing executable).

- [ ] **Step 4: Commit and open the pull request**

```bash
git add docs ARCHITECTURE.md CLAUDE.md examples/skill-lens.toml tests/test_integration_live.py
git commit -m "docs: document the product judge

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git push -u origin HEAD
gh pr create --assignee @EmadMokhtar --title "feat: grade rubrics through an agent product's CLI" --body "$(cat <<'BODY'
Adds the product judge: `judge = "copilot"`, `"claude-code"` or `"cli"` sends the
shared judge prompt through the product named in `[runners.<name>]` and reads the
per-check verdict from the first JSON object in its reply. With Part 1 this makes every
eval case kind runnable without a provider API key.
Design: docs/superpowers/specs/2026-09-17-skill-lens-m9-design.md §6.

Closes #44

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
)"
```

---

## Self-review against the spec

- §6 judge: prompt (T1), empty cwd with no skill (T2), `--tools ""` for `claude-code` only (T2, `judge_args`), first balanced object validated as `JudgeOutput` else `JudgeVerdict.error` (T1, T2), tokens/cost/model from the trace (T2), `judge_temperature` not consulted (T3 wiring, T4 docs), same table (T3 via `Config.product`). Preflight for the judge (spec §10 item 7, "preflight spends nothing") — T2 `preflight()`, T3 orchestrator hook.
- Names used across tasks: `judge_args` (T2 → T3 tests), `find_executable`/`probe_version`/`read_trace`/`invoke` (Part 1 → T2), `_build_judge`/`_KEYED_JUDGES`/`_JUDGE_NAMES` (T3), `_preflight_hooks` (T3). Consistent with Part 1's `_build_runner`/`_KEYED_RUNNERS`/`_RUNNER_NAMES`.
