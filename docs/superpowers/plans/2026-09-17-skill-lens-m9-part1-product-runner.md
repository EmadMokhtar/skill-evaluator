# skill-lens M9 Part 1 — Product Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run every eval case through an agent product's own command-line interface (`copilot`, `claude-code`, or a configured `cli`), with the skill under test placed where that product discovers skills, and read the result back from the product's trace — so a skill written for a product is measured under it, and an engineer with a product seat but no provider API key can run skill-lens.

**Architecture:** One `ProductRunner` (`runners/product.py`) behind the unchanged `Runner` protocol, parameterised by a frozen `Product` value (argv template with a `{prompt}` element, skill directory, invocation spelling, trace parser, version command). `runners/traces.py` holds two pure parsers producing one `Trace` shape. The subprocess helpers that `scripts.py` already has move to `process.py` and are shared. Config gains a `[runners.<name>]` table; the orchestrator gains an optional per-runner `preflight` hook; the report gains `products`.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, `subprocess` (no shell), pytest. No agent framework is imported by any new module.

**Spec:** `docs/superpowers/specs/2026-09-17-skill-lens-m9-design.md` (this plan implements Part 1; Part 2 — the judge — has its own plan).

## Global Constraints

- **No agent-framework import** outside `runners/pydantic_ai.py`, `judges/pydantic_ai.py`, `runners/langchain.py`, `judges/langchain.py` (`tests/test_framework_isolation.py`).
- **`skill_lens` never appears in user-facing text**; the name is `skill-lens` (`tests/test_naming.py`).
- **Runners never raise for a product failure**; they set `RunResult.error`. The one raise is `ProductSetupError`, only from `preflight`.
- **`extra="forbid"`** on every new Pydantic model.
- **All file IO pins `encoding="utf-8"`.**
- **`shell=False` always**; the prompt is substituted as one argv element; every `subprocess.Popen`/`run` call carries `# noqa: S603` with its reason (ruff `S` rules are on).
- **Every doc test passes**: `tests/test_docs.py` requires every `Config` field in `docs/configuration.md` and every CLI flag in `docs/cli.md`.
- **Conventional Commits**, imperative, lowercase, no trailing period. Commit messages end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Run `uv run ruff check . && uv run ruff format .` before every commit; the pre-commit hook runs `cz check`.
- Tests are zero-cost and offline: the fake product is a Python script started with `sys.executable`; nothing touches the network (`--block-network` is in `addopts`).
- Default numbers, copied from the spec: `timeout_seconds = 600.0`, `max_output_bytes = 8_000_000`, prompt cap `100 * 1024` bytes, version probe timeout `30.0` s, stderr tail cap `500` characters.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/skill_lens/process.py` (new) | `group_kwargs`, `reap_and_kill_group`, `read_capped_handle` and their private helpers — moved verbatim from `scripts.py`. |
| `src/skill_lens/scripts.py` | Imports the three helpers from `process.py`; otherwise unchanged. |
| `src/skill_lens/models.py` | `Skill.markdown`, `RunResult.usage_note`, `ProductStatus`, `RunReport.products`. |
| `src/skill_lens/skills/loader.py` | `parse_skill_text` sets `markdown`. |
| `src/skill_lens/evaluators/budget.py` | `max_tokens` under `usage_note` is a failing, unevaluated check. |
| `src/skill_lens/runners/traces.py` (new) | `Trace`, `parse_lines`, `parse_copilot`, `parse_claude_code`. Pure. |
| `src/skill_lens/runners/product.py` (new) | `Product`, `PRESETS`, `ProductSetupError`, `Invocation`, `invoke`, `read_trace`, `deliver_skill`, `ProductRunner`. |
| `src/skill_lens/config.py` | `ProductSettings`, `Config.runners`, `Config.product(name)`, `PRODUCT_NAMES`. |
| `src/skill_lens/orchestrator.py` | Calls each runner's optional `preflight`; `RunReport.products`. |
| `src/skill_lens/runners/base.py` | Documents the optional `preflight` member. |
| `src/skill_lens/reporters/{console,markdown,junit,json_reporter}.py` | Render `products`. |
| `src/skill_lens/cli.py` | Runner factory; `--runner copilot`; the `--model`/`--judge-model` rule; `ProductSetupError` in `_AUTHORING_ERRORS`; the `Plan:` line. |
| `tests/fake_product.py` (new) | The stand-in product executable. |
| `tests/fixtures/products/*.jsonl` (new) | Scrubbed traces. |
| `tests/test_process.py`, `tests/test_traces.py`, `tests/test_product_runner.py`, `tests/test_product_preflight.py` (new); additions to `test_models.py`, `test_skill_loader.py`, `test_baseline_resolution.py`, `test_budget_evaluator.py`, `test_orchestrator.py`, `test_reporters.py`, `test_junit_reporter.py`, `test_markdown_reporter.py`, `test_config.py`, `test_cli.py`, `test_integration_live.py`. |
| Docs: `docs/runners.md`, `docs/configuration.md`, `docs/cli.md`, `docs/eval-files.md`, `docs/gating.md`, `docs/security.md`, `docs/ci.md`, `docs/roadmap.md`, `ARCHITECTURE.md`, `CLAUDE.md`, `examples/skill-lens.toml`. |

---

### Task 1: Move the process helpers into `process.py`

**Files:**
- Create: `src/skill_lens/process.py`
- Modify: `src/skill_lens/scripts.py` (remove lines defining `_group_kwargs`, `_REAP_TIMEOUT_SECONDS`, `_exit_observed`, `_kill_group_posix`, `_kill_tree_windows`, `_reap_and_kill_group`, `_read_capped_handle`; today `src/skill_lens/scripts.py:373-524`)
- Test: `tests/test_process.py`

**Interfaces:**
- Produces: `process.group_kwargs() -> dict[str, Any]`, `process.reap_and_kill_group(process: subprocess.Popen[bytes], timeout: float) -> tuple[int | None, bool]`, `process.read_capped_handle(handle: IO[bytes], budget: int) -> str`. Same bodies as the private functions they replace; only the leading underscore goes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_process.py
"""The process helpers shared by `scripts.py` and `runners/product.py`."""

from __future__ import annotations

import subprocess
import sys

from skill_lens.process import group_kwargs, read_capped_handle, reap_and_kill_group


def _start(code: str, stdout) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", code],
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=subprocess.DEVNULL,
        **group_kwargs(),
    )


def test_a_quick_exit_is_reaped_with_its_code(tmp_path):
    with (tmp_path / "out").open("w+b") as out:
        process = _start("import sys; sys.exit(7)", out)
        assert reap_and_kill_group(process, timeout=30.0) == (7, False)


def test_a_timeout_kills_the_process_and_says_so(tmp_path):
    with (tmp_path / "out").open("w+b") as out:
        process = _start("import time; time.sleep(60)", out)
        exit_code, timed_out = reap_and_kill_group(process, timeout=0.5)
        assert timed_out is True
        assert exit_code is None
        assert process.poll() is not None  # reaped, not left behind


def test_read_capped_handle_reads_through_the_handle_and_marks_the_cut(tmp_path):
    with (tmp_path / "out").open("w+b") as out:
        process = _start("print('x' * 1000)", out)
        reap_and_kill_group(process, timeout=30.0)
        text = read_capped_handle(out, budget=100)
    assert text.startswith("x" * 100)
    assert "[truncated, 901 bytes omitted]" in text  # 1000 x's + newline - 100


def test_read_capped_handle_returns_everything_under_the_cap(tmp_path):
    with (tmp_path / "out").open("w+b") as out:
        process = _start("print('hello')", out)
        reap_and_kill_group(process, timeout=30.0)
        assert read_capped_handle(out, budget=100) == "hello\n"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_process.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'skill_lens.process'`

- [ ] **Step 3: Create `process.py` by moving the code**

Create `src/skill_lens/process.py` with this header, then paste the bodies of `_group_kwargs`, `_REAP_TIMEOUT_SECONDS`, `_exit_observed`, `_kill_group_posix`, `_kill_tree_windows`, `_reap_and_kill_group`, `_read_capped_handle` from `scripts.py` **unchanged**, renaming only the three public ones (`_group_kwargs` → `group_kwargs`, `_reap_and_kill_group` → `reap_and_kill_group`, `_read_capped_handle` → `read_capped_handle`) and the internal calls between them:

```python
"""Start, wait for, and clean up after a child process — shared by every caller
that spawns one.

`scripts.py` (a bundled script under the sandbox) and `runners/product.py` (an
agent product's own CLI) spawn under different trust models, but the mechanics
they must agree on are the same: the child leads its own process group, the
group is killed after *every* exit so nothing is left behind, and output is
read back through the handle the harness opened -- never by reopening a path
the child could have replaced. One implementation, so two callers cannot
drift. Imports no agent framework and nothing from the rest of skill-lens.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import IO, Any
```

Keep every docstring. `_exit_observed`, `_kill_group_posix`, `_kill_tree_windows` and `_REAP_TIMEOUT_SECONDS` stay private inside `process.py`.

- [ ] **Step 4: Point `scripts.py` at the new module**

In `src/skill_lens/scripts.py`: delete the moved definitions; add `from skill_lens.process import group_kwargs, reap_and_kill_group, read_capped_handle` to the imports; in `run_script` replace `**_group_kwargs()` with `**group_kwargs()`, `_reap_and_kill_group(process, policy.timeout_seconds)` with `reap_and_kill_group(process, policy.timeout_seconds)`, and both `_read_capped_handle(` calls with `read_capped_handle(`. Remove `signal` and `IO` from `scripts.py`'s imports if ruff reports them unused (`time` stays: `preflight` uses it — check with ruff rather than guessing).

- [ ] **Step 5: Run the tests and lint**

Run: `uv run pytest tests/test_process.py tests/test_scripts.py -v && uv run ruff check . && uv run ruff format .`
Expected: all PASS; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/skill_lens/process.py src/skill_lens/scripts.py tests/test_process.py
git commit -m "refactor: move the process-group helpers into process.py

The product runner spawns a child under a different trust model from a
bundled script but needs the same kill-after-every-exit and capped-read
mechanics. One module, two callers, no drift.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `Skill.markdown`, `RunResult.usage_note`, `ProductStatus`, `RunReport.products`

**Files:**
- Modify: `src/skill_lens/models.py` (`Skill` at ~line 19, `RunResult` at ~line 113, `RunReport` at ~line 380; add `ProductStatus` after `ScriptNote`)
- Modify: `src/skill_lens/skills/loader.py:60-80` (`parse_skill_text`)
- Test: `tests/test_models.py`, `tests/test_skill_loader.py`, `tests/test_baseline_resolution.py`, `tests/test_arms.py`

**Interfaces:**
- Produces: `Skill.markdown: str = ""`; `RunResult.usage_note: str = ""`; `ProductStatus(name: str, executable: str, version: str, trust: str)`; `RunReport.products: list[ProductStatus]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_models.py`:

```python
from skill_lens.models import ProductStatus


def test_run_result_carries_a_usage_note_for_tokens_it_could_not_count():
    result = RunResult(usage_note="copilot did not report token usage")
    assert result.tokens == 0
    assert result.usage_note == "copilot did not report token usage"


def test_a_product_status_is_a_typed_record():
    status = ProductStatus(
        name="copilot", executable="/usr/local/bin/copilot", version="1.0.37", trust="t"
    )
    assert status.model_dump() == {
        "name": "copilot",
        "executable": "/usr/local/bin/copilot",
        "version": "1.0.37",
        "trust": "t",
    }


def test_a_report_lists_no_products_by_default():
    assert RunReport().products == []


def test_a_skill_defaults_to_no_markdown():
    assert Skill(name="s", path=Path("/tmp/s")).markdown == ""
```

Append to `tests/test_skill_loader.py`:

```python
def test_the_skill_carries_its_file_text_verbatim(tmp_path):
    body = SKILL_MD + "\n<!-- trailing comment the parser ignores -->\n"
    _write_skill(tmp_path, "pdf", body)
    (skill,) = load_skills(tmp_path / "pdf")
    assert skill.markdown == body
```

Append to `tests/test_baseline_resolution.py`:

```python
def test_the_previous_version_carries_its_own_file_text(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("1.0.0", "v1"), "first")
    _commit(repo, _skill_md("1.1.0", "v2"), "second")
    previous = _resolve(tmp_path, parse_skill_file(repo / "SKILL.md"))
    assert previous.markdown == _skill_md("1.0.0", "v1")
```

(If `parse_skill_file` is not already imported at the top of that file, add `from skill_lens.skills.loader import parse_skill_file`; look at how the existing tests there build the candidate skill and use the same call.)

Append to `tests/test_arms.py` (it already imports `_baseline_skill` or drives `run_evals` with `baseline="none"` — follow the file's existing helper):

```python
def test_the_none_baseline_has_no_markdown(tmp_path):
    from skill_lens.orchestrator import _baseline_skill, _BaselineStore

    skill = Skill(name="pdf", description="d", instructions="i", path=tmp_path, markdown="---\nname: pdf\n---\nbody")
    baseline = _baseline_skill(skill, "none", [], _BaselineStore())
    assert baseline is not None
    assert baseline.markdown == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_models.py tests/test_skill_loader.py tests/test_baseline_resolution.py tests/test_arms.py -v`
Expected: the new tests FAIL (`ImportError: cannot import name 'ProductStatus'`, unexpected keyword `usage_note`, `markdown == ""`).

- [ ] **Step 3: Add the fields and the model**

In `src/skill_lens/models.py`:

```python
class Skill(BaseModel):
    """...existing docstring, then add:

    `markdown` is the `SKILL.md` text verbatim -- what a product runner writes
    into the product's skill directory. Products honour frontmatter keys
    skill-lens does not model (`allowed-tools`, `disable-model-invocation`),
    so a re-rendering from the parsed fields would change the product's
    behaviour and the eval would measure the re-rendering. Empty when there is
    no file: a Skill built by hand, and the `--baseline none` skill, which is
    how a product runner knows to write no directory at all.
    """
    ...
    bundle_root: Path | None = None
    markdown: str = ""
```

```python
class RunResult(BaseModel):
    """...existing docstring, then add:

    `usage_note` says why `input_tokens`/`output_tokens` are 0 when the runner
    could not count them (a product that reports no token usage). It is to
    tokens what `cost_note` is to cost: `BudgetEvaluator` refuses to evaluate
    `max_tokens` against a zero it knows is not a measurement.
    """
    ...
    cost_note: str = ""
    usage_note: str = ""
```

After `ScriptNote`:

```python
class ProductStatus(BaseModel):
    """One agent product a run executed, as preflight found it.

    `trust` is fixed harness text -- permission prompts disabled, no skill-lens
    sandbox, bundled scripts reachable through the product's own tools -- on
    the model rather than in each reporter so the three reporters cannot
    drift, and so the JSON report carries the same sentence a human reads.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    executable: str
    version: str = ""
    trust: str = ""
```

In `RunReport`, after `script_notes`:

```python
    products: list[ProductStatus] = Field(default_factory=list)
```

In `src/skill_lens/skills/loader.py`, `parse_skill_text` returns `Skill(..., path=path, markdown=text)`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_models.py tests/test_skill_loader.py tests/test_baseline_resolution.py tests/test_arms.py -v`
Expected: PASS. (`_baseline_skill` for `none` builds a `Skill` without `markdown`, so the default `""` already satisfies the arms test.)

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/models.py src/skill_lens/skills/loader.py tests/test_models.py tests/test_skill_loader.py tests/test_baseline_resolution.py tests/test_arms.py
git commit -m "feat: carry the SKILL.md text and product facts on the models

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: A token budget the runner could not measure fails

**Files:**
- Modify: `src/skill_lens/evaluators/budget.py`
- Test: `tests/test_budget_evaluator.py`

**Interfaces:**
- Consumes: `RunResult.usage_note` (Task 2).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_budget_evaluator.py`:

```python
def test_a_token_limit_the_runner_could_not_measure_is_a_failing_check():
    case = EvalCase(name="c", task="t", budget=BudgetSpec(max_tokens=500))
    result = RunResult(output="x", usage_note="copilot did not report token usage")
    score = BudgetEvaluator().evaluate(case, result)
    assert score.passed is False
    assert score.score == 0.0  # nothing was evaluated
    (check,) = score.checks
    assert check.id == "max_tokens"
    assert check.passed is False
    assert check.evidence == "token budget not evaluated: copilot did not report token usage"


def test_an_unmeasured_token_limit_does_not_dilute_the_measured_ones():
    case = EvalCase(
        name="c", task="t", budget=BudgetSpec(max_tokens=500, max_latency_ms=1000)
    )
    result = RunResult(output="x", latency_ms=10, usage_note="not counted")
    score = BudgetEvaluator().evaluate(case, result)
    assert score.passed is False  # the skipped limit still fails the case
    assert score.score == 1.0  # the one evaluated limit held
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_budget_evaluator.py -v`
Expected: the two new tests FAIL (today `0 <= 500` passes).

- [ ] **Step 3: Make the rule symmetric with cost**

In `src/skill_lens/evaluators/budget.py`, replace the `max_tokens` branch of `_checks`:

```python
    if spec.max_tokens is not None:
        if result.usage_note:
            checks.append(
                CheckResult(
                    id="max_tokens",
                    passed=False,
                    evidence=f"token budget not evaluated: {result.usage_note}",
                )
            )
        else:
            evaluated += 1
            held = result.tokens <= spec.max_tokens
            checks.append(
                CheckResult(
                    id="max_tokens",
                    passed=held,
                    evidence=f"used {result.tokens} tokens, limit is {spec.max_tokens}",
                )
            )
```

and in `BudgetEvaluator.evaluate` generalise the skipped set:

```python
        skipped_ids = {
            c.id
            for c in checks
            if (c.id == "max_cost_usd" and result.cost_note)
            or (c.id == "max_tokens" and result.usage_note)
        }
```

Update the `_checks` docstring's first paragraph to say "A cost limit is declared but not evaluated when `result.cost_note` is non-empty, and a token limit when `result.usage_note` is: ..." and the class docstring's "An unpriced model degrades cost to 0.0" sentence to "An unpriced model degrades cost to 0.0, and a product that reports no usage leaves tokens at 0".

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_budget_evaluator.py -v`
Expected: PASS, including every existing test.

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/evaluators/budget.py tests/test_budget_evaluator.py
git commit -m "feat: fail a token budget the runner could not measure

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `runners/traces.py` — the two parsers and their fixtures

**Files:**
- Create: `src/skill_lens/runners/traces.py`
- Create: `tests/fixtures/products/claude-code-trigger.jsonl`, `claude-code-negative.jsonl`, `claude-code-error.jsonl`, `copilot-trigger.jsonl`, `copilot-no-shutdown.jsonl`, `copilot-error.jsonl`
- Test: `tests/test_traces.py`

**Interfaces:**
- Produces: `Trace` (frozen dataclass, every field defaulted; `complete` is True when the product's final event was seen), `parse_lines(text: str) -> list[dict[str, Any]]`, `parse_copilot(text: str) -> Trace`, `parse_claude_code(text: str) -> Trace`. `Trace.tool_calls` is a `list[ToolCall]` (from `models.py`).

- [ ] **Step 1: Write the fixtures**

These are hand-trimmed from the 2026-09-17 probes (Claude Code) and from Copilot's session-log schema plus its errored probe; every path, id and timestamp is a placeholder. One JSON object per line, no blank line at the end.

`tests/fixtures/products/claude-code-trigger.jsonl`:

```
{"type":"system","subtype":"init","cwd":"/work","session_id":"s1","tools":["Bash","Read","Skill"],"model":"claude-opus-5[1m]","permissionMode":"bypassPermissions","skills":["ping"],"claude_code_version":"9.9.9"}
{"type":"assistant","message":{"model":"claude-opus-5","content":[{"type":"tool_use","id":"t1","name":"Skill","input":{"skill":"ping"}}],"usage":{"input_tokens":2,"cache_creation_input_tokens":100,"cache_read_input_tokens":50,"output_tokens":5}},"session_id":"s1"}
{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","content":"Launching skill: ping"}]},"session_id":"s1"}
{"type":"assistant","message":{"model":"claude-opus-5","content":[{"type":"tool_use","id":"t2","name":"Bash","input":{"command":"ls"}}],"usage":{"input_tokens":1,"cache_creation_input_tokens":0,"cache_read_input_tokens":150,"output_tokens":6}},"session_id":"s1"}
{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t2","content":"SKILL.md"}]},"session_id":"s1"}
{"type":"assistant","message":{"model":"claude-opus-5","content":[{"type":"text","text":"PONG-7731"}],"usage":{"input_tokens":1,"cache_creation_input_tokens":0,"cache_read_input_tokens":160,"output_tokens":8}},"session_id":"s1"}
{"type":"result","subtype":"success","is_error":false,"num_turns":5,"result":"PONG-7731","session_id":"s1","total_cost_usd":0.273076,"duration_ms":4275,"duration_api_ms":3954,"stop_reason":"end_turn","usage":{"input_tokens":4,"cache_creation_input_tokens":100,"cache_read_input_tokens":360,"output_tokens":19}}
```

`tests/fixtures/products/claude-code-negative.jsonl`:

```
{"type":"system","subtype":"init","cwd":"/work","session_id":"s2","tools":["Bash","Read","Skill"],"model":"claude-opus-5[1m]","permissionMode":"bypassPermissions","skills":["ping"],"claude_code_version":"9.9.9"}
{"type":"assistant","message":{"model":"claude-opus-5","content":[{"type":"text","text":"4"}],"usage":{"input_tokens":2,"cache_creation_input_tokens":0,"cache_read_input_tokens":20,"output_tokens":1}},"session_id":"s2"}
{"type":"result","subtype":"success","is_error":false,"num_turns":1,"result":"4","session_id":"s2","total_cost_usd":0.07138,"duration_ms":2100,"duration_api_ms":1994,"stop_reason":"end_turn","usage":{"input_tokens":2,"cache_creation_input_tokens":0,"cache_read_input_tokens":20,"output_tokens":1}}
```

`tests/fixtures/products/claude-code-error.jsonl`:

```
{"type":"system","subtype":"init","cwd":"/work","session_id":"s3","tools":[],"model":"claude-opus-5[1m]","permissionMode":"bypassPermissions","skills":[],"claude_code_version":"9.9.9"}
{"type":"result","subtype":"error_during_execution","is_error":true,"num_turns":1,"result":"API Error: 401 authentication_error","session_id":"s3","total_cost_usd":0.0,"duration_ms":300,"duration_api_ms":250,"usage":{"input_tokens":0,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"output_tokens":0}}
```

`tests/fixtures/products/copilot-trigger.jsonl`:

```
{"type":"session.skills_loaded","data":{"skills":[{"name":"ping","description":"Use when asked to ping.","source":"project","userInvocable":true,"enabled":true,"path":"/work/.agents/skills/ping/SKILL.md"}]},"id":"e1","timestamp":"2026-09-17T10:00:00.000Z","ephemeral":true}
{"type":"session.tools_updated","data":{"model":"gpt-5.4"},"id":"e2","timestamp":"2026-09-17T10:00:00.100Z","ephemeral":true}
{"type":"user.message","data":{"content":"/ping Please ping.","attachments":[]},"id":"e3","timestamp":"2026-09-17T10:00:00.200Z"}
{"type":"assistant.turn_start","data":{"turnId":"0"},"id":"e4","timestamp":"2026-09-17T10:00:00.300Z"}
{"type":"skill.invoked","data":{"name":"ping","description":"Use when asked to ping.","path":"/work/.agents/skills/ping/SKILL.md","content":"# ping"},"id":"e5","timestamp":"2026-09-17T10:00:00.400Z"}
{"type":"assistant.message","data":{"messageId":"m1","content":"","toolRequests":[{"toolCallId":"c1","name":"bash","arguments":{"command":"ls","intent":"list files"},"type":"function"}]},"id":"e6","timestamp":"2026-09-17T10:00:01.000Z"}
{"type":"tool.execution_start","data":{"toolCallId":"c1","toolName":"bash","arguments":{"command":"ls","intent":"list files"}},"id":"e7","timestamp":"2026-09-17T10:00:01.100Z"}
{"type":"tool.execution_complete","data":{"toolCallId":"c1","success":true,"result":{"content":"SKILL.md"},"toolTelemetry":{}},"id":"e8","timestamp":"2026-09-17T10:00:01.200Z"}
{"type":"assistant.message","data":{"messageId":"m2","content":"PONG-7731","toolRequests":[]},"id":"e9","timestamp":"2026-09-17T10:00:02.000Z"}
{"type":"assistant.turn_end","data":{"turnId":"0"},"id":"e10","timestamp":"2026-09-17T10:00:02.100Z"}
{"type":"session.shutdown","data":{"currentModel":"gpt-5.4","conversationTokens":1200,"totalPremiumRequests":1,"modelMetrics":{"gpt-5.4":{"requests":{"count":2,"cost":1},"usage":{"inputTokens":1000,"outputTokens":40,"cacheReadTokens":200,"cacheWriteTokens":100,"reasoningTokens":0}}}},"id":"e11","timestamp":"2026-09-17T10:00:02.200Z"}
{"type":"result","timestamp":"2026-09-17T10:00:02.300Z","sessionId":"s1","exitCode":0,"usage":{"premiumRequests":1,"totalApiDurationMs":3000,"sessionDurationMs":4000,"codeChanges":{"linesAdded":0,"linesRemoved":0,"filesModified":[]}}}
```

`tests/fixtures/products/copilot-no-shutdown.jsonl`: the trigger file with the `session.shutdown` line removed (copy it and delete line 11).

`tests/fixtures/products/copilot-error.jsonl`:

```
{"type":"session.tools_updated","data":{"model":"gpt-4.1"},"id":"e1","timestamp":"2026-09-17T10:30:01.084Z","ephemeral":true}
{"type":"user.message","data":{"content":"Please ping.","attachments":[]},"id":"e2","timestamp":"2026-09-17T10:30:01.085Z"}
{"type":"assistant.turn_start","data":{"turnId":"0"},"id":"e3","timestamp":"2026-09-17T10:30:01.139Z"}
{"type":"assistant.turn_end","data":{"turnId":"0"},"id":"e4","timestamp":"2026-09-17T10:30:01.940Z"}
{"type":"session.error","data":{"errorType":"quota","message":"402 You have exceeded your monthly quota (Request ID: REDACTED)","statusCode":402},"id":"e5","timestamp":"2026-09-17T10:30:01.941Z"}
{"type":"result","timestamp":"2026-09-17T10:30:01.969Z","sessionId":"s9","exitCode":1,"usage":{"premiumRequests":0,"totalApiDurationMs":0,"sessionDurationMs":2888,"codeChanges":{"linesAdded":0,"linesRemoved":0,"filesModified":[]}}}
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_traces.py
"""The two product trace parsers, against scrubbed recordings."""

from __future__ import annotations

from pathlib import Path

import pytest

from skill_lens.models import ToolCall
from skill_lens.runners.traces import parse_claude_code, parse_copilot, parse_lines

FIXTURES = Path(__file__).parent / "fixtures" / "products"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- parse_lines ---


def test_parse_lines_skips_blank_and_non_json_lines():
    text = 'Warning: something\n\n{"type":"a"}\n[1,2]\n{"type":"b"}\n'
    assert parse_lines(text) == [{"type": "a"}, {"type": "b"}]


# --- Claude Code ---


def test_claude_code_output_tool_calls_and_skill_load():
    trace = parse_claude_code(_fixture("claude-code-trigger.jsonl"))
    assert trace.error is None
    assert trace.output == "PONG-7731"
    assert trace.tool_calls == [
        ToolCall(name="Skill", arguments={"skill": "ping"}),
        ToolCall(name="Bash", arguments={"command": "ls"}),
    ]
    assert trace.invoked_skills == frozenset({"ping"})


def test_claude_code_tokens_include_cache_reads_and_writes():
    trace = parse_claude_code(_fixture("claude-code-trigger.jsonl"))
    assert trace.input_tokens == 4 + 100 + 360
    assert trace.output_tokens == 19
    assert trace.usage_note == ""


def test_claude_code_cost_is_the_products_own_figure_with_no_note():
    trace = parse_claude_code(_fixture("claude-code-trigger.jsonl"))
    assert trace.cost_usd == pytest.approx(0.273076)
    assert trace.cost_note == ""


def test_claude_code_model_comes_from_the_init_event():
    assert parse_claude_code(_fixture("claude-code-trigger.jsonl")).model == "claude-opus-5[1m]"


def test_claude_code_transcript_keeps_every_event():
    trace = parse_claude_code(_fixture("claude-code-trigger.jsonl"))
    assert [event["type"] for event in trace.transcript] == [
        "system", "assistant", "user", "assistant", "user", "assistant", "result"
    ]


def test_claude_code_negative_control_loads_no_skill():
    trace = parse_claude_code(_fixture("claude-code-negative.jsonl"))
    assert trace.output == "4"
    assert trace.tool_calls == []
    assert trace.invoked_skills == frozenset()


def test_claude_code_error_result_is_the_products_own_explanation():
    trace = parse_claude_code(_fixture("claude-code-error.jsonl"))
    assert trace.error == "claude-code: API Error: 401 authentication_error"
    assert trace.model == "claude-opus-5[1m]"


def test_claude_code_without_a_result_event_is_an_error():
    lines = _fixture("claude-code-trigger.jsonl").splitlines()[:-1]
    trace = parse_claude_code("\n".join(lines))
    assert trace.error == "no result event in the claude-code trace"
    assert trace.complete is False


def test_claude_code_marks_a_trace_with_a_result_event_complete():
    assert parse_claude_code(_fixture("claude-code-trigger.jsonl")).complete is True
    assert parse_claude_code(_fixture("claude-code-error.jsonl")).complete is True


# --- Copilot ---


def test_copilot_output_is_the_last_non_empty_assistant_message():
    trace = parse_copilot(_fixture("copilot-trigger.jsonl"))
    assert trace.error is None
    assert trace.output == "PONG-7731"


def test_copilot_tool_calls_are_the_models_requests_in_order():
    trace = parse_copilot(_fixture("copilot-trigger.jsonl"))
    assert trace.tool_calls == [
        ToolCall(name="bash", arguments={"command": "ls", "intent": "list files"})
    ]


def test_copilot_skill_load_is_the_skill_invoked_event():
    assert parse_copilot(_fixture("copilot-trigger.jsonl")).invoked_skills == frozenset({"ping"})


def test_copilot_tokens_come_from_the_shutdown_metrics():
    trace = parse_copilot(_fixture("copilot-trigger.jsonl"))
    assert trace.input_tokens == 1000 + 200 + 100
    assert trace.output_tokens == 40
    assert trace.usage_note == ""
    assert trace.model == "gpt-5.4"


def test_copilot_without_shutdown_metrics_says_tokens_were_not_reported():
    trace = parse_copilot(_fixture("copilot-no-shutdown.jsonl"))
    assert trace.error is None
    assert trace.input_tokens == 0 and trace.output_tokens == 0
    assert trace.usage_note == "copilot did not report token usage"
    assert trace.model == "gpt-5.4"  # falls back to session.tools_updated


def test_copilot_cost_is_zero_with_a_premium_request_note():
    trace = parse_copilot(_fixture("copilot-trigger.jsonl"))
    assert trace.cost_usd == 0.0
    assert trace.cost_note == (
        "copilot bills per premium request, not per token; 1 premium request(s)"
    )


def test_copilot_transcript_drops_ephemeral_events():
    trace = parse_copilot(_fixture("copilot-trigger.jsonl"))
    assert "session.skills_loaded" not in [event["type"] for event in trace.transcript]
    assert trace.transcript[0]["type"] == "user.message"


def test_copilot_session_error_is_the_error_even_with_a_result_line():
    trace = parse_copilot(_fixture("copilot-error.jsonl"))
    assert trace.error == "copilot: 402 You have exceeded your monthly quota (Request ID: REDACTED)"
    assert trace.model == "gpt-4.1"


def test_copilot_non_zero_exit_without_a_session_error_is_named():
    lines = [
        line for line in _fixture("copilot-error.jsonl").splitlines()
        if '"session.error"' not in line
    ]
    assert parse_copilot("\n".join(lines)).error == "copilot exited with code 1"


def test_copilot_without_a_result_event_is_an_error():
    lines = _fixture("copilot-trigger.jsonl").splitlines()[:-1]
    trace = parse_copilot("\n".join(lines))
    assert trace.error == "no result event in the copilot trace"
    assert trace.complete is False


def test_copilot_marks_a_trace_with_a_result_event_complete():
    assert parse_copilot(_fixture("copilot-trigger.jsonl")).complete is True
    assert parse_copilot(_fixture("copilot-error.jsonl")).complete is True


def test_copilot_non_dict_arguments_are_preserved_raw():
    line = (
        '{"type":"assistant.message","data":{"content":"","toolRequests":'
        '[{"toolCallId":"c","name":"bash","arguments":"not a dict","type":"function"}]}}\n'
        '{"type":"result","exitCode":0,"usage":{"premiumRequests":0}}'
    )
    assert parse_copilot(line).tool_calls == [
        ToolCall(name="bash", arguments={"_raw": "not a dict"})
    ]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_traces.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'skill_lens.runners.traces'`

- [ ] **Step 4: Write the parsers**

```python
# src/skill_lens/runners/traces.py
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
            for request in data.get("toolRequests") or []:
                if isinstance(request, dict) and isinstance(request.get("name"), str):
                    tool_calls.append(
                        ToolCall(name=request["name"], arguments=_arguments(request.get("arguments")))
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
    premium = _int((result or {}).get("usage", {}).get("premiumRequests")) if result else 0
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
            for block in message.get("content") or []:
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
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
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
        cost_usd=float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else 0.0,
        invoked_skills=frozenset(invoked),
        complete=True,
        error=f"claude-code: {output or result.get('subtype')}" if failed else None,
    )
```

- [ ] **Step 5: Run the tests and lint**

Run: `uv run pytest tests/test_traces.py -v && uv run ruff check . && uv run ruff format .`
Expected: PASS; ruff clean. (Line-length fixes from `ruff format` are fine.)

- [ ] **Step 6: Commit**

```bash
git add src/skill_lens/runners/traces.py tests/test_traces.py tests/fixtures/products
git commit -m "feat: parse the Copilot and Claude Code traces into one shape

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The fake product and `ProductRunner.run`

**Files:**
- Create: `tests/fake_product.py`
- Create: `src/skill_lens/runners/product.py`
- Test: `tests/test_product_runner.py`

**Interfaces:**
- Consumes: `process.group_kwargs/reap_and_kill_group/read_capped_handle` (Task 1); `Skill.markdown`, `RunResult.usage_note` (Task 2); `traces.Trace/parse_copilot/parse_claude_code` (Task 4); `bundle.BUNDLE_DIRS`; `skills.loader.SKILL_FILENAME`.
- Produces: `Product` (frozen dataclass: `name`, `argv: tuple[str, ...]`, `skills_dir: str`, `invoke: str`, `parse: Callable[[str], Trace] | None`, `version_command: tuple[str, ...] | None`, `timeout_seconds: float = 600.0`, `max_output_bytes: int = 8_000_000`); `PRESETS: dict[str, Product]`; `PROMPT_PLACEHOLDER = "{prompt}"`; `DEFAULT_SKILLS_DIR = ".agents/skills"`; `DEFAULT_INVOKE = "{task}"`; `MAX_PROMPT_BYTES`; `TRUST_NOTE`; `ProductSetupError`; `Invocation`; `invoke(product, prompt, cwd) -> Invocation`; `read_trace(product, invocation) -> Trace`; `deliver_skill(skill, cwd, skills_dir) -> bool`; `ProductRunner(product)` with `name`, `needs_api_key = False`, `run(...)`. (`preflight` is Task 6.)

- [ ] **Step 1: Write the fake product**

```python
# tests/fake_product.py
"""A stand-in for an agent product's CLI, driven by environment variables.

Started by the tests as `python fake_product.py -p <prompt> ...`. It records
what it saw to the JSON file named by FAKE_PRODUCT_RECORD -- its cwd, its
argv, the prompt after `-p`, and every file under the directory named by
FAKE_PRODUCT_SKILLS_DIR (relative to cwd) -- then behaves as FAKE_PRODUCT_MODE
says:

  ok       print the file named by FAKE_PRODUCT_TRACE to stdout, exit 0 (default)
  sleep    sleep 60 s; the runner's timeout must kill it
  exit3    print "boom" to stderr, exit 3
  garbage  print text that is no JSON at all, exit 0
  huge     print the trace, then FAKE_PRODUCT_BYTES bytes of "x", exit 0

`--version` as the first argument prints "fake 1.2.3" and exits 0, or exits 1
when FAKE_PRODUCT_VERSION_FAILS is set. Nothing here touches the network.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    if argv[:1] == ["--version"]:
        if os.environ.get("FAKE_PRODUCT_VERSION_FAILS"):
            print("cannot start", file=sys.stderr)
            return 1
        print("fake 1.2.3")
        return 0
    prompt = argv[argv.index("-p") + 1] if "-p" in argv else ""
    record = os.environ.get("FAKE_PRODUCT_RECORD")
    if record:
        skills_dir = os.environ.get("FAKE_PRODUCT_SKILLS_DIR", "")
        root = Path.cwd() / skills_dir if skills_dir else None
        files = (
            sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
            if root is not None and root.is_dir()
            else []
        )
        Path(record).write_text(
            json.dumps({"cwd": str(Path.cwd()), "argv": argv, "prompt": prompt, "skill_files": files}),
            encoding="utf-8",
        )
    mode = os.environ.get("FAKE_PRODUCT_MODE", "ok")
    if mode == "sleep":
        time.sleep(60)
        return 0
    if mode == "exit3":
        print("boom", file=sys.stderr)
        return 3
    if mode == "garbage":
        print("this is not json")
        return 0
    trace = Path(os.environ["FAKE_PRODUCT_TRACE"]).read_text(encoding="utf-8")
    sys.stdout.write(trace)
    if mode == "huge":
        sys.stdout.write("\n" + "x" * int(os.environ.get("FAKE_PRODUCT_BYTES", "100000")))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_product_runner.py
"""ProductRunner against the fake product: the real subprocess path, offline."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from skill_lens.models import EvalCase, Skill, WorkspaceSpec
from skill_lens.runners.product import (
    PRESETS,
    Product,
    ProductRunner,
    deliver_skill,
)
from skill_lens.runners.traces import parse_claude_code, parse_copilot
from skill_lens.workspace import create_workspace

FAKE = Path(__file__).parent / "fake_product.py"
FIXTURES = Path(__file__).parent / "fixtures" / "products"

SKILL_MD = "---\nname: ping\ndescription: Ping.\nallowed-tools: Bash\n---\n\nReply PONG-7731.\n"


def _product(parse=parse_claude_code, **overrides) -> Product:
    fields = dict(
        name="claude-code",
        argv=(sys.executable, str(FAKE), "-p", "{prompt}", "--flag"),
        skills_dir=".claude/skills",
        invoke="/{name} {task}",
        parse=parse,
        version_command=(sys.executable, str(FAKE), "--version"),
    )
    fields.update(overrides)
    return Product(**fields)


def _skill(tmp_path, markdown=SKILL_MD, bundle=False) -> Skill:
    root = tmp_path / "ping"
    root.mkdir(exist_ok=True)
    bundle_root = None
    if bundle:
        (root / "scripts").mkdir(exist_ok=True)
        (root / "scripts" / "count.py").write_text("print(1)", encoding="utf-8")
        (root / "references").mkdir(exist_ok=True)
        (root / "references" / "notes.md").write_text("notes", encoding="utf-8")
        (root / "ping.eval.yaml").write_text("cases: []", encoding="utf-8")
        bundle_root = root.resolve()
    return Skill(
        name="ping", description="Ping.", instructions="Reply PONG-7731.",
        path=root, markdown=markdown, bundle_root=bundle_root,
    )


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """Point the fake at a trace and a record file; return a reader for the record."""
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_PRODUCT_RECORD", str(record))
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "claude-code-trigger.jsonl"))
    monkeypatch.setenv("FAKE_PRODUCT_SKILLS_DIR", ".claude/skills")
    monkeypatch.delenv("FAKE_PRODUCT_MODE", raising=False)

    def read():
        return json.loads(record.read_text(encoding="utf-8"))

    return read


def _case(**kwargs) -> EvalCase:
    kwargs.setdefault("name", "c")
    kwargs.setdefault("task", "Please ping.")
    return EvalCase(**kwargs)


# --- presets ---


def test_the_presets_are_the_verified_spellings():
    copilot = PRESETS["copilot"]
    assert copilot.argv == (
        "copilot", "-p", "{prompt}", "--allow-all-tools", "--output-format", "json",
        "--no-custom-instructions", "--no-auto-update",
    )
    assert copilot.skills_dir == ".agents/skills"
    assert copilot.invoke == "/{name} {task}"
    assert copilot.parse is parse_copilot
    assert copilot.version_command == ("copilot", "--version")
    claude = PRESETS["claude-code"]
    assert claude.argv == (
        "claude", "-p", "{prompt}", "--output-format", "stream-json", "--verbose",
        "--dangerously-skip-permissions", "--setting-sources", "project",
        "--strict-mcp-config", "--no-session-persistence",
    )
    assert claude.skills_dir == ".claude/skills"
    assert claude.parse is parse_claude_code
    assert claude.version_command == ("claude", "--version")
    assert set(PRESETS) == {"copilot", "claude-code"}
    for preset in PRESETS.values():
        assert preset.timeout_seconds == 600.0
        assert preset.max_output_bytes == 8_000_000


# --- delivery ---


def test_deliver_skill_writes_the_markdown_verbatim_and_the_three_bundle_dirs_only(tmp_path):
    skill = _skill(tmp_path, bundle=True)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    assert deliver_skill(skill, cwd, ".agents/skills") is True
    target = cwd / ".agents" / "skills" / "ping"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == SKILL_MD
    assert sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()) == [
        "SKILL.md", "references/notes.md", "scripts/count.py",
    ]  # the eval file beside SKILL.md is never delivered


def test_deliver_skill_writes_nothing_for_a_skill_with_no_markdown(tmp_path):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    assert deliver_skill(_skill(tmp_path, markdown=""), cwd, ".agents/skills") is False
    assert list(cwd.iterdir()) == []


# --- run: the happy path ---


def test_run_delivers_the_skill_into_a_private_cwd_and_reads_the_trace(tmp_path, fake):
    result = ProductRunner(_product()).run(_skill(tmp_path), _case())
    assert result.error is None
    assert result.output == "PONG-7731"
    assert [call.name for call in result.tool_calls] == ["Skill", "Bash"]
    assert result.input_tokens == 464 and result.output_tokens == 19
    assert result.cost_usd == pytest.approx(0.273076)
    assert result.model == "claude-opus-5[1m]"
    assert result.latency_ms >= 0
    assert result.skill_triggered is None  # loaded mode
    seen = fake()
    assert seen["skill_files"] == ["ping/SKILL.md"]
    assert seen["argv"][-1] == "--flag"
    assert not Path(seen["cwd"]).exists()  # the private directory is gone


def test_loaded_mode_invokes_the_skill_by_the_products_spelling(tmp_path, fake):
    ProductRunner(_product()).run(_skill(tmp_path), _case(task="Please ping."))
    assert fake()["prompt"] == "/ping Please ping."


def test_offered_mode_sends_the_bare_task_and_reads_the_load_event(tmp_path, fake):
    result = ProductRunner(_product()).run(_skill(tmp_path), _case(mode="offered"))
    assert fake()["prompt"] == "Please ping."
    assert result.skill_triggered is True


def test_offered_mode_negative_control_is_false(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "claude-code-negative.jsonl"))
    result = ProductRunner(_product()).run(_skill(tmp_path), _case(mode="offered"))
    assert result.skill_triggered is False


def test_the_baseline_none_skill_gets_no_directory_and_the_bare_task(tmp_path, fake):
    baseline = Skill(name="ping", description="", instructions="", path=tmp_path, variant="baseline")
    ProductRunner(_product()).run(baseline, _case())  # loaded mode, nothing to invoke
    seen = fake()
    assert seen["skill_files"] == []
    assert seen["prompt"] == "Please ping."


def test_a_workspace_is_the_cwd_and_keeps_the_delivered_skill(tmp_path, fake):
    workspace = create_workspace(WorkspaceSpec(files={"in.txt": "hi"}), label="t")
    try:
        ProductRunner(_product()).run(_skill(tmp_path), _case(workspace=WorkspaceSpec()), workspace=workspace)
        seen = fake()
        assert Path(seen["cwd"]) == workspace.root
        assert (workspace.root / ".claude" / "skills" / "ping" / "SKILL.md").is_file()
        assert (workspace.root / "in.txt").is_file()
    finally:
        workspace.cleanup()


def test_scripts_is_accepted_and_ignored(tmp_path, fake):
    result = ProductRunner(_product()).run(_skill(tmp_path), _case(), scripts=object())
    assert result.error is None


def test_a_generic_product_uses_stdout_and_reports_no_usage(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "claude-code-negative.jsonl"))
    product = _product(name="cli", parse=None, invoke="{task}", version_command=None)
    result = ProductRunner(product).run(_skill(tmp_path), _case())
    assert result.error is None
    assert result.output.startswith('{"type":"system"')  # stdout verbatim
    assert not result.output.endswith("\n")
    assert result.tool_calls == []
    assert result.usage_note == "the cli runner does not report token usage"
    assert result.cost_note == "the cli runner does not report cost"
    assert result.model == ""
    assert fake()["prompt"] == "Please ping."


def test_the_runner_name_is_the_products(tmp_path):
    assert ProductRunner(_product(name="copilot")).name == "copilot"
    assert ProductRunner(_product()).needs_api_key is False


# --- run: every failure is RunResult.error ---


def test_a_timeout_kills_the_product_and_is_an_error(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "sleep")
    result = ProductRunner(_product(timeout_seconds=0.5)).run(_skill(tmp_path), _case())
    assert result.error == "claude-code timed out after 0.5s"


def test_a_non_zero_exit_is_an_error_with_the_stderr_tail(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "exit3")
    result = ProductRunner(_product()).run(_skill(tmp_path), _case())
    assert result.error == "claude-code exited with code 3: boom"


def test_a_trace_with_no_result_event_is_an_error(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "garbage")
    result = ProductRunner(_product()).run(_skill(tmp_path), _case())
    assert result.error == "no result event in the claude-code trace"


def test_the_products_own_failure_is_the_error(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "claude-code-error.jsonl"))
    result = ProductRunner(_product()).run(_skill(tmp_path), _case())
    assert result.error == "claude-code: API Error: 401 authentication_error"
    assert result.model == "claude-opus-5[1m]"


def test_a_truncated_trace_is_an_error_naming_the_cap(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "huge")
    monkeypatch.setenv("FAKE_PRODUCT_BYTES", "5000")
    result = ProductRunner(_product(max_output_bytes=4000)).run(_skill(tmp_path), _case())
    assert result.error == (
        "claude-code output exceeded 4000 bytes; raise [runners.claude-code] max_output_bytes"
    )


def test_a_missing_executable_is_an_error_not_a_raise(tmp_path, fake):
    product = _product(argv=("/nonexistent/product", "-p", "{prompt}"))
    result = ProductRunner(product).run(_skill(tmp_path), _case())
    assert result.error is not None
    assert result.error.startswith("cannot start /nonexistent/product:")


def test_an_oversized_prompt_is_refused_before_anything_starts(tmp_path, fake):
    task = "x" * (100 * 1024 + 1)
    result = ProductRunner(_product()).run(_skill(tmp_path), _case(task=task))
    size = len(f"/ping {task}".encode("utf-8"))
    assert result.error == (
        f"prompt is {size} bytes; a product runner sends at most 102400 bytes as one argument"
    )


def test_the_private_cwd_is_removed_even_on_failure(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "exit3")
    ProductRunner(_product()).run(_skill(tmp_path), _case())
    assert not Path(fake()["cwd"]).exists()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_product_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'skill_lens.runners.product'`

- [ ] **Step 4: Write the runner**

```python
# src/skill_lens/runners/product.py
"""Run a case through an agent product's own command-line interface.

The two framework runners drive a generic agent loop with skill-lens's own
prompt and tools. A product runner starts the product the skill is actually
shipped to -- GitHub Copilot CLI, Claude Code, or any command named in
`skill-lens.toml` -- in its non-interactive mode, with the skill placed
where that product discovers skills, and reads the result from the product's
own trace. The skill is measured under the product's system prompt, tools
and loading mechanics, none of which skill-lens can emulate from outside.

The trust model is the product's, not skill-lens's: permission prompts are
disabled (the product cannot run non-interactively otherwise) and the full
environment is inherited (the product needs its own auth). Nothing here is
sandboxed; `ProductStatus.trust` says so on every report. Naming a product
runner is the decision.

Imports no agent framework: a product is an executable and a trace grammar.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from skill_lens.bundle import BUNDLE_DIRS
from skill_lens.models import EvalCase, RunResult, Skill
from skill_lens.process import group_kwargs, reap_and_kill_group, read_capped_handle
from skill_lens.runners.traces import Trace, parse_claude_code, parse_copilot
from skill_lens.skills.loader import SKILL_FILENAME
from skill_lens.workspace import PathRefused, Workspace, check_relative_path

PROMPT_PLACEHOLDER = "{prompt}"
DEFAULT_SKILLS_DIR = ".agents/skills"
DEFAULT_INVOKE = "{task}"
DEFAULT_TIMEOUT_SECONDS = 600.0
DEFAULT_MAX_OUTPUT_BYTES = 8_000_000
# Linux caps one argv element at 128 KiB; the prompt travels as one element.
MAX_PROMPT_BYTES = 100 * 1024
VERSION_PROBE_TIMEOUT_SECONDS = 30.0
STDERR_CAP_BYTES = 20_000
STDERR_TAIL_CHARS = 500
PRIVATE_PREFIX = "skill-lens-product-"
SCRATCH_PREFIX = "skill-lens-product-capture-"
TRUST_NOTE = (
    "runs with permission prompts disabled and the full environment; no skill-lens "
    "sandbox applies; bundled scripts are reachable through the product's own tools"
)


class ProductSetupError(Exception):
    """A product runner cannot run here: raised only from `preflight`, before any
    case, and turned into exit 2 by `cli.py`."""


@dataclass(frozen=True)
class Product:
    """One agent product: how to start it, where it finds skills, how to read it.

    `argv` holds exactly one element equal to `PROMPT_PLACEHOLDER`, replaced
    as a whole element -- never through a shell. `invoke` is how `mode:
    loaded` asks the product to load the skill (`{name}`, `{task}`);
    `parse` is None for a generic product, whose stdout is the output.
    """

    name: str
    argv: tuple[str, ...]
    skills_dir: str
    invoke: str
    parse: Callable[[str], Trace] | None
    version_command: tuple[str, ...] | None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES


PRESETS: dict[str, Product] = {
    "copilot": Product(
        name="copilot",
        # --allow-all-tools: required for -p. --output-format json: the trace.
        # --no-custom-instructions: nothing from ~/.copilot or a stray
        # AGENTS.md shapes the run. --no-auto-update: no network hop before
        # the prompt.
        argv=(
            "copilot", "-p", PROMPT_PLACEHOLDER, "--allow-all-tools",
            "--output-format", "json", "--no-custom-instructions", "--no-auto-update",
        ),
        skills_dir=".agents/skills",
        invoke="/{name} {task}",
        parse=parse_copilot,
        version_command=("copilot", "--version"),
    ),
    "claude-code": Product(
        name="claude-code",
        # --verbose is what stream-json needs in print mode. --setting-sources
        # project and --strict-mcp-config keep the user's own hooks, plugins
        # and MCP servers out of the eval (verified: the project skill is
        # still discovered and OAuth still works); --no-session-persistence
        # writes nothing under ~/.claude.
        argv=(
            "claude", "-p", PROMPT_PLACEHOLDER, "--output-format", "stream-json",
            "--verbose", "--dangerously-skip-permissions", "--setting-sources",
            "project", "--strict-mcp-config", "--no-session-persistence",
        ),
        skills_dir=".claude/skills",
        invoke="/{name} {task}",
        parse=parse_claude_code,
        version_command=("claude", "--version"),
    ),
}


@dataclass(frozen=True)
class Invocation:
    """What one product process did. `error` is set only when it could not start."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    truncated: bool = False
    error: str | None = None


def invoke(product: Product, prompt: str, cwd: Path) -> Invocation:
    """Start the product once with `prompt`, in `cwd`; never raises.

    Output goes to files in a scratch directory and is read back through the
    handles the harness opened (see `process.read_capped_handle`). The
    process group is killed after every exit, timeout or not.
    """
    argv = [prompt if element == PROMPT_PLACEHOLDER else element for element in product.argv]
    try:
        scratch = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX)).resolve()
    except OSError as exc:
        return Invocation(error=f"cannot create a capture directory: {exc}")
    try:
        with ExitStack() as stack:
            try:
                stdout_handle = stack.enter_context((scratch / "stdout").open("w+b"))
                stderr_handle = stack.enter_context((scratch / "stderr").open("w+b"))
            except OSError as exc:
                return Invocation(error=f"cannot create the capture files: {exc}")
            try:
                process = subprocess.Popen(  # noqa: S603 - argv list, shell=False, prompt is one element
                    argv,
                    cwd=cwd,
                    # The whole environment, on purpose: the product needs its
                    # own auth. The opposite of `scripts.script_environment`.
                    env=os.environ.copy(),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    **group_kwargs(),
                )
            except (OSError, ValueError) as exc:
                return Invocation(error=f"cannot start {product.argv[0]}: {exc}")
            exit_code, timed_out = reap_and_kill_group(process, product.timeout_seconds)
            truncated = os.fstat(stdout_handle.fileno()).st_size > product.max_output_bytes
            return Invocation(
                stdout=read_capped_handle(stdout_handle, product.max_output_bytes),
                stderr=read_capped_handle(stderr_handle, STDERR_CAP_BYTES),
                exit_code=exit_code,
                timed_out=timed_out,
                truncated=truncated,
            )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _stderr_tail(stderr: str) -> str:
    text = " ".join(stderr.strip().splitlines()[-5:]).strip()
    return text[-STDERR_TAIL_CHARS:]


def read_trace(product: Product, invocation: Invocation) -> Trace:
    """The invocation as a `Trace`: parsed when the product has a parser, else
    stdout as the output. A failure of any kind is `Trace.error`."""
    if invocation.error is not None:
        return Trace(error=invocation.error)
    if invocation.timed_out:
        return Trace(error=f"{product.name} timed out after {product.timeout_seconds:g}s")
    if invocation.truncated:
        return Trace(
            error=(
                f"{product.name} output exceeded {product.max_output_bytes} bytes; "
                f"raise [runners.{product.name}] max_output_bytes"
            )
        )
    exited = f"{product.name} exited with code {invocation.exit_code}"
    if product.parse is None:
        if invocation.exit_code != 0:
            return Trace(error=f"{exited}: {_stderr_tail(invocation.stderr)}".rstrip(": "))
        return Trace(
            output=invocation.stdout.removesuffix("\n"),
            usage_note=f"the {product.name} runner does not report token usage",
            cost_note=f"the {product.name} runner does not report cost",
        )
    trace = product.parse(invocation.stdout)
    if invocation.exit_code != 0 and (trace.error is None or not trace.complete):
        # A complete trace carrying the product's own error message is the
        # best explanation there is; otherwise the exit code and stderr are.
        return replace(trace, error=f"{exited}: {_stderr_tail(invocation.stderr)}".rstrip(": "))
    return trace


def deliver_skill(skill: Skill, cwd: Path, skills_dir: str) -> bool:
    """Write the skill where the product discovers it. False when there is nothing to write.

    `SKILL.md` is `skill.markdown` byte for byte; beside it go `scripts/`,
    `references/` and `assets/` from the bundle -- those three and nothing
    else, so an eval file beside `SKILL.md` never reaches the product.
    Symlinks are copied as symlinks, as `git archive` preserved them.
    """
    if not skill.markdown:
        return False
    target = cwd / skills_dir / skill.name
    target.mkdir(parents=True, exist_ok=True)
    (target / SKILL_FILENAME).write_text(skill.markdown, encoding="utf-8")
    if skill.bundle_root is not None:
        for name in BUNDLE_DIRS:
            source = skill.bundle_root / name
            if source.is_dir():
                shutil.copytree(source, target / name, symlinks=True, dirs_exist_ok=True)
    return True


def _check_skill_name(name: str) -> None:
    """A skill name must be one directory name; the product's discovery depends on it.

    Both separators are refused on every platform: a backslash is a legal
    POSIX filename character, but the same skill directory must also be
    discoverable on Windows.
    """
    try:
        check_relative_path(name)
    except PathRefused as exc:
        raise ProductSetupError(f"skill name {name!r} cannot be a directory name: {exc}") from exc
    if "/" in name or "\\" in name or len(Path(name).parts) != 1:
        raise ProductSetupError(
            f"skill name {name!r} cannot be a directory name: it contains a path separator"
        )


class ProductRunner:
    """Runs a case through one product, behind the framework-agnostic protocol."""

    needs_api_key = False

    def __init__(self, product: Product) -> None:
        self._product = product
        self.name = product.name

    @property
    def product(self) -> Product:
        return self._product

    def run(
        self,
        skill: Skill,
        case: EvalCase,
        workspace: Workspace | None = None,
        scripts: Any = None,
    ) -> RunResult:
        """`scripts` is accepted for protocol symmetry and ignored: the product
        brings its own tools, and its shell can already reach the bundle."""
        product = self._product
        started = time.monotonic()

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        private: Path | None = None
        try:
            if workspace is not None:
                cwd = workspace.root
            else:
                private = Path(tempfile.mkdtemp(prefix=PRIVATE_PREFIX)).resolve()
                cwd = private
            delivered = deliver_skill(skill, cwd, product.skills_dir)
            prompt = case.task
            if delivered and case.mode == "loaded":
                prompt = product.invoke.format(name=skill.name, task=case.task)
            size = len(prompt.encode("utf-8"))
            if size > MAX_PROMPT_BYTES:
                return RunResult(
                    error=(
                        f"prompt is {size} bytes; a product runner sends at most "
                        f"{MAX_PROMPT_BYTES} bytes as one argument"
                    ),
                    latency_ms=elapsed(),
                )
            trace = read_trace(product, invoke(product, prompt, cwd))
        except OSError as exc:
            return RunResult(error=f"{type(exc).__name__}: {exc}", latency_ms=elapsed())
        finally:
            if private is not None:
                shutil.rmtree(private, ignore_errors=True)

        if trace.error is not None:
            return RunResult(error=trace.error, latency_ms=elapsed(), model=trace.model)
        return RunResult(
            output=trace.output,
            tool_calls=list(trace.tool_calls),
            transcript=list(trace.transcript),
            input_tokens=trace.input_tokens,
            output_tokens=trace.output_tokens,
            latency_ms=elapsed(),
            cost_usd=trace.cost_usd,
            cost_note=trace.cost_note,
            usage_note=trace.usage_note,
            model=trace.model,
            skill_triggered=(
                (skill.name in trace.invoked_skills) if case.mode == "offered" else None
            ),
        )
```

`_check_skill_name`, `VERSION_PROBE_TIMEOUT_SECONDS` and `TRUST_NOTE` are used by Task 6's `preflight`; leave them in place (ruff does not flag an unused module-level constant or function; it would flag an unused *import*, which is why `ProductStatus` is imported only in Task 6).

- [ ] **Step 5: Run the tests and lint**

Run: `uv run pytest tests/test_product_runner.py -v && uv run ruff check . && uv run ruff format .`
Expected: PASS; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/skill_lens/runners/product.py tests/fake_product.py tests/test_product_runner.py
git commit -m "feat: add the product runner

Runs a case through an agent product's own CLI with the skill placed where
the product discovers skills, so the skill is measured under the product's
prompt, tools and loading mechanics. Two presets (copilot, claude-code)
and a generic command.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: `ProductRunner.preflight`

**Files:**
- Modify: `src/skill_lens/runners/product.py` (add `probe_version`, `ProductRunner.preflight`)
- Test: `tests/test_product_preflight.py`

**Interfaces:**
- Produces: `ProductRunner.preflight(skills: list[Skill], cases_by_skill: dict[str, list[EvalCase]]) -> ProductStatus`, raising `ProductSetupError`. The orchestrator (Task 7) calls it with the candidate-arm skills and cases that will run under this runner.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_product_preflight.py
"""Everything a product runner refuses before any case runs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from skill_lens.models import EvalCase, Skill, ToolSpec, TrajectorySpec
from skill_lens.runners.product import TRUST_NOTE, Product, ProductRunner, ProductSetupError
from skill_lens.runners.traces import parse_claude_code

FAKE = Path(__file__).parent / "fake_product.py"


def _product(**overrides) -> Product:
    fields = dict(
        name="claude-code",
        argv=(sys.executable, str(FAKE), "-p", "{prompt}"),
        skills_dir=".claude/skills",
        invoke="/{name} {task}",
        parse=parse_claude_code,
        version_command=(sys.executable, str(FAKE), "--version"),
    )
    fields.update(overrides)
    return Product(**fields)


def _skill(name="ping") -> Skill:
    return Skill(name=name, description="d", instructions="i", path=Path("/tmp/x"), markdown="m")


def _case(**kwargs) -> EvalCase:
    kwargs.setdefault("name", "c")
    kwargs.setdefault("task", "t")
    return EvalCase(**kwargs)


def test_a_clean_run_records_the_executable_version_and_trust():
    status = ProductRunner(_product()).preflight([_skill()], {"ping": [_case()]})
    assert status.name == "claude-code"
    assert status.executable == sys.executable
    assert status.version == "fake 1.2.3"
    assert status.trust == TRUST_NOTE


def test_a_missing_executable_names_the_runner_and_the_command():
    product = _product(argv=("no-such-product-xyz", "-p", "{prompt}"), version_command=None)
    with pytest.raises(ProductSetupError, match=r"runner claude-code: 'no-such-product-xyz' is not on PATH"):
        ProductRunner(product).preflight([], {})


def test_the_message_says_where_a_cli_command_is_configured():
    product = _product(name="cli", argv=("no-such-product-xyz", "{prompt}"), parse=None, version_command=None)
    with pytest.raises(ProductSetupError, match=r"\[runners\.cli\] command"):
        ProductRunner(product).preflight([], {})


def test_a_version_probe_that_fails_is_a_setup_error(monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_VERSION_FAILS", "1")
    with pytest.raises(
        ProductSetupError, match=r"runner claude-code: .*--version exited with code 1: cannot start"
    ):
        ProductRunner(_product()).preflight([], {})


def test_a_generic_product_has_no_version_probe():
    product = _product(name="cli", parse=None, version_command=None)
    assert ProductRunner(product).preflight([], {}).version == ""


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b"])
def test_a_skill_name_that_is_not_one_directory_name_is_refused(name):
    with pytest.raises(ProductSetupError, match=r"cannot be a directory name"):
        ProductRunner(_product()).preflight([_skill(name)], {name: [_case()]})


def test_a_case_with_mock_tools_is_refused_naming_case_and_runner():
    case = _case(name="uses tools", tools=[ToolSpec(name="lookup")])
    with pytest.raises(ProductSetupError) as info:
        ProductRunner(_product()).preflight([_skill()], {"ping": [case]})
    assert "case 'uses tools' of skill 'ping' declares tools:" in str(info.value)
    assert "claude-code runner cannot provide" in str(info.value)


def test_a_generic_product_refuses_trajectory_and_offered():
    product = _product(name="cli", parse=None, version_command=None)
    with pytest.raises(ProductSetupError, match=r"trajectory:.*cli runner records no tool calls"):
        ProductRunner(product).preflight([_skill()], {"ping": [_case(trajectory=TrajectorySpec(called=["x"]))]})
    with pytest.raises(ProductSetupError, match=r"mode: offered.*cli runner cannot observe"):
        ProductRunner(product).preflight([_skill()], {"ping": [_case(mode="offered")]})


def test_a_preset_accepts_trajectory_and_offered():
    cases = [_case(trajectory=TrajectorySpec(called=["Bash"])), _case(name="o", mode="offered")]
    ProductRunner(_product()).preflight([_skill()], {"ping": cases})  # no raise


def test_only_the_cases_given_are_inspected():
    # A case the orchestrator filtered out never reaches preflight; nothing
    # here re-discovers it.
    ProductRunner(_product()).preflight([_skill()], {})  # no raise, no cases
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_product_preflight.py -v`
Expected: FAIL with `AttributeError: 'ProductRunner' object has no attribute 'preflight'` (the import of `TRUST_NOTE` succeeds — it exists since Task 5).

- [ ] **Step 3: Add the probe and the hook**

In `src/skill_lens/runners/product.py`, change the models import to `from skill_lens.models import EvalCase, ProductStatus, RunResult, Skill`, then add after `_check_skill_name`:

```python
def _install_hint(product: Product) -> str:
    if product.name == "cli":
        return "set [runners.cli] command in skill-lens.toml to a command on PATH"
    return f"install the product, or set [runners.{product.name}] command in skill-lens.toml"


def probe_version(product: Product, executable: str, role: str = "runner") -> str:
    """Run the product's version command; the first line of what it prints.

    Executed, not merely found: a product on PATH that cannot start should be
    exit 2 up front, not one errored case per work item. `role` ("runner" or
    "judge") names the seat in the message. Raises `ProductSetupError`;
    returns "" when the product has no version command.
    """
    if product.version_command is None:
        return ""
    argv = [executable, *product.version_command[1:]]
    spoken = " ".join(argv)
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv from a preset or config, no shell
            argv,
            capture_output=True,
            timeout=VERSION_PROBE_TIMEOUT_SECONDS,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProductSetupError(f"{role} {product.name}: {spoken} could not run: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip().splitlines()
        first = detail[0] if detail else ""
        raise ProductSetupError(
            f"{role} {product.name}: {spoken} exited with code {completed.returncode}: {first}"
            .rstrip(": ")
        )
    lines = completed.stdout.decode("utf-8", errors="replace").strip().splitlines()
    return lines[0].strip() if lines else ""
```

and inside `ProductRunner`, after `run`:

```python
    def preflight(
        self, skills: list[Skill], cases_by_skill: dict[str, list[EvalCase]]
    ) -> ProductStatus:
        """Refuse, before any case runs, everything this runner cannot do.

        Called once per run by the orchestrator with the candidate-arm skills
        and cases that will run under this runner -- compatibility is a
        property of (case, runner), unlike the sandbox decision, which is
        run-wide. Raises `ProductSetupError`; returns the status the report
        carries.
        """
        product = self._product
        executable = shutil.which(product.argv[0])
        if executable is None:
            raise ProductSetupError(
                f"runner {product.name}: {product.argv[0]!r} is not on PATH; "
                f"{_install_hint(product)}"
            )
        version = probe_version(product, executable)
        for skill in skills:
            _check_skill_name(skill.name)
            for case in cases_by_skill.get(skill.name, []):
                where = f"case {case.name!r} of skill {skill.name!r}"
                if case.tools:
                    raise ProductSetupError(
                        f"{where} declares tools:, which the {product.name} runner cannot "
                        "provide -- mock tools reach only pydantic-ai and langchain"
                    )
                if product.parse is None:
                    if case.trajectory is not None:
                        raise ProductSetupError(
                            f"{where} declares trajectory:, but the cli runner records no "
                            "tool calls; use a preset (copilot, claude-code)"
                        )
                    if case.mode == "offered":
                        raise ProductSetupError(
                            f"{where} is mode: offered, but the cli runner cannot observe "
                            "whether a skill was loaded; use a preset or mode: loaded"
                        )
        return ProductStatus(
            name=product.name, executable=executable, version=version, trust=TRUST_NOTE
        )
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_product_preflight.py tests/test_product_runner.py -v && uv run ruff check . && uv run ruff format .`
Expected: PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/runners/product.py tests/test_product_preflight.py
git commit -m "feat: check a product runner's prerequisites before any case runs

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: The orchestrator calls each runner's `preflight` hook

**Files:**
- Modify: `src/skill_lens/orchestrator.py` (`run_evals`, ~line 540; add `_preflight_runners` before `run_evals`)
- Modify: `src/skill_lens/runners/base.py` (the `Runner` docstring)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `ProductStatus` (Task 2); a runner's optional `preflight(skills, cases_by_skill) -> ProductStatus | None`.
- Produces: `RunReport.products` filled from every hook that returned a status.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py`:

```python
from skill_lens.models import ProductStatus


class _PreflightRunner(FakeRunner):
    """A FakeRunner that also defines the optional preflight hook."""

    name = "probed"

    def __init__(self, *, raises: Exception | None = None):
        super().__init__(default=RunResult(output="yes"))
        self.calls: list[tuple[list[str], dict[str, list[str]]]] = []
        self._raises = raises

    def preflight(self, skills, cases_by_skill):
        self.calls.append(
            ([s.name for s in skills], {k: [c.name for c in v] for k, v in cases_by_skill.items()})
        )
        if self._raises is not None:
            raise self._raises
        return ProductStatus(name=self.name, executable="/bin/probed", version="1", trust="t")


def test_preflight_runs_once_with_the_cases_that_will_run(tmp_path):
    # The file's CASES_YAML: "passes" carries tags: [smoke], "fails" does not.
    runner = _PreflightRunner()
    report = run_evals([_skill_with_cases(tmp_path)], [runner], tag="smoke", repeat=3)
    assert runner.calls == [(["pdf"], {"pdf": ["passes"]})]  # filtered, and not per repeat
    assert report.products == [
        ProductStatus(name="probed", executable="/bin/probed", version="1", trust="t")
    ]


def test_preflight_sees_only_the_candidate_arm(tmp_path):
    runner = _PreflightRunner()
    run_evals([_skill_with_cases(tmp_path)], [runner], baseline="none")
    (call,) = runner.calls
    assert call == (["pdf"], {"pdf": ["passes", "fails"]})  # one entry per case, not per arm


def test_a_preflight_error_aborts_before_any_case_runs(tmp_path):
    class Boom(Exception):
        pass

    runner = _PreflightRunner(raises=Boom("no product"))
    with pytest.raises(Boom):
        run_evals([_skill_with_cases(tmp_path)], [runner])


def test_a_runner_without_the_hook_is_untouched(tmp_path):
    report = run_evals([_skill_with_cases(tmp_path)], [_runner()])
    assert report.products == []


def test_a_hook_returning_none_adds_no_status(tmp_path):
    class Quiet(FakeRunner):
        name = "quiet"

        def preflight(self, skills, cases_by_skill):
            return None

    report = run_evals([_skill_with_cases(tmp_path)], [Quiet(default=RunResult(output="yes"))])
    assert report.products == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator.py -k preflight -v`
Expected: the new tests FAIL (`products == []`, hook never called).

- [ ] **Step 3: Add the hook call**

In `src/skill_lens/orchestrator.py`, add before `run_evals`:

```python
def _preflight_runners(plan: _Plan, runners: list[Runner]) -> list[ProductStatus]:
    """Give every runner that defines `preflight` one look at what it will run.

    Called after discovery and before execution, so a runner can refuse the
    run -- by raising an authoring error -- before any quota is spent. Each
    runner sees the candidate-arm (skill, case) pairs planned for it, once
    each: compatibility is a property of (case, runner), so a case `--tag`
    or `--case` filtered out is not its concern, and neither arm nor repeat
    changes the answer.
    """
    statuses: list[ProductStatus] = []
    for runner in runners:
        hook = getattr(runner, "preflight", None)
        if hook is None:
            continue
        skills: list[Skill] = []
        cases_by_skill: dict[str, list[EvalCase]] = {}
        seen: set[tuple[str, str]] = set()
        for item in plan.items:
            if item.runner is not runner or item.arm != "candidate":
                continue
            key = (item.skill.name, item.case.name)
            if key in seen:
                continue
            seen.add(key)
            if item.skill.name not in cases_by_skill:
                skills.append(item.skill)
                cases_by_skill[item.skill.name] = []
            cases_by_skill[item.skill.name].append(item.case)
        status = hook(skills, cases_by_skill)
        if status is not None:
            statuses.append(status)
    return statuses
```

Add `ProductStatus` to the `from skill_lens.models import (...)` block. In `run_evals`, after the script preflight block (after the `else:` that builds `notes`) and before `_execute`:

```python
        # After the script preflight, so a ScriptSetupError and a
        # ProductSetupError cannot race for the exit.
        products = _preflight_runners(plan, runners)
        outcomes = _execute(plan.items, evaluators, concurrency, executor_factory, options, runtime)
```

and pass `products=products` to the `RunReport(...)` at the end. Add one sentence to the `run_evals` docstring after the `options` paragraph: "Every runner that defines a `preflight(skills, cases_by_skill)` hook is called once here too, with the candidate-arm cases planned for it; a status it returns lands on `RunReport.products`."

In `src/skill_lens/runners/base.py`, add to the `Runner` class docstring:

```
    A runner may also define an optional `preflight(skills, cases_by_skill)
    -> ProductStatus | None`. The orchestrator calls it once per run, after
    discovery and before any case runs, with the candidate-arm skills and the
    cases planned for this runner. It raises an authoring error to abort the
    run before anything is spent, and may return a `ProductStatus` for the
    report. The framework runners define none; `ProductRunner` does.
```

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_orchestrator.py -v && uv run ruff check . && uv run ruff format .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/orchestrator.py src/skill_lens/runners/base.py tests/test_orchestrator.py
git commit -m "feat: run each runner's preflight hook before any case

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Reporters render `products`

**Files:**
- Modify: `src/skill_lens/reporters/console.py` (`_script_lines` ~line 91; `render_console` ~line 200)
- Modify: `src/skill_lens/reporters/markdown.py` (`_scripts` ~line 334; the `optional` list ~line 380)
- Modify: `src/skill_lens/reporters/junit.py` (`_script_properties` ~line 130)
- Modify: `src/skill_lens/reporters/json_reporter.py` (~line 67)
- Test: `tests/test_reporters.py`, `tests/test_junit_reporter.py`, `tests/test_markdown_reporter.py`

**Interfaces:**
- Consumes: `RunReport.products: list[ProductStatus]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reporters.py` (add `ProductStatus` to its `skill_lens.models` import):

```python
_PRODUCT = ProductStatus(
    name="copilot", executable="/opt/homebrew/bin/copilot", version="1.0.37", trust="no sandbox"
)


def test_console_names_each_product_its_version_and_its_trust_model():
    report = _report().model_copy(update={"products": [_PRODUCT]})
    assert render_console(report).splitlines()[0] == (
        "product copilot 1.0.37 (/opt/homebrew/bin/copilot): no sandbox"
    )


def test_console_omits_a_blank_version():
    status = _PRODUCT.model_copy(update={"name": "cli", "version": ""})
    report = _report().model_copy(update={"products": [status]})
    assert render_console(report).splitlines()[0] == (
        "product cli (/opt/homebrew/bin/copilot): no sandbox"
    )


def test_console_prints_products_before_the_script_lines():
    report = _report().model_copy(
        update={
            "products": [_PRODUCT],
            "scripts": ScriptStatus(sandbox="bwrap", detail="bwrap probe succeeded"),
        }
    )
    first, second = render_console(report).splitlines()[:2]
    assert first.startswith("product copilot")
    assert second == "scripts: on, sandbox: bwrap"


def test_json_carries_the_products():
    report = _report().model_copy(update={"products": [_PRODUCT]})
    assert json.loads(render_json(report))["products"] == [_PRODUCT.model_dump()]
    assert json.loads(render_json(_report()))["products"] == []
```

Append to `tests/test_junit_reporter.py` (add `ProductStatus` to its import):

```python
_PRODUCT = ProductStatus(name="copilot", executable="/x/copilot", version="1.0.37", trust="t")


def test_products_are_a_property_on_every_kind_of_suite_first():
    # Same promise as the script properties: every <testsuite> of the run
    # carries the run-level facts, before any <testcase>.
    report = RunReport(
        outcomes=[_outcome()],
        skipped_skills=["docx"],
        tag_filtered_skills=["xlsx"],
        case_filtered_skills=["pptx"],
        products=[_PRODUCT],
    )
    root = _parse(report)
    suites = root.findall("testsuite")
    assert [s.get("name") for s in suites] == ["pdf", "docx", "xlsx", "pptx"]
    for suite in suites:
        assert suite[0].tag == "properties", suite.get("name")
        assert _property_names(suite) == ["skill-lens.products"]
        (prop,) = suite.findall("properties/property")
        assert prop.get("value") == "copilot 1.0.37 (/x/copilot): t"


def test_products_follow_the_script_properties_when_both_apply():
    root = _parse(RunReport(outcomes=[_outcome()], scripts=_SCRIPTS, products=[_PRODUCT]))
    assert _property_names(root.find("testsuite")) == [
        "skill-lens.scripts.sandbox",
        "skill-lens.scripts.detail",
        "skill-lens.products",
    ]


def test_the_zero_case_error_suite_carries_the_products():
    root = _parse(RunReport(outcomes=[], products=[_PRODUCT]), gate=evaluate_gate(RunReport()))
    (suite,) = root.findall("testsuite")
    assert _property_names(suite) == ["skill-lens.products"]
```

Place these after `test_no_suite_of_any_kind_carries_properties_when_scripts_are_off` so `_SCRIPTS`, `_property_names`, `_parse` and `_outcome` are in scope.

Append to `tests/test_markdown_reporter.py` (add `ProductStatus` to its import):

```python
def test_the_products_block_names_each_product():
    report = _report().model_copy(
        update={
            "products": [
                ProductStatus(name="copilot", executable="/x/copilot", version="1.0.37", trust="t")
            ]
        }
    )
    text = render_markdown(report)
    assert "Product `copilot` 1.0.37 (`/x/copilot`): t" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_reporters.py tests/test_junit_reporter.py tests/test_markdown_reporter.py -k product -v`
Expected: FAIL.

- [ ] **Step 3: Render**

`console.py` — add before `_script_lines`:

```python
def _product_lines(report: RunReport) -> list[str]:
    """One line per product the run executed: which, which version, under what trust.

    Printed on every run that used one, because the trust model -- no
    permission prompts, no sandbox -- is the product's and an operator must
    see it in the log, not remember it.
    """
    lines = []
    for product in report.products:
        version = f" {product.version}" if product.version else ""
        lines.append(f"product {product.name}{version} ({product.executable}): {product.trust}")
    return lines
```

and in `render_console` change `lines: list[str] = _script_lines(report)` to `lines: list[str] = [*_product_lines(report), *_script_lines(report)]`.

`markdown.py` — add before `_scripts`:

```python
def _products(report: RunReport) -> str:
    bits = []
    for product in report.products:
        version = f" {_escape(product.version)}" if product.version else ""
        bits.append(
            f"Product {_code(product.name)}{version} ({_code(product.executable)}): "
            f"{_escape(product.trust)}"
        )
    return "<sub>" + "<br>".join(bits) + "</sub>" if bits else ""
```

and insert `_products(report),` into the `optional` list immediately before `_scripts(report),`.

`junit.py` — rename `_script_properties` to `_run_properties` (update its three call sites) and make it:

```python
def _run_properties(suite: Element, report: RunReport) -> None:
    """The run's script and product facts, as the suite's first child. ...keep the
    existing docstring, adding: A product's line is the console's, so a CI UI
    and a terminal say the same thing."""
    if report.scripts is None and not report.products:
        return
    properties = SubElement(suite, "properties")
    if report.scripts is not None:
        SubElement(properties, "property", name="skill-lens.scripts.sandbox", value=_xml_safe(report.scripts.sandbox))
        SubElement(properties, "property", name="skill-lens.scripts.detail", value=_xml_safe(report.scripts.detail))
    if report.products:
        SubElement(
            properties,
            "property",
            name="skill-lens.products",
            value=_xml_safe("; ".join(_product_line(p) for p in report.products)),
        )


def _product_line(product: ProductStatus) -> str:
    version = f" {product.version}" if product.version else ""
    return f"{product.name}{version} ({product.executable}): {product.trust}"
```

`json_reporter.py` — after the `script_notes` line: `payload["products"] = [p.model_dump() for p in report.products]`.

- [ ] **Step 4: Run every reporter test and lint**

Run: `uv run pytest tests/test_reporters.py tests/test_junit_reporter.py tests/test_markdown_reporter.py tests/test_failure_context.py -v && uv run ruff check . && uv run ruff format .`
Expected: PASS — including the existing "no properties element when scripts are off" tests, which still hold because `products` is empty there.

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/reporters tests/test_reporters.py tests/test_junit_reporter.py tests/test_markdown_reporter.py
git commit -m "feat: report which products a run executed

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: `[runners.<name>]` in `skill-lens.toml`

**Files:**
- Modify: `src/skill_lens/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `runners.product.PRESETS`, `Product`, `PROMPT_PLACEHOLDER`, `DEFAULT_SKILLS_DIR`, `DEFAULT_INVOKE`, `DEFAULT_TIMEOUT_SECONDS`, `DEFAULT_MAX_OUTPUT_BYTES` (Task 5); `workspace.check_relative_path`.
- Produces: `PRODUCT_NAMES = ("copilot", "claude-code", "cli")`; `ProductSettings`; `Config.runners: dict[str, ProductSettings]`; `Config.product(name: str) -> Product` (raises `ConfigError`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
from skill_lens.config import PRODUCT_NAMES, ProductSettings
from skill_lens.runners.product import PRESETS


def test_product_names_are_the_presets_plus_cli():
    assert set(PRODUCT_NAMES) == set(PRESETS) | {"cli"}


def test_product_settings_have_the_documented_defaults():
    settings = ProductSettings()
    assert settings.command is None
    assert settings.args == []
    assert settings.timeout_seconds == 600.0
    assert settings.max_output_bytes == 8_000_000
    assert settings.skills_dir is None
    assert settings.invoke is None


def test_a_preset_table_appends_args_and_sets_the_timeout(tmp_path):
    (tmp_path / "skill-lens.toml").write_text(
        '[runners.copilot]\nargs = ["--model", "gpt-5.2"]\ntimeout_seconds = 900\n',
        encoding="utf-8",
    )
    product = load_config(tmp_path / "skill-lens.toml").product("copilot")
    assert product.argv == (*PRESETS["copilot"].argv, "--model", "gpt-5.2")
    assert product.timeout_seconds == 900.0
    assert product.parse is PRESETS["copilot"].parse


def test_command_replaces_a_presets_argv_wholesale(tmp_path):
    (tmp_path / "skill-lens.toml").write_text(
        '[runners.claude-code]\ncommand = ["claude", "-p", "{prompt}", "--output-format", "stream-json", "--verbose"]\n',
        encoding="utf-8",
    )
    product = load_config(tmp_path / "skill-lens.toml").product("claude-code")
    assert product.argv == ("claude", "-p", "{prompt}", "--output-format", "stream-json", "--verbose")
    assert product.skills_dir == ".claude/skills"  # the preset's, untouched


def test_a_preset_needs_no_table_at_all():
    assert Config().product("copilot") == PRESETS["copilot"]


def test_cli_builds_from_its_table(tmp_path):
    (tmp_path / "skill-lens.toml").write_text(
        '[runners.cli]\ncommand = ["my-agent", "--prompt", "{prompt}"]\n'
        'skills_dir = ".github/skills"\ninvoke = "use {name}: {task}"\n',
        encoding="utf-8",
    )
    product = load_config(tmp_path / "skill-lens.toml").product("cli")
    assert product.name == "cli"
    assert product.argv == ("my-agent", "--prompt", "{prompt}")
    assert product.skills_dir == ".github/skills"
    assert product.invoke == "use {name}: {task}"
    assert product.parse is None
    assert product.version_command is None


def test_cli_defaults_to_the_agents_directory_and_the_bare_task(tmp_path):
    (tmp_path / "skill-lens.toml").write_text(
        '[runners.cli]\ncommand = ["my-agent", "{prompt}"]\n', encoding="utf-8"
    )
    product = load_config(tmp_path / "skill-lens.toml").product("cli")
    assert product.skills_dir == ".agents/skills"
    assert product.invoke == "{task}"


def test_cli_without_a_command_is_a_config_error_naming_the_key():
    with pytest.raises(ConfigError, match=r"\[runners\.cli\] command"):
        Config().product("cli")


def test_an_unknown_product_is_a_config_error():
    with pytest.raises(ConfigError, match=r"unknown product"):
        Config().product("vim")


@pytest.mark.parametrize(
    "toml, message",
    [
        ('[runners.vim]\nargs = []\n', "unknown product"),
        ('[runners.copilot]\ncommand = ["copilot", "-p"]\n', "exactly one element equal to {prompt}"),
        ('[runners.copilot]\ncommand = ["copilot", "{prompt}", "{prompt}"]\n', "exactly one element"),
        ('[runners.copilot]\ncommand = ["copilot", "-p{prompt}"]\n', "exactly one element"),
        ('[runners.copilot]\nskills_dir = ".x"\n', "fixed by the copilot preset"),
        ('[runners.claude-code]\ninvoke = "{task}"\n', "fixed by the claude-code preset"),
        ('[runners.cli]\ncommand = ["a", "{prompt}"]\nskills_dir = "/abs"\n', "skills_dir"),
        ('[runners.cli]\ncommand = ["a", "{prompt}"]\nskills_dir = "../up"\n', "skills_dir"),
        ('[runners.cli]\ncommand = ["a", "{prompt}"]\ninvoke = "no task here"\n', "{task}"),
        ('[runners.copilot]\ntimeout_seconds = 0\n', "timeout_seconds"),
        ('[runners.copilot]\ntimeout_seconds = inf\n', "timeout_seconds"),
        ('[runners.copilot]\nmax_output_bytes = 0\n', "max_output_bytes"),
        ('[runners.copilot]\nnonsense = 1\n', "nonsense"),
    ],
)
def test_invalid_product_settings_are_config_errors(tmp_path, toml, message):
    (tmp_path / "skill-lens.toml").write_text(toml, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path / "skill-lens.toml")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k "product or cli or preset" -v`
Expected: FAIL with `ImportError: cannot import name 'PRODUCT_NAMES'`.

- [ ] **Step 3: Add the settings model and the builder**

In `src/skill_lens/config.py`:

```python
from dataclasses import replace

from skill_lens.runners.product import (
    DEFAULT_INVOKE,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_SKILLS_DIR,
    DEFAULT_TIMEOUT_SECONDS,
    PRESETS,
    PROMPT_PLACEHOLDER,
    Product,
)
from skill_lens.workspace import DEFAULT_LIMITS, PathRefused, check_relative_path

PRODUCT_NAMES: tuple[str, ...] = (*PRESETS, "cli")


class ProductSettings(BaseModel):
    """One `[runners.<name>]` table: how a product runner or judge is started.

    `command` replaces the preset's whole argv (and is required for `cli`,
    which has no preset); `args` is appended to whichever argv results. Two
    knobs with two meanings: drop an isolation flag with `command`, add a
    model with `args`. `skills_dir` and `invoke` are accepted for `cli` only;
    a preset is the verified spelling for its product. No secret lives here:
    the product reads its own auth.
    """

    model_config = ConfigDict(extra="forbid")

    command: list[str] | None = None
    args: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0, allow_inf_nan=False)
    max_output_bytes: int = Field(default=DEFAULT_MAX_OUTPUT_BYTES, gt=0)
    skills_dir: str | None = None
    invoke: str | None = None

    @field_validator("command")
    @classmethod
    def _one_prompt_element(cls, value: list[str] | None) -> list[str] | None:
        """The prompt is substituted as one whole argv element, never through a shell."""
        if value is None:
            return None
        if value.count(PROMPT_PLACEHOLDER) != 1 or not value[0].strip():
            raise ValueError(
                f"must name an executable and contain exactly one element equal to "
                f"{PROMPT_PLACEHOLDER}"
            )
        return value

    @field_validator("skills_dir")
    @classmethod
    def _inside_the_working_directory(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            check_relative_path(value)
        except PathRefused as exc:
            raise ValueError(str(exc)) from exc
        return value

    @field_validator("invoke")
    @classmethod
    def _carries_the_task(cls, value: str | None) -> str | None:
        if value is not None and "{task}" not in value:
            raise ValueError('must contain "{task}"')
        return value
```

In `Config`, after `script_interpreters`:

```python
    runners: dict[str, ProductSettings] = Field(default_factory=dict)

    @field_validator("runners")
    @classmethod
    def _product_tables_are_well_formed(
        cls, value: dict[str, ProductSettings]
    ) -> dict[str, ProductSettings]:
        for key, settings in value.items():
            if key not in PRODUCT_NAMES:
                raise ValueError(
                    f"runners.{key}: unknown product; expected one of {', '.join(PRODUCT_NAMES)}"
                )
            if key != "cli" and (settings.skills_dir is not None or settings.invoke is not None):
                raise ValueError(
                    f"runners.{key}: skills_dir and invoke are fixed by the {key} preset; "
                    "describe a product with different spellings under runners.cli"
                )
        return value

    def product(self, name: str) -> Product:
        """The product a runner or judge named `name` starts: preset plus its table.

        Checked here rather than at load time because the name can arrive
        from `default_runner`, `judge` or the `--runner` flag, and only the
        first two are visible to the model.
        """
        if name not in PRODUCT_NAMES:
            raise ConfigError(f"unknown product: {name}")
        settings = self.runners.get(name, ProductSettings())
        if name == "cli":
            if settings.command is None:
                raise ConfigError(
                    "runner cli needs [runners.cli] command in skill-lens.toml, "
                    "e.g. command = [\"my-agent\", \"--prompt\", \"{prompt}\"]"
                )
            base = Product(
                name="cli",
                argv=tuple(settings.command),
                skills_dir=settings.skills_dir or DEFAULT_SKILLS_DIR,
                invoke=settings.invoke or DEFAULT_INVOKE,
                parse=None,
                version_command=None,
            )
        else:
            base = PRESETS[name]
            if settings.command is not None:
                base = replace(base, argv=tuple(settings.command))
        return replace(
            base,
            argv=(*base.argv, *settings.args),
            timeout_seconds=settings.timeout_seconds,
            max_output_bytes=settings.max_output_bytes,
        )
```

Add to the `Config` docstring, after the `allow_scripts` paragraph: "`runners` holds one `[runners.<name>]` table per product runner or judge (`copilot`, `claude-code`, `cli`); see `ProductSettings`. Config-only: which product a repository evaluates under, and how, is repository policy."

- [ ] **Step 4: Run the tests and lint**

Run: `uv run pytest tests/test_config.py -v && uv run ruff check . && uv run ruff format .`
Expected: PASS. (`ruff` may reorder the imports; accept.)

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/config.py tests/test_config.py
git commit -m "feat: configure product runners in skill-lens.toml

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: The command line — `--runner copilot`, the `--model` rule, exit 2 for setup errors

**Files:**
- Modify: `src/skill_lens/cli.py` (`_RUNNERS` ~line 40, `_AUTHORING_ERRORS` ~line 48, `_resolve_runners` ~line 108, the runner/judge construction inside `run` ~lines 237-270, the `Plan:` block ~line 271)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `ProductRunner`, `ProductSetupError` (Tasks 5–6); `Config.product`, `PRODUCT_NAMES` (Task 9).
- Produces: `_build_runner(name: str, settings: Config, model_name: str) -> Runner`; `_RUNNER_NAMES`; `_KEYED_RUNNERS`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
import sys
from pathlib import Path

FAKE_PRODUCT = Path(__file__).parent / "fake_product.py"
PRODUCT_FIXTURES = Path(__file__).parent / "fixtures" / "products"

PRODUCT_CASES_YAML = """cases:
  - name: pongs
    task: Please ping.
    assertions:
      - kind: contains
        value: PONG
"""

TOOLS_CASES_YAML = """cases:
  - name: uses a mock tool
    task: anything
    tools:
      - name: lookup
    assertions:
      - kind: contains
        value: x
"""


def _product_config(tmp_path, name="copilot") -> Path:
    command = [sys.executable, str(FAKE_PRODUCT), "-p", "{prompt}"]
    body = f"[runners.{name}]\ncommand = {command!r}\n".replace("'", '"')
    path = tmp_path / "skill-lens.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_product_runner_runs_the_case_and_names_the_product(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(PRODUCT_FIXTURES / "copilot-trigger.jsonl"))
    skill_dir = _make_skill(tmp_path, cases=PRODUCT_CASES_YAML)
    config = _product_config(tmp_path)
    result = runner.invoke(
        app, ["run", str(skill_dir), "--runner", "copilot", "--config", str(config)]
    )
    assert result.exit_code == 0, result.output
    assert "product copilot Python" in result.output  # the fake's argv[0] is the interpreter
    assert "permission prompts disabled" in result.output
    assert "Plan: up to 1 arm(s) x 1 repeat(s) x 1 runner(s) x 1 case(s) = 1 runs" in result.output
    assert "pdf :: pongs (copilot)" in result.output


def test_a_product_that_is_not_installed_is_exit_2_before_any_case(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # nothing on it
    skill_dir = _make_skill(tmp_path, cases=PRODUCT_CASES_YAML)
    result = runner.invoke(app, ["run", str(skill_dir), "--runner", "copilot"])
    assert result.exit_code == 2
    assert "runner copilot: 'copilot' is not on PATH" in plain(result.output)


def test_cli_without_a_command_table_is_exit_2_naming_the_key(tmp_path):
    skill_dir = _make_skill(tmp_path, cases=PRODUCT_CASES_YAML)
    result = runner.invoke(app, ["run", str(skill_dir), "--runner", "cli"])
    assert result.exit_code == 2
    assert "[runners.cli] command" in plain(result.output)


def test_a_case_with_mock_tools_under_a_product_runner_is_exit_2(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(PRODUCT_FIXTURES / "copilot-trigger.jsonl"))
    skill_dir = _make_skill(tmp_path, cases=TOOLS_CASES_YAML)
    config = _product_config(tmp_path)
    result = runner.invoke(
        app, ["run", str(skill_dir), "--runner", "copilot", "--config", str(config)]
    )
    assert result.exit_code == 2
    assert "declares tools:" in plain(result.output)


def test_model_with_nothing_that_reads_it_is_a_user_error(tmp_path):
    skill_dir = _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(skill_dir), "--runner", "fake", "--model", "gpt-5.2"])
    assert result.exit_code == 2
    assert "--model is read by pydantic-ai and langchain only" in plain(result.output)
    assert "[runners.<name>] args" in plain(result.output)


def test_model_is_allowed_when_a_keyed_judge_falls_back_to_it(tmp_path):
    skill_dir = _make_skill(tmp_path)
    (tmp_path / "skill-lens.toml").write_text('judge = "pydantic-ai"\n', encoding="utf-8")
    result = runner.invoke(
        app,
        ["run", str(skill_dir), "--runner", "fake", "--model", "openai:gpt-4o-mini",
         "--config", str(tmp_path / "skill-lens.toml")],
        env={"OPENAI_API_KEY": "k"},
    )
    assert result.exit_code == 0, result.output  # no judge: block, so nothing is spent


def test_judge_model_with_a_judge_that_does_not_read_it_is_a_user_error(tmp_path):
    skill_dir = _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(skill_dir), "--judge-model", "gpt-5.2"])
    assert result.exit_code == 2
    assert '--judge-model is read by judge = "pydantic-ai" or "langchain" only' in plain(result.output)


def test_a_product_runner_and_a_keyed_runner_share_one_invocation(tmp_path, monkeypatch):
    # --model reaches the keyed runner; the product ignores it. Preflight for
    # the keyed runner (no key) stops the run first, which is enough to prove
    # both names resolve.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill_dir = _make_skill(tmp_path, cases=PRODUCT_CASES_YAML)
    config = _product_config(tmp_path)
    result = runner.invoke(
        app,
        ["run", str(skill_dir), "--runner", "copilot", "--runner", "pydantic-ai",
         "--model", "openai:gpt-4o-mini", "--config", str(config)],
    )
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "product or model_with or judge_model_with or cli_without or mock_tools_under" -v`
Expected: FAIL (`unknown runner: copilot`, exit 0 where 2 is expected).

- [ ] **Step 3: Wire the CLI**

In `src/skill_lens/cli.py`:

Imports — add `from skill_lens.config import PRODUCT_NAMES, Config, ConfigError, load_config` (extending the existing line), `from skill_lens.runners.base import Runner, RunnerDependencyError`, `from skill_lens.runners.product import ProductRunner, ProductSetupError`.

Replace the `_RUNNERS` line:

```python
# Keyed runners share one model and one API key; product runners are built
# from their [runners.<name>] table; `fake` takes nothing.
_KEYED_RUNNERS = {"pydantic-ai": PydanticAIRunner, "langchain": LangChainRunner}
_RUNNER_NAMES: tuple[str, ...] = ("fake", *_KEYED_RUNNERS, *PRODUCT_NAMES)
```

Add `ProductSetupError,` to `_AUTHORING_ERRORS` with the comment `# a product runner that cannot run here: executable missing, version probe failed, or a case it cannot serve`.

In `_resolve_runners`, change `if name not in _RUNNERS:` to `if name not in _RUNNER_NAMES:`.

Add after `_resolve_runners`:

```python
def _build_runner(name: str, settings: Config, model_name: str) -> Runner:
    """One runner by name: `fake` takes nothing, a keyed runner takes the shared
    model, a product runner takes its `[runners.<name>]` table."""
    if name == "fake":
        return FakeRunner()
    if name in _KEYED_RUNNERS:
        return _KEYED_RUNNERS[name](
            model=model_name,
            temperature=settings.temperature,
            retries=settings.retries,
            retry_backoff_seconds=settings.retry_backoff_seconds,
        )
    return ProductRunner(settings.product(name))
```

In `run`, replace the block from `runner_names = _resolve_runners(...)` down to and including the `active_judge = judge_class()` else-branch with:

```python
        runner_names = _resolve_runners(runner, settings.default_runner)
        needs_key = any(name in _KEYED_RUNNERS for name in runner_names)
        uses_product = any(name in PRODUCT_NAMES for name in runner_names)
        model_name = model if model is not None else settings.model
        judge_name = settings.judge
        if judge_name not in _JUDGES:
            raise typer.BadParameter(f"unknown judge: {judge_name}")
        judge_class = _JUDGES[judge_name]
        judge_needs_key = getattr(judge_class, "needs_api_key", False)
        # A flag nothing reads is a trap, not a no-op: `--runner copilot
        # --model gpt-5.2` would look honoured while the product ran its own
        # default. `--model` is read by a keyed runner, or by a keyed judge
        # whose own model is unset (it falls back to `model`).
        model_is_read = needs_key or (
            judge_needs_key and judge_model is None and not settings.judge_model
        )
        if model is not None and not model_is_read:
            raise typer.BadParameter(
                "--model is read by pydantic-ai and langchain only, and this run names "
                "neither; a product's model is set with "
                '[runners.<name>] args = ["--model", "..."] in skill-lens.toml'
            )
        if judge_model is not None and not judge_needs_key:
            raise typer.BadParameter(
                '--judge-model is read by judge = "pydantic-ai" or "langchain" only; '
                f'this run\'s judge is "{judge_name}"'
            )
        if needs_key:
            # Once for the whole matrix: every keyed runner shares one model.
            _require_a_model("--model", model_name)
            check_api_key(model_name, os.environ)
        active_runners = [_build_runner(name, settings, model_name) for name in runner_names]
        # An empty judge_model means "grade with the same model you run with",
        # so a project opting into real judging only has to name one model.
        resolved_judge_model = (
            judge_model if judge_model is not None else (settings.judge_model or model_name)
        )
        if judge_needs_key:
            _require_a_model("--judge-model", resolved_judge_model)
            check_api_key(resolved_judge_model, os.environ)
            active_judge = judge_class(
                model=resolved_judge_model,
                temperature=settings.judge_temperature,
                retries=settings.retries,
                retry_backoff_seconds=settings.retry_backoff_seconds,
            )
        else:
            active_judge = judge_class()
```

Change the `Plan:` guard from `if needs_key:` to `if needs_key or uses_product:` and its comment's first line to "A ceiling, not a forecast. Printed for keyed and product runners alike: both spend."

- [ ] **Step 4: Run the whole CLI suite and lint**

Run: `uv run pytest tests/test_cli.py tests/test_cli_init.py -v && uv run ruff check . && uv run ruff format .`
Expected: PASS. Every existing test still passes: `test_the_real_runner_is_registered` passes `--model` with a keyed runner; `test_the_fake_runner_needs_no_key` passes no `--model`.

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/cli.py tests/test_cli.py
git commit -m "feat: run cases through a product from the command line

--runner copilot, claude-code and cli resolve from the new tables; a
product runner that cannot run here is exit 2 before any case; and a
--model or --judge-model that nothing reads is a user error rather than a
silently ignored flag.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Documentation

**Files:**
- Modify: `docs/runners.md`, `docs/configuration.md`, `docs/cli.md`, `docs/eval-files.md`, `docs/gating.md`, `docs/security.md`, `docs/ci.md`, `docs/roadmap.md`, `ARCHITECTURE.md`, `CLAUDE.md`, `examples/skill-lens.toml`
- Test: `tests/test_docs.py` (existing), `uv run mkdocs build --strict`

- [ ] **Step 1: Run the docs tests to see what fails**

Run: `uv run pytest tests/test_docs.py -v`
Expected: `test_every_config_field_is_documented` FAILS (`runners` missing from `docs/configuration.md`). Everything else passes — the plan still writes the pages below, because `tests/test_docs.py` checks presence, not completeness.

- [ ] **Step 2: `docs/runners.md`**

Change the opening paragraph so it names three ways to run a real agent (a framework extra, or an installed product), then add this section immediately after "Two frameworks, one measurement":

````markdown
## Product runners

A skill written for a named product — GitHub Copilot CLI, Claude Code — is deployed into
that product's skill directory and loaded by that product's own mechanics. A framework
runner approximates that; a **product runner** starts the product itself, in its
non-interactive mode, with the skill placed where the product discovers skills, and reads
the result from the product's machine-readable trace. No provider API key is involved: the
product uses its own auth.

```bash
skill-lens run ./skills --runner copilot
skill-lens run ./skills --runner claude-code
skill-lens run ./skills --runner copilot --runner pydantic-ai --model openai:gpt-4o-mini
```

| Runner | Starts | Skill directory | Trace |
| --- | --- | --- | --- |
| `copilot` | `copilot -p <prompt> --allow-all-tools --output-format json --no-custom-instructions --no-auto-update` | `.agents/skills/<name>/` | Copilot's JSONL |
| `claude-code` | `claude -p <prompt> --output-format stream-json --verbose --dangerously-skip-permissions --setting-sources project --strict-mcp-config --no-session-persistence` | `.claude/skills/<name>/` | Claude Code's `stream-json` |
| `cli` | the `command` in `[runners.cli]` | `skills_dir` (default `.agents/skills`) | none — stdout is the output |

The flags are the verified minimum: what the product needs to run without a terminal,
what emits the trace, and what keeps *your* setup out of the eval. `--no-custom-instructions`
stops a stray `AGENTS.md` or a personal instructions file shaping a Copilot run;
`--setting-sources project --strict-mcp-config` keeps your own hooks, plugins and MCP
servers out of a Claude Code run while the project skill is still discovered and
OAuth auth still works. Personal skills and plugins under `~/.copilot` do still load for
Copilot — that is the product as you have it; for a hermetic run point `COPILOT_HOME`
at an empty directory and set `COPILOT_GITHUB_TOKEN`. Add flags with
`[runners.<name>] args` (a model, say); replace the whole argv with `command`. See
[Configuration](configuration.md#product-runners).

**What the product sees.** The eval's working directory (the case's workspace when it
declares one, a fresh temporary directory otherwise) holds `SKILL.md` **byte for byte** —
products honour frontmatter keys skill-lens does not model, such as `allowed-tools` — and
beside it `scripts/`, `references/` and `assets/`, nothing else. The prompt is the case's
`task`, verbatim: the product owns its system prompt, and skill-lens adds no preamble.
`mode: loaded` invokes the skill through the product's own spelling (`/<name> <task>`);
`mode: offered` sends the bare task and reads the product's skill-load event — the
`skill.invoked` event in Copilot, the `Skill` tool call in Claude Code — so a negative
control is measured, never assumed. Under `--baseline none` the baseline arm has no skill
directory and gets the bare task; under `--baseline previous` the previous version is
delivered with its own bundle.

**What a product runner can measure.**

| | `copilot` | `claude-code` | `cli` |
| --- | --- | --- | --- |
| Output text and `assertions:` | yes | yes | yes (stdout) |
| `trajectory:` (the product's own tool names, e.g. `bash`, `Bash`) | yes | yes | no — an authoring error |
| `mode: offered` / `skill_triggered` | yes | yes | no — an authoring error |
| `budget: max_tokens` | when the trace reports usage; otherwise a failing "not evaluated" check | yes (input + cache read + cache write) | failing "not evaluated" check |
| `budget: max_cost_usd` | failing "not evaluated" check — Copilot bills per premium request | yes, at the list price the product reports | failing "not evaluated" check |
| `budget: max_latency_ms` | yes | yes | yes |
| `tools:` (mock tools) | authoring error under any product runner | | |

A case the runner cannot serve is an **authoring error** (exit 2) found in preflight,
before any case runs and before any quota is spent — never a vacuous pass. `--model` is
not read by a product runner: a flag nothing reads is refused as a user error rather than
silently ignored; set the product's model in its table.

**Errors.** A timeout (`timeout_seconds`, default 600), a non-zero exit, a product-reported
failure, a trace cut short by `max_output_bytes`, and a missing executable at run time are
all **errored** cases — never raised, never failed. Preflight checks the executable is on
`PATH` and actually starts (`--version`), and the report names the product and version on
every run.

**Trust.** The product runs with permission prompts disabled and inherits your whole
environment — it needs its own auth, and cannot run non-interactively otherwise. No
skill-lens sandbox applies; the skill's bundled scripts are reachable through the product's
own shell whatever `allow_scripts` says, which governs only skill-lens's `run_script` tool.
**Naming a product runner is that decision**, and every report says so. See
[Security](security.md#product-runners).
````

Also, in "Budget limits and pricing", add one sentence: "A runner that cannot count tokens sets `usage_note`, and `max_tokens` is then a failing *not evaluated* check, exactly as `max_cost_usd` is under `cost_note`."

- [ ] **Step 3: `docs/configuration.md`**

Add a row to the key table: `| `runners` | `{}` | — |`. Then add this section before "## Bundled scripts":

````markdown
## Product runners

One `[runners.<name>]` table per product runner — `copilot`, `claude-code`, or `cli`:

```toml
default_runner = ["copilot", "claude-code"]

[runners.copilot]
args = ["--model", "gpt-5.2"]      # appended to the preset's argv
timeout_seconds = 900              # default 600; finite and positive

[runners.claude-code]
# Replaces the preset's argv entirely; keep the output-format flag or the
# trace cannot be read.
command = ["claude", "-p", "{prompt}", "--output-format", "stream-json", "--verbose", "--dangerously-skip-permissions"]

[runners.cli]
command = ["my-agent", "--prompt", "{prompt}"]   # required for cli
skills_dir = ".agents/skills"                     # default
invoke = "/{name} {task}"                         # default "{task}"
max_output_bytes = 8000000                        # default
```

| Key | Default | Meaning |
| --- | --- | --- |
| `command` | the preset's argv (required for `cli`) | The whole argv; exactly one element must be `{prompt}`, substituted as one element, never through a shell. |
| `args` | `[]` | Appended after `command`. |
| `timeout_seconds` | `600.0` | Wall clock per case; the process group is killed at expiry. |
| `max_output_bytes` | `8000000` | Cap on the trace; a longer one is an errored case naming this key. |
| `skills_dir` | `".agents/skills"` | `cli` only. Where the skill is written, relative to the working directory. |
| `invoke` | `"{task}"` | `cli` only. The prompt in `mode: loaded`; `{name}` and `{task}` are substituted. |

`skills_dir` or `invoke` under a preset, an unknown table name, a `command` with no
`{prompt}` element, and `cli` named as a runner with no `command` are all config errors
(exit 2). No API key or token belongs here: the product reads its own auth.
````

- [ ] **Step 4: `docs/cli.md`**

In the `run` section, extend the `--runner` description: "`fake`, `pydantic-ai`, `langchain`, `copilot`, `claude-code` or `cli`; repeatable." Add under `--model`: "Read by `pydantic-ai` and `langchain` (and by a keyed judge whose `judge_model` is unset). Passing it to a run where nothing reads it — `--runner fake`, or only product runners — is a user error (exit 2); a product's model is set with `[runners.<name>] args`." Under `--judge-model`: "Read by `judge = "pydantic-ai"` or `"langchain"` only; otherwise exit 2."

- [ ] **Step 5: `docs/eval-files.md`**

After "Did the agent reach for the skill?", add:

```markdown
## Which runners serve which case features

| Case feature | `fake` | `pydantic-ai` / `langchain` | `copilot` / `claude-code` | `cli` |
| --- | --- | --- | --- | --- |
| `assertions:` | yes | yes | yes | yes |
| `tools:` (mock tools) | yes | yes | authoring error | authoring error |
| `trajectory:` | yes | yes | yes, the product's tool names | authoring error |
| `mode: offered` | yes | yes | yes | authoring error |
| `budget:` | yes | yes | see [Product runners](runners.md#product-runners) | latency only |
| `workspace:` | yes | yes | yes — the product's working directory | yes |
| `judge:` | yes | yes | yes | yes |

An authoring error here is found in preflight and exits 2 before any case runs.
```

- [ ] **Step 6: `docs/gating.md`**

In "JSON report", document the new top-level field: "`products` — one entry per product runner the run executed: `name`, `executable`, `version`, `trust` (the fixed sentence about permission prompts and the missing sandbox). Empty when no product runner ran." Under the exit-code list, add to the exit-2 causes: "a product runner that cannot run here (executable not on `PATH`, `--version` failing, a case with `tools:`, or `trajectory:`/`offered` under `cli`), and a `--model` or `--judge-model` that nothing in the run reads." In "JUnit XML", add: "`skill-lens.products` is a suite property on every suite when a product runner ran."

- [ ] **Step 7: `docs/security.md`**

Add before "Why these rules":

```markdown
## Product runners

A product runner (`--runner copilot`, `claude-code`, `cli`) hands the skill to an agent
product with its **permission prompts disabled** (`--allow-all-tools`,
`--dangerously-skip-permissions`) — the product cannot run non-interactively otherwise —
and with the **whole environment inherited**, because the product needs its own auth.
Everything the product can do, the skill under evaluation can make it do: run shell
commands as you, read what you can read, and run the skill's own bundled scripts through
the product's shell, whatever `allow_scripts` says. `allow_scripts` governs only
skill-lens's `run_script` tool; no skill-lens sandbox applies to a product.

**Naming a product runner is the trust decision.** The report states it on every run
(`product copilot 1.0.37 (...): runs with permission prompts disabled ...`), and the
JSON and JUnit reports carry the same sentence.

`skill-lens.toml` is inside the trust boundary: in a `pull_request` workflow the checkout
is the pull request, so the file can name a product runner for itself. A workflow that
runs untrusted pull requests should pin `runner:` explicitly in the action (the flag
replaces the file) and pass the product's token only to jobs it trusts. For a hermetic
Copilot run, point `COPILOT_HOME` at an empty directory and provide
`COPILOT_GITHUB_TOKEN`, so nothing from your personal `~/.copilot` loads.
```

- [ ] **Step 8: `docs/ci.md`**

After "Running the matrix", add:

````markdown
## Running under a product

A product runner needs the product installed on the runner and its token in the
environment. Pin `runner:` so a pull request cannot pick the product for itself:

```yaml
- uses: actions/setup-node@<sha> # vX.Y.Z
  with: { node-version: 22 }
- run: npm install -g @github/copilot
- uses: EmadMokhtar/skill-evaluator@v0.7.0
  with:
    path: ./skills
    runner: copilot
  env:
    COPILOT_GITHUB_TOKEN: ${{ secrets.COPILOT_TOKEN }}
```

Each case spends the product's quota; the `Plan:` line prints the ceiling.
````

Use the real current version in the `uses:` line (every version spelling gets a line of its own — `tests/test_release_config.py` requires every spelled version to be the current one; check `pyproject.toml`).

- [ ] **Step 9: `docs/roadmap.md`, `ARCHITECTURE.md`, `CLAUDE.md`, `examples/skill-lens.toml`**

`docs/roadmap.md`: add the row `| M9 | Product runners: `copilot`, `claude-code`, a configured `cli`; product judge | in progress |` and a "## What M9 part 1 shipped" paragraph summarising §1 of the spec (runner, presets, `[runners.<name>]`, preflight, `products` on the report, the `usage_note` rule, the `--model` rule; the judge is part 2), linking the spec.

`ARCHITECTURE.md` module map — add rows:

```markdown
| `process.py` | Starts a child in its own process group, waits with a timeout, kills the group after every exit, and reads output through the harness's own handle. Shared by `scripts.py` and `runners/product.py`; imports nothing from the rest of the project. |
| `runners/traces.py` | The Copilot JSONL and Claude Code `stream-json` parsers, each producing one `Trace`. Pure functions; a structural problem is `Trace.error`, never a raise. |
| `runners/product.py` | The product runner: a `Product` value (argv template, skill directory, invocation spelling, trace parser, version command), the two presets, skill delivery into the product's working directory, the subprocess invocation, and the once-per-run `preflight`. Imports no agent framework. |
```

Update the `orchestrator.py` row: "... between discovery and execution it also calls every runner's optional `preflight` hook, and puts what they return on `RunReport.products`." Add to "The three protocols" after the shared rule: "`Runner` may also define an optional `preflight(skills, cases_by_skill)`; the orchestrator calls it once per run before any case, and it raises an authoring error to abort." Add the M9 invariants (spec §10, items 1–10) to the invariants section in the same voice as the existing ones.

`CLAUDE.md`: update the status paragraph ("Currently at **M9 (part 1 complete)** ... M9 part 1 adds product runners ...", naming the spec `docs/superpowers/specs/2026-09-17-skill-lens-m9-design.md`), add `runners/product.py` and `runners/traces.py` to the "No agent-framework type may appear outside the four adapter modules" bullet's list of neutral modules, and add these bullets to the invariants list:

```markdown
- **The product sees `SKILL.md` byte for byte.** `Skill.markdown` is the file; only the loader
  and the baseline resolver set it; `--baseline none` has none, so no directory is written.
- **A product runner's prompt is the task verbatim; the baseline-none arm never sees the
  skill's name.** `loaded` invokes the skill by the product's own spelling; `offered` sends
  the bare task and reads the product's load event — a product without one (`cli`) makes
  `offered` an authoring error, never a silent `false`.
- **A limit the product cannot measure fails, it never passes.** `RunResult.usage_note` for
  tokens mirrors `cost_note` for cost in `BudgetEvaluator`.
- **A truncated product trace is `RunResult.error`, never a partial parse**; a complete
  trace carrying the product's own error message wins over the exit code.
- **Naming a product runner is the trust decision, and the report says so.** Prompts
  disabled, full environment, no sandbox; `allow_scripts` governs only `run_script`.
- **Product preflight spends nothing**: executable found and executed, skill names checked,
  `tools:` refused, all before the first case; only the cases that will run are inspected.
- **`--model` / `--judge-model` with nothing that reads them are user errors** (exit 2).
- **`process.py` is the one implementation** of group kill and capped read; `scripts.py`
  and `runners/product.py` both import it.
```

`examples/skill-lens.toml`: before the `[per_skill_min]` table (which must stay last — the comment there says why), add:

```toml
# Product runners: start the product the skill ships to, no API key needed.
# Each table is optional; the preset supplies the verified argv.
# [runners.copilot]
# args = ["--model", "gpt-5.2"]     # appended to the preset's argv
# timeout_seconds = 600.0
# max_output_bytes = 8000000
#
# [runners.cli]
# command = ["my-agent", "--prompt", "{prompt}"]   # exactly one "{prompt}" element
# skills_dir = ".agents/skills"
# invoke = "{task}"
```

- [ ] **Step 10: Verify the docs**

Run: `uv sync --group docs && uv run mkdocs build --strict && uv run pytest tests/test_docs.py tests/test_naming.py tests/test_examples.py tests/test_release_config.py -v`
Expected: PASS; `mkdocs` builds with no warnings (a broken anchor such as `runners.md#product-runners` would fail `--strict`).

- [ ] **Step 11: Commit**

```bash
git add docs ARCHITECTURE.md CLAUDE.md examples/skill-lens.toml
git commit -m "docs: document product runners

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 12: Live checks, the full suite, and the pull request

**Files:**
- Modify: `tests/test_integration_live.py`
- Test: the whole suite

- [ ] **Step 1: Add the live tests**

Append to `tests/test_integration_live.py` (these carry their own skip, separate from the module's `OPENAI_API_KEY` skip — put them in a new module-level block that overrides `pytestmark` for these two functions by using explicit decorators):

```python
import shutil

from skill_lens.runners.product import PRESETS, ProductRunner


@pytest.mark.integration
@pytest.mark.block_network(allowed_hosts=[r".*"])
@pytest.mark.skipif(shutil.which("claude") is None, reason="needs the claude executable")
def test_the_greeting_example_passes_under_claude_code():
    report = run_evals(load_skills(EXAMPLES / "greeting"), [ProductRunner(PRESETS["claude-code"])])
    assert report.errored == 0, [o.result.error for o in report.outcomes if o.result.errored]
    assert report.pass_rate == 1.0, [
        (o.case_name, [s.detail for s in o.scores if not s.passed])
        for o in report.outcomes
        if o.status == "failed"
    ]


@pytest.mark.integration
@pytest.mark.block_network(allowed_hosts=[r".*"])
@pytest.mark.skipif(shutil.which("copilot") is None, reason="needs the copilot executable")
def test_the_greeting_example_passes_under_copilot():
    report = run_evals(load_skills(EXAMPLES / "greeting"), [ProductRunner(PRESETS["copilot"])])
    assert report.errored == 0, [o.result.error for o in report.outcomes if o.result.errored]
    assert report.pass_rate == 1.0, [
        (o.case_name, [s.detail for s in o.scores if not s.passed])
        for o in report.outcomes
        if o.status == "failed"
    ]
```

Because the module's `pytestmark` adds the `OPENAI_API_KEY` skip to every test, move that skip off `pytestmark` and onto the two existing provider tests as decorators, so the product tests are gated on the executable only. Note `examples/greeting` declares `budget: max_tokens: 500`: under Copilot that check fails as "not evaluated" unless the `-p` stream carries `session.shutdown` — run the Copilot test once quota allows, and if the stream has no usage, change the assertion to expect that one budget failure and say so in a comment.

- [ ] **Step 2: Run the whole zero-cost suite and every guard**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mkdocs build --strict`
Expected: all PASS, 0 lint findings.

- [ ] **Step 3: Run the live Claude Code check once**

Run: `uv run pytest tests/test_integration_live.py::test_the_greeting_example_passes_under_claude_code -m integration -v`
Expected: PASS (one real `claude -p` run). If Copilot quota is available, run its twin too and record a real `-p` trace into `tests/fixtures/products/copilot-trigger.jsonl` (scrub paths and ids; keep the schema), then re-run `tests/test_traces.py`.

- [ ] **Step 4: Commit and open the pull request**

```bash
git add tests/test_integration_live.py
git commit -m "test: add live product runner checks

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git push -u origin HEAD
gh pr create --assignee @EmadMokhtar --title "feat: run cases through an agent product's CLI" --body "$(cat <<'BODY'
Adds product runners: `--runner copilot`, `--runner claude-code` and a configured
`--runner cli` start the product the skill ships to, with `SKILL.md` placed where that
product discovers skills, and read the result from the product's trace. No provider API
key is needed. Design: docs/superpowers/specs/2026-09-17-skill-lens-m9-design.md (Part 1).

- `runners/product.py`, `runners/traces.py`, `process.py` (shared with `scripts.py`)
- `[runners.<name>]` in `skill-lens.toml`; per-runner `preflight` hook; `products` on the report
- `RunResult.usage_note`: a token limit the product cannot measure fails, never passes
- `--model` / `--judge-model` with nothing that reads them are now exit 2 (previously silently ignored)

Part of #44 (the judge follows in Part 2).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
)"
```

---

## Self-review against the spec

- §1 scope, Part 1: runner (T5), traces (T4), `Skill.markdown` (T2), `[runners.<name>]` (T9), preflight (T6, T7), `products` (T2, T7, T8), `usage_note` rule (T3), `--model` rule (T10) — covered. Part 2 (judge) is the second plan.
- §3 models — T2. §4 runner — T5/T6. §5 parsers — T4. §7 config/CLI/orchestrator/reporters — T9/T10/T7/T8. §8 docs — T11. §9 tests — T1–T10 each carry theirs; live tier T12. §10 invariants — asserted by tests in T3, T4, T5, T6, T7, T10 and written into `CLAUDE.md`/`ARCHITECTURE.md` in T11.
- Names used across tasks: `group_kwargs`/`reap_and_kill_group`/`read_capped_handle` (T1 → T5); `Trace.complete` (T4 → T5); `Product.version_command` (T5 → T6, T9); `PRODUCT_NAMES` in `config.py` (T9 → T10); `Config.product` (T9 → T10); `ProductStatus.trust` (T2 → T6, T8); `_build_runner` (T10). Consistent.
