# skill-lens M8 Part 1 — LangChain Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--runner langchain` and `judge = "langchain"` behind the existing `Runner` and `Judge` protocols, installable as a `[langchain]` extra, with the prompt and retry rules both adapters share extracted into two framework-neutral helpers.

**Architecture:** `runners/langchain.py` translates the framework-neutral inputs the PydanticAI adapter already consumes (`AgentTool`s, `instructions()`, `calculate_cost`) into LangChain 1.x's `create_agent` and reads a `RunResult` back out of the agent's message list. `judges/langchain.py` wraps a chat model in `with_structured_output(JudgeOutput, include_raw=True)` and returns a `JudgeVerdict`. Two extractions come first so the rules that `--min-delta` measures against exist once: `runners/prompting.py` (three preambles, the system-prompt builder) and `runners/retry.py` (the transient-retry loop). Tests are offline against a scripted `BaseChatModel` subclass; three cassette tests skip until recorded.

**Tech Stack:** Python 3.11+, Pydantic v2, `langchain>=1.0` (`create_agent`, `init_chat_model`, `StructuredTool`), `langchain-openai`, `langchain-anthropic`, `genai-prices`, pytest, ruff, uv.

**Spec:** `docs/superpowers/specs/2026-09-11-skill-lens-m8-design.md` (§1–§6, §8–§11; §7 is Part 2)

## Global Constraints

- Every commit message and the PR title are Conventional Commits (`feat:`, `test:`, `docs:`, `refactor:`), imperative, lowercase, no trailing period. `cz check` runs on commit.
- No agent-framework import may appear outside `runners/pydantic_ai.py`, `judges/pydantic_ai.py`, `runners/langchain.py`, `judges/langchain.py`. Framework imports inside those four modules are **lazy** (inside functions), so `cli.py` can import the module without the extra installed.
- Runners and judges **never raise** for provider failures: `RunResult.error` / `JudgeVerdict.error`. `RunnerDependencyError` is the one exception that must propagate (it is a setup error, exit 2).
- `skill_lens` (underscore) never appears in user-facing text; the install hint is `pip install 'skill-lens[langchain]'`.
- All file IO pins `encoding="utf-8"`.
- The zero-cost tier is offline and deterministic: pytest runs with `--block-network`; no test here may reach a provider.
- Docs ship with the change: `uv run mkdocs build --strict` and `uv run pytest tests/test_docs.py` must pass at the end.
- Run `uv run ruff check . && uv run ruff format --check .` before every commit; the pre-commit hook runs `cz check` on the message.
- Before running tests after Task 3: `uv sync --all-extras --dev` (the new extra must be installed locally).

---

### Task 1: Extract `runners/prompting.py`

**Files:**
- Create: `src/skill_lens/runners/prompting.py`
- Create: `tests/test_prompting.py`
- Modify: `src/skill_lens/runners/pydantic_ai.py` (remove the three constants, `_system_prompt`, `_instructions`; import `instructions`)
- Modify: `tests/test_pydantic_ai_runner.py` (imports; delete the five pure-function tests that move)
- Modify: `tests/test_cassettes.py:36` (import `BASELINE_PREAMBLE` from `prompting`)

**Interfaces:**
- Produces: `skill_lens.runners.prompting.OFFERED_PREAMBLE: str`, `BASELINE_PREAMBLE: str`, `WORKSPACE_PREAMBLE: str`, `system_prompt(skill: Skill) -> str`, `instructions(skill: Skill, case: EvalCase, has_workspace: bool) -> str`. Tasks 3 and 5 import `instructions`; the cassette tests import `BASELINE_PREAMBLE`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_prompting.py`:

```python
"""The system prompt both adapters build -- pure, no model, no network.

These are the rules `--min-delta` measures against, so they live beside the
one function every runner calls rather than inside either adapter's tests.
"""

from pathlib import Path

from skill_lens.models import EvalCase, Skill
from skill_lens.runners.prompting import (
    BASELINE_PREAMBLE,
    OFFERED_PREAMBLE,
    WORKSPACE_PREAMBLE,
    instructions,
    system_prompt,
)

SKILL = Skill(
    name="order-support",
    description="Handle refund requests",
    instructions="Always look up the order first.",
    path=Path("."),
)

EMPTY_SKILL = Skill(
    name="order-support",
    description="",
    instructions="",
    variant="baseline",
    path=Path("."),
)


def case(**kwargs) -> EvalCase:
    kwargs.setdefault("name", "c")
    kwargs.setdefault("task", "refund order 1234")
    return EvalCase(**kwargs)


def test_the_system_prompt_puts_identity_before_instructions():
    prompt = system_prompt(SKILL)
    assert prompt == "# order-support\n\nHandle refund requests\n\nAlways look up the order first."


def test_a_skill_with_no_description_keeps_its_header_and_instructions():
    skill = Skill(name="terse", description="", instructions="Do it.", path=Path("."))
    assert system_prompt(skill) == "# terse\n\nDo it."


def test_a_skill_with_nothing_to_say_gets_the_neutral_preamble():
    # The rule keys on emptiness, not on the arm: a runner that could branch
    # on the arm could cheat.
    assert system_prompt(EMPTY_SKILL) == BASELINE_PREAMBLE


def test_the_neutral_preamble_never_names_the_skill():
    assert "order-support" not in system_prompt(EMPTY_SKILL)


def test_a_baseline_resolved_from_git_still_gets_its_own_prompt():
    previous = Skill(
        name="order-support",
        description="Handle refunds",
        instructions="Old instructions.",
        variant="baseline",
        path=Path("."),
    )
    assert "Old instructions." in system_prompt(previous)


def test_an_offered_case_gets_only_the_offered_preamble():
    # Anything appended beyond OFFERED_PREAMBLE -- even a hint about what the
    # skill does -- would turn the trigger rate into a measurement of the
    # prompt, not the skill.
    assert instructions(SKILL, case(mode="offered"), has_workspace=False) == OFFERED_PREAMBLE


def test_no_workspace_leaves_the_instructions_untouched():
    plain = instructions(SKILL, case(), has_workspace=False)
    assert plain == system_prompt(SKILL)
    assert WORKSPACE_PREAMBLE not in plain


def test_the_workspace_preamble_is_byte_identical_in_both_arms():
    # If it were added to the candidate arm only, --min-delta would be
    # measuring the preamble rather than the skill.
    candidate = instructions(SKILL, case(), has_workspace=True)
    baseline = instructions(EMPTY_SKILL, case(), has_workspace=True)
    assert candidate.endswith(WORKSPACE_PREAMBLE)
    assert baseline.endswith(WORKSPACE_PREAMBLE)
    assert baseline == f"{BASELINE_PREAMBLE}\n\n{WORKSPACE_PREAMBLE}"


def test_the_workspace_preamble_never_names_the_skill():
    assert "order-support" not in WORKSPACE_PREAMBLE
    assert EMPTY_SKILL.name not in instructions(EMPTY_SKILL, case(), has_workspace=True)


def test_an_offered_case_keeps_its_own_preamble_and_gains_the_workspace_one():
    offered = instructions(SKILL, case(mode="offered"), has_workspace=True)
    assert offered == f"{OFFERED_PREAMBLE}\n\n{WORKSPACE_PREAMBLE}"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_prompting.py -q`
Expected: `ModuleNotFoundError: No module named 'skill_lens.runners.prompting'`

- [ ] **Step 3: Create the module**

Create `src/skill_lens/runners/prompting.py`:

```python
"""The system prompt each arm of a case receives -- shared by every adapter.

These rules are what `--min-delta` measures against: the baseline arm must
never see the skill's name, and the workspace preamble must be byte-identical
in both arms. One function every runner calls is how two adapters stay in
step; a second copy could drift, and the delta would then measure the drift.
This module imports no agent framework, which is what lets it sit outside the
adapter boundary `tests/test_framework_isolation.py` guards.
"""

from __future__ import annotations

from skill_lens.models import EvalCase, Skill

# In offered mode the agent must be able to *decline* the skill, so the system
# prompt says nothing about what the skill does -- only that tools exist and
# describe themselves. Anything more would be a nudge, and a nudged trigger
# rate measures the prompt rather than the skill.
OFFERED_PREAMBLE = (
    "You are a helpful assistant. Some capabilities are available to you as tools. "
    "Read their descriptions and use one when it genuinely fits the request. "
    "If none fits, just answer directly."
)

# A skill with no description and no instructions has nothing to say. Emitting
# the usual `# {name}` header anyway would put the skill's name into a baseline
# run's prompt, and the delta would then measure that leak rather than the
# skill. The rule keys on emptiness, not on the arm, so no runner has to know
# which arm it is serving -- a runner that *could* branch on the arm could cheat.
BASELINE_PREAMBLE = "You are a helpful assistant."

# Appended to whatever preamble the arm already uses, byte-identically in both
# arms, and naming no skill. The agent has to be told a working directory
# exists or it cannot use it; added to the candidate arm only, this text would
# become part of what --min-delta measures.
WORKSPACE_PREAMBLE = (
    "You have a working directory. Use `list_files` to see what is in it, "
    "`read_file` to read a file, and `write_file` to create or replace one. "
    "All paths are relative to that directory."
)


def system_prompt(skill: Skill) -> str:
    """The skill, as the agent sees it: identity first, then its instructions."""
    if not skill.description and not skill.instructions:
        return BASELINE_PREAMBLE
    header = f"# {skill.name}"
    if skill.description:
        header = f"{header}\n\n{skill.description}"
    return f"{header}\n\n{skill.instructions}".strip()


def instructions(skill: Skill, case: EvalCase, has_workspace: bool) -> str:
    """The full system prompt for one arm of one case.

    Kept apart from any agent construction so the arm-identical rule above can
    be tested without a model, a provider or a network.
    """
    base = OFFERED_PREAMBLE if case.mode == "offered" else system_prompt(skill)
    return f"{base}\n\n{WORKSPACE_PREAMBLE}" if has_workspace else base
```

- [ ] **Step 4: Point the PydanticAI adapter at it**

In `src/skill_lens/runners/pydantic_ai.py`:

1. Delete the `OFFERED_PREAMBLE`, `BASELINE_PREAMBLE`, `WORKSPACE_PREAMBLE` blocks (constants and their comments) and the `_system_prompt` and `_instructions` functions.
2. Add to the imports: `from skill_lens.runners.prompting import instructions`
3. In `_build_agent`, replace `instructions=_instructions(skill, case, workspace is not None),` with `instructions=instructions(skill, case, workspace is not None),`.

- [ ] **Step 5: Move the test imports**

In `tests/test_pydantic_ai_runner.py`:

1. Replace the `from skill_lens.runners.pydantic_ai import (...)` block with:

```python
from skill_lens.runners.prompting import BASELINE_PREAMBLE, OFFERED_PREAMBLE, WORKSPACE_PREAMBLE
from skill_lens.runners.pydantic_ai import PydanticAIRunner
```

2. Delete these five tests, now in `tests/test_prompting.py`: `test_no_workspace_leaves_the_instructions_untouched`, `test_the_workspace_preamble_is_byte_identical_in_both_arms`, `test_the_workspace_preamble_never_names_the_skill`, `test_an_offered_case_keeps_its_own_preamble_and_gains_the_workspace_one`, and (pure-function duplicate of the model-driven one that stays) nothing else — the model-driven tests `test_a_skill_with_nothing_to_say_gets_a_neutral_preamble`, `test_a_baseline_prompt_never_leaks_the_skill_name`, `test_a_baseline_resolved_from_git_still_gets_its_own_prompt`, `test_an_offered_skill_is_not_forced_into_the_system_prompt` **stay**: they prove the adapter delivers the prompt to the model, which the pure tests cannot.

In `tests/test_cassettes.py` line 36, replace:

```python
from skill_lens.runners.pydantic_ai import BASELINE_PREAMBLE, PydanticAIRunner
```

with:

```python
from skill_lens.runners.prompting import BASELINE_PREAMBLE
from skill_lens.runners.pydantic_ai import PydanticAIRunner
```

- [ ] **Step 6: Run the suite**

Run: `uv run pytest tests/test_prompting.py tests/test_pydantic_ai_runner.py tests/test_cassettes.py tests/test_framework_isolation.py -q`
Expected: all pass (cassette tests replay from their recordings). Then `uv run ruff check . && uv run ruff format --check .` clean.

- [ ] **Step 7: Commit**

```bash
git add src/skill_lens/runners/prompting.py src/skill_lens/runners/pydantic_ai.py tests/test_prompting.py tests/test_pydantic_ai_runner.py tests/test_cassettes.py
git commit -m "refactor: move the system-prompt rules out of the PydanticAI adapter"
```

---

### Task 2: Extract `runners/retry.py` and move `RunnerDependencyError`

**Files:**
- Create: `src/skill_lens/runners/retry.py`
- Create: `tests/test_retry.py`
- Modify: `src/skill_lens/runners/base.py` (add `RunnerDependencyError`)
- Modify: `src/skill_lens/runners/pydantic_ai.py` (use `run_with_retries`, `transient_status`; import the error from `base`)
- Modify: `src/skill_lens/judges/pydantic_ai.py` (use `run_with_retries`)
- Modify: `src/skill_lens/cli.py:33` (import `RunnerDependencyError` from `base`)

**Interfaces:**
- Produces: `skill_lens.runners.retry.TRANSIENT_STATUSES: frozenset[int]`, `transient_status(status_code: int) -> bool`, `run_with_retries(call: Callable[[], T], is_transient: Callable[[Exception], bool], retries: int, backoff_seconds: float, sleep: Callable[[float], None]) -> T`; `skill_lens.runners.base.RunnerDependencyError`. Tasks 3–5 use all of them.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_retry.py`:

```python
"""The transient-retry loop every adapter shares."""

import pytest

from skill_lens.runners.retry import TRANSIENT_STATUSES, run_with_retries, transient_status


class Transient(Exception):
    pass


class Permanent(Exception):
    pass


def is_transient(exc: Exception) -> bool:
    return isinstance(exc, Transient)


def test_a_call_that_succeeds_is_returned_without_sleeping():
    slept = []
    value = run_with_retries(lambda: "ok", is_transient, 2, 1.0, slept.append)
    assert value == "ok"
    assert slept == []


def test_a_transient_failure_is_retried_and_can_succeed():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise Transient()
        return "recovered"

    slept = []
    assert run_with_retries(flaky, is_transient, 2, 0.01, slept.append) == "recovered"
    assert slept == [0.01]


def test_backoff_doubles_between_attempts_and_the_last_failure_is_raised():
    def always():
        raise Transient("still down")

    slept = []
    with pytest.raises(Transient, match="still down"):
        run_with_retries(always, is_transient, 3, 1.0, slept.append)
    assert slept == [1.0, 2.0, 4.0]


def test_a_permanent_failure_is_raised_at_once():
    attempts = {"n": 0}

    def unauthorized():
        attempts["n"] += 1
        raise Permanent()

    slept = []
    with pytest.raises(Permanent):
        run_with_retries(unauthorized, is_transient, 3, 0.01, slept.append)
    assert attempts["n"] == 1
    assert slept == []


def test_zero_retries_means_one_attempt():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        raise Transient()

    with pytest.raises(Transient):
        run_with_retries(flaky, is_transient, 0, 0.01, lambda _: None)
    assert attempts["n"] == 1


@pytest.mark.parametrize("status", sorted(TRANSIENT_STATUSES) + [500, 502, 503, 599])
def test_rate_limits_timeouts_conflicts_and_server_errors_are_transient(status):
    assert transient_status(status) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_not_transient(status):
    assert transient_status(status) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_retry.py -q`
Expected: `ModuleNotFoundError: No module named 'skill_lens.runners.retry'`

- [ ] **Step 3: Create the module**

Create `src/skill_lens/runners/retry.py`:

```python
"""Retry a provider call on transient failures -- shared by every adapter.

The policy is one function so two adapters cannot disagree about what "try
again" means. Each adapter supplies its own `is_transient`, because what a
rate limit or an outage looks like is spelled differently by every framework
-- and this module imports none of them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

# Statuses worth another attempt: request timeouts, conflicts and rate limits.
# A 401 or 404 will never fix itself.
TRANSIENT_STATUSES = frozenset({408, 409, 429})


def transient_status(status_code: int) -> bool:
    """Whether an HTTP status is worth another attempt.

    The three listed statuses, plus anything the provider blames on itself.
    """
    return status_code in TRANSIENT_STATUSES or status_code >= 500


def run_with_retries(
    call: Callable[[], T],
    is_transient: Callable[[Exception], bool],
    retries: int,
    backoff_seconds: float,
    sleep: Callable[[float], None],
) -> T:
    """Call `call()`; on a transient exception, sleep and try again.

    Up to `retries` further attempts are made, sleeping `backoff_seconds`
    before the first and doubling before each one after. A non-transient
    exception, or the last attempt failing, re-raises as-is so the adapter can
    report it through `RunResult.error` / `JudgeVerdict.error`.
    """
    delay = backoff_seconds
    attempt = 0
    while True:
        try:
            return call()
        except Exception as exc:
            if attempt >= retries or not is_transient(exc):
                raise
            sleep(delay)
            delay *= 2
            attempt += 1
```

- [ ] **Step 4: Move `RunnerDependencyError` to `runners/base.py`**

Append to `src/skill_lens/runners/base.py`:

```python


class RunnerDependencyError(Exception):
    """Raised when the optional extra providing a runner or judge is not installed.

    A setup error, not a provider failure: `cli.py` turns it into a clean exit
    2 with the install hint, so an adapter must let it propagate rather than
    swallow it into `RunResult.error`.
    """
```

In `src/skill_lens/runners/pydantic_ai.py`: delete the `class RunnerDependencyError` definition and add `from skill_lens.runners.base import RunnerDependencyError` to the imports (it is used by `_require_pydantic_ai`, so the import is not unused; `tests/test_pydantic_ai_runner.py` reaches it as `adapter.RunnerDependencyError`, which still resolves).

In `src/skill_lens/cli.py` line 33, replace:

```python
from skill_lens.runners.pydantic_ai import PydanticAIRunner, RunnerDependencyError
```

with:

```python
from skill_lens.runners.base import RunnerDependencyError
from skill_lens.runners.pydantic_ai import PydanticAIRunner
```

- [ ] **Step 5: Switch both PydanticAI adapters to the shared loop**

In `src/skill_lens/runners/pydantic_ai.py`:

1. Delete `_TRANSIENT_STATUSES = {408, 409, 429}` and its comment.
2. Add `from skill_lens.runners.retry import run_with_retries, transient_status` to the imports.
3. Replace the body of `_is_transient` so the status line reads:

```python
    if isinstance(exc, ModelHTTPError):
        return transient_status(exc.status_code)
```

4. Replace the whole `_run_with_retries` method with:

```python
    def _run_with_retries(self, agent: Any, task: str) -> Any:
        settings = self._model_settings()
        return run_with_retries(
            lambda: agent.run_sync(task, model_settings=settings),
            _is_transient,
            self._retries,
            self._retry_backoff_seconds,
            self._sleep,
        )
```

In `src/skill_lens/judges/pydantic_ai.py`:

1. Add `from skill_lens.runners.retry import run_with_retries` to the imports.
2. Replace the whole `_run_with_retries` method with:

```python
    def _run_with_retries(self, agent: Any, prompt: str) -> Any:
        settings = self._model_settings()
        return run_with_retries(
            lambda: agent.run_sync(prompt, model_settings=settings),
            _is_transient,
            self._retries,
            self._retry_backoff_seconds,
            self._sleep,
        )
```

3. Update the module docstring's third paragraph to: `The transient rule, dependency check and model-name helpers are imported from the runner adapter rather than duplicated, and the retry loop itself comes from `runners/retry.py`: both modules are already inside the framework boundary, and a second copy of the policy would be a second thing to keep in step.`

- [ ] **Step 6: Run the suite**

Run: `uv run pytest tests/test_retry.py tests/test_pydantic_ai_runner.py tests/test_pydantic_ai_judge.py tests/test_cli.py tests/test_framework_isolation.py -q`
Expected: all pass — the retry and backoff tests in the two adapter files exercise the shared loop unchanged. Then ruff clean.

- [ ] **Step 7: Commit**

```bash
git add src/skill_lens/runners/retry.py src/skill_lens/runners/base.py src/skill_lens/runners/pydantic_ai.py src/skill_lens/judges/pydantic_ai.py src/skill_lens/cli.py tests/test_retry.py
git commit -m "refactor: share the retry loop and the dependency error between adapters"
```

---

### Task 3: The `[langchain]` extra, the scripted model, and `LangChainRunner`'s happy path

**Files:**
- Modify: `pyproject.toml:53-54` (extras), then `uv.lock` via `uv lock`
- Create: `tests/langchain_fakes.py`
- Create: `src/skill_lens/runners/langchain.py`
- Create: `tests/test_langchain_runner.py`
- Modify: `tests/test_framework_isolation.py` (second pattern; allow `runners/langchain.py`)

**Interfaces:**
- Consumes: `instructions` (Task 1), `run_with_retries`, `transient_status`, `RunnerDependencyError` (Task 2), `AgentTool`/`build_mock_tool`/`build_skill_tool`/`build_workspace_tools`/`skill_tool_name` from `runners/tools.py`, `calculate_cost`/`provider_of` from `runners/pricing.py`.
- Produces: `skill_lens.runners.langchain.LangChainRunner(model, temperature=0.0, retries=2, retry_backoff_seconds=1.0, sleep=time.sleep)` with `name = "langchain"`, `needs_api_key = True`, `run(skill, case, workspace=None) -> RunResult`; module helpers `DEFAULT_MODEL`, `_require_langchain()`, `_is_transient(exc)`, `_chat_model(model, temperature)`, `_usage(messages) -> tuple[int, int]`, `_model_name(messages, fallback) -> str`, `_cost(input_tokens, output_tokens, model_name, configured) -> tuple[float, str]` — Task 5's judge imports the last six. `tests.langchain_fakes.FunctionChatModel`, `scripted(*replies)`, `text(content, **kw)`, `tool_call(name, args, **kw)`, `StatusError(status_code)`.

- [ ] **Step 1: Declare the extra and lock it**

In `pyproject.toml`, replace:

```toml
[project.optional-dependencies]
pydantic-ai = ["pydantic-ai-slim[openai]>=2.22"]
```

with:

```toml
[project.optional-dependencies]
# Anthropic is in both so a matrix run can name one --model every runner accepts.
pydantic-ai = ["pydantic-ai-slim[openai,anthropic]>=2.22"]
langchain = [
    "langchain>=1.0",
    "langchain-openai>=1.0",
    "langchain-anthropic>=1.0",
    # `runners/pricing.py` imports it; without this line it only arrives as a
    # transitive dependency of pydantic-ai, and a [langchain]-only install
    # would stamp every case with "genai-prices is not installed".
    "genai-prices>=0.1",
]
```

Run: `uv lock && uv sync --all-extras --dev`
Expected: `uv.lock` updated; `uv run python -c "import langchain, langchain_openai, langchain_anthropic, genai_prices; print('ok')"` prints `ok`.

- [ ] **Step 2: Write the scripted model**

Create `tests/langchain_fakes.py`:

```python
"""A scripted chat model for the LangChain adapters' zero-cost tests.

The counterpart of PydanticAI's `FunctionModel`. langchain-core's own
`GenericFakeChatModel` cannot be used: `create_agent` calls `bind_tools`, and
`with_structured_output` refuses a model that has not overridden it -- the
generic fake overrides neither. This subclass answers each turn by calling
`reply(messages, turn)`, records what it was asked and which tools were
bound, and lets `bind_tools` return itself so both the agent loop and the
judge's structured output can be driven offline.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolCall
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

Reply = Callable[[list[BaseMessage], int], AIMessage]

# Every scripted reply carries usage so the adapter's token arithmetic is
# exercised; a real provider always sends it.
USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


class StatusError(Exception):
    """An exception shaped like the openai/anthropic SDKs' HTTP errors."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class FunctionChatModel(BaseChatModel):
    """Answers turn `n` with `reply(messages, n)`; `bind_tools` returns itself."""

    reply: Reply
    turns: list[list[BaseMessage]] = Field(default_factory=list)
    bound_tools: list[list[str]] = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        turn = len(self.turns)
        self.turns.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.reply(list(messages), turn))])

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self.bound_tools.append([getattr(tool, "name", getattr(tool, "__name__", "")) for tool in tools])
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted"


def scripted(*replies: AIMessage) -> FunctionChatModel:
    """A model that replays `replies` in order, then repeats the last one."""

    def reply(messages: list[BaseMessage], turn: int) -> AIMessage:
        return replies[min(turn, len(replies) - 1)]

    return FunctionChatModel(reply=reply)


def text(content: str, **metadata: Any) -> AIMessage:
    """A plain text reply. `model_name=` lands in `response_metadata`."""
    return AIMessage(content=content, usage_metadata=dict(USAGE), response_metadata=dict(metadata))


def tool_call(name: str, args: dict[str, Any], **metadata: Any) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[ToolCall(name=name, args=args, id=f"call-{name}")],
        usage_metadata=dict(USAGE),
        response_metadata=dict(metadata),
    )


def system_text(turn: list[BaseMessage]) -> str:
    """The system prompt the model saw on one turn ('' when there was none)."""
    return str(turn[0].content) if turn and turn[0].type == "system" else ""


def tool_results(turn: list[BaseMessage]) -> dict[str, str]:
    """Every tool result the model saw on one turn, by tool name."""
    return {str(m.name): str(m.content) for m in turn if m.type == "tool"}
```

- [ ] **Step 3: Write the failing tests for the happy path**

Create `tests/test_langchain_runner.py`:

```python
"""The second real adapter, exercised offline with a scripted model."""

from pathlib import Path

import pytest

from langchain_fakes import scripted, system_text, text, tool_call, tool_results
from skill_lens.models import EvalCase, Skill, ToolSpec
from skill_lens.runners.base import Runner
from skill_lens.runners.langchain import LangChainRunner

SKILL = Skill(
    name="order-support",
    description="Handle refund requests",
    instructions="Always look up the order first.",
    path=Path("."),
)


def case(**kwargs) -> EvalCase:
    kwargs.setdefault("name", "c")
    kwargs.setdefault("task", "refund order 1234")
    return EvalCase(**kwargs)


def test_the_final_text_becomes_the_output():
    result = LangChainRunner(model=scripted(text("Order 1234 was delivered."))).run(SKILL, case())
    assert result.output == "Order 1234 was delivered."
    assert result.errored is False


def test_the_runner_registers_its_name_and_declares_that_it_needs_a_key():
    assert LangChainRunner(model=scripted(text("x"))).name == "langchain"
    assert LangChainRunner.needs_api_key is True


def test_langchain_runner_satisfies_the_runner_protocol():
    assert isinstance(LangChainRunner(model=scripted(text("x"))), Runner)


def test_declared_tools_are_offered_to_the_model():
    model = scripted(text("done"))
    LangChainRunner(model=model).run(
        SKILL, case(tools=[ToolSpec(name="lookup_order"), ToolSpec(name="issue_refund")])
    )
    assert sorted(model.bound_tools[0]) == ["issue_refund", "lookup_order"]


def test_the_skill_instructions_reach_the_model_as_the_system_prompt():
    model = scripted(text("done"))
    LangChainRunner(model=model).run(SKILL, case())
    seen = system_text(model.turns[0])
    assert "Always look up the order first." in seen
    assert "order-support" in seen


def test_the_task_reaches_the_model():
    model = scripted(text("done"))
    LangChainRunner(model=model).run(SKILL, case(task="refund order 1234"))
    assert model.turns[0][-1].content == "refund order 1234"


def test_tool_calls_are_captured_in_order_with_their_arguments():
    runner = LangChainRunner(
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
    assert result.output == "Refund declined."


def test_the_canned_return_value_is_handed_back_to_the_model():
    model = scripted(tool_call("lookup_order", {"order_id": "1234"}), text("done"))
    LangChainRunner(model=model).run(
        SKILL, case(tools=[ToolSpec(name="lookup_order", returns='{"status": "shipped"}')])
    )
    assert tool_results(model.turns[1]) == {"lookup_order": '{"status": "shipped"}'}


def test_a_model_omitting_a_required_tool_argument_does_not_error_the_case():
    # The JSON schema is descriptive only: LangChain passes a dict schema's
    # arguments straight through, and every AgentTool accepts any arguments.
    # A model choosing to omit one is captured faithfully, never turned into
    # an infra error.
    runner = LangChainRunner(model=scripted(tool_call("lookup_order", {}), text("done")))
    result = runner.run(
        SKILL, case(tools=[ToolSpec(name="lookup_order", parameters={"order_id": "string"})])
    )
    assert result.errored is False
    assert result.tool_calls[0].arguments == {}
    assert result.output == "done"


def test_usage_is_summed_over_every_model_turn():
    runner = LangChainRunner(model=scripted(tool_call("ping", {}), text("done")))
    result = runner.run(SKILL, case(tools=[ToolSpec(name="ping")]))
    # Two turns, 10 in / 5 out each (tests/langchain_fakes.py USAGE).
    assert result.input_tokens == 20
    assert result.output_tokens == 10
    assert result.tokens == 30
    assert result.latency_ms >= 0


def test_the_transcript_is_the_whole_message_list_as_plain_dicts():
    result = LangChainRunner(model=scripted(text("done"))).run(SKILL, case())
    # human task, then the model's reply -- the system prompt is create_agent's,
    # not a message in the state.
    assert [m["type"] for m in result.transcript] == ["human", "ai"]
    assert isinstance(result.transcript[0], dict)


def test_the_served_model_name_is_read_from_the_last_response_that_has_one():
    runner = LangChainRunner(
        model=scripted(tool_call("ping", {}, model_name="gpt-4o-mini-2026-01-01"), text("done"))
    )
    result = runner.run(SKILL, case(tools=[ToolSpec(name="ping")]))
    assert result.model == "gpt-4o-mini-2026-01-01"


def test_a_model_instance_with_no_served_name_reports_an_empty_model():
    # A string model falls back to the configured id; an instance has none.
    result = LangChainRunner(model=scripted(text("done"))).run(SKILL, case())
    assert result.model == ""


def test_an_unpriced_model_reports_zero_cost_with_a_note():
    result = LangChainRunner(model=scripted(text("done"))).run(SKILL, case())
    assert result.cost_usd == 0.0
    assert "no price data" in result.cost_note


def test_a_loaded_run_reports_no_triggering_decision():
    result = LangChainRunner(model=scripted(text("done"))).run(SKILL, case())
    assert result.skill_triggered is None
```

Note `from langchain_fakes import ...`: `tests/` is on `sys.path` through pytest's rootdir conftest (the existing tests import `conftest` helpers the same way). If the import fails, add `pythonpath = ["tests"]` under `[tool.pytest.ini_options]` in `pyproject.toml`.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_langchain_runner.py -q`
Expected: `ModuleNotFoundError: No module named 'skill_lens.runners.langchain'`

- [ ] **Step 5: Create the adapter**

Create `src/skill_lens/runners/langchain.py`:

```python
"""The LangChain adapter -- the third module that imports an agent framework.

Everything the core sees is a plain `RunResult`. Provider failures are reported
through `RunResult.error`, never raised, so the orchestrator can tell an infra
problem (errored) apart from a low score (failed). The prompt rules and the
retry loop come from `runners/prompting.py` and `runners/retry.py`, which is
what keeps this adapter and the PydanticAI one measuring the same thing.

Framework imports are inside functions so `cli.py` can import this module --
to register the runner -- without the `langchain` extra installed.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from skill_lens.models import EvalCase, RunResult, Skill, ToolCall
from skill_lens.runners.base import RunnerDependencyError
from skill_lens.runners.pricing import calculate_cost, provider_of
from skill_lens.runners.prompting import instructions
from skill_lens.runners.retry import run_with_retries, transient_status
from skill_lens.runners.tools import (
    AgentTool,
    build_mock_tool,
    build_skill_tool,
    build_workspace_tools,
    skill_tool_name,
)
from skill_lens.workspace import Workspace

DEFAULT_MODEL = "openai:gpt-4o-mini"

# The SDKs LangChain wraps (`openai`, `anthropic`) name their network errors
# `APIConnectionError` / `APITimeoutError`. Those carry no status code and do
# not subclass the builtins, so the class name is the one provider-neutral
# signal left; matching a suffix rather than importing either SDK keeps the
# rule identical for a provider package that is not installed.
_TRANSIENT_NAME_SUFFIXES = ("ConnectionError", "TimeoutError")


def _require_langchain() -> None:
    try:
        import langchain  # noqa: F401
    except ImportError as exc:
        raise RunnerDependencyError(
            "the 'langchain' optional extra is required for this runner or judge: "
            "pip install 'skill-lens[langchain]'"
        ) from exc


def _is_transient(exc: Exception) -> bool:
    """Duck-typed, because LangChain does not normalise provider exceptions.

    An exception carrying an integer `status_code` -- both the `openai` and the
    `anthropic` SDK put one on their HTTP errors -- is judged by the shared
    status policy; the builtin timeout and connection errors, and the SDKs'
    own, are transient; nothing else is.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return transient_status(status)
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return type(exc).__name__.endswith(_TRANSIENT_NAME_SUFFIXES)


def _chat_model(model: Any, temperature: float | str) -> Any:
    """A `provider:model` string becomes a chat model; an instance is used as-is.

    Reasoning models reject any temperature but 1, so 'unset' sends none.
    """
    if not isinstance(model, str):
        return model
    from langchain.chat_models import init_chat_model

    if temperature == "unset":
        return init_chat_model(model)
    return init_chat_model(model, temperature=float(temperature))


def _structured_tools(built: list[AgentTool]) -> list[Any]:
    """Wrap framework-neutral tools for LangChain.

    A dict `args_schema` is passed to the model as-is and the model's
    arguments are handed to the callable unvalidated, which is what every
    `AgentTool` expects: a call never raises, whatever the model sent.
    """
    from langchain_core.tools import StructuredTool

    return [
        StructuredTool.from_function(
            func=tool.call,
            name=tool.name,
            description=tool.description,
            args_schema=tool.json_schema,
        )
        for tool in built
    ]


def _ai_messages(messages: list[Any]) -> list[Any]:
    from langchain_core.messages import AIMessage

    return [message for message in messages if isinstance(message, AIMessage)]


def _tool_calls(messages: list[Any]) -> list[ToolCall]:
    """Read the trajectory out of the message history, in order.

    The history is authoritative: it records what the model asked for,
    including calls whose execution then failed. LangChain has already parsed
    the arguments into a dict, so no normalisation is needed.
    """
    return [
        ToolCall(name=str(call["name"]), arguments=dict(call["args"]))
        for message in _ai_messages(messages)
        for call in message.tool_calls
    ]


def _usage(messages: list[Any]) -> tuple[int, int]:
    """(input_tokens, output_tokens) summed over every model turn.

    LangChain reports usage per response rather than per run; a response
    without `usage_metadata` contributes nothing.
    """
    input_tokens = output_tokens = 0
    for message in _ai_messages(messages):
        usage = message.usage_metadata or {}
        input_tokens += int(usage.get("input_tokens", 0))
        output_tokens += int(usage.get("output_tokens", 0))
    return input_tokens, output_tokens


def _model_name(messages: list[Any], fallback: str) -> str:
    """The model the provider actually served, which may be a dated snapshot."""
    for message in reversed(_ai_messages(messages)):
        name = message.response_metadata.get("model_name")
        if name:
            return str(name)
    return fallback


def _output(messages: list[Any]) -> str:
    replies = _ai_messages(messages)
    return replies[-1].text if replies else ""


def _transcript(messages: list[Any]) -> list[dict[str, Any]]:
    return [message.model_dump(mode="json") for message in messages]


def _cost(
    input_tokens: int, output_tokens: int, model_name: str, configured: str
) -> tuple[float, str]:
    """Price a run. `genai_prices.Usage` is the shape `calculate_cost` prices.

    Built here rather than in `pricing.py` so that module stays free of an
    import only the extras provide; the fallback note mirrors its own.
    """
    try:
        from genai_prices import Usage
    except ImportError:  # pragma: no cover - the extra declares genai-prices
        return 0.0, "genai-prices is not installed; cost not calculated"
    usage = Usage(input_tokens=input_tokens, output_tokens=output_tokens)
    return calculate_cost(usage, model_name, provider_of(configured))


class LangChainRunner:
    """Runs a case through LangChain's prebuilt agent, behind the protocol."""

    name = "langchain"
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

    def _build_agent(self, skill: Skill, case: EvalCase, workspace: Workspace | None) -> Any:
        from langchain.agents import create_agent

        built = [build_mock_tool(spec) for spec in case.tools]
        if case.mode == "offered":
            built.append(build_skill_tool(skill))
        if workspace is not None:
            built.extend(build_workspace_tools(workspace))
        return create_agent(
            _chat_model(self._model, self._temperature),
            tools=_structured_tools(built),
            system_prompt=instructions(skill, case, workspace is not None),
        )

    def _invoke(self, agent: Any, task: str) -> list[Any]:
        from langchain_core.messages import HumanMessage

        state = run_with_retries(
            lambda: agent.invoke({"messages": [HumanMessage(task)]}),
            _is_transient,
            self._retries,
            self._retry_backoff_seconds,
            self._sleep,
        )
        return list(state["messages"])

    def run(self, skill: Skill, case: EvalCase, workspace: Workspace | None = None) -> RunResult:
        _require_langchain()
        configured = self._model if isinstance(self._model, str) else ""
        offered = skill_tool_name(skill.name) if case.mode == "offered" else None
        started = time.monotonic()
        try:
            messages = self._invoke(self._build_agent(skill, case, workspace), case.task)
            input_tokens, output_tokens = _usage(messages)
            model_name = _model_name(messages, configured)
            cost_usd, cost_note = _cost(input_tokens, output_tokens, model_name, configured)
            tool_calls = _tool_calls(messages)
            return RunResult(
                output=_output(messages),
                tool_calls=tool_calls,
                transcript=_transcript(messages),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=int((time.monotonic() - started) * 1000),
                cost_usd=cost_usd,
                cost_note=cost_note,
                model=model_name,
                skill_triggered=(
                    None if offered is None else any(call.name == offered for call in tool_calls)
                ),
            )
        except Exception as exc:
            return RunResult(
                error=f"{type(exc).__name__}: {exc}",
                latency_ms=int((time.monotonic() - started) * 1000),
                model=configured,
            )
```

- [ ] **Step 6: Extend the framework-isolation guard**

In `tests/test_framework_isolation.py`, replace the `ALLOWED` set and `FRAMEWORK_IMPORT` pattern with:

```python
ALLOWED = {
    Path("runners/pydantic_ai.py"),
    Path("judges/pydantic_ai.py"),
    Path("runners/langchain.py"),
}
# `pydantic_ai`; `langchain`, `langchain_core`, `langchain_openai`, ...; `langgraph`.
FRAMEWORK_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(?:pydantic_ai|langchain(?:_\w+)?|langgraph)\b", re.MULTILINE
)
```

Update the module docstring's first line to "No agent-framework type may appear outside the adapter modules." and rename `test_only_the_two_adapters_import_the_agent_framework` to `test_only_the_adapters_import_an_agent_framework`, `test_both_allowed_adapters_actually_exist` to `test_every_allowed_adapter_actually_exists`. (`judges/langchain.py` joins `ALLOWED` in Task 5, when it exists.)

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_langchain_runner.py tests/test_framework_isolation.py -q`
Expected: all pass. If `from langchain_fakes import ...` fails with `ModuleNotFoundError`, add `pythonpath = ["tests"]` under `[tool.pytest.ini_options]` in `pyproject.toml` and rerun. Then ruff clean (`ruff format` will rewrap the long `bound_tools.append(...)` line in the fake; accept its formatting).

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock src/skill_lens/runners/langchain.py tests/langchain_fakes.py tests/test_langchain_runner.py tests/test_framework_isolation.py
git commit -m "feat: run a case through LangChain's agent behind the Runner protocol"
```

---

### Task 4: `LangChainRunner` failure paths, retries, temperature, offered mode, workspace, registration

**Files:**
- Modify: `tests/test_langchain_runner.py` (append)
- Modify: `src/skill_lens/cli.py:40` (`_RUNNERS`)
- Modify: `tests/test_cli.py` (one registration test)

**Interfaces:**
- Consumes: everything Task 3 produced; `Workspace` from `skill_lens.workspace`; `BUILTIN_TOOL_NAMES`, `skill_tool_name` from `runners/tools.py`.
- Produces: `cli._RUNNERS["langchain"] is LangChainRunner`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_langchain_runner.py`:

```python


# --- failures and retries -------------------------------------------------

from langchain_fakes import FunctionChatModel, StatusError  # noqa: E402
from skill_lens.runners.tools import BUILTIN_TOOL_NAMES, skill_tool_name  # noqa: E402
from skill_lens.workspace import Workspace  # noqa: E402


def raising(exc: Exception) -> FunctionChatModel:
    def reply(messages, turn):
        raise exc

    return FunctionChatModel(reply=reply)


def flaky(exc: Exception, then: str) -> FunctionChatModel:
    """Raises `exc` on the first turn, answers `then` afterwards."""

    def reply(messages, turn):
        if turn == 0:
            raise exc
        return text(then)

    return FunctionChatModel(reply=reply)


def test_a_provider_failure_is_reported_not_raised():
    result = LangChainRunner(model=raising(StatusError(500)), retries=0).run(SKILL, case())
    assert result.errored is True
    assert "StatusError: status 500" == result.error
    assert result.output == ""


def test_a_transient_failure_is_retried_and_can_succeed():
    slept = []
    runner = LangChainRunner(
        model=flaky(StatusError(429), "recovered"),
        retries=2,
        retry_backoff_seconds=0.01,
        sleep=slept.append,
    )
    result = runner.run(SKILL, case())
    assert result.output == "recovered"
    assert result.errored is False
    assert slept == [0.01]


def test_a_5xx_failure_is_retried_and_can_succeed():
    slept = []
    runner = LangChainRunner(
        model=flaky(StatusError(503), "recovered"), retries=2, retry_backoff_seconds=0.01, sleep=slept.append
    )
    assert runner.run(SKILL, case()).output == "recovered"
    assert slept == [0.01]


def test_backoff_grows_exponentially_between_attempts():
    slept = []
    runner = LangChainRunner(
        model=raising(StatusError(429)), retries=3, retry_backoff_seconds=1.0, sleep=slept.append
    )
    result = runner.run(SKILL, case())
    assert slept == [1.0, 2.0, 4.0]
    assert result.errored is True


def test_a_permanent_failure_is_not_retried():
    model = raising(StatusError(401))
    slept = []
    result = LangChainRunner(model=model, retries=3, sleep=slept.append).run(SKILL, case())
    assert len(model.turns) == 1
    assert slept == []
    assert result.errored is True


class APIConnectionError(Exception):
    """Named like the openai/anthropic SDKs' network error: no status code."""


def test_an_sdk_connection_error_is_transient_by_name():
    # The SDKs' connection and timeout errors carry no status_code and do not
    # subclass the builtins; the class name is the only provider-neutral signal.
    slept = []
    runner = LangChainRunner(
        model=flaky(APIConnectionError("reset"), "recovered"), retries=1, sleep=slept.append
    )
    assert runner.run(SKILL, case()).output == "recovered"
    assert slept == [1.0]


def test_a_builtin_timeout_is_transient():
    runner = LangChainRunner(model=flaky(TimeoutError(), "recovered"), retries=1, sleep=lambda _: None)
    assert runner.run(SKILL, case()).output == "recovered"


def test_an_unrelated_exception_is_not_retried():
    model = raising(ValueError("bad request shape"))
    result = LangChainRunner(model=model, retries=3, sleep=lambda _: None).run(SKILL, case())
    assert len(model.turns) == 1
    assert result.error == "ValueError: bad request shape"


def test_the_configured_model_is_reported_on_a_failure():
    # A model string LangChain cannot resolve raises during construction,
    # before any network call, so this stays offline -- and the report row
    # must still say which model was attempted.
    result = LangChainRunner(model="not-a-real-provider:some-model").run(SKILL, case())
    assert result.errored is True
    assert result.model == "not-a-real-provider:some-model"


def test_a_failure_while_capturing_the_result_is_reported_not_raised(monkeypatch):
    import skill_lens.runners.langchain as adapter

    def boom(messages):
        raise RuntimeError("transcript exploded")

    monkeypatch.setattr(adapter, "_transcript", boom)
    result = LangChainRunner(model=scripted(text("done"))).run(SKILL, case())
    assert result.errored is True
    assert "transcript exploded" in result.error


def test_a_missing_optional_extra_propagates_rather_than_becoming_an_errored_case(monkeypatch):
    import skill_lens.runners.langchain as adapter

    def explode() -> None:
        raise adapter.RunnerDependencyError("the 'langchain' runner needs its optional extra")

    monkeypatch.setattr(adapter, "_require_langchain", explode)
    with pytest.raises(adapter.RunnerDependencyError):
        LangChainRunner(model=scripted(text("done"))).run(SKILL, case())


def test_the_dependency_message_names_the_extra(monkeypatch):
    import builtins

    import skill_lens.runners.langchain as adapter

    real_import = builtins.__import__

    def no_langchain(name, *args, **kwargs):
        if name == "langchain":
            raise ImportError("No module named 'langchain'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_langchain)
    with pytest.raises(adapter.RunnerDependencyError, match=r"skill-lens\[langchain\]"):
        adapter._require_langchain()


# --- temperature ----------------------------------------------------------


def test_a_numeric_temperature_reaches_the_chat_model(monkeypatch):
    import skill_lens.runners.langchain as adapter

    seen = {}

    def fake_chat_model(model, temperature):
        seen["model"], seen["temperature"] = model, temperature
        return scripted(text("done"))

    monkeypatch.setattr(adapter, "_chat_model", fake_chat_model)
    LangChainRunner(model="openai:gpt-4o-mini", temperature=0.7).run(SKILL, case())
    assert seen == {"model": "openai:gpt-4o-mini", "temperature": 0.7}


def test_init_chat_model_receives_the_temperature_and_omits_it_when_unset(monkeypatch):
    # ChatOpenAI refuses to construct without a key even though nothing is
    # sent; a placeholder keeps this offline.
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-for-construction")
    from skill_lens.runners.langchain import _chat_model

    warm = _chat_model("openai:gpt-4o-mini", 0.7)
    assert warm.temperature == 0.7
    unset = _chat_model("openai:gpt-4o-mini", "unset")
    # ChatOpenAI leaves temperature None when none was given.
    assert unset.temperature is None


# --- offered mode ---------------------------------------------------------


def offered_case(**kwargs) -> EvalCase:
    kwargs.setdefault("name", "c")
    kwargs.setdefault("task", "refund order 1234")
    kwargs["mode"] = "offered"
    return EvalCase(**kwargs)


def test_an_offered_skill_is_registered_as_a_tool_named_after_it():
    model = scripted(text("done"))
    LangChainRunner(model=model).run(SKILL, offered_case(tools=[ToolSpec(name="lookup_order")]))
    assert sorted(model.bound_tools[0]) == ["lookup_order", "order_support"]


def test_an_offered_skill_is_not_forced_into_the_system_prompt():
    from skill_lens.runners.prompting import OFFERED_PREAMBLE

    model = scripted(text("done"))
    LangChainRunner(model=model).run(SKILL, offered_case())
    assert system_text(model.turns[0]) == OFFERED_PREAMBLE


def test_an_offered_skill_that_is_declined_reports_false():
    assert LangChainRunner(model=scripted(text("done"))).run(SKILL, offered_case()).skill_triggered is False


def test_an_offered_skill_that_is_chosen_reports_true_and_appears_in_the_trajectory():
    runner = LangChainRunner(model=scripted(tool_call("order_support", {}), text("done")))
    result = runner.run(SKILL, offered_case())
    assert result.skill_triggered is True
    assert [call.name for call in result.tool_calls] == ["order_support"]


def test_choosing_the_skill_delivers_its_instructions_to_the_model():
    model = scripted(tool_call("order_support", {}), text("done"))
    LangChainRunner(model=model).run(SKILL, offered_case())
    assert "Always look up the order first." in tool_results(model.turns[1])["order_support"]


def test_registration_and_detection_agree_on_a_name_replace_would_get_wrong():
    weird = Skill(name="123 weird name!", description="odd", instructions="Weird.", path=Path("."))
    expected = skill_tool_name(weird.name)
    model = scripted(tool_call(expected, {}), text("done"))
    result = LangChainRunner(model=model).run(weird, offered_case())
    assert expected in model.bound_tools[0]
    assert result.skill_triggered is True


# --- baseline arm and workspace ------------------------------------------

EMPTY_SKILL = Skill(
    name="order-support", description="", instructions="", variant="baseline", path=Path(".")
)


def test_a_baseline_prompt_never_leaks_the_skill_name():
    from skill_lens.runners.prompting import BASELINE_PREAMBLE

    model = scripted(text("done"))
    LangChainRunner(model=model).run(EMPTY_SKILL, case())
    assert system_text(model.turns[0]) == BASELINE_PREAMBLE
    assert "order-support" not in system_text(model.turns[0])


def test_the_builtin_tools_are_registered_only_when_a_workspace_is_given(tmp_path):
    with_workspace = scripted(text("done"))
    LangChainRunner(model=with_workspace).run(SKILL, case(), workspace=Workspace(root=tmp_path.resolve()))
    assert set(BUILTIN_TOOL_NAMES) <= set(with_workspace.bound_tools[0])

    without = scripted(text("done"))
    LangChainRunner(model=without).run(SKILL, case())
    assert not set(BUILTIN_TOOL_NAMES) & set(without.bound_tools[0])


def test_the_workspace_preamble_is_delivered_with_a_workspace(tmp_path):
    from skill_lens.runners.prompting import WORKSPACE_PREAMBLE

    model = scripted(text("done"))
    LangChainRunner(model=model).run(SKILL, case(), workspace=Workspace(root=tmp_path.resolve()))
    assert system_text(model.turns[0]).endswith(WORKSPACE_PREAMBLE)


def test_a_model_writing_a_file_lands_it_in_the_workspace(tmp_path):
    runner = LangChainRunner(
        model=scripted(tool_call("write_file", {"path": "report.md", "content": "north 120"}), text("done"))
    )
    workspace = Workspace(root=tmp_path.resolve())
    result = runner.run(SKILL, case(), workspace=workspace)
    assert result.error is None
    assert workspace.read("report.md") == "north 120"
    assert [call.name for call in result.tool_calls] == ["write_file"]


def test_a_model_writing_outside_the_root_is_refused_not_errored(tmp_path):
    runner = LangChainRunner(
        model=scripted(tool_call("write_file", {"path": "../escape.txt", "content": "x"}), text("done"))
    )
    workspace = Workspace(root=tmp_path.resolve())
    result = runner.run(SKILL, case(), workspace=workspace)
    assert result.error is None
    assert workspace.listing() == []
```

(Move the three `# noqa: E402` imports up into the file's import block instead if ruff's isort rule prefers it; the intent is one import block at the top.)

Append to `tests/test_cli.py`, after `test_the_real_runner_is_registered`:

```python


def test_the_langchain_runner_is_registered(tmp_path):
    skill_dir = _make_skill(tmp_path)
    result = runner.invoke(
        app,
        ["run", str(skill_dir), "--runner", "langchain", "--model", "openai:gpt-4o-mini"],
        env={"OPENAI_API_KEY": ""},
    )
    # No key, so preflight stops it before any spend -- the same contract as
    # the PydanticAI runner.
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output
```

- [ ] **Step 2: Run the tests to verify the new ones fail**

Run: `uv run pytest tests/test_langchain_runner.py tests/test_cli.py::test_the_langchain_runner_is_registered -q`
Expected: the CLI test fails with `unknown runner: langchain` (exit 2 but the assertion on `OPENAI_API_KEY` fails); the adapter tests pass already except any the implementation in Task 3 missed — fix those in the adapter, never by weakening the test.

- [ ] **Step 3: Register the runner**

In `src/skill_lens/cli.py`:

1. Add `from skill_lens.runners.langchain import LangChainRunner` to the imports (alphabetically after `fake`).
2. Replace `_RUNNERS = {"fake": FakeRunner, "pydantic-ai": PydanticAIRunner}` with:

```python
_RUNNERS = {"fake": FakeRunner, "pydantic-ai": PydanticAIRunner, "langchain": LangChainRunner}
```

- [ ] **Step 4: Run the suite**

Run: `uv run pytest -q`
Expected: all pass. Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/cli.py tests/test_langchain_runner.py tests/test_cli.py
git commit -m "feat: register the LangChain runner and cover its failure paths"
```

---

### Task 5: `LangChainJudge`

**Files:**
- Create: `src/skill_lens/judges/langchain.py`
- Create: `tests/test_langchain_judge.py`
- Modify: `src/skill_lens/cli.py:41` (`_JUDGES`)
- Modify: `tests/test_framework_isolation.py` (allow `judges/langchain.py`)
- Modify: `tests/test_cli.py` (one registration test)

**Interfaces:**
- Consumes: `_chat_model`, `_cost`, `_is_transient`, `_model_name`, `_require_langchain`, `_usage`, `DEFAULT_MODEL` (Task 3); `run_with_retries` (Task 2); `SYSTEM_PROMPT`, `render_request` from `judges/prompt.py`; `JudgeOutput`, `JudgeRequest`, `JudgeVerdict` from `models.py`.
- Produces: `skill_lens.judges.langchain.LangChainJudge(model, temperature=0.0, retries=2, retry_backoff_seconds=1.0, sleep=time.sleep)`, `name = "langchain"`, `needs_api_key = True`, `judge(request) -> JudgeVerdict`; `cli._JUDGES["langchain"]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_langchain_judge.py`:

```python
"""The LangChain judge, exercised offline with a scripted model."""

from langchain_core.messages import AIMessage, ToolCall

from langchain_fakes import USAGE, FunctionChatModel, StatusError
from skill_lens.judges.base import Judge
from skill_lens.judges.langchain import LangChainJudge
from skill_lens.models import JudgeRequest, RubricCheck

REQUEST = JudgeRequest(
    task="Why can't I return this?",
    output="The return window is 30 days.",
    checks=[RubricCheck(id="r1", text="states the 30-day window")],
)


def verdict(checks: list[dict], **metadata) -> AIMessage:
    """What a model answers through the judge's structured-output tool.

    LangChain's default `with_structured_output` binds the schema as a tool
    named after the class and parses that tool call's arguments.
    """
    return AIMessage(
        content="",
        tool_calls=[ToolCall(name="JudgeOutput", args={"checks": checks}, id="call-judge")],
        usage_metadata={"input_tokens": 50, "output_tokens": 9, "total_tokens": 59},
        response_metadata=dict(metadata),
    )


def structured(checks: list[dict], **metadata) -> FunctionChatModel:
    return FunctionChatModel(reply=lambda messages, turn: verdict(checks, **metadata))


def raising(exc: Exception) -> FunctionChatModel:
    def reply(messages, turn):
        raise exc

    return FunctionChatModel(reply=reply)


def test_it_satisfies_the_judge_protocol_and_registers_its_name():
    judge = LangChainJudge(model=structured([]))
    assert isinstance(judge, Judge)
    assert judge.name == "langchain"
    assert LangChainJudge.needs_api_key is True


def test_the_model_verdicts_become_check_results():
    judge = LangChainJudge(model=structured([{"id": "r1", "passed": True, "evidence": "'30 days'"}]))
    result = judge.judge(REQUEST)
    assert result.errored is False
    assert [(c.id, c.passed, c.evidence) for c in result.checks] == [("r1", True, "'30 days'")]


def test_usage_and_the_served_model_are_read_from_the_raw_response():
    judge = LangChainJudge(
        model=structured([{"id": "r1", "passed": True, "evidence": "x"}], model_name="dated-2026-08-01")
    )
    result = judge.judge(REQUEST)
    assert (result.input_tokens, result.output_tokens) == (50, 9)
    assert result.model == "dated-2026-08-01"


def test_the_system_prompt_and_the_rendered_request_reach_the_model():
    model = structured([])
    LangChainJudge(model=model).judge(REQUEST)
    system, human = model.turns[0][0], model.turns[0][-1]
    assert system.type == "system" and "evidence" in str(system.content)
    assert "r1: states the 30-day window" in str(human.content)
    assert "The return window is 30 days." in str(human.content)


def test_the_schema_is_bound_as_the_structured_output_tool():
    model = structured([])
    LangChainJudge(model=model).judge(REQUEST)
    assert model.bound_tools == [["JudgeOutput"]]


def test_a_malformed_verdict_is_errored_not_failed():
    # PydanticAI retries a malformed structured output internally; LangChain
    # hands it back as parsing_error. An unreadable verdict is an infra
    # signal, never a low score.
    model = FunctionChatModel(
        reply=lambda messages, turn: AIMessage(
            content="",
            tool_calls=[ToolCall(name="JudgeOutput", args={"checks": "not a list"}, id="c")],
            usage_metadata=dict(USAGE),
        )
    )
    result = LangChainJudge(model=model, retries=2, sleep=lambda _: None).judge(REQUEST)
    assert result.errored is True
    assert result.error.startswith("JudgeOutputInvalid:")
    assert len(model.turns) == 1  # not transient: one attempt


def test_a_provider_failure_is_reported_not_raised():
    result = LangChainJudge(model=raising(StatusError(500)), retries=0).judge(REQUEST)
    assert result.errored is True
    assert result.error == "StatusError: status 500"


def test_a_transient_failure_is_retried_before_giving_up():
    attempts = {"n": 0}

    def reply(messages, turn):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise StatusError(429)
        return verdict([{"id": "r1", "passed": True, "evidence": "x"}])

    judge = LangChainJudge(model=FunctionChatModel(reply=reply), retries=2, sleep=lambda _: None)
    assert judge.judge(REQUEST).errored is False
    assert attempts["n"] == 3


def test_a_permanent_failure_is_not_retried():
    model = raising(StatusError(401))
    result = LangChainJudge(model=model, retries=2, sleep=lambda _: None).judge(REQUEST)
    assert result.errored is True
    assert len(model.turns) == 1


def test_an_unpriceable_model_degrades_to_a_note_rather_than_erroring():
    result = LangChainJudge(model=structured([{"id": "r1", "passed": True, "evidence": "x"}])).judge(REQUEST)
    assert result.errored is False
    assert result.cost_usd == 0.0
    assert "no price data" in result.cost_note


def test_the_model_is_reported_on_a_provider_failure():
    result = LangChainJudge(model="not-a-real-provider:some-model").judge(REQUEST)
    assert result.errored is True
    assert result.model == "not-a-real-provider:some-model"


def test_a_failure_while_capturing_the_result_is_reported_not_raised(monkeypatch):
    # Patched on skill_lens.judges.langchain: the judge imports _cost by name.
    import skill_lens.judges.langchain as judge_module

    def boom(*args):
        raise RuntimeError("cost calc exploded")

    monkeypatch.setattr(judge_module, "_cost", boom)
    result = LangChainJudge(model=structured([{"id": "r1", "passed": True, "evidence": "x"}])).judge(REQUEST)
    assert result.errored is True
    assert "cost calc exploded" in result.error
```

Append to `tests/test_cli.py` after `test_a_real_judge_without_its_api_key_fails_preflight` (read that test for the shape; the new one is identical with `judge = "langchain"` in the config file):

```python


def test_the_langchain_judge_is_registered(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill_dir = _make_skill(tmp_path)
    (tmp_path / "skill-lens.toml").write_text('judge = "langchain"\n', encoding="utf-8")
    result = runner.invoke(
        app, ["run", str(skill_dir), "--config", str(tmp_path / "skill-lens.toml")]
    )
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_langchain_judge.py tests/test_cli.py::test_the_langchain_judge_is_registered -q`
Expected: `ModuleNotFoundError: No module named 'skill_lens.judges.langchain'`; the CLI test reports `unknown judge: langchain`.

- [ ] **Step 3: Create the judge**

Create `src/skill_lens/judges/langchain.py`:

```python
"""The LangChain judge -- the fourth and last module that imports a framework.

Everything the core sees is a plain `JudgeVerdict`. Provider failures are
reported through `JudgeVerdict.error`, never raised, so `JudgeEvaluator` can
tell an infra problem (errored) apart from a low score (failed).

The dependency check, the transient rule and the usage, model-name and cost
readers are imported from the runner adapter rather than duplicated, and the
retry loop comes from `runners/retry.py`: both modules are already inside the
framework boundary, and a second copy would be a second thing to keep in step.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from skill_lens.judges.prompt import SYSTEM_PROMPT, render_request
from skill_lens.models import JudgeOutput, JudgeRequest, JudgeVerdict
from skill_lens.runners.langchain import (
    DEFAULT_MODEL,
    _chat_model,
    _cost,
    _is_transient,
    _model_name,
    _require_langchain,
    _usage,
)
from skill_lens.runners.retry import run_with_retries


class LangChainJudge:
    """Grades a rubric with a real model, behind the framework-agnostic protocol."""

    name = "langchain"
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

    def _grade(self, request: JudgeRequest) -> dict[str, Any]:
        from langchain_core.messages import HumanMessage, SystemMessage

        # include_raw=True is the whole reason for this shape: without it
        # LangChain hands back only the parsed object, and tokens, cost and
        # the served model name -- what the report's judge-overhead line is
        # built from -- would be unreadable.
        grader = _chat_model(self._model, self._temperature).with_structured_output(
            JudgeOutput, include_raw=True
        )
        prompt = [SystemMessage(SYSTEM_PROMPT), HumanMessage(render_request(request))]
        return run_with_retries(
            lambda: grader.invoke(prompt),
            _is_transient,
            self._retries,
            self._retry_backoff_seconds,
            self._sleep,
        )

    def judge(self, request: JudgeRequest) -> JudgeVerdict:
        _require_langchain()
        configured = self._model if isinstance(self._model, str) else ""
        try:
            graded = self._grade(request)
            raw = graded["raw"]
            model_name = _model_name([raw], configured)
            parsed = graded.get("parsed")
            if parsed is None:
                # PydanticAI retries a malformed structured output internally;
                # LangChain hands it back. An unreadable verdict is an infra
                # signal, not a low score -- errored, never failed.
                return JudgeVerdict(
                    error=f"JudgeOutputInvalid: {graded.get('parsing_error')}", model=model_name
                )
            input_tokens, output_tokens = _usage([raw])
            cost_usd, cost_note = _cost(input_tokens, output_tokens, model_name, configured)
            return JudgeVerdict(
                checks=list(parsed.checks),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                cost_note=cost_note,
                model=model_name,
            )
        except Exception as exc:
            return JudgeVerdict(error=f"{type(exc).__name__}: {exc}", model=configured)
```

- [ ] **Step 4: Register it and close the boundary**

In `src/skill_lens/cli.py`: add `from skill_lens.judges.langchain import LangChainJudge` to the imports and replace `_JUDGES = {"fake": FakeJudge, "pydantic-ai": PydanticAIJudge}` with:

```python
_JUDGES = {"fake": FakeJudge, "pydantic-ai": PydanticAIJudge, "langchain": LangChainJudge}
```

In `tests/test_framework_isolation.py`, add `Path("judges/langchain.py"),` to `ALLOWED`.

- [ ] **Step 5: Run the suite**

Run: `uv run pytest -q`
Expected: all pass. Ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/skill_lens/judges/langchain.py src/skill_lens/cli.py tests/test_langchain_judge.py tests/test_cli.py tests/test_framework_isolation.py
git commit -m "feat: grade rubrics through a LangChain judge"
```

---

### Task 6: Cassette and live-tier tests

**Files:**
- Modify: `tests/test_cassettes.py` (three tests appended)
- Modify: `tests/test_integration_live.py` (one test appended)

**Interfaces:**
- Consumes: `LangChainRunner`, `LangChainJudge`; the module-level `SKILL`, `CASE`, `JUDGED_CASE`, `OFFERED_POSITIVE` fixtures already in `tests/test_cassettes.py`; the `replay` fixture from `tests/conftest.py`.

- [ ] **Step 1: Write the cassette tests**

In `tests/test_cassettes.py`, add the imports:

```python
from skill_lens.judges.langchain import LangChainJudge
from skill_lens.runners.langchain import LangChainRunner
```

and append at the end of the file:

```python


@pytest.mark.cassette
@pytest.mark.vcr
def test_langchain_traffic_drives_the_whole_loop(replay):
    # Same request, second framework. Only real traffic can show that the
    # message list LangChain hands back -- usage per turn, model_name in the
    # response metadata, tool-call args as a dict -- reads the way the
    # adapter expects; the scripted model is shaped by construction.
    result = LangChainRunner(model="openai:gpt-4o-mini", retries=0).run(SKILL, CASE)

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
def test_a_langchain_judge_grades_a_rubric_with_evidence(replay):
    request = JudgeRequest(
        task=JUDGED_CASE.task,
        output="Order 1234 was delivered 45 days ago, so the 30-day return window has closed.",
        expected=JUDGED_CASE.judge.expected,
        checks=[
            RubricCheck(id="r1", text=JUDGED_CASE.judge.rubric[0]),
            RubricCheck(id="r2", text=JUDGED_CASE.judge.rubric[1]),
        ],
    )
    verdict = LangChainJudge(model="openai:gpt-4o-mini", retries=0).judge(request)

    assert verdict.errored is False
    assert sorted(check.id for check in verdict.checks) == ["r1", "r2"]
    assert all(check.evidence for check in verdict.checks)
    assert verdict.cost_usd > 0


@pytest.mark.cassette
@pytest.mark.vcr
def test_a_langchain_agent_reaches_for_an_offered_skill(replay):
    result = LangChainRunner(model="openai:gpt-4o-mini", retries=0).run(SKILL, OFFERED_POSITIVE)
    assert result.errored is False
    assert result.skill_triggered is True
    assert TrajectoryEvaluator().evaluate(OFFERED_POSITIVE, result).passed is True
```

- [ ] **Step 2: Write the live-tier test**

In `tests/test_integration_live.py`, add the imports:

```python
from skill_lens.judges.langchain import LangChainJudge
from skill_lens.runners.langchain import LangChainRunner
```

and append:

```python


def test_the_examples_pass_against_a_real_provider_through_langchain():
    report = run_evals(
        load_skills(EXAMPLES),
        [LangChainRunner(model="openai:gpt-4o-mini")],
        judge=LangChainJudge(model="openai:gpt-4o-mini"),
    )
    assert report.total == 7  # greeting (1) + order-support (5) + csv-report (1)
    assert report.errored == 0, [o.result.error for o in report.outcomes if o.result.errored]
    assert report.pass_rate == 1.0, [
        (o.case_name, [s.detail for s in o.scores if not s.passed])
        for o in report.outcomes
        if o.status == "failed"
    ]
```

- [ ] **Step 3: Confirm the three cassette tests skip and nothing reaches the network**

Run: `uv run pytest tests/test_cassettes.py -v`
Expected: the six existing tests pass; the three new ones report `SKIPPED (cassette test_langchain_... not recorded; run ...)`. `uv run pytest -q` still green (`-m 'not integration'` keeps the live test deselected).

- [ ] **Step 4: Commit**

```bash
git add tests/test_cassettes.py tests/test_integration_live.py
git commit -m "test: cover the LangChain adapters at the cassette and live tiers"
```

**Recording (a person with a key, before or after the PR merges):**

```bash
OPENAI_API_KEY=... uv run pytest tests/test_cassettes.py -k langchain --record-mode=once
```

then `git add tests/cassettes && git commit -m "test: record cassettes for the LangChain adapters"`. The recordings must contain no secrets — `vcr_config` scrubs them; `git diff --cached | grep -i "sk-"` must print nothing.

---

### Task 7: Documentation

**Files:**
- Modify: `docs/runners.md`, `docs/cli.md`, `docs/configuration.md`, `docs/index.md`, `docs/getting-started.md`, `docs/roadmap.md`, `docs/releasing.md`, `docs/security.md`, `docs/ci.md`
- Modify: `README.md`, `ARCHITECTURE.md`, `CLAUDE.md`
- Modify: `.github/workflows/release.yml` (one comment line)

- [ ] **Step 1: `docs/runners.md`**

Replace the page's first paragraph and code block (lines 1–13) with:

```markdown
# Runners

The default runner is `fake` (offline, scripted, free). To evaluate a skill with a
real agent you need one of the two framework extras, a key in the environment, and a
model:

```bash
uv tool install "skill-lens[pydantic-ai]"       # or "skill-lens[langchain]", or both
export OPENAI_API_KEY=...
skill-lens run ./skills --runner pydantic-ai --model openai:gpt-4o-mini
skill-lens run ./skills --runner langchain --model openai:gpt-4o-mini
```

From a checkout instead, the extra comes from `uv sync --extra pydantic-ai` (or
`--extra langchain`) and every command runs as `uv run skill-lens ...`.

## Two frameworks, one measurement

| Runner | Extra | Agent loop | Bundled providers |
| --- | --- | --- | --- |
| `pydantic-ai` | `skill-lens[pydantic-ai]` | PydanticAI `Agent` | OpenAI, Anthropic |
| `langchain` | `skill-lens[langchain]` | LangChain 1.x `create_agent` (LangGraph underneath) | OpenAI, Anthropic |

Both runners receive the same inputs — the same system prompt built by one shared
function, the same mock tools, the same offered-mode skill tool, the same workspace
tools — and produce the same `RunResult`, so a case passing under one and failing under
the other says something about the skill's instructions, not about the harness. The
judge is chosen separately (`judge = "pydantic-ai"` or `"langchain"`), and one judge grades
every runner's output.

`--model` is passed to each framework unchanged. `openai:` and `anthropic:` are spelled
the same in both; other providers differ (PydanticAI `google-gla:`, LangChain
`google_genai:`) and need their provider package installed beside the extra
(`pip install langchain-google-genai`, or `pydantic-ai-slim[google]`).

Per turn, LangChain reports token usage on each model response and the served model
name in the response metadata; the runner sums the former and reads the latter from the
last response that carries one. A LangChain judge that returns a structured verdict the
schema cannot parse is recorded as **errored**, never as a failed check — an unreadable
verdict is an infrastructure signal, not a low score.
```

(The nested ```bash fence inside a ```markdown block above is illustrative — in the file, write the bash fence directly.)

- [ ] **Step 2: `docs/cli.md`**

Line 29, replace:

```markdown
| `--runner <name>` | `fake` | `fake` or `pydantic-ai` — see [Runners](runners.md) |
```

with:

```markdown
| `--runner <name>` | `fake` | `fake`, `pydantic-ai` or `langchain` — see [Runners](runners.md) |
```

- [ ] **Step 3: `docs/configuration.md`**

In the `## Judging` section, after the sentence ending `upgrading must never start spending money on its own.**`, add a paragraph:

```markdown
`judge = "pydantic-ai"` and `judge = "langchain"` each need their extra installed; a
repository that installs only `skill-lens[langchain]` can both run and grade.
```

- [ ] **Step 4: Install snippets — one line each**

`README.md` line 47–48: replace `(`pip install "skill-lens[pydantic-ai]"` works too. Drop the extra for the offline default runner alone.)` with:

```markdown
(`pip install "skill-lens[pydantic-ai]"` works too; `skill-lens[langchain]` gives you the
LangChain runner instead, or as well. Drop the extra for the offline default runner alone.)
```

`docs/index.md` lines 32–34: append to the paragraph: `The `langchain` extra installs the LangChain runner and judge the same way; the two can be installed together.`

`docs/getting-started.md` lines 12–14: append to the paragraph: `A `langchain` extra provides the second real-agent runner — see [Runners](runners.md).`

- [ ] **Step 5: `docs/roadmap.md`**

Change the M8 row to:

```markdown
| M8 | LangChain runner and judge (`[langchain]` extra); runner matrix | Part 1 shipped; Part 2 planned |
```

Append before `## The rename to skill-lens`:

```markdown
## What M8 part 1 shipped

A second agent framework behind the same seams. `--runner langchain` drives LangChain
1.x's `create_agent` with the same tools, prompt and workspace the PydanticAI runner
uses, and `judge = "langchain"` grades rubrics with the same prompt and per-check
verdict contract, so the `[langchain]` extra is self-sufficient. The prompt rules and the
retry policy were extracted into two framework-neutral modules so both adapters share
one implementation. Anthropic joined both extras. Part 2 — running every case through
more than one runner in one invocation — has its own pull request; see the
[M8 design](https://github.com/EmadMokhtar/skill-evaluator/blob/main/docs/superpowers/specs/2026-09-11-skill-lens-m8-design.md).
```

- [ ] **Step 6: SBOM wording**

`docs/releasing.md` line 24: replace `the runtime dependencies and the `pydantic-ai` extra` with `the runtime dependencies and every optional extra (`pydantic-ai`, `langchain`)`.
`docs/security.md` line 143: replace `optional `pydantic-ai` extra` with `optional extras (`pydantic-ai`, `langchain`)`.
`.github/workflows/release.yml` line 184: replace `the optional `pydantic-ai` extra` with `every optional extra`.
`docs/ci.md` line 58, the `install-spec` row: append ` Use `skill-lens[pydantic-ai,langchain]==…` when a job runs the LangChain runner.` to the description cell.

- [ ] **Step 7: `ARCHITECTURE.md`**

1. Module map (the table around line 60): change the `runners/pydantic_ai.py` row's bold text to `**One of the four modules that import an agent framework.**`, the `judges/pydantic_ai.py` row to `**Another of the four.**`, and add these rows in place (after `runners/fake.py`, after `runners/pricing.py`, after `judges/pydantic_ai.py` respectively):

```markdown
| `runners/prompting.py` | The three preambles and the system-prompt builder every runner calls. Framework-free, so the rules `--min-delta` measures against exist once. |
| `runners/retry.py` | The transient-retry loop and the HTTP status policy every adapter shares; each adapter supplies its own `is_transient`. |
| `runners/langchain.py` | The LangChain runner adapter. **One of the four modules that import an agent framework.** |
| `judges/langchain.py` | The LangChain judge adapter. **The last of the four.** |
```

2. The boundary paragraph (line 188): replace `**Agent-framework imports appear in exactly two modules** — `runners/pydantic_ai.py` and `judges/pydantic_ai.py`.` with `**Agent-framework imports appear in exactly four modules** — `runners/pydantic_ai.py`, `judges/pydantic_ai.py`, `runners/langchain.py` and `judges/langchain.py`.` and `allows only those two files` with `allows only those four files`. Add after that sentence: `The prompt rules (`runners/prompting.py`) and the retry loop (`runners/retry.py`) import no framework, which is what lets both adapters share them.`

3. Lines 255 and 446: replace `_system_prompt` with `prompting.system_prompt` and `_instructions` with `prompting.instructions`.

4. Extension points, "Adding a runner": append `Build the system prompt with `runners/prompting.instructions` and wrap provider calls in `runners/retry.run_with_retries` rather than writing either again — both adapters must measure the same thing.`

- [ ] **Step 8: `CLAUDE.md`**

1. The status paragraph: after the sentence ending `and an end-to-end quickstart.` add: `M8 part 1 adds a LangChain runner and judge behind the same protocols, installable as the `[langchain]` extra, with the prompt rules and retry loop extracted into `runners/prompting.py` and `runners/retry.py`.` and add `, and the M8 design is in `docs/superpowers/specs/2026-09-11-skill-lens-m8-design.md`` before the final period of the milestone-list sentence.
2. The invariant bullet at line 127: replace with:

```markdown
- **No agent-framework type may appear outside the four adapter modules** —
  `runners/pydantic_ai.py`, `judges/pydantic_ai.py`, `runners/langchain.py`,
  `judges/langchain.py`. `runners/tools.py` builds framework-neutral `AgentTool`s (name +
  JSON schema + callable); `runners/prompting.py` and `runners/retry.py` hold the prompt and
  retry rules both adapters share; the adapters wrap them. `tests/test_framework_isolation.py`
  guards this: it asserts no other module under `src/skill_lens/` imports `pydantic_ai`,
  `langchain*` or `langgraph` at the top level.
```

- [ ] **Step 9: Build and test the docs**

Run: `uv sync --group docs && uv run mkdocs build --strict && uv run pytest tests/test_docs.py tests/test_naming.py tests/test_release_workflow.py tests/test_supply_chain.py -q`
Expected: build clean; all pass.

- [ ] **Step 10: Commit**

```bash
git add docs README.md ARCHITECTURE.md CLAUDE.md .github/workflows/release.yml
git commit -m "docs: describe the LangChain runner and judge"
```

---

### Task 8: Final verification and the pull request

- [ ] **Step 1: The whole gate, as CI runs it**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest -q && uv run skill-lens list ./examples && uv audit --preview-features audit --locked
```

Expected: every command exits 0. If `uv audit` reports a finding in a new LangChain dependency, do not add an `ignore` — report it; the fix is a version floor or an `ignore-until-fixed` entry in `[tool.uv.audit]`, decided by a person.

- [ ] **Step 2: Open the PR**

```bash
git push -u origin HEAD
gh pr create --assignee @EmadMokhtar --title "feat: run cases and grade rubrics through LangChain" --body "$(cat <<'BODY'
## Summary

- `--runner langchain` and `judge = "langchain"`: LangChain 1.x's `create_agent` and `with_structured_output` behind the unchanged `Runner` / `Judge` protocols, installable as `skill-lens[langchain]`.
- The prompt rules and the retry loop both adapters share move to `runners/prompting.py` and `runners/retry.py`.
- Anthropic joins both extras so a matrix run (M8 part 2) can name one `--model`.
- Offline tests against a scripted `BaseChatModel`; three cassette tests skip until recorded.

Spec: `docs/superpowers/specs/2026-09-11-skill-lens-m8-design.md`.

## Test plan

- [ ] `uv run pytest -q` green with the cassettes skipped
- [ ] `uv run mkdocs build --strict`
- [ ] Cassettes recorded with `uv run pytest tests/test_cassettes.py -k langchain --record-mode=once` (needs `OPENAI_API_KEY`) — in this PR or a follow-up `test:` PR

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
)"
```
