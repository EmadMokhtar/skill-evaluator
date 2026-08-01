# skill-eval M2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `skill-eval` its first real agent runner (PydanticAI), score what the agent *did* rather than only what it said (Trajectory + Budget evaluators), and prove the whole loop against recorded real API traffic.

**Architecture:** A new `PydanticAIRunner` implements the existing `Runner` protocol and is the **only** module allowed to import an agent framework. The tools an agent may call are declared by the eval case itself and built into framework-neutral `MockTool` objects by `runners/tools.py`; the adapter wraps those into PydanticAI `Tool`s. Two new evaluators score `result.tool_calls` and `result` usage. Tests run in three tiers: in-process `FunctionModel` for adapter mapping, VCR cassettes for real wire fidelity, and an opt-in live tier.

**Tech Stack:** Python 3.11+, uv, Pydantic v2, PydanticAI (`pydantic-ai-slim[openai]` ≥ 2.22), genai-prices, vcrpy + pytest-recording, Typer, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-08-01-skill-eval-m2-design.md`

## Global Constraints

- **`errored` ≠ `failed`.** `failed` = ran and scored below bar. `errored` = the runner blew up. **Runners must never raise for provider failures** — set `RunResult.error` instead.
- **Authoring errors abort the run and exit 2** — they never score as failures. This now includes malformed `tools:`, `trajectory:`, and `budget:` blocks.
- **Exit codes are the CI contract:** gate pass `0`, gate fail `1`, user/authoring error `2`.
- **`extra="forbid"`** on every user-authored model (`EvalCase`, `AssertionSpec`, `Config`, and the new `ToolSpec` / `TrajectorySpec` / `BudgetSpec`).
- **All file IO pins `encoding="utf-8"`**; YAML goes through `skill_eval.yaml_loading.safe_load`, never `yaml.safe_load`.
- **Secrets come from environment variables only** — never from `skill-eval.toml`, never committed to a cassette.
- **`skill_eval` (underscore) never appears in user-facing output.** The runner registers as `pydantic-ai`.
- **No agent-framework type appears outside `src/skill_eval/runners/pydantic_ai.py`.**
- **The built-in `default_runner` stays `"fake"`** — upgrading to M2 must never start spending money on its own.
- **TDD:** write the failing test first, watch it fail, then implement. Tier-1 tests stay offline and deterministic.
- **Conventional Commits are enforced** by a `commit-msg` hook (`cz check`). Every commit message in this plan is already conventional — use it verbatim.
- Line length 100 (ruff). Run `uv run ruff check .` and `uv run ruff format .` before each commit.

---

## File Structure

**Created:**
- `src/skill_eval/runners/tools.py` — framework-neutral `MockTool` built from a `ToolSpec`.
- `src/skill_eval/runners/preflight.py` — API-key check keyed off the model's provider prefix.
- `src/skill_eval/runners/pydantic_ai.py` — the adapter. The only framework importer.
- `src/skill_eval/runners/pricing.py` — usage → USD, degrading to a note.
- `src/skill_eval/evaluators/trajectory.py` — `TrajectoryEvaluator`.
- `src/skill_eval/evaluators/budget.py` — `BudgetEvaluator`.
- `tests/test_tools.py`, `tests/test_preflight.py`, `tests/test_pricing.py`,
  `tests/test_pydantic_ai_runner.py`, `tests/test_trajectory_evaluator.py`,
  `tests/test_budget_evaluator.py`, `tests/test_cassettes.py`, `tests/test_integration_live.py`
- `examples/order-support/SKILL.md`, `examples/order-support/order-support.eval.yaml`
- `tests/cassettes/test_cassettes/*.yaml` (recorded, committed)

**Modified:**
- `pyproject.toml` — optional extra, dev deps, pytest markers.
- `src/skill_eval/models.py` — `ToolSpec` / `TrajectorySpec` / `BudgetSpec`; `RunResult` token split.
- `src/skill_eval/runners/base.py` + `fake.py` — `run(skill, case)`.
- `src/skill_eval/orchestrator.py` — pass the case; default evaluator list.
- `src/skill_eval/cli.py` — runner registry, `--model`, preflight.
- `src/skill_eval/config.py` — `model`, `temperature`, `retries`, `retry_backoff_seconds`.
- `examples/greeting/greeting.eval.yaml` — real assertions.
- `.github/workflows/ci.yml`, `README.md`, `CLAUDE.md`, `tests/conftest.py`.

---

### Task 1: Dependencies and config surface

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/skill_eval/config.py:17-30`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.model: str`, `Config.temperature: float | Literal["unset"]`, `Config.retries: int`, `Config.retry_backoff_seconds: float`. Constant `skill_eval.config.DEFAULT_MODEL = "openai:gpt-4o-mini"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_config_reads_model_and_retry_settings(tmp_path):
    config_file = tmp_path / "skill-eval.toml"
    config_file.write_text(
        'default_runner = "pydantic-ai"\n'
        'model = "openai:gpt-4.1-mini"\n'
        "temperature = 0.2\n"
        "retries = 3\n"
        "retry_backoff_seconds = 0.5\n",
        encoding="utf-8",
    )
    settings = load_config(path=config_file)
    assert settings.default_runner == "pydantic-ai"
    assert settings.model == "openai:gpt-4.1-mini"
    assert settings.temperature == 0.2
    assert settings.retries == 3
    assert settings.retry_backoff_seconds == 0.5


def test_config_defaults_never_spend_money():
    settings = Config()
    assert settings.default_runner == "fake"
    assert settings.model == "openai:gpt-4o-mini"
    assert settings.temperature == 0.0
    assert settings.retries == 2


def test_config_temperature_accepts_unset_for_reasoning_models(tmp_path):
    # TOML has no null literal, and omitting the key must keep meaning "use the
    # default", so "unset" is the only way to say "send no temperature at all" --
    # which GPT-5-family and o-series models require.
    config_file = tmp_path / "skill-eval.toml"
    config_file.write_text('temperature = "unset"\n', encoding="utf-8")
    assert load_config(path=config_file).temperature == "unset"


def test_config_rejects_a_nonsense_temperature(tmp_path):
    config_file = tmp_path / "skill-eval.toml"
    config_file.write_text('temperature = "hot"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path=config_file)
```

Make sure `tests/test_config.py` imports `Config`, `ConfigError`, `load_config`, and `pytest` — add any that are missing to the existing import block.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_config.py -k "model_and_retry or never_spend or temperature" -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'model'`

- [ ] **Step 3: Add the config fields**

In `src/skill_eval/config.py`, extend the imports and the `Config` model:

```python
from typing import Literal

CONFIG_FILENAME = "skill-eval.toml"
DEFAULT_MODEL = "openai:gpt-4o-mini"


class Config(BaseModel):
    """Run defaults for `skill-eval run`.

    `default_runner` (`--runner`), `model` (`--model`) and `min_pass_rate`
    (`--min-pass-rate`) can be overridden by a CLI flag; the rest can only be
    set here. Secrets are never read from this file -- API keys come from the
    environment only.
    """

    model_config = ConfigDict(extra="forbid")

    default_runner: str = "fake"
    model: str = DEFAULT_MODEL
    temperature: float | Literal["unset"] = 0.0
    retries: int = 2
    retry_backoff_seconds: float = 1.0
    min_pass_rate: float = 1.0
    fail_on_error: bool = True
    per_skill_min: dict[str, float] = Field(default_factory=dict)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (all config tests)

- [ ] **Step 5: Add the dependencies**

In `pyproject.toml`, add the optional extra after `dependencies` and extend the dev group:

```toml
[project.optional-dependencies]
pydantic-ai = ["pydantic-ai-slim[openai]>=2.22"]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "ruff>=0.6",
    "commitizen>=3.29",
    "pre-commit>=4.6.1",
    "pytest-recording>=0.13",
    "vcrpy>=8.0",
    "pydantic-ai-slim[openai]>=2.22",
]
```

- [ ] **Step 6: Install and verify the environment**

Run: `uv sync --all-extras`
Then: `uv run python -c "import pydantic_ai, vcr, genai_prices; print(pydantic_ai.__version__)"`
Expected: prints a version ≥ `2.22.0` with no traceback.

- [ ] **Step 7: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add pyproject.toml uv.lock src/skill_eval/config.py tests/test_config.py
git commit -m "feat(config): add model, temperature and retry settings for real runners"
```

---

### Task 2: Case and result models for tools, trajectory and budget

**Files:**
- Modify: `src/skill_eval/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  - `ToolSpec(name: str, description: str = "", parameters: dict[str, ToolParamType] = {}, returns: str = "")` where `ToolParamType = Literal["string", "integer", "number", "boolean"]`.
  - `TrajectorySpec(called: list[str], forbidden: list[str], order: list[str], max_calls: int | None)`.
  - `BudgetSpec(max_tokens: int | None, max_cost_usd: float | None, max_latency_ms: int | None)`.
  - `EvalCase.tools: list[ToolSpec]`, `EvalCase.trajectory: TrajectorySpec | None`, `EvalCase.budget: BudgetSpec | None`.
  - `RunResult.input_tokens: int`, `RunResult.output_tokens: int`, `RunResult.model: str`, `RunResult.cost_note: str`, and `RunResult.tokens` as a read-only property returning their sum.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_models.py`:

```python
from skill_eval.models import BudgetSpec, EvalCase, RunResult, ToolSpec, TrajectorySpec


def test_tool_spec_rejects_an_unsupported_parameter_type():
    # An unknown type is an authoring mistake in the user's YAML, so it must be
    # rejected at parse time rather than reaching the runner.
    with pytest.raises(ValidationError):
        ToolSpec(name="lookup", parameters={"order_id": "uuid"})


def test_tool_spec_rejects_a_name_that_is_not_an_identifier():
    with pytest.raises(ValidationError):
        ToolSpec(name="look up")


def test_tool_spec_accepts_a_full_declaration():
    spec = ToolSpec(
        name="lookup_order",
        description="Look up an order by its id",
        parameters={"order_id": "string", "verbose": "boolean"},
        returns='{"id": "1234"}',
    )
    assert spec.parameters["order_id"] == "string"
    assert spec.returns == '{"id": "1234"}'


def test_case_carries_tools_trajectory_and_budget():
    case = EvalCase(
        name="refund",
        task="refund order 1234",
        tools=[ToolSpec(name="lookup_order", parameters={"order_id": "string"})],
        trajectory=TrajectorySpec(called=["lookup_order"], forbidden=["issue_refund"]),
        budget=BudgetSpec(max_tokens=4000),
    )
    assert case.tools[0].name == "lookup_order"
    assert case.trajectory.called == ["lookup_order"]
    assert case.budget.max_tokens == 4000


def test_case_without_the_new_blocks_keeps_working():
    case = EvalCase(name="plain", task="hello")
    assert case.tools == []
    assert case.trajectory is None
    assert case.budget is None


def test_trajectory_spec_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        TrajectorySpec(calls=["lookup_order"])


def test_budget_spec_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        BudgetSpec(max_token=10)


def test_run_result_tokens_is_the_sum_of_input_and_output():
    result = RunResult(input_tokens=112, output_tokens=15)
    assert result.tokens == 127


def test_run_result_rejects_writing_tokens_directly():
    # `tokens` is derived. Accepting it silently would let a runner report a
    # total that disagrees with the split it was priced from.
    with pytest.raises(ValidationError):
        RunResult(tokens=127)
```

Ensure `tests/test_models.py` imports `pytest` and `ValidationError` from `pydantic`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'BudgetSpec' from 'skill_eval.models'`

- [ ] **Step 3: Implement the models**

In `src/skill_eval/models.py`, add `field_validator` to the pydantic import, then insert the new specs above `EvalCase` and update `RunResult`:

```python
from pydantic import BaseModel, ConfigDict, Field, field_validator

ToolParamType = Literal["string", "integer", "number", "boolean"]


class ToolSpec(BaseModel):
    """A mock tool an eval case makes available to the agent.

    Nothing executes: calling the tool records the call and returns `returns`
    verbatim, so the trajectory is genuinely the model's choice and a run has
    no side effects.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    parameters: dict[str, ToolParamType] = Field(default_factory=dict)
    returns: str = ""

    @field_validator("name")
    @classmethod
    def _must_be_an_identifier(cls, value: str) -> str:
        if not value.isidentifier():
            raise ValueError(f"tool name must be a valid identifier, got {value!r}")
        return value


class TrajectorySpec(BaseModel):
    """What the agent should (and should not) have done to get its answer."""

    model_config = ConfigDict(extra="forbid")

    called: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)
    order: list[str] = Field(default_factory=list)
    max_calls: int | None = None


class BudgetSpec(BaseModel):
    """Efficiency ceilings for one case."""

    model_config = ConfigDict(extra="forbid")

    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_latency_ms: int | None = None
```

Update `EvalCase`:

```python
class EvalCase(BaseModel):
    """A single eval case: a task prompt, the environment it runs in, and how to score it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    task: str
    tools: list[ToolSpec] = Field(default_factory=list)
    assertions: list[AssertionSpec] = Field(default_factory=list)
    trajectory: TrajectorySpec | None = None
    budget: BudgetSpec | None = None
    tags: list[str] = Field(default_factory=list)
```

Replace `RunResult`'s usage fields:

```python
class RunResult(BaseModel):
    """The outcome of running one task against one skill with one runner."""

    model_config = ConfigDict(extra="forbid")

    output: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    cost_note: str = ""
    model: str = ""
    error: str | None = None

    @property
    def tokens(self) -> int:
        """Total tokens. Derived, so it can never disagree with the split."""
        return self.input_tokens + self.output_tokens

    @property
    def errored(self) -> bool:
        """True when the runner itself failed (infra), not when a score was low."""
        return self.error is not None
```

- [ ] **Step 4: Fix the existing tests that write `tokens=` directly**

`extra="forbid"` now rejects `RunResult(tokens=10, ...)`. In `tests/test_reporters.py`, change both constructions (lines ~18 and ~123) from `tokens=10` to `output_tokens=10`, leaving `cost_usd` and `latency_ms` as they are. The assertions on `total_tokens == 10` stay correct, because `tokens` sums to 10.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -v`
Expected: PASS — all tests, including the updated reporter tests.

- [ ] **Step 6: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/models.py tests/test_models.py tests/test_reporters.py
git commit -m "feat(models): add tool, trajectory and budget specs and split token counts"
```

---

### Task 3: Runner protocol takes the case

**Files:**
- Modify: `src/skill_eval/runners/base.py`
- Modify: `src/skill_eval/runners/fake.py`
- Modify: `src/skill_eval/orchestrator.py:18`
- Test: `tests/test_fake_runner.py`

**Interfaces:**
- Consumes: `EvalCase` from Task 2.
- Produces: `Runner.run(self, skill: Skill, case: EvalCase) -> RunResult`. `FakeRunner(responses: dict[str, RunResult] | None, default: RunResult | None)` still keys `responses` on the **task string** (`case.task`).

- [ ] **Step 1: Write the failing test**

Rewrite `tests/test_fake_runner.py` so every call passes a case. Replace the module's existing body with:

```python
"""FakeRunner keeps the pipeline testable with no network and no cost."""

from pathlib import Path

from skill_eval.models import EvalCase, RunResult, Skill, ToolCall
from skill_eval.runners.fake import FakeRunner

SKILL = Skill(name="pdf", description="", instructions="", path=Path("."))


def case(task: str) -> EvalCase:
    return EvalCase(name=task, task=task)


def test_scripted_response_is_keyed_on_the_task():
    runner = FakeRunner(responses={"extract": RunResult(output="used pdfplumber")})
    assert runner.run(SKILL, case("extract")).output == "used pdfplumber"


def test_same_task_returns_an_equal_result():
    runner = FakeRunner(responses={"extract": RunResult(output="used pdfplumber")})
    assert runner.run(SKILL, case("extract")) == runner.run(SKILL, case("extract"))


def test_callers_cannot_corrupt_the_scripted_state():
    runner = FakeRunner(responses={"task": RunResult(output="original")})
    result1 = runner.run(SKILL, case("task"))
    result1.output = "mutated"
    result1.tool_calls.append(ToolCall(name="sneaky"))
    result2 = runner.run(SKILL, case("task"))
    assert result2.output == "original"
    assert result2.tool_calls == []


def test_default_covers_unscripted_tasks():
    runner = FakeRunner(default=RunResult(output="fallback"))
    assert runner.run(SKILL, case("anything")).output == "fallback"


def test_unscripted_task_without_a_default_echoes_the_skill_name():
    runner = FakeRunner()
    assert "pdf" in runner.run(SKILL, case("anything")).output


def test_a_scripted_error_is_reported_not_raised():
    runner = FakeRunner(responses={"boom": RunResult(error="provider exploded")})
    assert runner.run(SKILL, case("boom")).errored is True


def test_scripted_tool_calls_survive_the_round_trip():
    runner = FakeRunner(responses={"t": RunResult(tool_calls=[ToolCall(name="read_pdf")])})
    assert runner.run(SKILL, case("t")).tool_calls[0].name == "read_pdf"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_fake_runner.py -v`
Expected: FAIL — `AttributeError: 'EvalCase' object has no attribute ...` / the runner treats the case object as a dict key and misses every scripted response.

- [ ] **Step 3: Update the protocol and FakeRunner**

`src/skill_eval/runners/base.py`:

```python
"""The Runner protocol — the seam every agent framework plugs into."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from skill_eval.models import EvalCase, RunResult, Skill


@runtime_checkable
class Runner(Protocol):
    """Runs a case against a skill and reports what happened."""

    name: str

    def run(self, skill: Skill, case: EvalCase) -> RunResult:
        """Execute `case` with `skill` loaded, returning a RunResult.

        Takes the whole case, not just its task string, because a runner also
        builds the environment the case declares (its mock tools).

        Implementations must not raise for provider failures; they set
        RunResult.error instead so the orchestrator can mark the case errored.
        """
        ...
```

`src/skill_eval/runners/fake.py`:

```python
"""A deterministic, offline runner used to test the whole pipeline."""

from __future__ import annotations

from skill_eval.models import EvalCase, RunResult, Skill


class FakeRunner:
    """Returns scripted RunResults. Never touches the network."""

    name = "fake"

    def __init__(
        self,
        responses: dict[str, RunResult] | None = None,
        default: RunResult | None = None,
    ) -> None:
        self._responses = responses or {}
        self._default = default

    def run(self, skill: Skill, case: EvalCase) -> RunResult:
        if case.task in self._responses:
            return self._responses[case.task].model_copy(deep=True)
        if self._default is not None:
            return self._default.model_copy(deep=True)
        return RunResult(output=f"[fake] {skill.name} handled: {case.task}")
```

- [ ] **Step 4: Update the orchestrator call site**

In `src/skill_eval/orchestrator.py`, change line 18 from `result = runner.run(skill, case.task)` to:

```python
    result = runner.run(skill, case)
```

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -v`
Expected: PASS — every test, including `tests/test_orchestrator.py` and `tests/test_cli.py`.

- [ ] **Step 6: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/runners tests/test_fake_runner.py src/skill_eval/orchestrator.py
git commit -m "refactor(runners)!: pass the eval case to Runner.run instead of the task string"
```

---

### Task 4: Trajectory evaluator

**Files:**
- Create: `src/skill_eval/evaluators/trajectory.py`
- Test: `tests/test_trajectory_evaluator.py`

**Interfaces:**
- Consumes: `TrajectorySpec`, `EvalCase`, `RunResult`, `ToolCall` from Task 2; the `Evaluator` protocol from `evaluators/base.py`.
- Produces: `TrajectoryEvaluator` with `name = "trajectory"` and `evaluate(case, result) -> EvalScore`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_trajectory_evaluator.py`:

```python
"""Scoring what the agent did, not what it said."""

from skill_eval.evaluators.trajectory import TrajectoryEvaluator
from skill_eval.models import EvalCase, RunResult, ToolCall, TrajectorySpec

EVALUATOR = TrajectoryEvaluator()


def case(**trajectory) -> EvalCase:
    return EvalCase(name="c", task="t", trajectory=TrajectorySpec(**trajectory))


def result(*names: str) -> RunResult:
    return RunResult(tool_calls=[ToolCall(name=name) for name in names])


def test_no_trajectory_block_is_a_vacuous_pass():
    score = EVALUATOR.evaluate(EvalCase(name="c", task="t"), result("anything"))
    assert score.passed is True
    assert score.score == 1.0
    assert "no trajectory checks" in score.detail


def test_called_passes_when_every_listed_tool_was_used():
    score = EVALUATOR.evaluate(case(called=["lookup_order"]), result("lookup_order"))
    assert score.passed is True


def test_called_fails_when_a_listed_tool_was_never_used():
    score = EVALUATOR.evaluate(case(called=["lookup_order"]), result("issue_refund"))
    assert score.passed is False
    assert "lookup_order" in score.detail


def test_forbidden_fails_when_a_banned_tool_was_used():
    score = EVALUATOR.evaluate(case(forbidden=["issue_refund"]), result("issue_refund"))
    assert score.passed is False
    assert "issue_refund" in score.detail


def test_forbidden_passes_when_the_banned_tool_was_avoided():
    assert EVALUATOR.evaluate(case(forbidden=["issue_refund"]), result("lookup_order")).passed


def test_order_allows_unrelated_calls_in_between():
    # "Order" means relative subsequence: a model taking a sensible extra step
    # between the two required ones has still done them in the right order.
    score = EVALUATOR.evaluate(
        case(order=["lookup_order", "issue_refund"]),
        result("lookup_order", "check_policy", "issue_refund"),
    )
    assert score.passed is True


def test_order_fails_when_the_sequence_is_inverted():
    score = EVALUATOR.evaluate(
        case(order=["lookup_order", "issue_refund"]),
        result("issue_refund", "lookup_order"),
    )
    assert score.passed is False
    assert "order" in score.detail


def test_order_fails_when_a_listed_tool_is_missing_entirely():
    score = EVALUATOR.evaluate(
        case(order=["lookup_order", "issue_refund"]), result("lookup_order")
    )
    assert score.passed is False


def test_max_calls_catches_a_loop():
    score = EVALUATOR.evaluate(case(max_calls=2), result("a", "a", "a"))
    assert score.passed is False
    assert "3" in score.detail


def test_max_calls_passes_at_the_limit():
    assert EVALUATOR.evaluate(case(max_calls=2), result("a", "a")).passed is True


def test_score_is_the_fraction_of_checks_that_held():
    # called (fails) + forbidden (holds) => 1 of 2.
    score = EVALUATOR.evaluate(
        case(called=["lookup_order"], forbidden=["issue_refund"]), result("check_policy")
    )
    assert score.score == 0.5
    assert score.passed is False


def test_evaluator_reports_its_name():
    assert EVALUATOR.evaluate(case(called=["a"]), result("a")).evaluator == "trajectory"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_trajectory_evaluator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.evaluators.trajectory'`

- [ ] **Step 3: Implement the evaluator**

Create `src/skill_eval/evaluators/trajectory.py`:

```python
"""Scoring the tool-call trajectory: what the agent did to reach its answer."""

from __future__ import annotations

from skill_eval.models import EvalCase, EvalScore, RunResult, TrajectorySpec


def _is_subsequence(required: list[str], actual: list[str]) -> bool:
    """True when `required` appears in `actual` in order, gaps allowed."""
    remaining = iter(actual)
    return all(name in remaining for name in required)


def _check(spec: TrajectorySpec, called: list[str]) -> list[str]:
    """Return one failure description per check that did not hold."""
    failures: list[str] = []

    missing = [name for name in spec.called if name not in called]
    if missing:
        failures.append(f"never called: {', '.join(missing)}")

    used = [name for name in spec.forbidden if name in called]
    if used:
        failures.append(f"called forbidden tool: {', '.join(used)}")

    if spec.order and not _is_subsequence(spec.order, called):
        failures.append(f"order {' -> '.join(spec.order)} not followed, got {called}")

    if spec.max_calls is not None and len(called) > spec.max_calls:
        failures.append(f"made {len(called)} tool calls, limit is {spec.max_calls}")

    return failures


def _total_checks(spec: TrajectorySpec) -> int:
    return sum(
        [
            bool(spec.called),
            bool(spec.forbidden),
            bool(spec.order),
            spec.max_calls is not None,
        ]
    )


class TrajectoryEvaluator:
    """Every declared check must hold; the score is the fraction that held."""

    name = "trajectory"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        spec = case.trajectory
        total = _total_checks(spec) if spec is not None else 0
        if spec is None or total == 0:
            return EvalScore(
                evaluator=self.name, passed=True, score=1.0, detail="no trajectory checks"
            )
        called = [call.name for call in result.tool_calls]
        failures = _check(spec, called)
        detail = "all trajectory checks held" if not failures else "; ".join(failures)
        return EvalScore(
            evaluator=self.name,
            passed=not failures,
            score=(total - len(failures)) / total,
            detail=detail,
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_trajectory_evaluator.py -v`
Expected: PASS — 12 tests.

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/evaluators/trajectory.py tests/test_trajectory_evaluator.py
git commit -m "feat(evaluators): add trajectory evaluator for tool-use checks"
```

---

### Task 5: Budget evaluator

**Files:**
- Create: `src/skill_eval/evaluators/budget.py`
- Test: `tests/test_budget_evaluator.py`

**Interfaces:**
- Consumes: `BudgetSpec`, `EvalCase`, `RunResult` from Task 2.
- Produces: `BudgetEvaluator` with `name = "budget"` and `evaluate(case, result) -> EvalScore`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_budget_evaluator.py`:

```python
"""Efficiency is a goal in its own right, not a footnote on the report."""

from skill_eval.evaluators.budget import BudgetEvaluator
from skill_eval.models import BudgetSpec, EvalCase, RunResult

EVALUATOR = BudgetEvaluator()


def case(**budget) -> EvalCase:
    return EvalCase(name="c", task="t", budget=BudgetSpec(**budget))


def test_no_budget_block_is_a_vacuous_pass():
    score = EVALUATOR.evaluate(EvalCase(name="c", task="t"), RunResult(input_tokens=10_000))
    assert score.passed is True
    assert score.score == 1.0
    assert "no budget checks" in score.detail


def test_token_budget_counts_input_plus_output():
    result = RunResult(input_tokens=600, output_tokens=500)
    score = EVALUATOR.evaluate(case(max_tokens=1000), result)
    assert score.passed is False
    assert "1100" in score.detail


def test_token_budget_passes_at_the_limit():
    result = RunResult(input_tokens=600, output_tokens=400)
    assert EVALUATOR.evaluate(case(max_tokens=1000), result).passed is True


def test_cost_budget_fails_when_exceeded():
    score = EVALUATOR.evaluate(case(max_cost_usd=0.01), RunResult(cost_usd=0.02))
    assert score.passed is False
    assert "cost" in score.detail


def test_latency_budget_fails_when_exceeded():
    score = EVALUATOR.evaluate(case(max_latency_ms=1000), RunResult(latency_ms=2500))
    assert score.passed is False
    assert "2500" in score.detail


def test_score_is_the_fraction_of_limits_respected():
    result = RunResult(input_tokens=5000, cost_usd=0.001, latency_ms=100)
    score = EVALUATOR.evaluate(
        case(max_tokens=1000, max_cost_usd=0.01, max_latency_ms=1000), result
    )
    assert score.score == 2 / 3
    assert score.passed is False


def test_a_blown_budget_is_a_failure_not_an_error():
    # An inefficient skill is an eval signal. Only the runner blowing up is
    # infra, and that is `RunResult.error` -- never this evaluator's business.
    result = RunResult(input_tokens=10_000)
    score = EVALUATOR.evaluate(case(max_tokens=10), result)
    assert score.passed is False
    assert result.errored is False


def test_evaluator_reports_its_name():
    assert EVALUATOR.evaluate(case(max_tokens=10), RunResult()).evaluator == "budget"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_budget_evaluator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.evaluators.budget'`

- [ ] **Step 3: Implement the evaluator**

Create `src/skill_eval/evaluators/budget.py`:

```python
"""Scoring efficiency: a skill that works but costs too much has a real problem."""

from __future__ import annotations

from skill_eval.models import BudgetSpec, EvalCase, EvalScore, RunResult


def _check(spec: BudgetSpec, result: RunResult) -> tuple[int, list[str]]:
    """Return (number of limits declared, one description per limit exceeded)."""
    limits: list[tuple[bool, str]] = []

    if spec.max_tokens is not None:
        limits.append(
            (result.tokens > spec.max_tokens, f"used {result.tokens} tokens, limit is {spec.max_tokens}")
        )
    if spec.max_cost_usd is not None:
        limits.append(
            (
                result.cost_usd > spec.max_cost_usd,
                f"cost ${result.cost_usd:.6f}, limit is ${spec.max_cost_usd:.6f}",
            )
        )
    if spec.max_latency_ms is not None:
        limits.append(
            (
                result.latency_ms > spec.max_latency_ms,
                f"took {result.latency_ms}ms, limit is {spec.max_latency_ms}ms",
            )
        )

    return len(limits), [detail for exceeded, detail in limits if exceeded]


class BudgetEvaluator:
    """Every declared limit must hold; the score is the fraction that held."""

    name = "budget"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        spec = case.budget
        if spec is None:
            return EvalScore(
                evaluator=self.name, passed=True, score=1.0, detail="no budget checks"
            )
        total, failures = _check(spec, result)
        if total == 0:
            return EvalScore(
                evaluator=self.name, passed=True, score=1.0, detail="no budget checks"
            )
        detail = "within budget" if not failures else "; ".join(failures)
        return EvalScore(
            evaluator=self.name,
            passed=not failures,
            score=(total - len(failures)) / total,
            detail=detail,
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_budget_evaluator.py -v`
Expected: PASS — 8 tests.

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/evaluators/budget.py tests/test_budget_evaluator.py
git commit -m "feat(evaluators): add budget evaluator for token, cost and latency limits"
```

---

### Task 6: Wire the new evaluators into the orchestrator

**Files:**
- Modify: `src/skill_eval/orchestrator.py:54`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `TrajectoryEvaluator` (Task 4), `BudgetEvaluator` (Task 5).
- Produces: `run_evals`'s default evaluator list is `[AssertionEvaluator(), TrajectoryEvaluator(), BudgetEvaluator()]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_orchestrator.py` (reusing that module's existing helpers for building a skill directory; if it has none, build the skill inline as shown):

```python
def test_default_evaluators_include_trajectory_and_budget(tmp_path):
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: s\n---\nbody\n", encoding="utf-8")
    (skill_dir / "s.eval.yaml").write_text(
        "cases:\n"
        "  - name: c\n"
        "    task: t\n"
        "    trajectory:\n"
        "      called: [lookup_order]\n"
        "    budget:\n"
        "      max_tokens: 100\n",
        encoding="utf-8",
    )
    skills = load_skills(skill_dir)
    runner = FakeRunner(
        default=RunResult(tool_calls=[ToolCall(name="lookup_order")], input_tokens=10)
    )
    report = run_evals(skills, [runner])
    assert [score.evaluator for score in report.outcomes[0].scores] == [
        "assertion",
        "trajectory",
        "budget",
    ]
    assert report.outcomes[0].status == "passed"


def test_a_trajectory_violation_fails_the_case(tmp_path):
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: s\n---\nbody\n", encoding="utf-8")
    (skill_dir / "s.eval.yaml").write_text(
        "cases:\n"
        "  - name: c\n"
        "    task: t\n"
        "    trajectory:\n"
        "      forbidden: [issue_refund]\n",
        encoding="utf-8",
    )
    runner = FakeRunner(default=RunResult(tool_calls=[ToolCall(name="issue_refund")]))
    report = run_evals(load_skills(skill_dir), [runner])
    assert report.outcomes[0].status == "failed"
    assert report.outcomes[0].result.errored is False
```

Add `ToolCall` to that module's `skill_eval.models` import if it is not already there.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_orchestrator.py -k "default_evaluators or trajectory_violation" -v`
Expected: FAIL — the scores list is `["assertion"]`.

- [ ] **Step 3: Update the default evaluator list**

In `src/skill_eval/orchestrator.py`, add the imports and change the default:

```python
from skill_eval.evaluators.assertion import AssertionEvaluator
from skill_eval.evaluators.base import Evaluator
from skill_eval.evaluators.budget import BudgetEvaluator
from skill_eval.evaluators.trajectory import TrajectoryEvaluator
```

```python
    evaluators = (
        evaluators
        if evaluators is not None
        else [AssertionEvaluator(), TrajectoryEvaluator(), BudgetEvaluator()]
    )
```

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): score trajectory and budget by default"
```

---

### Task 7: Framework-neutral mock tools

**Files:**
- Create: `src/skill_eval/runners/tools.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: `ToolSpec` from Task 2.
- Produces: `MockTool(name: str, description: str, json_schema: dict[str, Any], call: Callable[..., str])` and `build_mock_tool(spec: ToolSpec) -> MockTool`.
- **Must not import `pydantic_ai`.** Turning a `ToolSpec` into a JSON schema plus a callable is framework-independent; the adapter in Task 8 wraps the result. This is what keeps M6's real-execution toolset from having to be rewritten per framework.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tools.py`:

```python
"""Mock tools: the agent's environment, declared by the eval case."""

import skill_eval.runners.tools as tools_module
from skill_eval.models import ToolSpec
from skill_eval.runners.tools import build_mock_tool


def test_schema_describes_every_declared_parameter():
    tool = build_mock_tool(
        ToolSpec(
            name="lookup_order",
            description="Look up an order",
            parameters={"order_id": "string", "verbose": "boolean"},
        )
    )
    assert tool.name == "lookup_order"
    assert tool.description == "Look up an order"
    assert tool.json_schema["type"] == "object"
    assert tool.json_schema["properties"] == {
        "order_id": {"type": "string"},
        "verbose": {"type": "boolean"},
    }
    assert sorted(tool.json_schema["required"]) == ["order_id", "verbose"]
    assert tool.json_schema["additionalProperties"] is False


def test_a_tool_with_no_parameters_still_has_a_valid_schema():
    tool = build_mock_tool(ToolSpec(name="ping"))
    assert tool.json_schema["properties"] == {}
    assert tool.json_schema["required"] == []


def test_calling_the_tool_returns_the_canned_value_verbatim():
    tool = build_mock_tool(ToolSpec(name="lookup_order", returns='{"id": "1234"}'))
    assert tool.call(order_id="1234") == '{"id": "1234"}'


def test_calling_the_tool_ignores_whatever_arguments_it_is_handed():
    # The model can hallucinate an argument; a mock must not explode on it,
    # because that would surface as an infra error instead of an eval signal.
    tool = build_mock_tool(ToolSpec(name="ping", returns="pong"))
    assert tool.call(unexpected="x", another=2) == "pong"


def test_a_tool_with_no_return_value_yields_an_empty_string():
    assert build_mock_tool(ToolSpec(name="ping")).call() == ""


def test_module_does_not_import_an_agent_framework():
    # No agent-framework type may appear outside runners/pydantic_ai.py.
    source = open(tools_module.__file__, encoding="utf-8").read()
    assert "pydantic_ai" not in source
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.runners.tools'`

- [ ] **Step 3: Implement the builder**

Create `src/skill_eval/runners/tools.py`:

```python
"""Build framework-neutral mock tools from a case's tool declarations.

Nothing here knows about any agent framework: a MockTool is a name, a JSON
schema and a callable, which every adapter can register in its own way.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from skill_eval.models import ToolSpec


@dataclass(frozen=True)
class MockTool:
    """A tool the agent may call. Calling it has no side effects."""

    name: str
    description: str
    json_schema: dict[str, Any]
    call: Callable[..., str]


def build_mock_tool(spec: ToolSpec) -> MockTool:
    """Turn a declared ToolSpec into a callable plus its JSON schema.

    Parameter types are already constrained by `ToolSpec`, so an unsupported
    type is rejected by the case loader as an authoring error long before it
    reaches here.
    """
    properties = {name: {"type": type_name} for name, type_name in spec.parameters.items()}
    returns = spec.returns

    def call(**_arguments: Any) -> str:
        """Return the canned value, whatever the model passed in."""
        return returns

    return MockTool(
        name=spec.name,
        description=spec.description,
        json_schema={
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
        call=call,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_tools.py -v`
Expected: PASS — 6 tests.

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/runners/tools.py tests/test_tools.py
git commit -m "feat(runners): build framework-neutral mock tools from case declarations"
```

---

### Task 8: Pricing and preflight helpers

**Files:**
- Create: `src/skill_eval/runners/pricing.py`
- Create: `src/skill_eval/runners/preflight.py`
- Test: `tests/test_pricing.py`, `tests/test_preflight.py`

**Interfaces:**
- Produces:
  - `calculate_cost(usage: Any, model_name: str, provider_id: str) -> tuple[float, str]` returning `(cost_usd, cost_note)`; the note is `""` on success.
  - `provider_of(model: str) -> str` — the part before `:` in a model string, `""` if absent.
  - `check_api_key(model: str, environ: Mapping[str, str]) -> None`, raising `MissingAPIKey`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pricing.py`:

```python
"""Cost capture must degrade, never explode."""

from dataclasses import dataclass

from skill_eval.runners.pricing import calculate_cost, provider_of


@dataclass
class Usage:
    input_tokens: int = 1000
    output_tokens: int = 500
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    requests: int = 1


def test_provider_is_the_prefix_of_the_model_string():
    assert provider_of("openai:gpt-4o-mini") == "openai"
    assert provider_of("anthropic:claude-sonnet-4-6") == "anthropic"
    assert provider_of("bare-model-name") == ""


def test_a_known_model_is_priced():
    cost, note = calculate_cost(Usage(), "gpt-4o-mini", "openai")
    assert cost > 0
    assert note == ""


def test_an_unknown_model_costs_zero_and_explains_why():
    # A missing price is a gap in a pricing table, not a failure of the run.
    cost, note = calculate_cost(Usage(), "no-such-model-exists", "openai")
    assert cost == 0.0
    assert "no price data" in note
    assert "no-such-model-exists" in note


def test_an_unknown_provider_costs_zero_and_explains_why():
    cost, note = calculate_cost(Usage(), "gpt-4o-mini", "")
    assert cost == 0.0
    assert note != ""
```

Create `tests/test_preflight.py`:

```python
"""Check for the key before spending, not after."""

import pytest

from skill_eval.runners.preflight import MissingAPIKey, check_api_key


def test_a_present_key_passes():
    check_api_key("openai:gpt-4o-mini", {"OPENAI_API_KEY": "sk-test"})


def test_a_missing_key_is_reported_with_the_variable_name():
    with pytest.raises(MissingAPIKey) as exc:
        check_api_key("openai:gpt-4o-mini", {})
    assert "OPENAI_API_KEY" in str(exc.value)


def test_an_empty_key_counts_as_missing():
    with pytest.raises(MissingAPIKey):
        check_api_key("anthropic:claude-sonnet-4-6", {"ANTHROPIC_API_KEY": ""})


def test_the_message_never_suggests_putting_secrets_in_config():
    with pytest.raises(MissingAPIKey) as exc:
        check_api_key("openai:gpt-4o-mini", {})
    message = str(exc.value)
    assert "skill-eval.toml" in message
    assert "never" in message.lower()


def test_an_unknown_provider_is_not_blocked():
    # Better to attempt the run and report the provider's own error than to
    # refuse a provider we simply have not catalogued.
    check_api_key("some-new-provider:model", {})


def test_a_model_without_a_provider_prefix_is_not_blocked():
    check_api_key("gpt-4o-mini", {})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pricing.py tests/test_preflight.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.runners.pricing'`

- [ ] **Step 3: Implement pricing**

Create `src/skill_eval/runners/pricing.py`:

```python
"""Turn provider usage into USD, without ever failing a run over it."""

from __future__ import annotations

from typing import Any


def provider_of(model: str) -> str:
    """The provider prefix of a model string: 'openai:gpt-4o-mini' -> 'openai'."""
    return model.split(":", 1)[0] if ":" in model else ""


def calculate_cost(usage: Any, model_name: str, provider_id: str) -> tuple[float, str]:
    """Price `usage`, returning (cost_usd, note).

    A blank note means the price is real. Any lookup problem -- an unpriced
    model, an unknown provider, a missing pricing package -- yields 0.0 and an
    explanatory note, because cost is reporting metadata and must never be the
    reason a run errors.
    """
    try:
        from genai_prices import calc_price
    except ImportError:  # pragma: no cover - genai-prices ships with pydantic-ai
        return 0.0, "genai-prices is not installed; cost not calculated"

    try:
        calculation = calc_price(usage, model_name, provider_id=provider_id or None)
    except Exception as exc:
        label = f"{provider_id}:{model_name}" if provider_id else model_name
        return 0.0, f"no price data for {label} ({type(exc).__name__})"

    return float(calculation.total_price), ""
```

- [ ] **Step 4: Implement preflight**

Create `src/skill_eval/runners/preflight.py`:

```python
"""Verify the provider's API key is present before any spend."""

from __future__ import annotations

from collections.abc import Mapping

from skill_eval.runners.pricing import provider_of

PROVIDER_ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google-gla": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cohere": "CO_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


class MissingAPIKey(Exception):
    """Raised when the environment variable a model needs is unset."""


def check_api_key(model: str, environ: Mapping[str, str]) -> None:
    """Raise MissingAPIKey when `model`'s provider has no key in `environ`.

    An unrecognised provider prefix is not blocked: reporting the provider's
    own error beats refusing a provider we have not catalogued.
    """
    variable = PROVIDER_ENV_VARS.get(provider_of(model))
    if variable is None:
        return
    if not environ.get(variable):
        raise MissingAPIKey(
            f"{variable} is not set, and model {model!r} needs it. "
            f"Export it in your environment -- skill-eval never reads secrets "
            f"from skill-eval.toml."
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_pricing.py tests/test_preflight.py -v`
Expected: PASS — 10 tests.

- [ ] **Step 6: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/runners/pricing.py src/skill_eval/runners/preflight.py tests/test_pricing.py tests/test_preflight.py
git commit -m "feat(runners): add cost calculation and API-key preflight helpers"
```

---

### Task 9: PydanticAI runner

**Files:**
- Create: `src/skill_eval/runners/pydantic_ai.py`
- Test: `tests/test_pydantic_ai_runner.py`

**Interfaces:**
- Consumes: `build_mock_tool` (Task 7), `calculate_cost` / `provider_of` (Task 8), `Runner` protocol (Task 3).
- Produces: `PydanticAIRunner(model: str = DEFAULT_MODEL, temperature: float | str = 0.0, retries: int = 2, retry_backoff_seconds: float = 1.0, sleep: Callable[[float], None] = time.sleep)` with `name = "pydantic-ai"` and `needs_api_key = True`. Also `RunnerDependencyError`.
- The runner accepts an already-constructed PydanticAI model object in `model` as well as a string, which is how the tests inject `FunctionModel`.

**Note on testing:** PydanticAI's `FunctionModel` is a scripted in-process model — it never opens a socket. Every test in this task is offline and deterministic.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pydantic_ai_runner.py`:

```python
"""The first real adapter, exercised offline with a scripted model."""

from pathlib import Path

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from skill_eval.models import EvalCase, Skill, ToolSpec
from skill_eval.runners.pydantic_ai import PydanticAIRunner

SKILL = Skill(
    name="order-support",
    description="Handle refund requests",
    instructions="Always look up the order first.",
    path=Path("."),
)


def scripted(*responses: ModelResponse) -> FunctionModel:
    """A model that replays `responses` in order, then repeats the last one."""
    calls = {"n": 0}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        index = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[index]

    return FunctionModel(reply)


def text(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])


def tool_call(name: str, args) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args)])


def case(**kwargs) -> EvalCase:
    kwargs.setdefault("name", "c")
    kwargs.setdefault("task", "refund order 1234")
    return EvalCase(**kwargs)


def test_the_final_text_becomes_the_output():
    runner = PydanticAIRunner(model=scripted(text("Order 1234 was delivered.")))
    result = runner.run(SKILL, case())
    assert result.output == "Order 1234 was delivered."
    assert result.errored is False


def test_the_runner_registers_its_name():
    assert PydanticAIRunner(model=scripted(text("x"))).name == "pydantic-ai"


def test_declared_tools_are_offered_to_the_model():
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        seen["tools"] = sorted(tool.name for tool in info.function_tools)
        return text("done")

    runner = PydanticAIRunner(model=FunctionModel(reply))
    runner.run(
        SKILL,
        case(tools=[ToolSpec(name="lookup_order"), ToolSpec(name="issue_refund")]),
    )
    assert seen["tools"] == ["issue_refund", "lookup_order"]


def test_the_skill_instructions_reach_the_model():
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        seen["instructions"] = messages[0].instructions or ""
        return text("done")

    PydanticAIRunner(model=FunctionModel(reply)).run(SKILL, case())
    assert "Always look up the order first." in seen["instructions"]
    assert "order-support" in seen["instructions"]


def test_tool_calls_are_captured_in_order():
    runner = PydanticAIRunner(
        model=scripted(
            tool_call("lookup_order", {"order_id": "1234"}),
            tool_call("check_policy", {}),
            text("Refund declined."),
        )
    )
    result = runner.run(
        SKILL,
        case(
            tools=[
                ToolSpec(name="lookup_order", parameters={"order_id": "string"}),
                ToolSpec(name="check_policy"),
            ]
        ),
    )
    assert [call.name for call in result.tool_calls] == ["lookup_order", "check_policy"]
    assert result.tool_calls[0].arguments == {"order_id": "1234"}


def test_json_string_arguments_are_normalised_to_a_dict():
    # Real providers hand back a JSON string; FunctionModel hands back a dict.
    # Both must land in RunResult as a dict.
    runner = PydanticAIRunner(
        model=scripted(tool_call("lookup_order", '{"order_id": "1234"}'), text("done"))
    )
    result = runner.run(
        SKILL, case(tools=[ToolSpec(name="lookup_order", parameters={"order_id": "string"})])
    )
    assert result.tool_calls[0].arguments == {"order_id": "1234"}


def test_unparseable_arguments_are_preserved_rather_than_dropped():
    runner = PydanticAIRunner(model=scripted(tool_call("ping", "not json at all"), text("done")))
    result = runner.run(SKILL, case(tools=[ToolSpec(name="ping")]))
    assert result.tool_calls[0].arguments == {"_raw": "not json at all"}


def test_the_canned_return_value_is_handed_back_to_the_model():
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        for message in messages:
            for part in getattr(message, "parts", []):
                if type(part).__name__ == "ToolReturnPart":
                    seen["content"] = part.content
        if "content" not in seen:
            return tool_call("lookup_order", {})
        return text("done")

    runner = PydanticAIRunner(model=FunctionModel(reply))
    runner.run(SKILL, case(tools=[ToolSpec(name="lookup_order", returns='{"status": "shipped"}')]))
    assert seen["content"] == '{"status": "shipped"}'


def test_usage_latency_and_transcript_are_captured():
    runner = PydanticAIRunner(model=scripted(text("done")))
    result = runner.run(SKILL, case())
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.tokens == result.input_tokens + result.output_tokens
    assert result.latency_ms >= 0
    assert len(result.transcript) >= 2
    assert isinstance(result.transcript[0], dict)


def test_an_unpriced_model_reports_zero_cost_with_a_note():
    result = PydanticAIRunner(model=scripted(text("done"))).run(SKILL, case())
    assert result.cost_usd == 0.0
    assert "no price data" in result.cost_note


def test_a_provider_failure_is_reported_not_raised():
    def explode(messages, info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(status_code=500, model_name="scripted", body=None)

    result = PydanticAIRunner(model=FunctionModel(explode), retries=0).run(SKILL, case())
    assert result.errored is True
    assert "500" in result.error
    assert result.output == ""


def test_a_transient_failure_is_retried_and_can_succeed():
    attempts = {"n": 0}

    def flaky(messages, info: AgentInfo) -> ModelResponse:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ModelHTTPError(status_code=429, model_name="scripted", body=None)
        return text("recovered")

    slept = []
    runner = PydanticAIRunner(
        model=FunctionModel(flaky), retries=2, retry_backoff_seconds=0.01, sleep=slept.append
    )
    result = runner.run(SKILL, case())
    assert result.output == "recovered"
    assert result.errored is False
    assert slept == [0.01]


def test_backoff_grows_exponentially_between_attempts():
    def always_429(messages, info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(status_code=429, model_name="scripted", body=None)

    slept = []
    runner = PydanticAIRunner(
        model=FunctionModel(always_429), retries=3, retry_backoff_seconds=1.0, sleep=slept.append
    )
    result = runner.run(SKILL, case())
    assert slept == [1.0, 2.0, 4.0]
    assert result.errored is True


def test_a_permanent_failure_is_not_retried():
    attempts = {"n": 0}

    def unauthorized(messages, info: AgentInfo) -> ModelResponse:
        attempts["n"] += 1
        raise ModelHTTPError(status_code=401, model_name="scripted", body=None)

    slept = []
    runner = PydanticAIRunner(
        model=FunctionModel(unauthorized), retries=3, retry_backoff_seconds=0.01, sleep=slept.append
    )
    result = runner.run(SKILL, case())
    assert attempts["n"] == 1
    assert slept == []
    assert result.errored is True


def test_the_runner_declares_that_it_needs_a_key():
    assert PydanticAIRunner.needs_api_key is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_pydantic_ai_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.runners.pydantic_ai'`

- [ ] **Step 3: Implement the adapter**

Create `src/skill_eval/runners/pydantic_ai.py`:

```python
"""The PydanticAI adapter — the only module that imports an agent framework.

Everything the core sees is a plain `RunResult`. Provider failures are reported
through `RunResult.error`, never raised, so the orchestrator can tell an infra
problem (errored) apart from a low score (failed).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from skill_eval.models import EvalCase, RunResult, Skill, ToolCall
from skill_eval.runners.pricing import calculate_cost, provider_of
from skill_eval.runners.tools import build_mock_tool

DEFAULT_MODEL = "openai:gpt-4o-mini"

# Statuses worth another attempt: rate limits, request timeouts, conflicts and
# anything the provider blames on itself. A 401 or 404 will never fix itself.
_TRANSIENT_STATUSES = {408, 409, 429}


class RunnerDependencyError(Exception):
    """Raised when the optional extra providing this runner is not installed."""


def _require_pydantic_ai() -> None:
    try:
        import pydantic_ai  # noqa: F401
    except ImportError as exc:
        raise RunnerDependencyError(
            "the 'pydantic-ai' runner needs its optional extra: "
            "pip install 'skill-eval[pydantic-ai]'"
        ) from exc


def _system_prompt(skill: Skill) -> str:
    """The skill, as the agent sees it: identity first, then its instructions."""
    header = f"# {skill.name}"
    if skill.description:
        header = f"{header}\n\n{skill.description}"
    return f"{header}\n\n{skill.instructions}".strip()


def _arguments(args: Any) -> dict[str, Any]:
    """Normalise tool-call arguments to a dict.

    Real providers send a JSON string; in-process models send a dict. An
    unparseable payload is preserved verbatim so a capture problem can never
    masquerade as a model problem.
    """
    if isinstance(args, dict):
        return args
    if not args:
        return {}
    try:
        parsed = json.loads(args)
    except (TypeError, ValueError):
        return {"_raw": str(args)}
    return parsed if isinstance(parsed, dict) else {"_raw": str(args)}


def _tool_calls(messages: list[Any]) -> list[ToolCall]:
    """Read the trajectory out of the message history, in order.

    The message history is authoritative: it records what the model asked for,
    including calls whose execution then failed.
    """
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    calls: list[ToolCall] = []
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                calls.append(ToolCall(name=part.tool_name, arguments=_arguments(part.args)))
    return calls


def _transcript(messages: list[Any]) -> list[dict[str, Any]]:
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    return ModelMessagesTypeAdapter.dump_python(messages, mode="json")


def _model_name(messages: list[Any], fallback: str) -> str:
    """The model the provider actually served, which may be a dated snapshot."""
    from pydantic_ai.messages import ModelResponse

    for message in reversed(messages):
        if isinstance(message, ModelResponse) and message.model_name:
            return message.model_name
    return fallback


def _is_transient(exc: Exception) -> bool:
    from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError

    if isinstance(exc, ModelHTTPError):
        return exc.status_code in _TRANSIENT_STATUSES or exc.status_code >= 500
    if isinstance(exc, ModelAPIError):
        return True
    return isinstance(exc, (TimeoutError, ConnectionError))


class PydanticAIRunner:
    """Runs a case through a real agent, behind the framework-agnostic protocol."""

    name = "pydantic-ai"
    needs_api_key = True

    def __init__(
        self,
        model: Any = DEFAULT_MODEL,
        temperature: float | str = 0.0,
        retries: int = 2,
        retry_backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._retries = retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._sleep = sleep

    def _model_settings(self) -> Any:
        """Reasoning models reject any temperature but 1, so 'unset' sends none."""
        from pydantic_ai.settings import ModelSettings

        if self._temperature == "unset":
            return None
        return ModelSettings(temperature=float(self._temperature))

    def _build_agent(self, skill: Skill, case: EvalCase) -> Any:
        from pydantic_ai import Agent, Tool

        tools = []
        for spec in case.tools:
            mock = build_mock_tool(spec)
            tools.append(
                Tool.from_schema(
                    mock.call,
                    name=mock.name,
                    description=mock.description,
                    json_schema=mock.json_schema,
                )
            )
        return Agent(self._model, instructions=_system_prompt(skill), tools=tools)

    def _run_with_retries(self, agent: Any, task: str) -> Any:
        settings = self._model_settings()
        delay = self._retry_backoff_seconds
        attempt = 0
        while True:
            try:
                return agent.run_sync(task, model_settings=settings)
            except Exception as exc:
                if attempt >= self._retries or not _is_transient(exc):
                    raise
                self._sleep(delay)
                delay *= 2
                attempt += 1

    def run(self, skill: Skill, case: EvalCase) -> RunResult:
        _require_pydantic_ai()
        configured = self._model if isinstance(self._model, str) else ""
        started = time.monotonic()
        try:
            agent = self._build_agent(skill, case)
            result = self._run_with_retries(agent, case.task)
        except RunnerDependencyError:
            raise
        except Exception as exc:
            return RunResult(
                error=f"{type(exc).__name__}: {exc}",
                latency_ms=int((time.monotonic() - started) * 1000),
                model=configured,
            )

        latency_ms = int((time.monotonic() - started) * 1000)
        messages = result.all_messages()
        usage = result.usage
        model_name = _model_name(messages, configured)
        cost_usd, cost_note = calculate_cost(usage, model_name, provider_of(configured))
        return RunResult(
            output=result.output if isinstance(result.output, str) else str(result.output),
            tool_calls=_tool_calls(messages),
            transcript=_transcript(messages),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            cost_note=cost_note,
            model=model_name,
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_pydantic_ai_runner.py -v`
Expected: PASS — 15 tests, all offline.

- [ ] **Step 5: Verify no network was touched**

Run: `uv run pytest tests/test_pydantic_ai_runner.py -v -p no:cacheprovider`
Expected: PASS in well under a second, with no `OPENAI_API_KEY` set in the environment.

- [ ] **Step 6: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/runners/pydantic_ai.py tests/test_pydantic_ai_runner.py
git commit -m "feat(runners): add PydanticAI adapter with trajectory, usage and retries"
```

---

### Task 10: CLI wiring

**Files:**
- Modify: `src/skill_eval/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `PydanticAIRunner`, `RunnerDependencyError` (Task 9); `check_api_key`, `MissingAPIKey` (Task 8); `Config.model` etc. (Task 1).
- Produces: `skill-eval run --runner pydantic-ai --model <id>`; preflight before any run; exit 2 for a missing key or a missing extra.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py` (this module already has a `runner` CliRunner and a helper that writes a skill + eval file; reuse them — if the helper is named differently, adapt these calls to it):

```python
def test_unknown_runner_is_a_user_error(tmp_path):
    skill_dir = write_skill(tmp_path)
    result = runner.invoke(app, ["run", str(skill_dir), "--runner", "nope"])
    assert result.exit_code == 2


def test_the_real_runner_is_registered(tmp_path):
    skill_dir = write_skill(tmp_path)
    result = runner.invoke(
        app,
        ["run", str(skill_dir), "--runner", "pydantic-ai", "--model", "openai:gpt-4o-mini"],
        env={"OPENAI_API_KEY": ""},
    )
    # No key, so preflight stops it before any spend.
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output


def test_preflight_names_the_missing_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    skill_dir = write_skill(tmp_path)
    result = runner.invoke(
        app,
        ["run", str(skill_dir), "--runner", "pydantic-ai", "--model", "anthropic:claude-sonnet-4-6"],
    )
    assert result.exit_code == 2
    assert "ANTHROPIC_API_KEY" in result.output
    assert "skill-eval.toml" in result.output


def test_the_fake_runner_needs_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill_dir = write_skill(tmp_path)
    result = runner.invoke(app, ["run", str(skill_dir), "--runner", "fake"])
    assert result.exit_code in (0, 1)  # gate verdict, not a preflight refusal
    assert "OPENAI_API_KEY" not in result.output


def test_model_flag_beats_the_config_file(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill_dir = write_skill(tmp_path)
    config_file = tmp_path / "skill-eval.toml"
    config_file.write_text('model = "anthropic:claude-sonnet-4-6"\n', encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "run",
            str(skill_dir),
            "--runner",
            "pydantic-ai",
            "--model",
            "openai:gpt-4o-mini",
            "--config",
            str(config_file),
        ],
    )
    assert "OPENAI_API_KEY" in result.output
    assert "ANTHROPIC_API_KEY" not in result.output
```

If `tests/test_cli.py` has no `write_skill` helper, add this one near the top of the module and use it:

```python
def write_skill(tmp_path):
    skill_dir = tmp_path / "greeting"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: greeting\n---\nGreet warmly.\n", encoding="utf-8")
    (skill_dir / "greeting.eval.yaml").write_text(
        "cases:\n  - name: c\n    task: greet Ada\n", encoding="utf-8"
    )
    return skill_dir
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli.py -k "real_runner or preflight or model_flag" -v`
Expected: FAIL — exit code 2 with "unknown runner: pydantic-ai".

- [ ] **Step 3: Wire the CLI**

In `src/skill_eval/cli.py`, extend the imports:

```python
import os

from skill_eval.runners.fake import FakeRunner
from skill_eval.runners.preflight import MissingAPIKey, check_api_key
from skill_eval.runners.pydantic_ai import PydanticAIRunner, RunnerDependencyError
```

Replace the registry and error tuple:

```python
_RUNNERS = {"fake": FakeRunner, "pydantic-ai": PydanticAIRunner}

# Authoring errors: bad skill/case/config files, or a malformed assertion in an
# eval YAML (Tasks 6/7 decided the latter aborts the whole run rather than
# being silently swallowed as a failed case). Missing keys and missing optional
# extras are user errors too -- all get the same clean "print the message, exit
# 2" treatment instead of a raw traceback.
_AUTHORING_ERRORS = (
    SkillParseError,
    CaseParseError,
    ConfigError,
    UnknownAssertionKind,
    InvalidAssertionValue,
    MissingAPIKey,
    RunnerDependencyError,
)
```

Add the `--model` option to `run`'s signature, immediately after `runner`:

```python
    model: Annotated[str | None, typer.Option(help="Model id, e.g. openai:gpt-4o-mini.")] = None,
```

Replace the body of the `try` block that builds and runs the matrix:

```python
    try:
        settings = load_config(path=config)
        skills = load_skills(path)
        runner_name = runner if runner is not None else settings.default_runner
        if runner_name not in _RUNNERS:
            raise typer.BadParameter(f"unknown runner: {runner_name}")
        runner_class = _RUNNERS[runner_name]
        model_name = model if model is not None else settings.model
        if getattr(runner_class, "needs_api_key", False):
            check_api_key(model_name, os.environ)
            active_runner = runner_class(
                model=model_name,
                temperature=settings.temperature,
                retries=settings.retries,
                retry_backoff_seconds=settings.retry_backoff_seconds,
            )
        else:
            active_runner = runner_class()
        report = run_evals(skills, [active_runner], evals_path=evals, tag=tag)
    except _AUTHORING_ERRORS as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS — every CLI test.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/cli.py tests/test_cli.py
git commit -m "feat(cli): register the pydantic-ai runner with --model and key preflight"
```

---

### Task 11: Cassette test tier

**Files:**
- Modify: `pyproject.toml`
- Modify: `tests/conftest.py`
- Create: `tests/test_cassettes.py`
- Create (by recording): `tests/cassettes/test_cassettes/*.yaml`

**Interfaces:**
- Consumes: `PydanticAIRunner` (Task 9), `TrajectoryEvaluator` (Task 4), `BudgetEvaluator` (Task 5).
- Produces: a `vcr_config` fixture scrubbing credentials, and a cassette-backed test of the full loop.

**Recording note:** recording needs a real `OPENAI_API_KEY` in the environment and costs a fraction of a cent. Replay needs no key. The cassette must be inspected for secrets before it is committed.

- [ ] **Step 1: Add the marker and the VCR fixtures**

In `pyproject.toml`, extend the pytest markers:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "integration: hits real provider APIs; requires an API key (deselected by default)",
    "cassette: replays recorded provider traffic; no network, no key needed",
]
addopts = "-m 'not integration'"
```

Append to `tests/conftest.py`:

```python
import os
from pathlib import Path

import pytest

CASSETTE_DIR = Path(__file__).parent / "cassettes"


@pytest.fixture(scope="module")
def vcr_config():
    """Replay-only by default, with every credential scrubbed on record.

    Matching on the body as well as the URL matters here: every request goes to
    the same chat-completions path, so the body is the only thing that tells one
    turn of a conversation from the next.
    """
    return {
        "filter_headers": [
            "authorization",
            "api-key",
            "x-api-key",
            "openai-organization",
            "openai-project",
            "cookie",
            "set-cookie",
        ],
        "match_on": ["method", "scheme", "host", "port", "path", "body"],
        "decode_compressed_response": True,
    }


@pytest.fixture
def replay(request, monkeypatch):
    """Set up a cassette-backed test: dummy key, and skip if never recorded.

    Provider clients refuse to construct without a key even when every response
    is replayed, so a placeholder is required. A fresh clone with no cassettes
    must not look like a broken build, hence the skip -- but a *mismatched*
    request still fails loudly rather than reaching the network, which is the
    behaviour the tier exists for.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-for-replay")
    cassette = CASSETTE_DIR / request.node.module.__name__ / f"{request.node.name}.yaml"
    if not cassette.is_file():
        pytest.skip(f"cassette {cassette.name} not recorded; see the recording command in the plan")
```

`tests/` is not a package (no `__init__.py`), so this has to be a fixture rather than a
helper function imported from `conftest`.

- [ ] **Step 2: Write the cassette test**

Create `tests/test_cassettes.py`:

```python
"""The full loop against recorded real provider traffic.

Tier 2 of the strategy in the design spec: real wire fidelity, zero cost, every
PR. Tier 1 (FunctionModel) proves the mapping; this proves the mapping matches
what a provider actually sends.
"""

from pathlib import Path

import pytest

from skill_eval.evaluators.assertion import AssertionEvaluator
from skill_eval.evaluators.budget import BudgetEvaluator
from skill_eval.evaluators.trajectory import TrajectoryEvaluator
from skill_eval.models import BudgetSpec, EvalCase, Skill, ToolSpec, TrajectorySpec
from skill_eval.runners.pydantic_ai import PydanticAIRunner

SKILL = Skill(
    name="order-support",
    description="Handle customer refund requests",
    instructions=(
        "Always call lookup_order before saying anything about an order. "
        "Never issue a refund for an order delivered more than 30 days ago."
    ),
    path=Path("."),
)

CASE = EvalCase(
    name="checks the order before refusing",
    task="I want a refund for order 1234",
    tools=[
        ToolSpec(
            name="lookup_order",
            description="Look up an order by its id",
            parameters={"order_id": "string"},
            returns='{"id": "1234", "status": "delivered", "days_since_delivery": 45}',
        ),
        ToolSpec(
            name="issue_refund",
            description="Issue a refund for an order",
            parameters={"order_id": "string"},
            returns='{"ok": true}',
        ),
    ],
    trajectory=TrajectorySpec(called=["lookup_order"], forbidden=["issue_refund"], max_calls=3),
    budget=BudgetSpec(max_tokens=2000, max_cost_usd=0.01),
)


@pytest.mark.cassette
@pytest.mark.vcr
def test_real_traffic_drives_the_whole_loop(replay):
    result = PydanticAIRunner(model="openai:gpt-4o-mini").run(SKILL, CASE)

    assert result.errored is False
    assert result.output != ""
    assert [call.name for call in result.tool_calls] == ["lookup_order"]
    assert result.tool_calls[0].arguments == {"order_id": "1234"}
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.cost_usd > 0
    assert result.cost_note == ""
    assert result.model.startswith("gpt-4o-mini")

    assert TrajectoryEvaluator().evaluate(CASE, result).passed is True
    assert BudgetEvaluator().evaluate(CASE, result).passed is True
    assert AssertionEvaluator().evaluate(CASE, result).passed is True


@pytest.mark.cassette
@pytest.mark.vcr
def test_arguments_from_a_real_provider_arrive_as_a_dict(replay):
    # Providers send tool arguments as a JSON *string*; the adapter normalises
    # them. Only real traffic can prove that, which is why it lives here.
    result = PydanticAIRunner(model="openai:gpt-4o-mini").run(SKILL, CASE)
    assert isinstance(result.tool_calls[0].arguments, dict)
    assert result.tool_calls[0].arguments["order_id"] == "1234"
```

- [ ] **Step 3: Run the tests and watch them skip**

Run: `uv run pytest tests/test_cassettes.py -v`
Expected: 2 SKIPPED with "cassette ... not recorded".

- [ ] **Step 4: Record the cassettes**

With a real key exported in your shell:

```bash
uv run pytest tests/test_cassettes.py --record-mode=once -v
```

Expected: 2 PASSED, and two files created under `tests/cassettes/test_cassettes/`.

- [ ] **Step 5: Verify the cassettes carry no secrets**

```bash
grep -ric "sk-" tests/cassettes/ ; grep -ric "authorization" tests/cassettes/
```

Expected: `0` for both, for every file. **If either is non-zero, delete the cassettes and fix `vcr_config` before going any further — do not commit.**

- [ ] **Step 6: Verify replay works with no key at all**

```bash
env -u OPENAI_API_KEY uv run pytest tests/test_cassettes.py -v
```

Expected: 2 PASSED, in well under a second.

- [ ] **Step 7: Verify an unmatched request fails rather than reaching the network**

Temporarily change `CASE.task` in `tests/test_cassettes.py` to `"a completely different question"`, then run:

```bash
env -u OPENAI_API_KEY uv run pytest tests/test_cassettes.py -v
```

Expected: FAILED — vcrpy reports it cannot play a matching response (`CannotOverwriteExistingCassetteException`). **Revert the edit** and confirm the tests pass again.

- [ ] **Step 8: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add pyproject.toml tests/conftest.py tests/test_cassettes.py tests/cassettes
git commit -m "test: add cassette tier replaying real provider traffic"
```

---

### Task 12: Real example skills and CI retargeting

**Files:**
- Modify: `examples/greeting/greeting.eval.yaml`
- Create: `examples/order-support/SKILL.md`
- Create: `examples/order-support/order-support.eval.yaml`
- Create: `tests/test_integration_live.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_examples.py`

**Interfaces:**
- Consumes: everything above.
- Produces: example suites whose assertions describe real model behavior; CI's zero-cost dogfood step becomes `skill-eval list ./examples`.

- [ ] **Step 1: Rewrite the greeting eval file**

Replace `examples/greeting/greeting.eval.yaml` entirely:

```yaml
# Assertions here describe what a real agent should produce. They are exercised
# for real by the live-integration tier; the zero-cost CI step only validates
# that this file parses and that its checks are well formed.
cases:
  - name: greets the named person in one sentence
    task: greet Ada
    tags: [smoke]
    budget:
      max_tokens: 500
    assertions:
      - kind: contains
        value: Ada
      - kind: not_contains
        value: Traceback
      - kind: regex
        value: "^[^.!?]*[.!?]\\s*$"
```

- [ ] **Step 2: Write the order-support skill**

Create `examples/order-support/SKILL.md`:

```markdown
---
name: order-support
description: Handle customer refund requests against the 30-day return policy
---

You handle refund requests for an online store.

Always call `lookup_order` before saying anything about the state of an order.
You cannot know an order's status without looking it up, and guessing at it is
worse than asking.

Never call `issue_refund` for an order that was delivered more than 30 days ago
— the return window has closed. Explain the decision in one short paragraph and
name the order id so the customer knows which order you mean.
```

- [ ] **Step 3: Write the order-support eval suite**

Create `examples/order-support/order-support.eval.yaml`:

```yaml
# Two cases on the same skill: one where the policy forbids a refund and one
# where it allows it. The trajectory checks are the point -- "did it look the
# order up before deciding" is invisible to an output assertion.
cases:
  - name: refuses a refund outside the return window
    task: I want a refund for order 1234
    tags: [smoke, refund]
    tools:
      - name: lookup_order
        description: Look up an order by its id
        parameters:
          order_id: string
        returns: '{"id": "1234", "status": "delivered", "days_since_delivery": 45}'
      - name: issue_refund
        description: Issue a refund for an order
        parameters:
          order_id: string
        returns: '{"ok": true}'
    trajectory:
      called: [lookup_order]
      forbidden: [issue_refund]
      max_calls: 3
    budget:
      max_tokens: 2000
      max_cost_usd: 0.01
    assertions:
      - kind: contains
        value: "1234"

  - name: refunds an order inside the return window
    task: Please refund order 5678
    tags: [refund]
    tools:
      - name: lookup_order
        description: Look up an order by its id
        parameters:
          order_id: string
        returns: '{"id": "5678", "status": "delivered", "days_since_delivery": 3}'
      - name: issue_refund
        description: Issue a refund for an order
        parameters:
          order_id: string
        returns: '{"ok": true}'
    trajectory:
      order: [lookup_order, issue_refund]
      max_calls: 4
    budget:
      max_tokens: 2000
      max_cost_usd: 0.01
    assertions:
      - kind: contains
        value: "5678"
```

- [ ] **Step 4: Check the example files parse**

Run: `uv run skill-eval list ./examples`
Expected: two lines — `greeting  1 case(s) ...` and `order-support  2 case(s) ...`

- [ ] **Step 5: Update the examples test**

`tests/test_examples.py` currently runs the examples through `FakeRunner` and expects them to pass. That is no longer meaningful, so replace its body with checks that the shipped examples are well formed:

```python
"""The shipped examples must always parse and be well formed.

They are no longer run through FakeRunner: their assertions describe real model
behaviour now, so the zero-cost check is that discovery and schema validation
work on real files. The full run path is covered by the cassette tier.
"""

from pathlib import Path

from skill_eval.cases.loader import load_cases_for_skill
from skill_eval.skills.loader import load_skills

EXAMPLES = Path(__file__).parent.parent / "examples"


def test_every_example_skill_is_discovered():
    names = [skill.name for skill in load_skills(EXAMPLES)]
    assert names == ["greeting", "order-support"]


def test_every_example_skill_has_at_least_one_case():
    for skill in load_skills(EXAMPLES):
        assert load_cases_for_skill(skill), f"{skill.name} has no eval cases"


def test_declared_trajectory_tools_are_actually_declared_as_tools():
    # A trajectory check naming a tool the case never declares can never pass,
    # and would look like a skill failure rather than the typo it is.
    for skill in load_skills(EXAMPLES):
        for case in load_cases_for_skill(skill):
            declared = {tool.name for tool in case.tools}
            if case.trajectory is None:
                continue
            referenced = set(
                case.trajectory.called + case.trajectory.forbidden + case.trajectory.order
            )
            assert referenced <= declared, f"{skill.name}/{case.name}: {referenced - declared}"
```

- [ ] **Step 6: Write the live-integration test**

Create `tests/test_integration_live.py`:

```python
"""Tier 3: the real thing, against the real examples. Opt-in, real money.

Deselected by default (`addopts = "-m 'not integration'"`), and skipped even
when selected if no key is present. Run it with:
    uv run pytest -m integration -v
"""

import os
from pathlib import Path

import pytest

from skill_eval.orchestrator import run_evals
from skill_eval.runners.pydantic_ai import PydanticAIRunner
from skill_eval.skills.loader import load_skills

EXAMPLES = Path(__file__).parent.parent / "examples"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="needs OPENAI_API_KEY"),
]


def test_the_examples_pass_against_a_real_provider():
    report = run_evals(load_skills(EXAMPLES), [PydanticAIRunner(model="openai:gpt-4o-mini")])
    assert report.total == 3
    assert report.errored == 0, [o.result.error for o in report.outcomes if o.result.errored]
    assert report.pass_rate == 1.0, [
        (o.case_name, [s.detail for s in o.scores if not s.passed])
        for o in report.outcomes
        if o.status == "failed"
    ]
```

- [ ] **Step 7: Run the offline suite**

Run: `uv run pytest -v`
Expected: PASS — the integration tier is deselected, the cassette tier replays.

- [ ] **Step 8: Run the live tier once, deliberately**

With a real key exported:

```bash
uv run pytest -m integration -v
```

Expected: PASS. If a case fails, read the failure detail: either the example skill's instructions need sharpening or the assertion is too brittle. Fix the example, not the evaluator.

- [ ] **Step 9: Retarget the CI dogfood step**

In `.github/workflows/ci.yml`, replace the self-check step:

```yaml
      - name: Self-check (dogfood the CLI on examples/)
        # The examples now assert real model behaviour, so they cannot be run
        # with FakeRunner. `list` still exercises discovery, YAML parsing and
        # tool/trajectory/budget schema validation on real files at zero cost;
        # the full run path is covered every PR by the cassette tier.
        run: uv run skill-eval list ./examples
```

- [ ] **Step 10: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add examples tests/test_examples.py tests/test_integration_live.py .github/workflows/ci.yml
git commit -m "feat(examples): add order-support skill and retarget each test tier"
```

---

### Task 13: Documentation

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: everything above. No code changes.

- [ ] **Step 1: Update the README**

In `README.md`, add a section documenting the real runner after the existing usage section:

````markdown
## Running against a real agent

The default runner is `fake` (offline, scripted, free). To evaluate a skill with a
real agent, install the extra and pick a model:

```bash
pip install 'skill-eval[pydantic-ai]'
export OPENAI_API_KEY=...
skill-eval run ./skills --runner pydantic-ai --model openai:gpt-4o-mini
```

API keys are read from the environment only — never from `skill-eval.toml`.
`skill-eval` checks for the key before making any request, so a missing key costs
nothing and exits 2.

### Declaring tools and scoring the trajectory

An eval case can declare the tools the agent may call. Nothing executes: a tool
records the call and returns its canned value, so the trajectory is the model's
own choice and the run has no side effects.

```yaml
cases:
  - name: checks the order before refusing
    task: I want a refund for order 1234
    tools:
      - name: lookup_order
        description: Look up an order by its id
        parameters:
          order_id: string
        returns: '{"id": "1234", "days_since_delivery": 45}'
    trajectory:
      called: [lookup_order]        # each of these ran
      forbidden: [issue_refund]     # none of these ran
      order: [lookup_order]         # ran in this relative order
      max_calls: 3                  # no looping
    budget:
      max_tokens: 2000
      max_cost_usd: 0.01
      max_latency_ms: 30000
    assertions:
      - kind: contains
        value: "1234"
```

`order` is a relative subsequence: unrelated calls may appear in between, but the
listed tools must not appear out of sequence.

### Configuration

```toml
default_runner = "pydantic-ai"
model = "openai:gpt-4o-mini"
temperature = 0.0            # or "unset" for reasoning models, which reject it
retries = 2
retry_backoff_seconds = 1.0
```
````

- [ ] **Step 2: Update CLAUDE.md**

In `CLAUDE.md`, replace the "Currently at M0+M1" paragraph:

```markdown
Currently at **M2**: the pipeline runs real agents through `PydanticAIRunner`
(provider-flexible, via PydanticAI), scores tool use and efficiency as well as
output text, and is tested against recorded provider traffic. `FakeRunner`
remains the default and the backbone of the zero-cost test tier. Milestones are
defined in `docs/superpowers/specs/2026-07-30-skill-eval-design.md` §9; the M2
design is in `docs/superpowers/specs/2026-08-01-skill-eval-m2-design.md`.
```

Add to the Commands block:

```bash
uv run pytest -m integration          # opt-in tier; needs OPENAI_API_KEY, costs real money
uv run pytest tests/test_cassettes.py --record-mode=once   # re-record cassettes (needs a key)
uv run skill-eval list ./examples     # dogfood discovery; CI runs this as a self-check
```

Add these entries to the "Invariants that are easy to break" list:

```markdown
- **No agent-framework type may appear outside `runners/pydantic_ai.py`.** `runners/tools.py`
  builds framework-neutral `MockTool`s (name + JSON schema + callable); the adapter wraps them.
  A test asserts `pydantic_ai` does not appear in `tools.py`.
- **`RunResult.tokens` is derived**, not stored — `extra="forbid"` makes writing it a loud
  error rather than a total that silently disagrees with the input/output split it was priced from.
- **Cost lookup degrades, never raises.** An unpriced model yields `cost_usd = 0.0` plus a
  `cost_note`; pricing is reporting metadata and must never be why a run errors.
- **Mock tools accept any arguments.** A model hallucinating an argument must not raise, or an
  eval signal would surface as an infra error.
- **Cassettes are replay-only and secret-free.** Recording is a deliberate, key-bearing act;
  a missing cassette skips rather than fails, but a mismatched request fails rather than
  reaching the network.
```

- [ ] **Step 3: Verify the documented commands actually work**

```bash
uv run skill-eval list ./examples
uv run skill-eval run ./examples --runner pydantic-ai --model openai:gpt-4o-mini --help
```

Expected: the first prints two skills; the second prints help without error.

- [ ] **Step 4: Run the full suite one final time**

Run: `uv run pytest -v && uv run ruff check . && uv run ruff format --check .`
Expected: all PASS, no lint or format complaints.

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: document the pydantic-ai runner, tools, trajectory and budget"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
| --- | --- |
| §1 `PydanticAIRunner`, registry name `pydantic-ai` | 9, 10 |
| §1 case-declared mock tools | 2, 7, 9 |
| §1 `TrajectoryEvaluator` | 4, 6 |
| §1 `BudgetEvaluator` | 5, 6 |
| §1 cost & latency capture | 2, 8, 9 |
| §1 retries + preflight | 8, 9, 10 |
| §1 cassette tier + integration marker | 11, 12 |
| §1 real `examples/` skills | 12 |
| §3 `ToolSpec` / `TrajectorySpec` / `BudgetSpec` YAML surface | 2 |
| §3 unknown param type is an authoring error | 2 (rejected by `ToolParamType`, surfaced as `CaseParseError`) |
| §4 system prompt, tool registration, temperature `"unset"` | 1, 9 |
| §4 trajectory capture from messages, arg normalisation | 9 |
| §4 transcript as plain JSON | 9 |
| §4 errors never raise, transient retry with backoff | 9 |
| §4 cost degrades to a note | 8, 9 |
| §4 preflight by provider prefix, env-only secrets | 8, 10 |
| §5 model changes, `tokens` derived | 2 |
| §5 `Runner.run(skill, case)` | 3 |
| §6 evaluator semantics (subsequence order, vacuous pass, fraction score) | 4, 5 |
| §7 three tiers, replay-only, redaction, skip-if-missing | 11, 12 |
| §7 CI dogfood becomes `list` | 12 |
| §8 greeting rewrite + order-support | 12 |
| §9 config additions | 1 |
| §10 invariants | Global Constraints; asserted in 2, 5, 7, 9, 11 |

**Deferred by the spec, deliberately absent from this plan:** real-execution tools (M6), skill-triggering mode (M3), baseline/repeat/delta (M4), orchestrator concurrency (M5).

**Type consistency:** `run(skill, case)` is used identically in Tasks 3, 9, 11 and 12. `MockTool.call` / `.json_schema` / `.name` / `.description` are produced in Task 7 and consumed unchanged in Task 9. `calculate_cost(usage, model_name, provider_id) -> (float, str)` and `provider_of(model) -> str` are defined in Task 8 and used with the same signature in Tasks 9 and 10. `check_api_key(model, environ)` and `MissingAPIKey` are defined in Task 8 and used in Task 10. `Config.temperature` accepts `float | "unset"` in Task 1 and is consumed as such in Tasks 9 and 10. `RunResult.tokens` is a property everywhere after Task 2.

**Known follow-on within this plan:** Task 2 makes `RunResult` reject `tokens=`, which breaks two existing constructions in `tests/test_reporters.py`; Task 2 Step 4 fixes them in the same commit. Task 3 changes the `Runner` protocol, which breaks `tests/test_fake_runner.py` and the orchestrator call site; Task 3 fixes both in the same commit.
