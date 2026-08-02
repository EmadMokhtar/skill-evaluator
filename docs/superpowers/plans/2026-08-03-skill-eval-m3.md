# skill-eval M3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add rubric-based LLM-as-judge scoring with evidence-bearing per-check verdicts, and an `offered` case mode that makes a skill's triggering decision an observable, gateable fact.

**Architecture:** A third protocol seam, `Judge`, mirrors the existing `Runner` and `Evaluator` seams: `judges/base.py` defines it, `judges/pydantic_ai.py` becomes the second and last module allowed to import an agent framework, and `judges/fake.py` keeps the zero-cost tier working. `evaluators/judge.py` holds only rubric logic and receives a `Judge` by constructor injection. Triggering reuses the trajectory machinery already built in M2: in `offered` mode the runner registers the skill as a tool that returns its own instructions, so choosing it is just a `ToolCall`, and the runner reports the decision on `RunResult.skill_triggered` for the evaluator to score.

**Tech Stack:** Python 3.11+, uv, Pydantic v2, PydanticAI (`pydantic-ai-slim[openai]`), genai-prices, vcrpy + pytest-recording, Typer, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-08-03-skill-eval-m3-design.md`

## Global Constraints

- **`errored` ≠ `failed`.** `failed` = ran and scored below bar (an eval signal). `errored` = infra blew up. **This milestone extends `errored` to evaluators**: an evaluator may now report an infra failure, and the orchestrator must classify the case as `errored`, not `failed`.
- **Judges must never raise for provider failures** — they set `JudgeVerdict.error` instead, exactly as runners set `RunResult.error`.
- **Nothing scores a vacuous pass.** An unverified rubric is `errored`, never `passed` — the same rule M2 applied to an unpriceable cost limit.
- **A judge pass with no evidence is recorded as a failure.** An unsupported PASS is the judge's characteristic failure mode.
- **skill-eval derives `passed` and `score`; the judge only supplies per-check verdicts and evidence.** Never ask the model for a blended number.
- **Authoring errors abort the run and exit 2** — now including a malformed `judge:` block, an empty `rubric`, `skill_triggered` on a `mode: loaded` case, and a case tool colliding with the skill's offered tool name.
- **Exit codes are the CI contract:** gate pass `0`, gate fail `1`, user/authoring error `2`.
- **`extra="forbid"`** on every user-authored model — `JudgeSpec` included.
- **`models.py` holds every Pydantic model.** Other modules import from it and never define their own data shapes.
- **All file IO pins `encoding="utf-8"`**; YAML goes through `skill_eval.yaml_loading.safe_load`, never `yaml.safe_load`.
- **Secrets come from environment variables only** — never from `skill-eval.toml`, never committed to a cassette.
- **`skill_eval` (underscore) never appears in user-facing output.** The judge registers as `pydantic-ai`.
- **Agent-framework imports appear in exactly two modules:** `src/skill_eval/runners/pydantic_ai.py` and `src/skill_eval/judges/pydantic_ai.py`. Task 12 adds the test that enforces this.
- **The built-in `judge` default stays `"fake"`** — upgrading to M3 must never start spending money on its own.
- **TDD:** write the failing test first, watch it fail, then implement. Tier-1 tests stay offline, deterministic, and network-free.
- **Conventional Commits are enforced** by a `commit-msg` hook (`cz check`). Every commit message below is already conventional — use it verbatim.
- Line length 100 (ruff). Run `uv run ruff check .` and `uv run ruff format .` before each commit.

---

## File Structure

**Created:**
- `src/skill_eval/judges/__init__.py` — empty package marker.
- `src/skill_eval/judges/base.py` — the `Judge` protocol. No framework, no IO.
- `src/skill_eval/judges/prompt.py` — `JudgeRequest` → prompt text. Pure function.
- `src/skill_eval/judges/fake.py` — `FakeJudge`: scripted, offline; unscripted it errors.
- `src/skill_eval/judges/pydantic_ai.py` — `PydanticAIJudge`. The second framework importer.
- `src/skill_eval/evaluators/judge.py` — `JudgeEvaluator`: rubric logic only.
- `tests/test_judge_prompt.py`, `tests/test_fake_judge.py`, `tests/test_judge_evaluator.py`,
  `tests/test_pydantic_ai_judge.py`, `tests/test_framework_isolation.py`
- `tests/cassettes/test_cassettes/*.yaml` (recorded, committed)

**Modified:**
- `src/skill_eval/models.py` — `CheckResult`, `RubricCheck`, `JudgeRequest`, `JudgeVerdict`, `JudgeOutput`, `JudgeSpec`; new fields on `EvalScore`, `RunResult`, `TrajectorySpec`, `EvalCase`; `RunReport.judge_cost_usd`.
- `src/skill_eval/runners/tools.py` — `skill_tool_name`, `build_skill_tool`.
- `src/skill_eval/cases/loader.py` — three new authoring-error checks; `parse_cases_file` takes the skill.
- `src/skill_eval/evaluators/trajectory.py` — the `skill_triggered` check and its errored path.
- `src/skill_eval/runners/pydantic_ai.py` — `offered` mode.
- `src/skill_eval/orchestrator.py` — errored-score propagation; default evaluator list.
- `src/skill_eval/config.py` — `judge`, `judge_model`.
- `src/skill_eval/cli.py` — judge registry, `--judge-model`, judge preflight.
- `src/skill_eval/reporters/console.py`, `src/skill_eval/reporters/json_reporter.py` — per-check evidence, judge overhead.
- `tests/test_cassettes.py`, `tests/test_integration_live.py`
- `examples/order-support/order-support.eval.yaml`
- `README.md`, `CLAUDE.md`

---

### Task 1: Judge and triggering models

**Files:**
- Modify: `src/skill_eval/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `CheckResult(id: str, passed: bool, evidence: str = "")`
  - `RubricCheck(id: str, text: str)`
  - `JudgeRequest(task: str, output: str, expected: str = "", checks: list[RubricCheck] = [])`
  - `JudgeVerdict(checks: list[CheckResult] = [], input_tokens: int = 0, output_tokens: int = 0, cost_usd: float = 0.0, cost_note: str = "", model: str = "", error: str | None = None)` with `.errored` property
  - `JudgeOutput(checks: list[CheckResult] = [])` — the structured-output shape a judge model must return
  - `JudgeSpec(expected: str = "", rubric: list[str] = [])`, `extra="forbid"`
  - `EvalScore` gains `checks: list[CheckResult]`, `errored: bool`, `cost_usd: float`
  - `RunResult` gains `skill_triggered: bool | None`
  - `TrajectorySpec` gains `skill_triggered: bool | None`
  - `EvalCase` gains `mode: Literal["loaded", "offered"]` (default `"loaded"`) and `judge: JudgeSpec | None`
  - `RunReport.judge_cost_usd` property
  - `CaseMode = Literal["loaded", "offered"]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_models.py`:

```python
import pytest
from pydantic import ValidationError

from skill_eval.models import (
    CaseOutcome,
    CheckResult,
    EvalCase,
    EvalScore,
    JudgeRequest,
    JudgeSpec,
    JudgeVerdict,
    RubricCheck,
    RunReport,
    RunResult,
    TrajectorySpec,
)


def test_a_case_defaults_to_loaded_mode_with_no_judge():
    case = EvalCase(name="c", task="t")
    assert case.mode == "loaded"
    assert case.judge is None


def test_a_case_can_declare_offered_mode_and_a_rubric():
    case = EvalCase(
        name="c",
        task="t",
        mode="offered",
        judge=JudgeSpec(expected="a plain answer", rubric=["names the order id"]),
        trajectory=TrajectorySpec(skill_triggered=True),
    )
    assert case.mode == "offered"
    assert case.judge.rubric == ["names the order id"]
    assert case.trajectory.skill_triggered is True


def test_an_unknown_mode_is_rejected():
    with pytest.raises(ValidationError):
        EvalCase(name="c", task="t", mode="offerred")


def test_a_judge_spec_forbids_unknown_keys():
    # Without extra="forbid" a typo like `rubrics:` yields a vacuously-passing case.
    with pytest.raises(ValidationError):
        JudgeSpec(rubrics=["oops"])


def test_a_result_reports_no_triggering_decision_by_default():
    # None means "this run was not an offered run", which is distinct from False.
    assert RunResult().skill_triggered is None


def test_a_verdict_knows_when_it_errored():
    assert JudgeVerdict().errored is False
    assert JudgeVerdict(error="boom").errored is True


def test_a_request_carries_the_checks_it_wants_graded():
    request = JudgeRequest(
        task="why?",
        output="because",
        checks=[RubricCheck(id="r1", text="explains why")],
    )
    assert [check.id for check in request.checks] == ["r1"]


def test_an_errored_score_cannot_also_be_passed():
    # The two must never disagree: an infra failure is not a green case.
    with pytest.raises(ValidationError):
        EvalScore(evaluator="judge", passed=True, errored=True)


def test_a_score_carries_per_check_evidence():
    score = EvalScore(
        evaluator="judge",
        passed=False,
        checks=[CheckResult(id="r1", passed=False, evidence="never mentions the window")],
    )
    assert score.checks[0].evidence == "never mentions the window"
    assert score.cost_usd == 0.0


def test_judge_cost_is_summed_across_outcomes_and_kept_off_the_run_cost():
    report = RunReport(
        outcomes=[
            CaseOutcome(
                skill_name="s",
                case_name="c",
                runner="fake",
                status="passed",
                scores=[EvalScore(evaluator="judge", passed=True, cost_usd=0.002)],
                result=RunResult(cost_usd=0.01),
            )
        ]
    )
    assert report.judge_cost_usd == pytest.approx(0.002)
    assert report.outcomes[0].result.cost_usd == pytest.approx(0.01)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'CheckResult' from 'skill_eval.models'`

- [ ] **Step 3: Add the models**

In `src/skill_eval/models.py`, change the pydantic import line to include `model_validator`:

```python
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
```

Add beside the existing type aliases near the top:

```python
CaseMode = Literal["loaded", "offered"]
```

Add after `ToolCall`:

```python
class CheckResult(BaseModel):
    """One rubric check's verdict, with the evidence that supports it.

    Evidence is not decoration: an unsupported PASS is the judge's
    characteristic failure mode, so a pass that cannot cite anything is
    recorded as a failure by `JudgeEvaluator`.
    """

    id: str
    passed: bool
    evidence: str = ""


class RubricCheck(BaseModel):
    """One thing a judge must verify, and the id its verdict comes back under."""

    id: str
    text: str


class JudgeRequest(BaseModel):
    """Everything a judge needs, and nothing about eval-case shape.

    A judge grades an output against a list of checks; it does not know what an
    EvalCase is. That keeps the seam reusable and `FakeJudge` trivial.
    """

    task: str
    output: str = ""
    expected: str = ""
    checks: list[RubricCheck] = Field(default_factory=list)


class JudgeOutput(BaseModel):
    """The structured output a judge model must return: verdicts, nothing else.

    Deliberately narrower than `JudgeVerdict` -- tokens, cost and error are
    facts about the call, not things the model gets to assert. Asking the model
    for an overall verdict or a blended score is exactly the failure mode this
    milestone exists to avoid.
    """

    checks: list[CheckResult] = Field(default_factory=list)


class JudgeVerdict(BaseModel):
    """What a judge reports back: per-check verdicts, its own spend, or a failure."""

    checks: list[CheckResult] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cost_note: str = ""
    model: str = ""
    error: str | None = None

    @property
    def errored(self) -> bool:
        """True when the judge itself failed (infra), not when a check did."""
        return self.error is not None
```

Add `skill_triggered` to `RunResult`, directly after `model: str = ""`:

```python
    skill_triggered: bool | None = None
```

...with this docstring note appended to the `RunResult` class docstring:

```python
    """The outcome of running one task against one skill with one runner.

    `skill_triggered` is None outside `mode: offered` -- "this was not a
    triggering run" is a different fact from "the skill was not triggered".
    """
```

Replace `EvalScore` with:

```python
class EvalScore(BaseModel):
    """One evaluator's verdict on one run.

    `errored` marks an infra failure *inside the evaluator* (a judge endpoint
    returning 500, structured output that does not match the rubric). It is not
    an eval signal, so the orchestrator must classify the case errored, never
    failed. `cost_usd` is eval-side spend -- judging is harness overhead and is
    never charged to the skill's budget.
    """

    evaluator: str
    passed: bool
    score: float = 0.0
    detail: str = ""
    checks: list[CheckResult] = Field(default_factory=list)
    errored: bool = False
    cost_usd: float = 0.0

    @model_validator(mode="after")
    def _errored_is_never_passed(self) -> EvalScore:
        if self.errored and self.passed:
            raise ValueError("an errored EvalScore cannot also be passed")
        return self
```

Add `skill_triggered` to `TrajectorySpec`, after `max_calls`:

```python
    skill_triggered: bool | None = None
```

Add after `BudgetSpec`:

```python
class JudgeSpec(BaseModel):
    """What "good" looks like, in prose, for an LLM judge to check.

    `rubric` entries are plain strings; ids are generated positionally by the
    evaluator so authors never have to invent them.
    """

    model_config = ConfigDict(extra="forbid")

    expected: str = ""
    rubric: list[str] = Field(default_factory=list)
```

Add to `EvalCase`, after `budget`:

```python
    mode: CaseMode = "loaded"
    judge: JudgeSpec | None = None
```

Add to `RunReport`:

```python
    @property
    def judge_cost_usd(self) -> float:
        """Eval-side spend, reported apart from what the skill's own runs cost."""
        return sum(score.cost_usd for o in self.outcomes for score in o.scores)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS

Run: `uv run pytest -q`
Expected: PASS — the additions are additive, so nothing existing breaks.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/models.py tests/test_models.py
git commit -m "feat(models): add judge, rubric and triggering shapes"
```

---

### Task 2: Offer a skill as a tool

**Files:**
- Modify: `src/skill_eval/runners/tools.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: `Skill`, `MockTool`, `build_mock_tool` (existing).
- Produces:
  - `skill_tool_name(skill_name: str) -> str` — deterministic identifier, e.g. `"order-support"` → `"order_support"`.
  - `build_skill_tool(skill: Skill) -> MockTool` — no parameters, description = `skill.description`, calling it returns `skill.instructions`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools.py`:

```python
from skill_eval.models import Skill
from skill_eval.runners.tools import build_skill_tool, skill_tool_name


def test_a_skill_name_becomes_a_valid_identifier():
    assert skill_tool_name("order-support") == "order_support"
    assert skill_tool_name("order support") == "order_support"
    assert skill_tool_name("already_fine") == "already_fine"


def test_a_name_that_cannot_start_an_identifier_is_prefixed():
    assert skill_tool_name("123-go").isidentifier()
    assert skill_tool_name("123-go").startswith("skill_")


def test_a_name_with_nothing_usable_falls_back_to_a_stable_default():
    assert skill_tool_name("---") == "skill"
    assert skill_tool_name("") == "skill"


def test_the_offered_tool_describes_the_skill_and_takes_no_arguments():
    tool = build_skill_tool(
        Skill(
            name="order-support",
            description="Handle refund requests",
            instructions="Always look up the order first.",
            path=Path("."),
        )
    )
    assert tool.name == "order_support"
    assert tool.description == "Handle refund requests"
    assert tool.json_schema["properties"] == {}
    assert tool.json_schema["required"] == []


def test_calling_the_offered_tool_delivers_the_skill_instructions():
    # Offered mode has to be honest: an agent that picks the skill must
    # actually receive it, or every later assertion is about an agent acting on
    # instructions it never saw.
    tool = build_skill_tool(
        Skill(name="s", description="d", instructions="Always look it up.", path=Path("."))
    )
    assert tool.call() == "Always look it up."
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_skill_tool'`

- [ ] **Step 3: Implement**

In `src/skill_eval/runners/tools.py`, add `Skill` to the models import:

```python
from skill_eval.models import Skill, ToolSpec
```

Append:

```python
_EMPTY_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def skill_tool_name(skill_name: str) -> str:
    """The identifier a skill is offered under: 'order-support' -> 'order_support'.

    Deterministic, because both the runner (which registers the tool) and the
    case loader (which rejects a case tool that would collide with it) have to
    agree on the answer without talking to each other.
    """
    cleaned = "".join(char if char.isalnum() else "_" for char in skill_name)
    if not cleaned.strip("_"):
        return "skill"
    if cleaned[0].isdigit():
        cleaned = f"skill_{cleaned}"
    return cleaned


def build_skill_tool(skill: Skill) -> MockTool:
    """The skill itself, offered as a tool the agent may decline to use.

    Calling it returns the skill's instructions, so an offered run only has the
    skill once the agent chose it -- and the rest of the run proceeds
    realistically with it loaded, rather than the agent acting on instructions
    it never received.
    """
    instructions = skill.instructions

    def call(**_arguments: Any) -> str:
        return instructions

    return MockTool(
        name=skill_tool_name(skill.name),
        description=skill.description,
        json_schema=dict(_EMPTY_SCHEMA),
        call=call,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tools.py -v`
Expected: PASS — including the existing `test_module_does_not_import_an_agent_framework`.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/runners/tools.py tests/test_tools.py
git commit -m "feat(tools): offer a skill as a tool that returns its instructions"
```

---

### Task 3: Authoring errors for judge and triggering blocks

**Files:**
- Modify: `src/skill_eval/cases/loader.py`
- Test: `tests/test_case_loader.py`

**Interfaces:**
- Consumes: `skill_tool_name` (Task 2), `JudgeSpec` / `mode` / `TrajectorySpec.skill_triggered` (Task 1).
- Produces: `parse_cases_file(path: Path, skill: Skill | None = None) -> list[EvalCase]` — the `skill` argument is optional so existing standalone callers keep working; `load_cases_for_skill` now passes it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_case_loader.py`:

```python
from pathlib import Path

import pytest

from skill_eval.cases.loader import CaseParseError, load_cases_for_skill, parse_cases_file
from skill_eval.models import Skill


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "x.eval.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_judge_block_with_an_empty_rubric_is_an_authoring_error(tmp_path):
    path = write(
        tmp_path,
        """
cases:
  - name: c
    task: t
    judge:
      expected: something good
""",
    )
    with pytest.raises(CaseParseError, match="empty rubric"):
        parse_cases_file(path)


def test_skill_triggered_on_a_loaded_case_is_an_authoring_error(tmp_path):
    # A loaded skill is always in force, so the check could never be false.
    path = write(
        tmp_path,
        """
cases:
  - name: c
    task: t
    trajectory:
      skill_triggered: true
""",
    )
    with pytest.raises(CaseParseError, match="mode: offered"):
        parse_cases_file(path)


def test_skill_triggered_is_accepted_on_an_offered_case(tmp_path):
    path = write(
        tmp_path,
        """
cases:
  - name: c
    task: t
    mode: offered
    trajectory:
      skill_triggered: false
""",
    )
    cases = parse_cases_file(path)
    assert cases[0].trajectory.skill_triggered is False


def test_a_case_tool_colliding_with_the_offered_skill_name_is_an_authoring_error(tmp_path):
    skill = Skill(name="order-support", description="d", instructions="i", path=tmp_path)
    write(
        tmp_path,
        """
cases:
  - name: c
    task: t
    mode: offered
    tools:
      - name: order_support
        description: not the skill
""",
    )
    with pytest.raises(CaseParseError, match="collides"):
        load_cases_for_skill(skill)


def test_the_collision_check_only_applies_to_offered_cases(tmp_path):
    # In loaded mode nothing is offered, so the name is free.
    skill = Skill(name="order-support", description="d", instructions="i", path=tmp_path)
    write(
        tmp_path,
        """
cases:
  - name: c
    task: t
    tools:
      - name: order_support
        description: just a tool
""",
    )
    assert len(load_cases_for_skill(skill)) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_case_loader.py -v`
Expected: FAIL — the four new error cases parse without raising.

- [ ] **Step 3: Implement**

In `src/skill_eval/cases/loader.py`, add the import:

```python
from skill_eval.runners.tools import skill_tool_name
```

Change `parse_cases_file` to accept and forward the skill:

```python
def parse_cases_file(path: Path, skill: Skill | None = None) -> list[EvalCase]:
    """Parse one YAML file into EvalCase models.

    `skill` is optional because a case file can be parsed on its own; it is
    only needed for the checks that depend on what the skill would be offered
    as (see `_validate_cross_references`).
    """
```

...and inside its loop, replace `_validate_cross_references(path, case)` with:

```python
        _validate_cross_references(path, case, skill)
```

Replace `_validate_cross_references` entirely:

```python
def _validate_cross_references(path: Path, case: EvalCase, skill: Skill | None = None) -> None:
    """Catch case-file mistakes that pass schema validation but can never be
    honoured at run time: they are authoring errors, not signals about the
    skill under test, and must abort the run rather than score as a failure.
    """
    seen: set[str] = set()
    for tool in case.tools:
        if tool.name in seen:
            raise CaseParseError(
                f"{path}: case {case.name!r} declares tool {tool.name!r} more than once"
            )
        seen.add(tool.name)

    declared = {tool.name for tool in case.tools}

    if case.judge is not None and not case.judge.rubric:
        raise CaseParseError(
            f"{path}: case {case.name!r} declares a judge block with an empty rubric. "
            f"Give the judge something to check, or remove the block -- an "
            f"unchecked rubric would score as a pass nobody verified."
        )

    if case.mode == "offered" and skill is not None:
        offered = skill_tool_name(skill.name)
        if offered in declared:
            raise CaseParseError(
                f"{path}: case {case.name!r} declares a tool named {offered!r}, which "
                f"collides with the name skill {skill.name!r} is offered under in "
                f"mode: offered. Rename the case's tool."
            )

    if case.trajectory is None:
        return

    if case.trajectory.skill_triggered is not None and case.mode != "offered":
        raise CaseParseError(
            f"{path}: case {case.name!r} sets trajectory.skill_triggered but runs in "
            f"mode {case.mode!r}. A loaded skill is always in force, so the check "
            f"could never be false -- set 'mode: offered'."
        )

    for field_name, names in (
        ("called", case.trajectory.called),
        ("forbidden", case.trajectory.forbidden),
        ("order", case.trajectory.order),
    ):
        for name in names:
            if name not in declared:
                raise CaseParseError(
                    f"{path}: case {case.name!r} trajectory.{field_name} names "
                    f"{name!r}, which is not declared in this case's tools"
                )
```

In `load_cases_for_skill`, change the parse loop to pass the skill:

```python
    cases: list[EvalCase] = []
    for path in paths:
        cases.extend(parse_cases_file(path, skill))
    return cases
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_case_loader.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/cases/loader.py tests/test_case_loader.py
git commit -m "feat(cases): reject judge and triggering blocks that can never hold"
```

---

### Task 4: The `skill_triggered` trajectory check

**Files:**
- Modify: `src/skill_eval/evaluators/trajectory.py`
- Test: `tests/test_trajectory_evaluator.py`

**Interfaces:**
- Consumes: `TrajectorySpec.skill_triggered`, `RunResult.skill_triggered` (Task 1).
- Produces: no new public names. `TrajectoryEvaluator.evaluate` may now return `EvalScore(errored=True)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_trajectory_evaluator.py`:

```python
from skill_eval.evaluators.trajectory import TrajectoryEvaluator
from skill_eval.models import EvalCase, RunResult, TrajectorySpec


def triggering_case(expected: bool) -> EvalCase:
    return EvalCase(
        name="c",
        task="t",
        mode="offered",
        trajectory=TrajectorySpec(skill_triggered=expected),
    )


def test_a_skill_that_triggered_when_it_should_have_passes():
    score = TrajectoryEvaluator().evaluate(
        triggering_case(True), RunResult(skill_triggered=True)
    )
    assert score.passed is True
    assert score.errored is False


def test_a_skill_that_did_not_trigger_when_it_should_have_fails():
    score = TrajectoryEvaluator().evaluate(
        triggering_case(True), RunResult(skill_triggered=False)
    )
    assert score.passed is False
    assert score.errored is False
    assert "not triggered" in score.detail


def test_a_negative_control_fails_when_the_skill_fires_anyway():
    # A positives-only suite scores a skill that fires on everything at 100%.
    score = TrajectoryEvaluator().evaluate(
        triggering_case(False), RunResult(skill_triggered=True)
    )
    assert score.passed is False
    assert "should not" in score.detail


def test_a_negative_control_passes_when_the_skill_stays_out_of_it():
    score = TrajectoryEvaluator().evaluate(
        triggering_case(False), RunResult(skill_triggered=False)
    )
    assert score.passed is True


def test_a_runner_that_reported_no_decision_errors_rather_than_failing():
    # None is "this runner does not do offered mode", which is infra, not a
    # signal about the skill -- it must not read as a skill that misfired.
    score = TrajectoryEvaluator().evaluate(
        triggering_case(True), RunResult(skill_triggered=None)
    )
    assert score.errored is True
    assert score.passed is False
    assert "does not support" in score.detail


def test_the_triggering_check_counts_toward_the_score_fraction():
    case = EvalCase(
        name="c",
        task="t",
        mode="offered",
        trajectory=TrajectorySpec(max_calls=5, skill_triggered=True),
    )
    score = TrajectoryEvaluator().evaluate(case, RunResult(skill_triggered=False))
    assert score.score == 0.5
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_trajectory_evaluator.py -v`
Expected: FAIL — `skill_triggered` is ignored, so every triggering test reports a vacuous pass.

- [ ] **Step 3: Implement**

In `src/skill_eval/evaluators/trajectory.py`, replace `_check`, `_total_checks` and `evaluate`:

```python
def _check(spec: TrajectorySpec, called: list[str], triggered: bool | None) -> list[str]:
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

    if spec.skill_triggered is not None and triggered != spec.skill_triggered:
        failures.append(
            "skill was triggered but should not have been"
            if triggered
            else "skill was not triggered but should have been"
        )

    return failures


def _total_checks(spec: TrajectorySpec) -> int:
    return sum(
        [
            bool(spec.called),
            bool(spec.forbidden),
            bool(spec.order),
            spec.max_calls is not None,
            spec.skill_triggered is not None,
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
        if spec.skill_triggered is not None and result.skill_triggered is None:
            # The runner reported no triggering decision at all. That is an
            # infra fact about the runner, not a signal about the skill, so it
            # must not read as a skill that failed to fire.
            return EvalScore(
                evaluator=self.name,
                passed=False,
                errored=True,
                score=0.0,
                detail=(
                    "trajectory.skill_triggered was declared but the runner reported no "
                    "triggering decision; this runner does not support 'mode: offered'"
                ),
            )
        called = [call.name for call in result.tool_calls]
        failures = _check(spec, called, result.skill_triggered)
        detail = "all trajectory checks held" if not failures else "; ".join(failures)
        return EvalScore(
            evaluator=self.name,
            passed=not failures,
            score=(total - len(failures)) / total,
            detail=detail,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_trajectory_evaluator.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/evaluators/trajectory.py tests/test_trajectory_evaluator.py
git commit -m "feat(trajectory): score whether an offered skill was triggered"
```

---

### Task 5: The judge seam — protocol, prompt, and FakeJudge

**Files:**
- Create: `src/skill_eval/judges/__init__.py`, `src/skill_eval/judges/base.py`,
  `src/skill_eval/judges/prompt.py`, `src/skill_eval/judges/fake.py`
- Test: `tests/test_judge_prompt.py`, `tests/test_fake_judge.py`

**Interfaces:**
- Consumes: `JudgeRequest`, `JudgeVerdict`, `RubricCheck`, `CheckResult` (Task 1).
- Produces:
  - `Judge` protocol with `name: str` and `judge(request: JudgeRequest) -> JudgeVerdict`
  - `skill_eval.judges.prompt.SYSTEM_PROMPT: str`
  - `skill_eval.judges.prompt.render_request(request: JudgeRequest) -> str`
  - `FakeJudge(verdicts: dict[str, JudgeVerdict] | None = None, default: JudgeVerdict | None = None)` with `name = "fake"` and `FakeJudge.NOT_CONFIGURED: str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_judge_prompt.py`:

```python
"""The judge prompt: a pure function, so it is tested as one."""

from skill_eval.judges.prompt import SYSTEM_PROMPT, render_request
from skill_eval.models import JudgeRequest, RubricCheck


def request(**kwargs) -> JudgeRequest:
    kwargs.setdefault("task", "Why can't I return this?")
    kwargs.setdefault("output", "The return window is 30 days.")
    kwargs.setdefault("checks", [RubricCheck(id="r1", text="states the 30-day window")])
    return JudgeRequest(**kwargs)


def test_the_system_prompt_demands_evidence_and_forbids_inventing_ids():
    assert "evidence" in SYSTEM_PROMPT
    assert "id" in SYSTEM_PROMPT


def test_the_rendered_request_carries_task_output_and_every_check():
    text = render_request(request(checks=[
        RubricCheck(id="r1", text="states the 30-day window"),
        RubricCheck(id="r2", text="avoids jargon"),
    ]))
    assert "Why can't I return this?" in text
    assert "The return window is 30 days." in text
    assert "r1: states the 30-day window" in text
    assert "r2: avoids jargon" in text


def test_the_expected_section_is_omitted_when_not_given():
    assert "good response" not in render_request(request())
    assert "good response" in render_request(request(expected="a plain explanation"))


def test_an_empty_output_is_labelled_rather_than_left_blank():
    # A blank section reads as a formatting glitch; the judge must be able to
    # tell "said nothing" apart from "the prompt lost the answer".
    assert "no output" in render_request(request(output=""))
```

Create `tests/test_fake_judge.py`:

```python
"""The offline judge that keeps the zero-cost tier honest."""

from skill_eval.judges.base import Judge
from skill_eval.judges.fake import FakeJudge
from skill_eval.models import CheckResult, JudgeRequest, JudgeVerdict, RubricCheck


def request(task: str = "t") -> JudgeRequest:
    return JudgeRequest(task=task, output="o", checks=[RubricCheck(id="r1", text="x")])


def test_it_satisfies_the_judge_protocol():
    assert isinstance(FakeJudge(), Judge)


def test_an_unscripted_judge_refuses_to_judge_rather_than_inventing_a_pass():
    # This is what makes judge = "fake" safe as the built-in default: a rubric
    # with no real judge configured errors, it never scores green.
    verdict = FakeJudge().judge(request())
    assert verdict.errored is True
    assert "no judge is configured" in verdict.error


def test_a_scripted_verdict_is_returned_for_its_task():
    scripted = JudgeVerdict(checks=[CheckResult(id="r1", passed=True, evidence="said it")])
    verdict = FakeJudge({"t": scripted}).judge(request("t"))
    assert verdict.checks[0].passed is True


def test_a_default_verdict_covers_every_other_task():
    scripted = JudgeVerdict(checks=[CheckResult(id="r1", passed=False, evidence="no")])
    verdict = FakeJudge(default=scripted).judge(request("anything"))
    assert verdict.checks[0].passed is False


def test_a_caller_cannot_corrupt_the_scripted_state():
    scripted = JudgeVerdict(checks=[CheckResult(id="r1", passed=True, evidence="said it")])
    judge = FakeJudge({"t": scripted})
    judge.judge(request("t")).checks[0].evidence = "tampered"
    assert judge.judge(request("t")).checks[0].evidence == "said it"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_judge_prompt.py tests/test_fake_judge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.judges'`

- [ ] **Step 3: Implement**

Create `src/skill_eval/judges/__init__.py` (empty file).

Create `src/skill_eval/judges/base.py`:

```python
"""The Judge protocol — the seam every LLM-as-judge implementation plugs into."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from skill_eval.models import JudgeRequest, JudgeVerdict


@runtime_checkable
class Judge(Protocol):
    """Grades one output against a list of checks."""

    name: str

    def judge(self, request: JudgeRequest) -> JudgeVerdict:
        """Return one verdict per check in `request`, with evidence for each.

        Implementations must not raise for provider failures; they set
        `JudgeVerdict.error` instead, so `JudgeEvaluator` can report an infra
        problem (errored) rather than a low score (failed).

        Implementations must not decide the overall verdict or a blended score:
        skill-eval derives both from the per-check results.
        """
        ...
```

Create `src/skill_eval/judges/prompt.py`:

```python
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
```

Create `src/skill_eval/judges/fake.py`:

```python
"""A deterministic, offline judge used to test the whole pipeline."""

from __future__ import annotations

from skill_eval.models import JudgeRequest, JudgeVerdict


class FakeJudge:
    """Returns scripted verdicts. Never touches the network.

    Unscripted, it refuses to judge rather than inventing a pass. That is what
    makes `judge = "fake"` safe as the built-in default: a case declaring a
    rubric with no real judge configured comes back **errored**, never green.
    Same rule as an unpriceable cost limit in `BudgetEvaluator` — nothing was
    verified, so nothing may be reported as verified.
    """

    name = "fake"

    NOT_CONFIGURED = (
        "no judge is configured, so this case's rubric was never checked. "
        'Set judge = "pydantic-ai" in skill-eval.toml to grade it for real.'
    )

    def __init__(
        self,
        verdicts: dict[str, JudgeVerdict] | None = None,
        default: JudgeVerdict | None = None,
    ) -> None:
        self._verdicts = verdicts or {}
        self._default = default

    def judge(self, request: JudgeRequest) -> JudgeVerdict:
        if request.task in self._verdicts:
            return self._verdicts[request.task].model_copy(deep=True)
        if self._default is not None:
            return self._default.model_copy(deep=True)
        return JudgeVerdict(error=self.NOT_CONFIGURED)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_judge_prompt.py tests/test_fake_judge.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/judges tests/test_judge_prompt.py tests/test_fake_judge.py
git commit -m "feat(judges): add the Judge protocol, prompt renderer and FakeJudge"
```

---

### Task 6: JudgeEvaluator

**Files:**
- Create: `src/skill_eval/evaluators/judge.py`
- Test: `tests/test_judge_evaluator.py`

**Interfaces:**
- Consumes: `Judge` (Task 5), `JudgeSpec`, `JudgeRequest`, `JudgeVerdict`, `CheckResult`, `RubricCheck`, `EvalScore` (Task 1).
- Produces:
  - `build_request(case: EvalCase, result: RunResult) -> JudgeRequest` — ids generated positionally as `r1..rN`.
  - `JudgeEvaluator(judge: Judge)` with `name = "judge"` and `evaluate(case, result) -> EvalScore`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_judge_evaluator.py`:

```python
"""Rubric scoring. The judge is scripted, so every test here is free."""

from skill_eval.evaluators.base import Evaluator
from skill_eval.evaluators.judge import JudgeEvaluator, build_request
from skill_eval.judges.fake import FakeJudge
from skill_eval.models import CheckResult, EvalCase, JudgeSpec, JudgeVerdict, RunResult

RESULT = RunResult(output="The return window is 30 days.")


def judged_case(*rubric: str, expected: str = "") -> EvalCase:
    return EvalCase(
        name="c",
        task="Why can't I return this?",
        judge=JudgeSpec(expected=expected, rubric=list(rubric)),
    )


def verdict(*checks: CheckResult, **kwargs) -> JudgeVerdict:
    return JudgeVerdict(checks=list(checks), **kwargs)


def evaluator(v: JudgeVerdict) -> JudgeEvaluator:
    return JudgeEvaluator(FakeJudge(default=v))


def test_it_satisfies_the_evaluator_protocol():
    assert isinstance(JudgeEvaluator(FakeJudge()), Evaluator)


def test_a_case_with_no_judge_block_is_a_vacuous_pass():
    score = JudgeEvaluator(FakeJudge()).evaluate(EvalCase(name="c", task="t"), RESULT)
    assert score.passed is True
    assert score.score == 1.0
    assert score.detail == "no judge checks"


def test_ids_are_generated_positionally_from_the_rubric():
    request = build_request(judged_case("states the window", "avoids jargon"), RESULT)
    assert [(c.id, c.text) for c in request.checks] == [
        ("r1", "states the window"),
        ("r2", "avoids jargon"),
    ]
    assert request.task == "Why can't I return this?"
    assert request.output == "The return window is 30 days."


def test_every_check_passing_with_evidence_passes_the_case():
    score = evaluator(
        verdict(
            CheckResult(id="r1", passed=True, evidence="'30 days'"),
            CheckResult(id="r2", passed=True, evidence="no jargon present"),
        )
    ).evaluate(judged_case("states the window", "avoids jargon"), RESULT)
    assert score.passed is True
    assert score.score == 1.0
    assert len(score.checks) == 2


def test_the_score_is_the_fraction_of_checks_that_held():
    score = evaluator(
        verdict(
            CheckResult(id="r1", passed=True, evidence="'30 days'"),
            CheckResult(id="r2", passed=False, evidence="says 'RMA'"),
        )
    ).evaluate(judged_case("states the window", "avoids jargon"), RESULT)
    assert score.passed is False
    assert score.score == 0.5
    assert "1 of 2" in score.detail


def test_a_pass_with_no_evidence_is_recorded_as_a_failure():
    # An unsupported PASS is the judge's characteristic failure mode, so it
    # gets a mechanical defence rather than a prompt asking nicely.
    score = evaluator(
        verdict(CheckResult(id="r1", passed=True, evidence="   "))
    ).evaluate(judged_case("states the window"), RESULT)
    assert score.passed is False
    assert score.checks[0].passed is False
    assert "no evidence" in score.checks[0].evidence


def test_a_judge_failure_errors_rather_than_failing():
    # A judge endpoint returning 500 must not look like a skill that got worse.
    score = evaluator(verdict(error="ModelHTTPError: 500")).evaluate(
        judged_case("states the window"), RESULT
    )
    assert score.errored is True
    assert score.passed is False
    assert "500" in score.detail


def test_an_unconfigured_judge_errors_rather_than_passing():
    score = JudgeEvaluator(FakeJudge()).evaluate(judged_case("states the window"), RESULT)
    assert score.errored is True
    assert score.passed is False


def test_verdicts_for_the_wrong_ids_error_rather_than_failing():
    # Structured output that does not match the rubric is the harness
    # misbehaving, not evidence about the skill.
    score = evaluator(
        verdict(CheckResult(id="r9", passed=True, evidence="x"))
    ).evaluate(judged_case("states the window"), RESULT)
    assert score.errored is True
    assert "r1" in score.detail


def test_a_missing_verdict_errors_even_when_the_rest_are_present():
    score = evaluator(
        verdict(CheckResult(id="r1", passed=True, evidence="x"))
    ).evaluate(judged_case("states the window", "avoids jargon"), RESULT)
    assert score.errored is True


def test_duplicate_verdicts_for_one_id_error():
    score = evaluator(
        verdict(
            CheckResult(id="r1", passed=True, evidence="x"),
            CheckResult(id="r1", passed=False, evidence="y"),
        )
    ).evaluate(judged_case("states the window"), RESULT)
    assert score.errored is True


def test_judge_spend_is_carried_on_the_score_not_the_run():
    score = evaluator(
        verdict(CheckResult(id="r1", passed=True, evidence="x"), cost_usd=0.002)
    ).evaluate(judged_case("states the window"), RESULT)
    assert score.cost_usd == 0.002


def test_a_rubric_with_no_checks_errors_rather_than_passing_vacuously():
    # The case loader rejects this, but the evaluator is a public seam and must
    # not report a pass for a rubric it never checked.
    case = EvalCase(name="c", task="t", judge=JudgeSpec(rubric=[]))
    score = JudgeEvaluator(FakeJudge()).evaluate(case, RESULT)
    assert score.errored is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_judge_evaluator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.evaluators.judge'`

- [ ] **Step 3: Implement**

Create `src/skill_eval/evaluators/judge.py`:

```python
"""Scoring what only a model can score: was the answer actually any good?

This module holds rubric logic and nothing else. The `Judge` arrives by
constructor injection, so no agent framework enters `evaluators/`.
"""

from __future__ import annotations

from skill_eval.judges.base import Judge
from skill_eval.models import (
    CheckResult,
    EvalCase,
    EvalScore,
    JudgeRequest,
    RubricCheck,
    RunResult,
)

NO_EVIDENCE = "recorded as failed: passed with no evidence"


def build_request(case: EvalCase, result: RunResult) -> JudgeRequest:
    """Turn a case's judge block into a request, numbering the rubric r1..rN.

    Ids are positional so authors never have to invent them, and each verdict
    still maps back to the check it graded by id rather than by the order the
    model happened to emit them in.
    """
    spec = case.judge
    if spec is None:
        return JudgeRequest(task=case.task, output=result.output)
    return JudgeRequest(
        task=case.task,
        output=result.output,
        expected=spec.expected,
        checks=[
            RubricCheck(id=f"r{index}", text=text)
            for index, text in enumerate(spec.rubric, start=1)
        ],
    )


def _settle(check: CheckResult) -> CheckResult:
    """A pass with no evidence is recorded as a failure."""
    if check.passed and not check.evidence.strip():
        return CheckResult(id=check.id, passed=False, evidence=NO_EVIDENCE)
    return check


class JudgeEvaluator:
    """Every rubric check must hold; the score is the fraction that held.

    skill-eval derives `passed` and `score` from the per-check verdicts. The
    judge is never asked for a blended number, because an unsupported PASS
    hidden inside one is the failure mode this evaluator exists to catch.
    """

    name = "judge"

    def __init__(self, judge: Judge) -> None:
        self._judge = judge

    def _errored(self, detail: str, cost_usd: float = 0.0) -> EvalScore:
        return EvalScore(
            evaluator=self.name,
            passed=False,
            errored=True,
            score=0.0,
            detail=detail,
            cost_usd=cost_usd,
        )

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        if case.judge is None:
            return EvalScore(evaluator=self.name, passed=True, score=1.0, detail="no judge checks")

        request = build_request(case, result)
        if not request.checks:
            return self._errored("a judge block was declared with an empty rubric")

        verdict = self._judge.judge(request)
        if verdict.error is not None:
            return self._errored(f"judge failed: {verdict.error}", verdict.cost_usd)

        wanted = [check.id for check in request.checks]
        got = [check.id for check in verdict.checks]
        if sorted(got) != sorted(wanted):
            return self._errored(
                f"judge returned verdicts for {got or 'nothing'}, expected exactly {wanted}",
                verdict.cost_usd,
            )

        by_id = {check.id: check for check in verdict.checks}
        checks = [_settle(by_id[check_id]) for check_id in wanted]
        held = [check for check in checks if check.passed]
        detail = (
            f"all {len(checks)} rubric checks held"
            if len(held) == len(checks)
            else f"{len(held)} of {len(checks)} rubric checks held"
        )
        return EvalScore(
            evaluator=self.name,
            passed=len(held) == len(checks),
            score=len(held) / len(checks),
            detail=detail,
            checks=checks,
            cost_usd=verdict.cost_usd,
        )
```

Note on the duplicate-id case: `sorted(got) != sorted(wanted)` already rejects
`["r1", "r1"]` against `["r1"]` because the lists differ in length, and rejects
`["r1", "r1"]` against `["r1", "r2"]` because the sorted contents differ. No
separate check is needed.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_judge_evaluator.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/evaluators/judge.py tests/test_judge_evaluator.py
git commit -m "feat(evaluators): score rubrics with evidence-bearing per-check verdicts"
```

---

### Task 7: An errored evaluator errors the case

**Files:**
- Modify: `src/skill_eval/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `EvalScore.errored` (Task 1), `JudgeEvaluator` (Task 6), `FakeJudge` (Task 5).
- Produces: the default evaluator list becomes `[AssertionEvaluator(), TrajectoryEvaluator(), BudgetEvaluator(), JudgeEvaluator(FakeJudge())]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py`:

```python
from pathlib import Path

from skill_eval.models import EvalCase, EvalScore, RunResult, Skill
from skill_eval.orchestrator import run_evals
from skill_eval.runners.fake import FakeRunner


class ErroringEvaluator:
    name = "boom"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        return EvalScore(evaluator=self.name, passed=False, errored=True, detail="judge died")


class PassingEvaluator:
    name = "fine"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        return EvalScore(evaluator=self.name, passed=True, score=1.0)


def write_skill(tmp_path: Path) -> Skill:
    (tmp_path / "SKILL.md").write_text(
        "---\nname: s\ndescription: d\n---\n\nbody\n", encoding="utf-8"
    )
    (tmp_path / "s.eval.yaml").write_text(
        "cases:\n  - name: c\n    task: t\n", encoding="utf-8"
    )
    return Skill(name="s", description="d", instructions="body", path=tmp_path)


def test_an_errored_evaluator_errors_the_case_rather_than_failing_it(tmp_path):
    # A judge endpoint returning 500 must not read as a skill that got worse.
    report = run_evals(
        [write_skill(tmp_path)],
        [FakeRunner()],
        evaluators=[PassingEvaluator(), ErroringEvaluator()],
    )
    assert report.outcomes[0].status == "errored"
    assert report.errored == 1
    assert report.failed == 0


def test_a_merely_failing_evaluator_still_fails_the_case(tmp_path):
    class FailingEvaluator:
        name = "nope"

        def evaluate(self, case, result):
            return EvalScore(evaluator=self.name, passed=False, score=0.0)

    report = run_evals(
        [write_skill(tmp_path)], [FakeRunner()], evaluators=[FailingEvaluator()]
    )
    assert report.outcomes[0].status == "failed"


def test_the_default_evaluators_include_a_judge(tmp_path):
    # Default judging is the offline FakeJudge, so this stays free -- and a
    # case with no judge block is a vacuous pass.
    report = run_evals([write_skill(tmp_path)], [FakeRunner()])
    assert "judge" in [score.evaluator for score in report.outcomes[0].scores]


def test_an_unjudged_rubric_errors_under_the_default_judge(tmp_path):
    skill = write_skill(tmp_path)
    (tmp_path / "s.eval.yaml").write_text(
        "cases:\n  - name: c\n    task: t\n    judge:\n      rubric:\n        - is polite\n",
        encoding="utf-8",
    )
    report = run_evals([skill], [FakeRunner()])
    assert report.outcomes[0].status == "errored"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: FAIL — the errored case reports `failed`, and no `judge` score is present.

- [ ] **Step 3: Implement**

In `src/skill_eval/orchestrator.py`, add the imports:

```python
from skill_eval.evaluators.judge import JudgeEvaluator
from skill_eval.judges.fake import FakeJudge
```

Replace the status calculation in `_run_one`:

```python
    scores = [evaluator.evaluate(case, result) for evaluator in evaluators]
    if any(score.errored for score in scores):
        # An evaluator that blew up (a judge endpoint returning 500, structured
        # output that did not match the rubric) is an infra signal, exactly like
        # a runner that blew up. It must not read as a skill that got worse.
        status = "errored"
    else:
        status = "passed" if all(score.passed for score in scores) else "failed"
```

Replace the default evaluator list in `run_evals`:

```python
    evaluators = (
        evaluators
        if evaluators is not None
        else [
            AssertionEvaluator(),
            TrajectoryEvaluator(),
            BudgetEvaluator(),
            # The offline judge by default: M3 must never start spending money
            # on its own. Unscripted it errors rather than passing, so a rubric
            # with no real judge configured is never a vacuous green.
            JudgeEvaluator(FakeJudge()),
        ]
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: PASS

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): treat an errored evaluator as an errored case"
```

---

### Task 8: PydanticAIJudge

**Files:**
- Create: `src/skill_eval/judges/pydantic_ai.py`
- Test: `tests/test_pydantic_ai_judge.py`

**Interfaces:**
- Consumes: `_require_pydantic_ai`, `_is_transient`, `_model_name`, `DEFAULT_MODEL` from `skill_eval.runners.pydantic_ai`; `calculate_cost`, `provider_of` from `skill_eval.runners.pricing`; `SYSTEM_PROMPT`, `render_request` (Task 5); `JudgeOutput`, `JudgeVerdict` (Task 1).
- Produces: `PydanticAIJudge(model=DEFAULT_MODEL, temperature=0.0, retries=2, retry_backoff_seconds=1.0, sleep=time.sleep)` with `name = "pydantic-ai"` and `needs_api_key = True`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pydantic_ai_judge.py`:

```python
"""The real judge, exercised offline with a scripted model."""

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from skill_eval.judges.base import Judge
from skill_eval.judges.pydantic_ai import PydanticAIJudge
from skill_eval.models import JudgeRequest, RubricCheck

REQUEST = JudgeRequest(
    task="Why can't I return this?",
    output="The return window is 30 days.",
    checks=[RubricCheck(id="r1", text="states the 30-day window")],
)


def structured(checks: list[dict]) -> FunctionModel:
    """A model that answers with the judge's structured-output tool."""

    def reply(messages, info: AgentInfo) -> ModelResponse:
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"checks": checks})])

    return FunctionModel(reply)


def raising(exc: Exception) -> FunctionModel:
    def reply(messages, info: AgentInfo) -> ModelResponse:
        raise exc

    return FunctionModel(reply)


def test_it_satisfies_the_judge_protocol():
    assert isinstance(PydanticAIJudge(model=structured([])), Judge)


def test_it_registers_its_name():
    assert PydanticAIJudge(model=structured([])).name == "pydantic-ai"


def test_the_model_verdicts_become_check_results():
    judge = PydanticAIJudge(
        model=structured([{"id": "r1", "passed": True, "evidence": "'30 days'"}])
    )
    verdict = judge.judge(REQUEST)
    assert verdict.errored is False
    assert [(c.id, c.passed, c.evidence) for c in verdict.checks] == [
        ("r1", True, "'30 days'")
    ]


def test_usage_is_captured():
    judge = PydanticAIJudge(model=structured([{"id": "r1", "passed": True, "evidence": "x"}]))
    verdict = judge.judge(REQUEST)
    assert verdict.input_tokens > 0
    assert verdict.output_tokens > 0


def test_the_rendered_request_reaches_the_model():
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        seen["instructions"] = messages[0].instructions or ""
        seen["prompt"] = str(messages[0].parts[-1].content)
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"checks": []})])

    PydanticAIJudge(model=FunctionModel(reply)).judge(REQUEST)
    assert "evidence" in seen["instructions"]
    assert "r1: states the 30-day window" in seen["prompt"]
    assert "The return window is 30 days." in seen["prompt"]


def test_a_provider_failure_is_reported_not_raised():
    # Judges must never raise: the evaluator turns the error into an errored
    # case, which is an infra signal, not a skill that got worse.
    judge = PydanticAIJudge(model=raising(ModelHTTPError(status_code=500, model_name="m")))
    verdict = judge.judge(REQUEST)
    assert verdict.errored is True
    assert "500" in verdict.error


def test_a_transient_failure_is_retried_before_giving_up():
    attempts = {"n": 0}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ModelHTTPError(status_code=429, model_name="m")
        name = info.output_tools[0].name
        return ModelResponse(
            parts=[ToolCallPart(tool_name=name, args={"checks": [
                {"id": "r1", "passed": True, "evidence": "x"}
            ]})]
        )

    judge = PydanticAIJudge(model=FunctionModel(reply), retries=2, sleep=lambda _: None)
    assert judge.judge(REQUEST).errored is False
    assert attempts["n"] == 3


def test_a_permanent_failure_is_not_retried():
    attempts = {"n": 0}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        attempts["n"] += 1
        raise ModelHTTPError(status_code=401, model_name="m")

    judge = PydanticAIJudge(model=FunctionModel(reply), retries=2, sleep=lambda _: None)
    assert judge.judge(REQUEST).errored is True
    assert attempts["n"] == 1


def test_an_unpriceable_model_degrades_to_a_note_rather_than_erroring():
    judge = PydanticAIJudge(model=structured([{"id": "r1", "passed": True, "evidence": "x"}]))
    verdict = judge.judge(REQUEST)
    assert verdict.errored is False
    assert verdict.cost_usd == 0.0
    assert verdict.cost_note != ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pydantic_ai_judge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.judges.pydantic_ai'`

- [ ] **Step 3: Implement**

Create `src/skill_eval/judges/pydantic_ai.py`:

```python
"""The PydanticAI judge — the second and last module that imports a framework.

Everything the core sees is a plain `JudgeVerdict`. Provider failures are
reported through `JudgeVerdict.error`, never raised, so `JudgeEvaluator` can
tell an infra problem (errored) apart from a low score (failed).

The transient-retry, dependency-check and model-name helpers are imported from
the runner adapter rather than duplicated: both modules are already inside the
framework boundary, and a second copy of the retry policy would be a second
thing to keep in step.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from skill_eval.judges.prompt import SYSTEM_PROMPT, render_request
from skill_eval.models import JudgeOutput, JudgeRequest, JudgeVerdict
from skill_eval.runners.pricing import calculate_cost, provider_of
from skill_eval.runners.pydantic_ai import (
    DEFAULT_MODEL,
    _is_transient,
    _model_name,
    _require_pydantic_ai,
)


class PydanticAIJudge:
    """Grades a rubric with a real model, behind the framework-agnostic protocol."""

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
        """Temperature 0 for determinism; 'unset' for models that reject it."""
        from pydantic_ai.settings import ModelSettings

        if self._temperature == "unset":
            return None
        return ModelSettings(temperature=float(self._temperature))

    def _build_agent(self) -> Any:
        from pydantic_ai import Agent

        return Agent(self._model, instructions=SYSTEM_PROMPT, output_type=JudgeOutput)

    def _run_with_retries(self, agent: Any, prompt: str) -> Any:
        settings = self._model_settings()
        delay = self._retry_backoff_seconds
        attempt = 0
        while True:
            try:
                return agent.run_sync(prompt, model_settings=settings)
            except Exception as exc:
                if attempt >= self._retries or not _is_transient(exc):
                    raise
                self._sleep(delay)
                delay *= 2
                attempt += 1

    def judge(self, request: JudgeRequest) -> JudgeVerdict:
        _require_pydantic_ai()
        configured = self._model if isinstance(self._model, str) else ""
        try:
            result = self._run_with_retries(self._build_agent(), render_request(request))
            messages = result.all_messages()
            usage = result.usage
            model_name = _model_name(messages, configured)
            cost_usd, cost_note = calculate_cost(usage, model_name, provider_of(configured))
            return JudgeVerdict(
                checks=list(result.output.checks),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=cost_usd,
                cost_note=cost_note,
                model=model_name,
            )
        except Exception as exc:
            return JudgeVerdict(error=f"{type(exc).__name__}: {exc}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_pydantic_ai_judge.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/judges/pydantic_ai.py tests/test_pydantic_ai_judge.py
git commit -m "feat(judges): add the PydanticAI judge with structured verdicts"
```

---

### Task 9: `offered` mode in the runner

**Files:**
- Modify: `src/skill_eval/runners/pydantic_ai.py`
- Test: `tests/test_pydantic_ai_runner.py`

**Interfaces:**
- Consumes: `build_skill_tool`, `skill_tool_name` (Task 2); `EvalCase.mode`, `RunResult.skill_triggered` (Task 1).
- Produces: `OFFERED_PREAMBLE: str`; `PydanticAIRunner.run` sets `RunResult.skill_triggered` (a bool in `offered` mode, `None` otherwise).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pydantic_ai_runner.py`:

```python
def offered_case(**kwargs) -> EvalCase:
    kwargs.setdefault("name", "c")
    kwargs.setdefault("task", "refund order 1234")
    kwargs["mode"] = "offered"
    return EvalCase(**kwargs)


def test_a_loaded_run_reports_no_triggering_decision():
    # None is "not a triggering run", which is distinct from "did not trigger".
    result = PydanticAIRunner(model=scripted(text("done"))).run(SKILL, case())
    assert result.skill_triggered is None


def test_an_offered_skill_is_registered_as_a_tool_named_after_it():
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        seen["tools"] = sorted(tool.name for tool in info.function_tools)
        return text("done")

    PydanticAIRunner(model=FunctionModel(reply)).run(
        SKILL, offered_case(tools=[ToolSpec(name="lookup_order")])
    )
    assert seen["tools"] == ["lookup_order", "order_support"]


def test_an_offered_skill_is_not_forced_into_the_system_prompt():
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        seen["instructions"] = messages[0].instructions or ""
        return text("done")

    PydanticAIRunner(model=FunctionModel(reply)).run(SKILL, offered_case())
    assert "Always look up the order first." not in seen["instructions"]


def test_an_offered_skill_that_is_declined_reports_false():
    result = PydanticAIRunner(model=scripted(text("done"))).run(SKILL, offered_case())
    assert result.skill_triggered is False


def test_an_offered_skill_that_is_chosen_reports_true():
    runner = PydanticAIRunner(
        model=scripted(tool_call("order_support", {}), text("done"))
    )
    result = runner.run(SKILL, offered_case())
    assert result.skill_triggered is True


def test_choosing_the_skill_delivers_its_instructions_to_the_model():
    # Offered mode has to be honest: an agent that picks the skill must
    # actually receive it, or every later assertion in the case is fiction.
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        for message in messages:
            for part in getattr(message, "parts", []):
                if getattr(part, "tool_name", None) == "order_support":
                    seen["returned"] = str(getattr(part, "content", ""))
        if "returned" in seen:
            return text("done")
        return tool_call("order_support", {})

    PydanticAIRunner(model=FunctionModel(reply)).run(SKILL, offered_case())
    assert "Always look up the order first." in seen["returned"]


def test_the_offered_tool_call_appears_in_the_trajectory():
    # It counts toward max_calls like any other call: the message history stays
    # the authoritative record of what the model asked for.
    runner = PydanticAIRunner(
        model=scripted(tool_call("order_support", {}), text("done"))
    )
    result = runner.run(SKILL, offered_case())
    assert [call.name for call in result.tool_calls] == ["order_support"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pydantic_ai_runner.py -v`
Expected: FAIL — the skill tool is never registered and `skill_triggered` is always `None`.

- [ ] **Step 3: Implement**

In `src/skill_eval/runners/pydantic_ai.py`, extend the tools import:

```python
from skill_eval.runners.tools import build_mock_tool, build_skill_tool, skill_tool_name
```

Add after `_TRANSIENT_STATUSES`:

```python
# In offered mode the agent must be able to *decline* the skill, so the system
# prompt says nothing about what the skill does -- only that tools exist and
# describe themselves. Anything more would be a nudge, and a nudged trigger
# rate measures the prompt rather than the skill.
OFFERED_PREAMBLE = (
    "You are a helpful assistant. Some capabilities are available to you as tools. "
    "Read their descriptions and use one when it genuinely fits the request. "
    "If none fits, just answer directly."
)
```

Replace `_build_agent`:

```python
    def _build_agent(self, skill: Skill, case: EvalCase) -> Any:
        from pydantic_ai import Agent, Tool

        mocks = [build_mock_tool(spec) for spec in case.tools]
        if case.mode == "offered":
            mocks.append(build_skill_tool(skill))
            instructions = OFFERED_PREAMBLE
        else:
            instructions = _system_prompt(skill)
        tools = [
            Tool.from_schema(
                mock.call,
                name=mock.name,
                description=mock.description,
                json_schema=mock.json_schema,
            )
            for mock in mocks
        ]
        return Agent(self._model, instructions=instructions, tools=tools)
```

In `run`, replace the body of the `try` block's `RunResult` construction so the
triggering decision is recorded:

```python
    def run(self, skill: Skill, case: EvalCase) -> RunResult:
        _require_pydantic_ai()
        configured = self._model if isinstance(self._model, str) else ""
        offered = skill_tool_name(skill.name) if case.mode == "offered" else None
        started = time.monotonic()
        try:
            agent = self._build_agent(skill, case)
            result = self._run_with_retries(agent, case.task)
            messages = result.all_messages()
            usage = result.usage
            model_name = _model_name(messages, configured)
            cost_usd, cost_note = calculate_cost(usage, model_name, provider_of(configured))
            tool_calls = _tool_calls(messages)
            run_result = RunResult(
                output=result.output if isinstance(result.output, str) else str(result.output),
                tool_calls=tool_calls,
                transcript=_transcript(messages),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                latency_ms=int((time.monotonic() - started) * 1000),
                cost_usd=cost_usd,
                cost_note=cost_note,
                model=model_name,
                skill_triggered=(
                    None
                    if offered is None
                    else any(call.name == offered for call in tool_calls)
                ),
            )
        except Exception as exc:
            return RunResult(
                error=f"{type(exc).__name__}: {exc}",
                latency_ms=int((time.monotonic() - started) * 1000),
                model=configured,
            )

        return run_result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_pydantic_ai_runner.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/runners/pydantic_ai.py tests/test_pydantic_ai_runner.py
git commit -m "feat(runner): offer the skill as a tool in mode: offered"
```

---

### Task 10: Config and CLI wiring

**Files:**
- Modify: `src/skill_eval/config.py`, `src/skill_eval/cli.py`
- Test: `tests/test_config.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `FakeJudge` (Task 5), `PydanticAIJudge` (Task 8), `JudgeEvaluator` (Task 6).
- Produces:
  - `Config.judge: str = "fake"`, `Config.judge_model: str = ""`
  - `skill-eval run --judge-model <id>`
  - `cli._JUDGES: dict[str, type]` registry

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
from pathlib import Path

import pytest

from skill_eval.config import ConfigError, load_config


def test_the_built_in_judge_default_never_spends_money():
    settings = load_config()
    assert settings.judge == "fake"
    assert settings.judge_model == ""


def test_a_project_can_opt_into_real_judging(tmp_path):
    (tmp_path / "skill-eval.toml").write_text(
        'judge = "pydantic-ai"\njudge_model = "openai:gpt-4o"\n', encoding="utf-8"
    )
    settings = load_config(path=tmp_path / "skill-eval.toml")
    assert settings.judge == "pydantic-ai"
    assert settings.judge_model == "openai:gpt-4o"


def test_an_unknown_config_key_is_still_rejected(tmp_path):
    (tmp_path / "skill-eval.toml").write_text('judg = "pydantic-ai"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path=tmp_path / "skill-eval.toml")
```

Append to `tests/test_cli.py` (follow the existing file's runner/fixture conventions —
it already builds skill dirs in `tmp_path` and invokes the Typer app through
`typer.testing.CliRunner`):

```python
def test_an_unknown_judge_in_config_is_a_user_error(tmp_path, cli):
    """`cli` is the existing CliRunner-based helper in this file."""
    skill_dir = make_skill(tmp_path)  # existing helper in tests/test_cli.py
    (tmp_path / "skill-eval.toml").write_text('judge = "psychic"\n', encoding="utf-8")
    result = cli(["run", str(skill_dir), "--config", str(tmp_path / "skill-eval.toml")])
    assert result.exit_code == 2
    assert "psychic" in result.output


def test_a_real_judge_without_its_api_key_fails_preflight(tmp_path, cli, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill_dir = make_skill(tmp_path)
    (tmp_path / "skill-eval.toml").write_text(
        'judge = "pydantic-ai"\njudge_model = "openai:gpt-4o-mini"\n', encoding="utf-8"
    )
    result = cli(["run", str(skill_dir), "--config", str(tmp_path / "skill-eval.toml")])
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output


def test_the_judge_model_falls_back_to_the_run_model(tmp_path, cli, monkeypatch):
    # An empty judge_model must not reach the provider as an empty model id.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    skill_dir = make_skill(tmp_path)
    (tmp_path / "skill-eval.toml").write_text(
        'judge = "pydantic-ai"\nmodel = "anthropic:claude-haiku-4-5-20251001"\n',
        encoding="utf-8",
    )
    result = cli(["run", str(skill_dir), "--config", str(tmp_path / "skill-eval.toml")])
    assert result.exit_code == 2
    assert "ANTHROPIC_API_KEY" in result.output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py tests/test_cli.py -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'judge'`

- [ ] **Step 3: Implement**

In `src/skill_eval/config.py`, add to `Config` after `retry_backoff_seconds`:

```python
    judge: str = "fake"
    judge_model: str = ""
```

...and extend the class docstring's second sentence:

```python
    """Run defaults for `skill-eval run`.

    `default_runner` (`--runner`), `model` (`--model`), `judge_model`
    (`--judge-model`) and `min_pass_rate` (`--min-pass-rate`) can be overridden
    by a CLI flag; the rest can only be set here. Secrets are never read from
    this file -- API keys come from the environment only.

    `judge` defaults to "fake" for the same reason `default_runner` does:
    upgrading must never start spending money on its own. An unscripted
    FakeJudge errors rather than passing, so that default cannot turn an
    unchecked rubric into a green case. An empty `judge_model` falls back to
    `model`.
    """
```

In `src/skill_eval/cli.py`, add the imports:

```python
from skill_eval.evaluators.assertion import (
    AssertionEvaluator,
    InvalidAssertionValue,
    UnknownAssertionKind,
)
from skill_eval.evaluators.budget import BudgetEvaluator
from skill_eval.evaluators.judge import JudgeEvaluator
from skill_eval.evaluators.trajectory import TrajectoryEvaluator
from skill_eval.judges.fake import FakeJudge
from skill_eval.judges.pydantic_ai import PydanticAIJudge
```

(The existing `from skill_eval.evaluators.assertion import InvalidAssertionValue,
UnknownAssertionKind` line is replaced by the multi-line form above — it now also
imports `AssertionEvaluator`, which the CLI needs to build the evaluator list.)

Add the registry beside `_RUNNERS`:

```python
_JUDGES = {"fake": FakeJudge, "pydantic-ai": PydanticAIJudge}
```

Add the option to `run`'s signature, after `model`:

```python
    judge_model: Annotated[
        str | None, typer.Option(help="Model id for the LLM judge; defaults to --model.")
    ] = None,
```

Inside the `try` block, after the runner is constructed and before `run_evals`:

```python
        judge_name = settings.judge
        if judge_name not in _JUDGES:
            raise typer.BadParameter(f"unknown judge: {judge_name}")
        judge_class = _JUDGES[judge_name]
        # An empty judge_model means "grade with the same model you run with",
        # so a project opting into real judging only has to name one model.
        resolved_judge_model = (
            judge_model
            if judge_model is not None
            else (settings.judge_model or model_name)
        )
        if getattr(judge_class, "needs_api_key", False):
            check_api_key(resolved_judge_model, os.environ)
            active_judge = judge_class(
                model=resolved_judge_model,
                temperature=settings.temperature,
                retries=settings.retries,
                retry_backoff_seconds=settings.retry_backoff_seconds,
            )
        else:
            active_judge = judge_class()
        evaluators = [
            AssertionEvaluator(),
            TrajectoryEvaluator(),
            BudgetEvaluator(),
            JudgeEvaluator(active_judge),
        ]
        report = run_evals(
            skills, [active_runner], evals_path=evals, tag=tag, evaluators=evaluators
        )
```

`typer.BadParameter` already exits 2, matching the existing unknown-runner path.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py tests/test_cli.py -v`
Expected: PASS

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/config.py src/skill_eval/cli.py tests/test_config.py tests/test_cli.py
git commit -m "feat(cli): select and preflight the judge, with --judge-model"
```

---

### Task 11: Report the evidence and the judge's overhead

**Files:**
- Modify: `src/skill_eval/reporters/console.py`, `src/skill_eval/reporters/json_reporter.py`
- Test: `tests/test_reporters.py`

**Interfaces:**
- Consumes: `EvalScore.checks`, `EvalScore.cost_usd`, `RunReport.judge_cost_usd` (Task 1).
- Produces: no new public names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reporters.py`:

```python
import json

from skill_eval.models import CaseOutcome, CheckResult, EvalScore, RunReport, RunResult
from skill_eval.reporters.console import render_console
from skill_eval.reporters.json_reporter import render_json


def judged_report(**score_kwargs) -> RunReport:
    score = EvalScore(
        evaluator="judge",
        passed=False,
        score=0.5,
        detail="1 of 2 rubric checks held",
        checks=[
            CheckResult(id="r1", passed=True, evidence="'30 days'"),
            CheckResult(id="r2", passed=False, evidence="uses the word 'RMA'"),
        ],
        **score_kwargs,
    )
    return RunReport(
        outcomes=[
            CaseOutcome(
                skill_name="order-support",
                case_name="explains plainly",
                runner="pydantic-ai",
                status="failed",
                scores=[score],
                result=RunResult(output="o", cost_usd=0.01),
            )
        ]
    )


def test_the_console_shows_the_evidence_for_each_failed_check():
    text = render_console(judged_report())
    assert "1 of 2 rubric checks held" in text
    assert "r2" in text
    assert "uses the word 'RMA'" in text


def test_the_console_does_not_repeat_evidence_for_checks_that_held():
    assert "'30 days'" not in render_console(judged_report())


def test_the_console_reports_judge_overhead_apart_from_the_run_cost():
    text = render_console(judged_report(cost_usd=0.002))
    assert "Total cost: $0.0100" in text
    assert "Judge overhead: $0.0020" in text


def test_the_json_report_carries_per_check_verdicts_and_judge_cost():
    payload = json.loads(render_json(judged_report(cost_usd=0.002)))
    assert payload["summary"]["judge_cost_usd"] == 0.002
    checks = payload["outcomes"][0]["scores"][0]["checks"]
    assert [c["id"] for c in checks] == ["r1", "r2"]
    assert checks[1]["evidence"] == "uses the word 'RMA'"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_reporters.py -v`
Expected: FAIL — no evidence lines, no `judge_cost_usd` key.

- [ ] **Step 3: Implement**

In `src/skill_eval/reporters/console.py`, replace the per-score loop:

```python
        for score in outcome.scores:
            if not score.passed:
                lines.append(f"        {score.evaluator}: {score.detail}")
                # The evidence is the point of a judge verdict: a summary line
                # cannot tell an author whether the judge read the response or
                # invented a reason.
                for check in score.checks:
                    if not check.passed:
                        lines.append(
                            f"            {check.id}: {check.evidence or 'no evidence given'}"
                        )
```

...and add the overhead to the totals, immediately after the `total_cost` block:

```python
    judge_cost = report.judge_cost_usd
    if judge_cost:
        totals_parts.append(f"Judge overhead: ${judge_cost:.4f}")
```

(Place it after the `if total_cost: / elif pricing_degraded:` block and before the
`if total_latency_ms:` block, so the totals line reads run cost, judge overhead, latency.)

In `src/skill_eval/reporters/json_reporter.py`, add to the `summary` dict, after
`total_cost_usd`:

```python
            # Kept apart from total_cost_usd: judging is harness overhead and is
            # never charged to the skill's budget.
            "judge_cost_usd": report.judge_cost_usd,
```

`scores` already serializes via `s.model_dump()`, so `checks`, `errored` and
`cost_usd` appear without further change.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_reporters.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/reporters tests/test_reporters.py
git commit -m "feat(reporters): surface rubric evidence and judge overhead"
```

---

### Task 12: The framework-isolation guard

**Files:**
- Create: `tests/test_framework_isolation.py`

**Interfaces:**
- Consumes: every module under `src/skill_eval/`.
- Produces: no runtime names — this task adds only the test that enforces a Global Constraint.

- [ ] **Step 1: Write the failing test**

Create `tests/test_framework_isolation.py`:

```python
"""No agent-framework type may appear outside the two adapter modules.

The rule is about *importing the framework*, not about the string
`pydantic_ai` appearing in a file: `cli.py` legitimately writes
`from skill_eval.runners.pydantic_ai import ...`, which is an import of our own
module. So this matches top-level `import pydantic_ai` / `from pydantic_ai...`
forms only.
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "skill_eval"

ALLOWED = {
    Path("runners/pydantic_ai.py"),
    Path("judges/pydantic_ai.py"),
}

FRAMEWORK_IMPORT = re.compile(r"^\s*(?:from|import)\s+pydantic_ai\b", re.MULTILINE)


def test_only_the_two_adapters_import_the_agent_framework():
    offenders = sorted(
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if path.relative_to(SRC) not in ALLOWED
        and FRAMEWORK_IMPORT.search(path.read_text(encoding="utf-8"))
    )
    assert offenders == []


def test_both_allowed_adapters_actually_exist():
    # Guards against the allowlist quietly outliving the modules it names,
    # which would turn this test into a permanent vacuous pass.
    for relative in ALLOWED:
        assert (SRC / relative).is_file()
```

- [ ] **Step 2: Run the test to verify it passes for the right reason**

Run: `uv run pytest tests/test_framework_isolation.py -v`
Expected: PASS

Now prove it can fail. Temporarily add `import pydantic_ai` at the top of
`src/skill_eval/evaluators/judge.py`, then:

Run: `uv run pytest tests/test_framework_isolation.py -v`
Expected: FAIL with `assert ['evaluators/judge.py'] == []`

Remove the temporary import and re-run:

Run: `uv run pytest tests/test_framework_isolation.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add tests/test_framework_isolation.py
git commit -m "test: enforce that only the two adapters import the agent framework"
```

---

### Task 13: Cassettes, examples and docs

**Files:**
- Modify: `tests/test_cassettes.py`, `examples/order-support/order-support.eval.yaml`,
  `README.md`, `CLAUDE.md`
- Create: `tests/cassettes/test_cassettes/*.yaml` (recorded)

**Interfaces:**
- Consumes: everything built in Tasks 1–12.
- Produces: no new runtime names.

- [ ] **Step 1: Write the failing cassette tests**

Append to `tests/test_cassettes.py`:

```python
from skill_eval.evaluators.judge import JudgeEvaluator
from skill_eval.judges.pydantic_ai import PydanticAIJudge
from skill_eval.models import JudgeRequest, JudgeSpec, RubricCheck

JUDGED_CASE = EvalCase(
    name="explains the refund refusal plainly",
    task="I want a refund for order 1234",
    judge=JudgeSpec(
        expected="A short, plain-language refusal that names the order id.",
        rubric=[
            "The reply names order 1234",
            "The reply explains that the return window has closed",
        ],
    ),
)

OFFERED_POSITIVE = EvalCase(
    name="reaches for the skill on a refund question",
    task="I want a refund for order 1234",
    mode="offered",
    trajectory=TrajectorySpec(skill_triggered=True),
)

OFFERED_NEGATIVE = EvalCase(
    name="leaves an unrelated question alone",
    task="What's the capital of Egypt?",
    mode="offered",
    trajectory=TrajectorySpec(skill_triggered=False),
)


@pytest.mark.cassette
@pytest.mark.vcr
def test_a_real_judge_grades_a_rubric_with_evidence(replay):
    # Only real traffic can prove the model actually fills the structured
    # output shape -- FunctionModel is scripted to fill it by construction.
    request = JudgeRequest(
        task=JUDGED_CASE.task,
        output="Order 1234 was delivered 45 days ago, so the 30-day return window has closed.",
        expected=JUDGED_CASE.judge.expected,
        checks=[
            RubricCheck(id="r1", text=JUDGED_CASE.judge.rubric[0]),
            RubricCheck(id="r2", text=JUDGED_CASE.judge.rubric[1]),
        ],
    )
    verdict = PydanticAIJudge(model="openai:gpt-4o-mini", retries=0).judge(request)

    assert verdict.errored is False
    assert sorted(check.id for check in verdict.checks) == ["r1", "r2"]
    assert all(check.evidence for check in verdict.checks)
    assert verdict.cost_usd > 0


@pytest.mark.cassette
@pytest.mark.vcr
def test_a_real_agent_reaches_for_an_offered_skill(replay):
    result = PydanticAIRunner(model="openai:gpt-4o-mini", retries=0).run(
        SKILL, OFFERED_POSITIVE
    )
    assert result.errored is False
    assert result.skill_triggered is True
    assert TrajectoryEvaluator().evaluate(OFFERED_POSITIVE, result).passed is True


@pytest.mark.cassette
@pytest.mark.vcr
def test_a_real_agent_leaves_an_offered_skill_alone_on_an_unrelated_task(replay):
    # The negative control. Without it, a skill that fires on everything scores
    # 100% on a positives-only suite.
    result = PydanticAIRunner(model="openai:gpt-4o-mini", retries=0).run(
        SKILL, OFFERED_NEGATIVE
    )
    assert result.errored is False
    assert result.skill_triggered is False
    assert TrajectoryEvaluator().evaluate(OFFERED_NEGATIVE, result).passed is True


@pytest.mark.cassette
@pytest.mark.vcr
def test_a_real_judge_drives_the_evaluator_end_to_end(replay):
    result = PydanticAIRunner(model="openai:gpt-4o-mini", retries=0).run(SKILL, JUDGED_CASE)
    score = JudgeEvaluator(PydanticAIJudge(model="openai:gpt-4o-mini", retries=0)).evaluate(
        JUDGED_CASE, result
    )
    assert score.errored is False
    assert score.passed is True
    assert len(score.checks) == 2
    assert score.cost_usd > 0
```

- [ ] **Step 2: Run them and confirm they skip, not fail**

Run: `uv run pytest tests/test_cassettes.py -v`
Expected: the four new tests SKIP with "cassette ... not recorded". A fresh clone
with no recordings must not be a red build.

- [ ] **Step 3: Record the cassettes**

```bash
export OPENAI_API_KEY=<your-key>
uv run pytest tests/test_cassettes.py --record-mode=once
```

Expected: PASS, and four new files under `tests/cassettes/test_cassettes/`.

Then verify the recordings are secret-free before they are ever staged:

```bash
grep -riE 'sk-|authorization|api-key|openai-organization' tests/cassettes/ || echo CLEAN
```

Expected: `CLEAN`. If anything matches, the scrubbing in `tests/conftest.py` did
not cover it — fix the scrub and re-record rather than editing a cassette by hand.

- [ ] **Step 4: Replay them offline**

```bash
uv run env -u OPENAI_API_KEY uv run pytest tests/test_cassettes.py -v
```

Expected: PASS with no network — the `replay` fixture supplies a dummy key.

- [ ] **Step 5: Commit the cassette tier**

```bash
uv run ruff format . && uv run ruff check .
git add tests/test_cassettes.py tests/cassettes
git commit -m "test: record judge and offered-mode traffic for the replay tier"
```

- [ ] **Step 6: Extend the example suite**

Append to `examples/order-support/order-support.eval.yaml`:

```yaml
  # The judge earns its keep where an assertion cannot: "explains it plainly"
  # is not a substring.
  - name: explains the refusal in plain language
    task: I want a refund for order 1234
    tags: [refund, judged]
    tools:
      - name: lookup_order
        description: Look up an order by its id
        parameters:
          order_id: string
        returns: '{"id": "1234", "status": "delivered", "days_since_delivery": 45}'
    judge:
      expected: A short, plain-language refusal that names the order id.
      rubric:
        - The reply names order 1234
        - The reply explains that the return window has closed
        - The reply does not promise a refund

  # Triggering, both directions. Run only the positive and a skill that fires
  # on everything scores 100% -- which is why the negative control ships too.
  - name: reaches for the skill on a refund question
    mode: offered
    task: I want a refund for order 1234
    tags: [triggering]
    trajectory:
      skill_triggered: true

  - name: leaves an unrelated question alone
    mode: offered
    task: What's the capital of Egypt?
    tags: [triggering]
    trajectory:
      skill_triggered: false
```

- [ ] **Step 7: Verify the examples still validate for free**

Run: `uv run skill-eval list ./examples`
Expected: `order-support	5 case(s)	examples/order-support` (and the greeting skill's line).
This is CI's dogfood step: it parses the new `judge:`, `mode:` and `skill_triggered`
keys on real files and exercises the new authoring-error checks, at zero cost.

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 8: Update the docs**

In `README.md`, add a section after the existing trajectory/budget documentation:

````markdown
### Judging output quality

Some things an assertion cannot check — "explains it plainly" is not a substring.
A `judge:` block hands those to an LLM judge, which returns one verdict per rubric
entry **with the evidence for it**:

```yaml
    judge:
      expected: A short, plain-language refusal that names the order id.
      rubric:
        - The reply names order 1234
        - The reply explains that the return window has closed
```

skill-eval derives the verdict and the score from the per-check results; the judge is
never asked for a blended number. **A check that passes without citing evidence is
recorded as a failure** — an unsupported PASS is a judge's characteristic failure mode.

Judging costs money, so it is opted into explicitly:

```toml
judge = "pydantic-ai"
judge_model = ""    # empty falls back to `model`
```

The default `judge = "fake"` does not grade at all — and rather than passing a rubric it
never checked, it reports the case as **errored**. Judge spend is reported as "judge
overhead", separately from what the runs themselves cost, and never counts against a
case's `budget:`.

### Does the agent even reach for the skill?

`mode: offered` stops force-loading the skill and offers it as a tool instead, named
after the skill and described by its frontmatter `description`. If the agent calls it,
it receives the skill's instructions and carries on; if it doesn't, it never sees them.

```yaml
  - name: reaches for the skill on a refund question
    mode: offered
    task: I want a refund for order 1234
    trajectory:
      skill_triggered: true

  - name: leaves an unrelated question alone
    mode: offered
    task: What's the capital of Egypt?
    trajectory:
      skill_triggered: false
```

**Always ship the negative control.** A suite of positives alone scores a skill that
fires on everything at 100%.

Two things to know: the offered tool call appears in `tool_calls` like any other, so it
counts toward `max_calls`; and it is checked with `skill_triggered`, not by naming it in
`called:` (which only accepts tools the case itself declares).
````

In `CLAUDE.md`, update the "What this is" paragraph and the invariants list:

- Change `Currently at **M2**` to `Currently at **M3**`, and note that the pipeline now
  also scores output quality with a rubric-based LLM judge and measures whether an
  offered skill was triggered. Add the M3 design doc path
  (`docs/superpowers/specs/2026-08-03-skill-eval-m3-design.md`).
- Add a third bullet to the two protocols: **`Judge`** (`judges/base.py`) —
  `judge(request) -> JudgeVerdict`. The seam every LLM-as-judge implementation plugs into.
- Change the invariant "**No agent-framework type may appear outside
  `runners/pydantic_ai.py`**" to name **both** `runners/pydantic_ai.py` and
  `judges/pydantic_ai.py`, and mention `tests/test_framework_isolation.py` as its guard.
- Add these invariants:
  - **An errored *evaluator* errors the case.** `errored` ≠ `failed` now applies to
    evaluators too: a judge endpoint returning 500 must not read as a skill that got worse.
  - **Judges never raise for provider failures** — they set `JudgeVerdict.error`.
  - **skill-eval derives `passed` and `score` from per-check verdicts.** The judge is never
    asked for a blended number, and a check that passes without evidence is recorded as a
    failure.
  - **An unscripted `FakeJudge` errors rather than passing.** That is what makes
    `judge = "fake"` safe as the built-in default: an unchecked rubric is never a green case.
  - **Judge spend never enters `RunResult`.** It lives on `EvalScore.cost_usd` and is
    reported as judge overhead; `budget:` measures the skill, not the harness.

- [ ] **Step 9: Final verification and commit**

```bash
uv run ruff format . && uv run ruff check .
uv run pytest -q
uv run skill-eval list ./examples
```

Expected: lint clean, full suite green, examples listed.

```bash
git add examples README.md CLAUDE.md
git commit -m "docs: document rubric judging and offered-mode triggering"
```

---

## Self-Review

**Spec coverage** — every section of `2026-08-03-skill-eval-m3-design.md` maps to a task:

| Spec section | Task(s) |
| --- | --- |
| §3 `judges/` seam (base, prompt, fake) | 5 |
| §3 `PydanticAIJudge`, retries, degrading cost | 8 |
| §4 `JudgeEvaluator`, evidence rule, id-mismatch rule, positional ids | 6 |
| §5 offered mode, synthetic tool, `skill_triggered` | 2, 9 |
| §5 the check and its scoring | 4 |
| §5 error-classification table | 3 (authoring), 4 (errored) |
| §6 case surface, `JudgeSpec`, empty-rubric rejection | 1, 3 |
| §7 model changes, judge cost off `RunResult` | 1, 11 |
| §8 orchestrator errored propagation | 7 |
| §9 config, CLI, preflight, safe default | 10 |
| §10 tier 1 / tier 2 / tier 3 | 1–12 / 13 / 13 |
| §11 examples, CI dogfood step | 13 |
| §12 invariants (framework isolation) | 12 |
| §12 invariants (documented) | 13 |

**Placeholder scan** — no "TBD", no "add error handling", no "similar to Task N". Every
code step carries the code; every command carries its expected output. The one place a
task references existing test helpers (`make_skill`, `cli` in Task 10) names them
explicitly and says they already exist in `tests/test_cli.py`.

**Type consistency** — checked across tasks: `skill_tool_name` / `build_skill_tool`
(Task 2) are used with those exact names in Tasks 3 and 9; `JudgeRequest` /
`JudgeVerdict` / `CheckResult` / `RubricCheck` / `JudgeOutput` (Task 1) are used unchanged
in Tasks 5, 6, 8; `EvalScore.errored` (Task 1) is read in Task 7 and written in Tasks 4
and 6; `RunResult.skill_triggered` is written in Task 9 and read in Task 4;
`RunReport.judge_cost_usd` is defined in Task 1 and read in Task 11; `JudgeEvaluator(judge)`
takes a positional `Judge` in Tasks 6, 7 and 10 alike.

**Ordering** — Tasks 1–7 are framework-free and land the whole feature against `FakeJudge`
and `FakeRunner`; Tasks 8–9 add the paid paths behind seams that already work; Tasks 10–13
wire, report, guard and document. Every task ends green.
