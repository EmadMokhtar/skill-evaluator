# skill-lens M7 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a failing case explain itself in every reporter, let one case be rerun by name, bring `init` up to M6, and give newcomers a versioned comparative example, an annotated config and an end-to-end quickstart.

**Architecture:** One new module, `reporters/failure_context.py`, computes the excerpt every reporter renders. `--case` follows `--tag` through `_plan_work`, `RunReport` and `evaluate_gate`. `init` grows a `scaffold_target` rule and a batch mode in `cli.py`, with the fifth case in `scaffold.py`. Everything else is content: examples and docs.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, pytest, ruff, mkdocs. `uv run` prefixes every command.

**Spec:** `docs/superpowers/specs/2026-09-11-skill-lens-m7-design.md`

## Global Constraints

- Every test is zero-cost, offline and deterministic. `FakeRunner` drives every reporter test; `CliRunner` drives every flag test.
- `skill_lens` (underscore) never appears in user-facing output; the user-facing name is `skill-lens`.
- All file IO pins `encoding="utf-8"`.
- No agent-framework type outside `runners/pydantic_ai.py` and `judges/pydantic_ai.py`.
- Conventional Commits on every commit. Commit messages end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Documentation ships with the change. After every task that touches `src/`, the matching `docs/` page is updated in the same task.
- `errored` ≠ `failed`; authoring errors exit 2; exit codes are the CI contract.
- Run `uv run ruff check . && uv run ruff format .` before every commit.
- `--case` has **no** config key. `full_output` has **both** a config key and a flag pair.
- Constants: `OUTPUT_LIMIT = 500`, `ARGUMENT_LIMIT = 80`, `TOOL_CALL_LIMIT = 20`.

---

## File structure

| File | Responsibility | Tasks |
| --- | --- | --- |
| `src/skill_lens/reporters/failure_context.py` (new) | The excerpt a non-passing case shows: output, cut count, tool-call lines. No markup. | 1 |
| `src/skill_lens/reporters/console.py` | Renders the excerpt as indented plain text. | 2, 7 |
| `src/skill_lens/reporters/markdown.py` | Renders it in fenced blocks inside the failures `<details>`. | 3, 7 |
| `src/skill_lens/reporters/junit.py` | Appends it to `<failure>`/`<error>` bodies. | 4, 7 |
| `src/skill_lens/reporters/json_reporter.py` | Emits `case_filtered_skills`. | 6 |
| `src/skill_lens/config.py` | `full_output` key. | 5 |
| `src/skill_lens/cli.py` | `--full-output/--no-full-output`, `--case`, `init` target rule and batch mode. | 5, 7, 10, 11 |
| `action.yml` | `full-output` and `case` inputs. | 5, 7 |
| `src/skill_lens/models.py` | `RunReport.case_filtered_skills`. | 6 |
| `src/skill_lens/orchestrator.py` | `_plan_work` applies the case filter. | 6 |
| `src/skill_lens/gating.py` | Fourth zero-cases reason. | 7 |
| `src/skill_lens/cases/loader.py` | Unfilled scan covers keys; `discover_eval_paths` becomes public. | 8, 11 |
| `src/skill_lens/scaffold.py` | Fifth case; `eval_filename`; `scaffold_target`. | 9, 10 |
| `examples/greeting/*` | Version 1.1.0 and the assertion only it satisfies. | 12 |
| `examples/skill-lens.toml` (new) | Annotated realistic config. | 13 |
| `docs/getting-started.md` | End-to-end tutorial. | 14 |
| `docs/*`, `README.md`, `ARCHITECTURE.md`, `CLAUDE.md` | Reference updates, stale text, invariants. | 5, 7, 8, 11–15 |

---

### Task 1: The shared failure excerpt — `reporters/failure_context.py`

**Files:**
- Create: `src/skill_lens/reporters/failure_context.py`
- Test: `tests/test_failure_context.py`

**Interfaces:**
- Produces:
  - `OUTPUT_LIMIT: int = 500`, `ARGUMENT_LIMIT: int = 80`, `TOOL_CALL_LIMIT: int = 20`
  - `@dataclass(frozen=True) class FailureContext: output: str; cut: int; tool_calls: list[str]; more_calls: int`
  - `failure_context(outcome: CaseOutcome, *, limit: int | None) -> FailureContext | None`
  - `format_tool_call(call: ToolCall) -> str`
  - `cut_note(context: FailureContext) -> str` → `"… (1,842 more characters; --full-output prints them)"`
  - `more_calls_note(context: FailureContext) -> str` → `"… +3 more calls"`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_failure_context.py
"""The excerpt a non-passing case shows. One helper, three reporters."""

from __future__ import annotations

from skill_lens.models import CaseOutcome, EvalScore, RunResult, ToolCall
from skill_lens.reporters.failure_context import (
    ARGUMENT_LIMIT,
    OUTPUT_LIMIT,
    TOOL_CALL_LIMIT,
    FailureContext,
    cut_note,
    failure_context,
    format_tool_call,
    more_calls_note,
)


_SAID = "the agent said this"


def _outcome(status="failed", arm="candidate", result=None):
    return CaseOutcome(
        skill_name="pdf",
        case_name="extracts",
        runner="fake",
        status=status,
        scores=[EvalScore(evaluator="assertion", passed=status == "passed")],
        result=RunResult(output=_SAID) if result is None else result,
        arm=arm,
    )


def test_a_passing_case_has_no_context():
    assert failure_context(_outcome(status="passed"), limit=OUTPUT_LIMIT) is None


def test_a_baseline_outcome_has_no_context():
    assert failure_context(_outcome(arm="baseline"), limit=OUTPUT_LIMIT) is None


def test_an_outcome_without_a_result_has_no_context():
    outcome = CaseOutcome(
        skill_name="pdf", case_name="x", runner="fake", status="errored", scores=[], result=None
    )
    assert failure_context(outcome, limit=OUTPUT_LIMIT) is None


def test_a_failed_case_carries_its_output_uncut_when_under_the_limit():
    context = failure_context(_outcome(), limit=OUTPUT_LIMIT)
    assert context == FailureContext(output=_SAID, cut=0, tool_calls=[], more_calls=0)


def test_an_errored_case_carries_its_output_too():
    context = failure_context(_outcome(status="errored"), limit=OUTPUT_LIMIT)
    assert context is not None
    assert context.output == _SAID


def test_the_cut_is_exact_at_the_boundary():
    for length, expected_cut in [(499, 0), (500, 0), (501, 1), (2342, 1842)]:
        outcome = _outcome(result=RunResult(output="x" * length))
        context = failure_context(outcome, limit=500)
        assert context is not None
        assert context.cut == expected_cut, length
        assert len(context.output) == min(length, 500), length


def test_no_limit_cuts_nothing():
    outcome = _outcome(result=RunResult(output="x" * 5000))
    context = failure_context(outcome, limit=None)
    assert context is not None
    assert context.cut == 0
    assert len(context.output) == 5000


def test_the_cut_note_states_the_exact_count_and_the_way_to_see_it():
    context = FailureContext(output="", cut=1842, tool_calls=[], more_calls=0)
    assert cut_note(context) == "… (1,842 more characters; --full-output prints them)"


def test_tool_calls_render_in_order_with_json_arguments():
    outcome = _outcome(
        result=RunResult(
            output="",
            tool_calls=[
                ToolCall(name="lookup_order", arguments={"order_id": "1234"}),
                ToolCall(name="issue_refund", arguments={"order_id": "1234", "amount": 12.5}),
                ToolCall(name="list_files", arguments={}),
            ],
        )
    )
    context = failure_context(outcome, limit=OUTPUT_LIMIT)
    assert context is not None
    assert context.tool_calls == [
        'lookup_order(order_id="1234")',
        'issue_refund(order_id="1234", amount=12.5)',
        "list_files()",
    ]
    assert context.more_calls == 0


def test_a_long_argument_value_is_cut_with_a_marker():
    call = ToolCall(name="write_file", arguments={"path": "a.md", "content": "y" * 500})
    text = format_tool_call(call)
    # The JSON string is `"yyyy..."`; the opening quote counts toward the limit.
    assert text.startswith('write_file(path="a.md", content="' + "y" * (ARGUMENT_LIMIT - 1))
    assert text.endswith("…)")
    assert len(text) < 500


def test_non_ascii_arguments_stay_readable():
    call = ToolCall(name="greet", arguments={"name": "Zoë"})
    assert format_tool_call(call) == 'greet(name="Zoë")'


def test_an_unserialisable_argument_does_not_raise():
    call = ToolCall(name="odd", arguments={"when": object()})
    assert format_tool_call(call).startswith("odd(when=")


def test_calls_beyond_the_cap_are_counted_not_listed():
    calls = [ToolCall(name=f"t{i}", arguments={}) for i in range(TOOL_CALL_LIMIT + 5)]
    outcome = _outcome(result=RunResult(output="", tool_calls=calls))
    context = failure_context(outcome, limit=OUTPUT_LIMIT)
    assert context is not None
    assert len(context.tool_calls) == TOOL_CALL_LIMIT
    assert context.tool_calls[0] == "t0()"
    assert context.more_calls == 5
    assert more_calls_note(context) == "… +5 more calls"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_failure_context.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'skill_lens.reporters.failure_context'`

- [ ] **Step 3: Write the module**

```python
# src/skill_lens/reporters/failure_context.py
"""What a non-passing case actually did: its output and its tool calls.

`contains('1234') did not hold` says that a case failed, not why. This module
computes the excerpt that answers "why" -- and it is the *only* place that
does, so the console, Markdown and JUnit reporters can never disagree on what
was shown. They own the markup; this owns the text.

The excerpt exists only for non-passing candidate outcomes. Fifty green cases
must not print fifty transcripts, and a baseline outcome is not the verdict --
the delta block is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from skill_lens.models import CaseOutcome, ToolCall

# Characters of agent output shown under a failing case by default. Enough to
# read what the agent said; small enough to keep a CI log readable when a
# suite has many red cases. `--full-output` lifts it.
OUTPUT_LIMIT = 500

# Characters per tool-call argument value. A `write_file` call carries the
# whole document; the reader needs the file name, not the document.
ARGUMENT_LIMIT = 80

# Tool calls listed before the rest are counted rather than shown.
TOOL_CALL_LIMIT = 20


@dataclass(frozen=True)
class FailureContext:
    """The excerpt. `output` is already cut; `cut` says by how much."""

    output: str
    cut: int
    tool_calls: list[str]
    more_calls: int


def _argument(value: object) -> str:
    # `default=str` because arguments come from a provider as JSON but a
    # library caller may hand a runner anything; a reporter must never raise.
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > ARGUMENT_LIMIT:
        return text[:ARGUMENT_LIMIT] + "…"
    return text


def format_tool_call(call: ToolCall) -> str:
    """`name(arg=<json>, ...)`, arguments in the order the model sent them."""
    arguments = ", ".join(f"{name}={_argument(value)}" for name, value in call.arguments.items())
    return f"{call.name}({arguments})"


def cut_note(context: FailureContext) -> str:
    """Never a silent cut: the exact count, and how to see the rest."""
    return f"… ({context.cut:,} more characters; --full-output prints them)"


def more_calls_note(context: FailureContext) -> str:
    return f"… +{context.more_calls} more calls"


def failure_context(outcome: CaseOutcome, *, limit: int | None) -> FailureContext | None:
    """The excerpt for `outcome`, or None when nothing should be expanded.

    `limit=None` means no cap on the output (the `full_output` path).
    """
    if outcome.status == "passed" or outcome.arm != "candidate" or outcome.result is None:
        return None
    output = outcome.result.output
    cut = 0
    if limit is not None and len(output) > limit:
        cut = len(output) - limit
        output = output[:limit]
    calls = outcome.result.tool_calls
    return FailureContext(
        output=output,
        cut=cut,
        tool_calls=[format_tool_call(call) for call in calls[:TOOL_CALL_LIMIT]],
        more_calls=max(0, len(calls) - TOOL_CALL_LIMIT),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_failure_context.py -v`
Expected: all PASS

- [ ] **Step 5: Lint, format, commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/reporters/failure_context.py tests/test_failure_context.py
git commit -m "feat: compute the output and tool-call excerpt a failing case shows

One helper feeds every reporter so they can never disagree on what was
shown. A cut is never silent: the note carries the exact count.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: The console shows the excerpt

**Files:**
- Modify: `src/skill_lens/reporters/console.py` (`render_console`, both branches)
- Test: `tests/test_reporters.py`

**Interfaces:**
- Consumes: `failure_context`, `cut_note`, `more_calls_note`, `OUTPUT_LIMIT` from Task 1.
- Produces: `render_console(report, gate=None, delta=None, output_limit: int | None = OUTPUT_LIMIT) -> str`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_reporters.py`)

```python
from skill_lens.models import ToolCall  # add to the existing models import


def test_a_failing_case_shows_the_agents_output_and_a_passing_one_does_not():
    text = render_console(_report())
    assert "        output: no" in text
    assert "output: yes" not in text


def test_an_empty_output_is_stated_not_omitted():
    report = RunReport(
        outcomes=[
            CaseOutcome(
                skill_name="pdf",
                case_name="silent",
                runner="fake",
                status="failed",
                scores=[EvalScore(evaluator="assertion", passed=False, detail="nope")],
                result=RunResult(output=""),
            )
        ]
    )
    assert "        output: (empty)" in render_console(report)


def _long_output_report(length=2342):
    return RunReport(
        outcomes=[
            CaseOutcome(
                skill_name="pdf",
                case_name="verbose",
                runner="fake",
                status="failed",
                scores=[EvalScore(evaluator="assertion", passed=False, detail="nope")],
                result=RunResult(output="x" * length),
            )
        ]
    )


def test_a_cut_output_says_exactly_how_much_was_cut():
    text = render_console(_long_output_report())
    assert "        … (1,842 more characters; --full-output prints them)" in text
    assert "x" * 500 in text
    assert "x" * 501 not in text


def test_no_output_limit_prints_everything():
    text = render_console(_long_output_report(), output_limit=None)
    assert "x" * 2342 in text
    assert "more characters" not in text


def test_multi_line_output_keeps_every_line_indented():
    report = RunReport(
        outcomes=[
            CaseOutcome(
                skill_name="pdf",
                case_name="wraps",
                runner="fake",
                status="failed",
                scores=[EvalScore(evaluator="assertion", passed=False, detail="nope")],
                result=RunResult(output="first line\nsecond line"),
            )
        ]
    )
    text = render_console(report)
    assert "        output: first line\n        second line" in text


def test_tool_calls_are_listed_under_the_output_and_omitted_when_none():
    with_calls = RunReport(
        outcomes=[
            CaseOutcome(
                skill_name="refund",
                case_name="refuses",
                runner="fake",
                status="failed",
                scores=[EvalScore(evaluator="assertion", passed=False, detail="nope")],
                result=RunResult(
                    output="sorry",
                    tool_calls=[ToolCall(name="lookup_order", arguments={"order_id": "1234"})],
                ),
            )
        ]
    )
    text = render_console(with_calls)
    assert '        tool calls:\n            lookup_order(order_id="1234")' in text
    assert "tool calls:" not in render_console(_report())


def test_the_comparative_branch_shows_the_candidates_output_but_not_the_baselines():
    report = _two_arm_report()
    # Make the candidate fail with a distinctive output and the baseline fail
    # with another; only the candidate's may appear.
    report.outcomes[0].status = "failed"
    report.outcomes[0].scores = [EvalScore(evaluator="assertion", passed=False, detail="nope")]
    report.outcomes[0].result = RunResult(output="CANDIDATE-SAID")
    for outcome in report.outcomes:
        if outcome.arm == "baseline":
            outcome.status = "failed"
            outcome.result = RunResult(output="BASELINE-SAID")
    text = render_console(report, delta=build_delta(report))
    assert "output: CANDIDATE-SAID" in text
    assert "BASELINE-SAID" not in text
```

`_two_arm_report()` (already in the file) returns two passed candidate repetitions followed by two failed baseline ones, all with output `"x"`, so `outcomes[0]` is a candidate and the assignments above are what the test needs.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_reporters.py -k "output or tool_calls or comparative_branch" -v`
Expected: FAIL — `output:` lines absent; `output_limit` is an unexpected keyword.

- [ ] **Step 3: Implement**

In `src/skill_lens/reporters/console.py`, add the import and two helpers, then use them in both branches:

```python
from skill_lens.models import CaseOutcome, RunReport
from skill_lens.reporters.failure_context import (
    OUTPUT_LIMIT,
    cut_note,
    failure_context,
    more_calls_note,
)

_INDENT = "        "  # 8 spaces: the evaluator-detail column
_DEEPER = "            "  # 12 spaces: the check-evidence column


def _failure_lines(outcome: CaseOutcome, output_limit: int | None) -> list[str]:
    """Why a non-passing outcome did not pass, then what the agent actually did.

    The first part is the M3 renderer: each failing evaluator's detail and each
    failing check's evidence -- the evidence is the point of a judge verdict,
    since a summary line cannot tell an author whether the judge read the
    response or invented a reason. The second part is what M7 added: the
    output and the tool calls, so `did not hold` comes with the text it was
    checked against.
    """
    lines: list[str] = []
    for score in outcome.scores:
        if not score.passed:
            lines.append(f"{_INDENT}{score.evaluator}: {score.detail}")
            for check in score.checks:
                if not check.passed:
                    lines.append(f"{_DEEPER}{check.id}: {check.evidence or 'no evidence given'}")
    if outcome.result is not None and outcome.result.error:
        lines.append(f"{_INDENT}error: {outcome.result.error}")
    lines.extend(_context_lines(outcome, output_limit))
    return lines


def _context_lines(outcome: CaseOutcome, output_limit: int | None) -> list[str]:
    context = failure_context(outcome, limit=output_limit)
    if context is None:
        return []
    lines: list[str] = []
    if context.output:
        first, *rest = context.output.split("\n")
        lines.append(f"{_INDENT}output: {first}")
        lines.extend(f"{_INDENT}{line}" for line in rest)
    else:
        # "The agent said nothing" is the most useful fact about a failed
        # assertion; an absent line would look like the feature is missing.
        lines.append(f"{_INDENT}output: (empty)")
    if context.cut:
        lines.append(f"{_INDENT}{cut_note(context)}")
    if context.tool_calls:
        lines.append(f"{_INDENT}tool calls:")
        lines.extend(f"{_DEEPER}{call}" for call in context.tool_calls)
        if context.more_calls:
            lines.append(f"{_DEEPER}{more_calls_note(context)}")
    return lines
```

Change the signature and both branches of `render_console`:

```python
def render_console(
    report: RunReport,
    gate: GateResult | None = None,
    delta: Delta | None = None,
    output_limit: int | None = OUTPUT_LIMIT,
) -> str:
    """...(keep the existing docstring, then add:)

    `output_limit` caps the agent output shown under a non-passing case; None
    prints all of it (`--full-output`).
    """
    lines: list[str] = []
    if delta is None:
        for outcome in report.outcomes:
            mark = _MARKS[outcome.status]
            lines.append(f"[{mark}] {outcome.skill_name} :: {outcome.case_name} ({outcome.runner})")
            lines.extend(_failure_lines(outcome, output_limit))
    else:
        for case in delta.cases:
            lines.append(_case_line(case))
            failing = next(
                (
                    o
                    for o in report.candidate_outcomes
                    if (o.skill_name, o.case_name, o.runner)
                    == (case.skill_name, case.case_name, case.runner)
                    and o.status != "passed"
                ),
                None,
            )
            if failing is not None:
                lines.extend(_failure_lines(failing, output_limit))
            if case.low_signal:
                lines.append(f"        low-signal: {', '.join(case.low_signal)}")
```

Note: `_failure_lines` runs for **every** outcome in the single-arm branch; for a passed outcome it yields no evaluator lines (all passed) and `_context_lines` returns `[]`, so passing cases stay one line, exactly as before.

- [ ] **Step 4: Run the whole reporter file**

Run: `uv run pytest tests/test_reporters.py -v`
Expected: all PASS, including the pre-existing tests (the refactor must not change any existing line).

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/reporters/console.py tests/test_reporters.py
git commit -m "feat: print a failing case's output and tool calls in the console

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The Markdown report shows the excerpt

**Files:**
- Modify: `src/skill_lens/reporters/markdown.py` (`_failure_lines`, `_failures`, `render_markdown`)
- Test: `tests/test_markdown_reporter.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: `render_markdown(report, gate=None, delta=None, max_chars=None, output_limit: int | None = OUTPUT_LIMIT) -> str`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_markdown_reporter.py`)

```python
from skill_lens.models import ToolCall  # add to the existing models import


def _without_fenced_blocks(text: str) -> str:
    """The document with every fenced code block removed, for structural checks."""
    return re.sub(r"`{3,}\n.*?\n`{3,}", "", text, flags=re.DOTALL)


def test_the_failures_block_shows_the_output_in_a_fence():
    report = RunReport(outcomes=[_outcome(name="rejects", status="failed",
        scores=[EvalScore(evaluator="assertion", passed=False, detail="nope")],
        result=RunResult(output="I cannot refund order 1234."))])
    text = render_markdown(report)
    assert "Output:" in text
    assert "```\nI cannot refund order 1234.\n```" in text


def test_a_passing_case_shows_no_output():
    assert "Output:" not in render_markdown(RunReport(outcomes=[_outcome()]))


def test_output_containing_a_closing_details_tag_stays_inside_its_fence():
    hostile = "fine.\n</details>\n\n# not a heading"
    report = RunReport(outcomes=[_outcome(name="rejects", status="failed",
        scores=[EvalScore(evaluator="assertion", passed=False, detail="nope")],
        result=RunResult(output=hostile))])
    text = render_markdown(report)
    structural = _without_fenced_blocks(text)
    assert structural.count("<details>") == structural.count("</details>") == 1
    assert "# not a heading" not in structural


def test_output_containing_triple_backticks_gets_a_longer_fence():
    report = RunReport(outcomes=[_outcome(name="rejects", status="failed",
        scores=[EvalScore(evaluator="assertion", passed=False, detail="nope")],
        result=RunResult(output="see ```this``` block"))])
    assert "````\nsee ```this``` block\n````" in render_markdown(report)


def test_a_cut_output_carries_the_count_and_no_limit_prints_all():
    report = RunReport(outcomes=[_outcome(name="verbose", status="failed",
        scores=[EvalScore(evaluator="assertion", passed=False, detail="nope")],
        result=RunResult(output="x" * 2342))])
    capped = render_markdown(report)
    assert "… (1,842 more characters; --full-output prints them)" in capped
    assert "x" * 501 not in capped
    full = render_markdown(report, output_limit=None)
    assert "x" * 2342 in full
    assert "more characters" not in full


def test_tool_calls_render_as_a_fenced_list():
    report = RunReport(outcomes=[_outcome(name="refuses", status="failed",
        scores=[EvalScore(evaluator="assertion", passed=False, detail="nope")],
        result=RunResult(output="sorry",
            tool_calls=[ToolCall(name="lookup_order", arguments={"order_id": "1234"})]))])
    text = render_markdown(report)
    assert 'Tool calls:\n\n```\nlookup_order(order_id="1234")\n```' in text


def test_an_empty_output_is_stated():
    report = RunReport(outcomes=[_outcome(name="silent", status="failed",
        scores=[EvalScore(evaluator="assertion", passed=False, detail="nope")],
        result=RunResult(output=""))])
    assert "```\n(empty)\n```" in render_markdown(report)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_markdown_reporter.py -k "output or tool_calls or empty" -v`
Expected: FAIL — `Output:` absent; `output_limit` unexpected keyword.

- [ ] **Step 3: Implement**

In `src/skill_lens/reporters/markdown.py`:

```python
from skill_lens.reporters.failure_context import (
    OUTPUT_LIMIT,
    cut_note,
    failure_context,
    more_calls_note,
)


def _failure_lines(outcome: CaseOutcome, repeat: int, output_limit: int | None) -> list[str]:
    label = outcome.case_name
    if repeat > 1:
        # Matches the JUnit reporter's suffix: without it, --repeat 5 renders
        # five byte-identical blocks for one case.
        label = f"{label} [run {outcome.repeat_index + 1}/{repeat}]"
    lines = [
        f"**{_code(outcome.skill_name)} :: {_code(label)}** ({outcome.runner}) — {outcome.status}"
    ]
    for score in outcome.scores:
        if score.passed:
            continue
        detail = _safe_text(score.detail, indent="  ")
        lines.append(f"- {_code(score.evaluator)}: {detail}")
        for check in score.checks:
            if not check.passed:
                evidence = _safe_text(check.evidence, indent="      ") or "no evidence given"
                lines.append(f"    - {_code(check.id)}: {evidence}")
    if outcome.result is not None and outcome.result.error:
        lines.extend(["", _fenced(outcome.result.error)])
    context = failure_context(outcome, limit=output_limit)
    if context is not None:
        # A fenced block is the one Markdown context where model output is
        # inert: `</details>` and `#` inside it are literal text. `_fenced`
        # already outgrows any run of backticks in the content.
        lines.extend(["", "Output:", "", _fenced(context.output or "(empty)")])
        if context.cut:
            lines.extend(["", cut_note(context)])
        if context.tool_calls:
            calls = list(context.tool_calls)
            if context.more_calls:
                calls.append(more_calls_note(context))
            lines.extend(["", "Tool calls:", "", _fenced("\n".join(calls))])
    lines.append("")
    return lines


def _failures(report: RunReport, output_limit: int | None) -> str:
    ...  # unchanged except: body.extend(_failure_lines(outcome, report.repeat, output_limit))
```

And in `render_markdown`, add `output_limit: int | None = OUTPUT_LIMIT` after `max_chars`, pass it: `_failures(report, output_limit)`. Extend the docstring with one sentence: "`output_limit` caps the agent output shown under each failing case; None prints all of it."

- [ ] **Step 4: Run the Markdown tests**

Run: `uv run pytest tests/test_markdown_reporter.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/reporters/markdown.py tests/test_markdown_reporter.py
git commit -m "feat: show a failing case's output and tool calls in the Markdown summary

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The JUnit report shows the excerpt

**Files:**
- Modify: `src/skill_lens/reporters/junit.py` (`_failure_body`, `_error_body`, `render_junit`)
- Test: `tests/test_junit_reporter.py`

**Interfaces:**
- Produces: `render_junit(report, gate=None, delta=None, output_limit: int | None = OUTPUT_LIMIT) -> str`.

- [ ] **Step 1: Write the failing tests** (append)

```python
from skill_lens.models import ToolCall  # add to the existing models import


def _failed(output, tool_calls=()):
    return _outcome(
        name="rejects",
        status="failed",
        scores=[EvalScore(evaluator="assertion", passed=False, detail="nope")],
        result=RunResult(output=output, tool_calls=list(tool_calls)),
    )


def test_a_failure_body_carries_the_output_after_the_detail():
    root = _parse(RunReport(outcomes=[_failed("I cannot refund order 1234.")]))
    failure = root.find("testsuite/testcase/failure")
    assert failure.text.split("\n")[0] == "assertion: nope"
    assert "output:\nI cannot refund order 1234." in failure.text
    # The attribute stays the one-line summary.
    assert failure.get("message") == "assertion: nope"


def test_an_error_body_carries_the_output_too():
    outcome = _outcome(
        name="boom",
        status="errored",
        scores=[],
        result=RunResult(output="partial answer", error="provider returned 500"),
    )
    root = _parse(RunReport(outcomes=[outcome]))
    error = root.find("testsuite/testcase/error")
    assert error.text.startswith("provider returned 500")
    assert "output:\npartial answer" in error.text


def test_tool_calls_and_the_cut_note_reach_the_body():
    calls = [ToolCall(name="lookup_order", arguments={"order_id": "1234"})]
    root = _parse(RunReport(outcomes=[_failed("x" * 2342, calls)]))
    text = root.find("testsuite/testcase/failure").text
    assert "… (1,842 more characters; --full-output prints them)" in text
    assert 'tool calls:\nlookup_order(order_id="1234")' in text


def test_no_output_limit_puts_the_whole_output_in_the_body():
    root = _parse(RunReport(outcomes=[_failed("x" * 2342)]), output_limit=None)
    assert "x" * 2342 in root.find("testsuite/testcase/failure").text


def test_control_characters_in_the_output_are_stripped_so_the_document_parses():
    root = _parse(RunReport(outcomes=[_failed("bad\x00byte\x01here")]))
    assert "badbytehere" in root.find("testsuite/testcase/failure").text


def test_an_empty_output_is_stated_in_the_body():
    root = _parse(RunReport(outcomes=[_failed("")]))
    assert "output:\n(empty)" in root.find("testsuite/testcase/failure").text
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_junit_reporter.py -k "output or tool_calls" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
from skill_lens.reporters.failure_context import (
    OUTPUT_LIMIT,
    cut_note,
    failure_context,
    more_calls_note,
)


def _context_lines(outcome: CaseOutcome, output_limit: int | None) -> list[str]:
    """The output and tool calls, as plain lines appended to a body."""
    context = failure_context(outcome, limit=output_limit)
    if context is None:
        return []
    lines = ["output:", context.output or "(empty)"]
    if context.cut:
        lines.append(cut_note(context))
    if context.tool_calls:
        lines.append("tool calls:")
        lines.extend(context.tool_calls)
        if context.more_calls:
            lines.append(more_calls_note(context))
    return lines


def _failure_body(outcome: CaseOutcome, output_limit: int | None) -> str:
    """...(keep docstring)"""
    lines: list[str] = []
    for score in outcome.scores:
        if score.passed:
            continue
        lines.append(f"{score.evaluator}: {score.detail}")
        for check in score.checks:
            if not check.passed:
                lines.append(
                    f"{score.evaluator}/{check.id}: {check.evidence or 'no evidence given'}"
                )
    head = "\n".join(lines) or "no failing evaluator reported a detail"
    return "\n".join([head, *_context_lines(outcome, output_limit)])


def _error_body(outcome: CaseOutcome, output_limit: int | None) -> str:
    """...(keep docstring)"""
    if outcome.result is not None and outcome.result.error:
        head = outcome.result.error
    else:
        details = [f"{score.evaluator}: {score.detail}" for score in outcome.scores if score.errored]
        head = "\n".join(details) if details else "no detail was reported"
    return "\n".join([head, *_context_lines(outcome, output_limit)])
```

`render_junit` gains `output_limit: int | None = OUTPUT_LIMIT` and passes it to both body builders. `_message(body)` is unchanged: it takes the first line, which is still the evaluator detail or the runner error.

- [ ] **Step 4: Run the JUnit tests**

Run: `uv run pytest tests/test_junit_reporter.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/reporters/junit.py tests/test_junit_reporter.py
git commit -m "feat: carry a failing case's output and tool calls in the JUnit body

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: `full_output` config key, `--full-output/--no-full-output`, the action input, docs

**Files:**
- Modify: `src/skill_lens/config.py`, `src/skill_lens/cli.py`, `action.yml`
- Modify: `docs/configuration.md`, `docs/cli.md`, `docs/gating.md`
- Test: `tests/test_config.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `OUTPUT_LIMIT` (Task 1); the `output_limit` parameter on all three renderers (Tasks 2–4).
- Produces: `Config.full_output: bool = False`; CLI flag pair; action input `full-output`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_full_output_defaults_off_and_loads_from_the_file(tmp_path):
    assert Config().full_output is False
    path = tmp_path / "skill-lens.toml"
    path.write_text("full_output = true\n", encoding="utf-8")
    assert load_config(path=path).full_output is True
```

Append to `tests/test_cli.py`:

```python
# A task long enough that the fake runner's echo of it exceeds OUTPUT_LIMIT.
LONG_TASK = "word " * 200

VERBOSE_FAILING_CASES_YAML = f"""cases:
  - name: cannot pass
    task: {LONG_TASK.strip()}
    assertions:
      - kind: contains
        value: definitely-not-in-output
"""


def test_a_failing_case_prints_its_output_cut_by_default(tmp_path):
    skill_dir = _make_skill(tmp_path, cases=VERBOSE_FAILING_CASES_YAML)
    result = runner.invoke(app, ["run", str(skill_dir)])
    assert result.exit_code == 1
    assert "output: [fake] pdf handled: word word" in result.stdout
    assert "more characters; --full-output prints them" in result.stdout


def test_full_output_prints_everything(tmp_path):
    skill_dir = _make_skill(tmp_path, cases=VERBOSE_FAILING_CASES_YAML)
    result = runner.invoke(app, ["run", str(skill_dir), "--full-output"])
    assert "more characters" not in result.stdout
    assert LONG_TASK.strip() in result.stdout


def test_no_full_output_flag_wins_over_a_true_config(tmp_path):
    skill_dir = _make_skill(tmp_path, cases=VERBOSE_FAILING_CASES_YAML)
    config = tmp_path / "skill-lens.toml"
    config.write_text("full_output = true\n", encoding="utf-8")
    result = runner.invoke(
        app, ["run", str(skill_dir), "--config", str(config), "--no-full-output"]
    )
    assert "more characters" in result.stdout


def test_the_config_alone_lifts_the_cap(tmp_path):
    skill_dir = _make_skill(tmp_path, cases=VERBOSE_FAILING_CASES_YAML)
    config = tmp_path / "skill-lens.toml"
    config.write_text("full_output = true\n", encoding="utf-8")
    result = runner.invoke(app, ["run", str(skill_dir), "--config", str(config)])
    assert "more characters" not in result.stdout


def test_a_passing_run_prints_no_output_lines(tmp_path):
    skill_dir = _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(skill_dir)])
    assert result.exit_code == 0
    assert "output:" not in result.stdout
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_config.py::test_full_output_defaults_off_and_loads_from_the_file tests/test_cli.py -k "full_output or prints_its_output or no_output_lines" -v`
Expected: FAIL — `Config` rejects `full_output` (extra="forbid"); `--full-output` is not an option.

- [ ] **Step 3: Config**

In `src/skill_lens/config.py`, add after `keep_workspace: bool = False`:

```python
    full_output: bool = False
```

And add this paragraph to the `Config` docstring, after the `keep_workspace` paragraph:

```
    `full_output` lifts the 500-character cap on the agent output printed
    under a non-passing case, in every reporter. `--full-output` /
    `--no-full-output` override it in either direction, like
    `keep_workspace`. The cap itself is never silent: a cut output always
    states exactly how many characters were removed.
```

- [ ] **Step 4: CLI**

In `src/skill_lens/cli.py`:

Add the import: `from skill_lens.reporters.failure_context import OUTPUT_LIMIT`.

Add the option after `keep_workspace`:

```python
    full_output: Annotated[
        bool | None,
        typer.Option(
            "--full-output/--no-full-output",
            help="Print a failing case's whole output instead of the first 500 characters.",
        ),
    ] = None,
```

After `resolved_keep_workspace = ...`:

```python
        resolved_full_output = full_output if full_output is not None else settings.full_output
        # None means "no cap" to every reporter.
        output_limit = None if resolved_full_output else OUTPUT_LIMIT
```

Thread it into the three render calls:

```python
    typer.echo(render_console(report, gate=gate, delta=delta, output_limit=output_limit))
    ...
        (json_output, "JSON", lambda: render_json(report, gate=gate, delta=delta)),
        (
            junit_output,
            "JUnit",
            lambda: render_junit(report, gate=gate, delta=delta, output_limit=output_limit),
        ),
        (
            markdown_output,
            "Markdown",
            lambda: render_markdown(
                report,
                gate=gate,
                delta=delta,
                max_chars=markdown_max_chars,
                output_limit=output_limit,
            ),
        ),
```

- [ ] **Step 5: Action**

In `action.yml`:

Add the input after `keep-workspace`:

```yaml
  full-output:
    description: Print a failing case's whole output instead of the first 500 characters. true or false.
```

Add the env line after `SE_KEEP_WORKSPACE`:

```yaml
        SE_FULL_OUTPUT: ${{ inputs.full-output }}
```

Add the forwarding line after `add_flag --keep-workspace ...`:

```bash
        add_flag --full-output "$SE_FULL_OUTPUT" --no-full-output
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_config.py tests/test_cli.py tests/test_action.py tests/test_docs.py -v`
Expected: everything passes except `tests/test_docs.py::test_every_cli_option_is_documented` and `test_every_config_field_is_documented`, which fail until Step 7.

- [ ] **Step 7: Docs**

`docs/cli.md` — add to the usage block on the `[--keep-workspace | --no-keep-workspace]` line: `[--full-output | --no-full-output]`, and add a row to the `run` table after `--keep-workspace`:

```markdown
| `--full-output` / `--no-full-output` | unset | Print a failing case's whole output instead of the first 500 characters. Overrides the `full_output` config key in either direction; omitting both flags leaves the config file's value in effect |
```

After the table's closing paragraph ("Each flag overrides the corresponding key..."), add:

```markdown
A non-passing case prints what the agent actually did — its output, and every tool it
called — under the evaluator detail, in the console and in the JUnit and Markdown reports
alike. The output is cut at 500 characters by default; a cut is never silent (`… (1,842 more
characters; --full-output prints them)`). Passing cases stay one line. See
[what a failing case shows](gating.md#what-a-failing-case-shows).
```

`docs/configuration.md` — add a row after `keep_workspace`:

```markdown
| `full_output` | `false` | `--full-output` / `--no-full-output` |
```

and a paragraph after the `keep_workspace` paragraph:

```markdown
`full_output` lifts the 500-character cap on the agent output printed under a non-passing
case, in every reporter. `--full-output` / `--no-full-output` override it in either
direction; leaving both unset keeps the config file's value. The cap is never silent — a cut
output always states exactly how many characters were removed — so the default is safe to
leave in CI, where a long red log helps nobody, and `true` is the right committed value for
a repository that reads its failures locally.
```

`docs/gating.md` — add a new section before `## JSON report`:

```markdown
## What a failing case shows

A case that did not pass shows *why* in every reporter: each failing evaluator's detail and
each failing judge check's evidence, and then what the agent actually did — its output and
every tool call, in order.

```
[FAIL] order-support :: refuses a refund outside the return window (pydantic-ai)
        assertion: failed: contains('1234')
            contains[0]: contains('1234') did not hold
        output: I'm sorry, but that order was delivered 45 days ago, so it is
        outside our 30-day return window and I can't refund it.
        tool calls:
            lookup_order(order_id="1234")
```

The output is cut at 500 characters. A cut is never silent — the line `… (1,842 more
characters; --full-output prints them)` states exactly how much was removed — and an empty
output prints `output: (empty)`, because "the agent said nothing" is the single most useful
fact about a failed assertion. Tool-call argument values are cut at 80 characters and the
list at 20 calls, with a `… +N more calls` count. `--full-output` (or `full_output = true` in
[`skill-lens.toml`](configuration.md)) lifts the output cap.

Only **non-passing candidate** outcomes are expanded. Passing cases stay one line, and
baseline outcomes are never expanded — they are not the verdict; the delta block is. The
Markdown report renders the same excerpt in fenced blocks inside its failures section, and
the JUnit report appends it to the `<failure>` or `<error>` body. All three read one shared
excerpt, so they can never disagree on what was shown.
```

Note: the inner code fence in that section needs the same three-backtick fence as the rest of the page; write it as a normal fenced block (the block above is shown nested only because this plan is itself Markdown).

`docs/ci.md` needs no edit: "Every `skill-lens run` flag is available as a kebab-cased input" already covers `full-output`.

- [ ] **Step 8: Verify docs and everything**

Run: `uv run pytest tests/test_docs.py tests/test_action.py tests/test_cli.py tests/test_config.py -v && uv sync --group docs && uv run mkdocs build --strict`
Expected: all PASS; mkdocs builds with no warnings.

- [ ] **Step 9: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/config.py src/skill_lens/cli.py action.yml docs/cli.md docs/configuration.md docs/gating.md tests/test_config.py tests/test_cli.py
git commit -m "feat: add full_output and --full-output to lift the output cap

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The case filter in the orchestrator, the report model and the JSON report

**Files:**
- Modify: `src/skill_lens/models.py` (`RunReport`), `src/skill_lens/orchestrator.py` (`_Plan`, `_plan_work`, `run_evals`), `src/skill_lens/reporters/json_reporter.py`
- Test: `tests/test_orchestrator.py`, `tests/test_reporters.py`

**Interfaces:**
- Produces: `RunReport.case_filtered_skills: list[str]`; `run_evals(..., case_filter: str | None = None)`; `_plan_work(skills, runners, evals_path, tag, case_filter, baseline, repeat)`; JSON key `case_filtered_skills`.

- [ ] **Step 1: Write the failing tests**

Read `tests/test_orchestrator.py` lines 1–130 first for `_skill_with_cases` and `_runner`. Then append:

```python
def test_case_filter_selects_by_case_insensitive_substring(tmp_path):
    yaml_text = (
        "cases:\n"
        "  - name: Refuses a refund outside the window\n    task: a\n"
        "  - name: refunds inside the window\n    task: b\n"
        "  - name: greets\n    task: c\n"
    )
    report = run_evals(
        [_skill_with_cases(tmp_path, yaml_text=yaml_text)], [_runner()], case_filter="REFUND"
    )
    assert [o.case_name for o in report.outcomes] == [
        "Refuses a refund outside the window",
        "refunds inside the window",
    ]


def test_case_filter_and_tag_filter_both_apply(tmp_path):
    yaml_text = (
        "cases:\n"
        "  - name: refund smoke\n    task: a\n    tags: [smoke]\n"
        "  - name: refund deep\n    task: b\n"
    )
    report = run_evals(
        [_skill_with_cases(tmp_path, yaml_text=yaml_text)],
        [_runner()],
        tag="smoke",
        case_filter="refund",
    )
    assert [o.case_name for o in report.outcomes] == ["refund smoke"]


def test_a_case_filter_matching_nothing_is_case_filtered_not_skipped(tmp_path):
    yaml_text = "cases:\n  - name: greets\n    task: good\n"
    report = run_evals(
        [_skill_with_cases(tmp_path, yaml_text=yaml_text)], [_runner()], case_filter="refund"
    )
    assert report.total == 0
    assert report.skipped_skills == []
    assert report.tag_filtered_skills == []
    assert report.case_filtered_skills == ["pdf"]


def test_a_skill_emptied_by_the_tag_filter_is_not_also_case_filtered(tmp_path):
    yaml_text = "cases:\n  - name: refund\n    task: good\n"
    report = run_evals(
        [_skill_with_cases(tmp_path, yaml_text=yaml_text)],
        [_runner()],
        tag="no-such-tag",
        case_filter="refund",
    )
    assert report.tag_filtered_skills == ["pdf"]
    assert report.case_filtered_skills == []
```

Append to `tests/test_reporters.py`:

```python
def test_json_includes_case_filtered_skills():
    report = RunReport(outcomes=[], case_filtered_skills=["pdf"])
    assert json.loads(render_json(report))["case_filtered_skills"] == ["pdf"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_orchestrator.py -k case_filter tests/test_reporters.py::test_json_includes_case_filtered_skills -v`
Expected: FAIL — unexpected keyword `case_filter`; `RunReport` rejects `case_filtered_skills`.

- [ ] **Step 3: Model**

In `src/skill_lens/models.py`, `RunReport`, after `tag_filtered_skills`:

```python
    case_filtered_skills: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Orchestrator**

In `src/skill_lens/orchestrator.py`:

`_Plan` gains, after `tag_filtered`:

```python
    case_filtered: list[str] = field(default_factory=list)
```

`_plan_work` gains a `case_filter: str | None` parameter after `tag`, and the filter block becomes:

```python
        if tag is not None:
            cases = [c for c in cases if tag in c.tags]
            if not cases:
                plan.tag_filtered.append(skill.name)
                continue
        if case_filter is not None:
            # Case-insensitive substring, like pytest -k: the CI log shows the
            # name, the user copies any distinctive part of it. Applied after
            # --tag so a skill emptied by --tag is recorded under --tag alone.
            needle = case_filter.casefold()
            cases = [c for c in cases if needle in c.name.casefold()]
            if not cases:
                plan.case_filtered.append(skill.name)
                continue
```

`run_evals` gains `case_filter: str | None = None` after `tag`, passes it to `_plan_work(skills, runners, evals_path, tag, case_filter, baseline, repeat)`, and sets `case_filtered_skills=plan.case_filtered` on the returned `RunReport`. Add to the docstring: "`case_filter` keeps only cases whose name contains it, case-insensitively; a skill it empties is recorded in `case_filtered_skills`, never silently dropped."

- [ ] **Step 5: JSON**

In `src/skill_lens/reporters/json_reporter.py`, after `"tag_filtered_skills": report.tag_filtered_skills,`:

```python
        "case_filtered_skills": report.case_filtered_skills,
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_orchestrator.py tests/test_reporters.py tests/test_models.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/models.py src/skill_lens/orchestrator.py src/skill_lens/reporters/json_reporter.py tests/test_orchestrator.py tests/test_reporters.py
git commit -m "feat: filter the work matrix by case name and record what it emptied

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: `--case` — the gate reason, the CLI flag, the skipped lines, the action input, docs

**Files:**
- Modify: `src/skill_lens/gating.py`, `src/skill_lens/cli.py`, `src/skill_lens/reporters/console.py`, `src/skill_lens/reporters/markdown.py`, `src/skill_lens/reporters/junit.py`, `action.yml`
- Modify: `docs/cli.md`, `docs/gating.md`, `ARCHITECTURE.md`
- Test: `tests/test_gating.py`, `tests/test_cli.py`, `tests/test_reporters.py`, `tests/test_junit_reporter.py`

**Interfaces:**
- Consumes: `RunReport.case_filtered_skills`, `run_evals(case_filter=...)` from Task 6.
- Produces: gate reason `no eval cases ran: the --case filter matched no case for skill(s): {names}`; CLI `--case <text>`; action input `case`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gating.py`:

```python
def test_empty_report_because_the_case_filter_matched_nothing_names_the_cause():
    report = RunReport(outcomes=[], case_filtered_skills=["pdf"])
    gate = evaluate_gate(report)
    assert not gate.passed
    assert any("--case" in r and "pdf" in r for r in gate.reasons)
    assert not any("--tag" in r for r in gate.reasons)


def test_both_filters_emptying_different_skills_give_one_reason_each():
    report = RunReport(outcomes=[], tag_filtered_skills=["a"], case_filtered_skills=["b"])
    reasons = evaluate_gate(report).reasons
    assert any("--tag" in r and "a" in r for r in reasons)
    assert any("--case" in r and "b" in r for r in reasons)
```

Append to `tests/test_cli.py`:

```python
TWO_CASES_YAML = """cases:
  - name: mentions the skill
    task: anything
    assertions:
      - kind: contains
        value: pdf
  - name: also fine
    task: anything else
    assertions:
      - kind: contains
        value: pdf
"""


def test_case_flag_runs_only_matching_cases(tmp_path):
    skill_dir = _make_skill(tmp_path, cases=TWO_CASES_YAML)
    result = runner.invoke(app, ["run", str(skill_dir), "--case", "MENTIONS"])
    assert result.exit_code == 0, result.stdout
    assert "mentions the skill" in result.stdout
    assert "also fine" not in result.stdout


def test_a_case_flag_matching_nothing_fails_the_gate_naming_the_flag(tmp_path):
    skill_dir = _make_skill(tmp_path, cases=TWO_CASES_YAML)
    result = runner.invoke(app, ["run", str(skill_dir), "--case", "no-such-case"])
    assert result.exit_code == 1
    assert "no cases matched --case filter" in result.stdout
    assert "the --case filter matched no case for skill(s): pdf" in result.stdout


def test_the_run_plan_counts_only_cases_the_case_filter_keeps(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-for-parsing")
    _make_skill(tmp_path, cases=TWO_CASES_YAML)
    result = runner.invoke(
        app, ["run", str(tmp_path), "--runner", "pydantic-ai", "--case", "also"]
    )
    assert "1 case(s) = 1 runs" in plain(result.stdout)
```

Append to `tests/test_reporters.py`:

```python
def test_console_lists_case_filtered_skills():
    report = RunReport(outcomes=[], case_filtered_skills=["pdf"])
    assert "Skipped (no cases matched --case filter): pdf" in render_console(report)
```

Append to `tests/test_junit_reporter.py`:

```python
def test_case_filtered_skills_become_skipped_suites():
    root = _parse(RunReport(outcomes=[_outcome()], case_filtered_skills=["xlsx"]))
    names = [s.get("name") for s in root.findall("testsuite")]
    assert "xlsx" in names
    skipped = root.find("testsuite[@name='xlsx']/testcase/skipped")
    assert skipped is not None
    assert "--case" in skipped.get("message")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_gating.py tests/test_cli.py tests/test_reporters.py tests/test_junit_reporter.py -k "case_filter or case_flag or case_filtered" -v`
Expected: FAIL.

- [ ] **Step 3: Gate**

In `src/skill_lens/gating.py`, replace the `if report.total == 0:` block with:

```python
    if report.total == 0:
        # A filter that matched nothing is named first: it is the likeliest
        # cause of an empty run and the one a typo produces.
        if report.tag_filtered_skills:
            names = ", ".join(report.tag_filtered_skills)
            reasons.append(
                f"no eval cases ran: the --tag filter excluded every case for skill(s): {names}"
            )
        if report.case_filtered_skills:
            names = ", ".join(report.case_filtered_skills)
            reasons.append(
                f"no eval cases ran: the --case filter matched no case for skill(s): {names}"
            )
        if not report.tag_filtered_skills and not report.case_filtered_skills:
            if report.skipped_skills:
                names = ", ".join(report.skipped_skills)
                reasons.append(
                    "no eval cases ran: all discovered skill(s) were skipped for "
                    f"having no eval cases: {names}"
                )
            else:
                reasons.append("no eval cases ran: no skills were found")
```

- [ ] **Step 4: CLI**

In `src/skill_lens/cli.py` `run`, add after the `tag` option:

```python
    case: Annotated[
        str | None,
        typer.Option(
            "--case",
            help="Only run cases whose name contains this text (case-insensitive).",
        ),
    ] = None,
```

In the plan-line loop, after the tag filter:

```python
                if case is not None:
                    needle = case.casefold()
                    cases = [c for c in cases if needle in c.name.casefold()]
```

Pass `case_filter=case,` to `run_evals`, after `tag=tag,`.

- [ ] **Step 5: Skipped lines in the three reporters**

`console.py`, after the `tag_filtered_skills` block:

```python
    if report.case_filtered_skills:
        lines.append("")
        lines.append(
            f"Skipped (no cases matched --case filter): {', '.join(report.case_filtered_skills)}"
        )
```

`markdown.py`, in `_skipped`, after the `tag_filtered_skills` block:

```python
    if report.case_filtered_skills:
        names = ", ".join(_escape(name) for name in report.case_filtered_skills)
        bits.append(f"Skipped (no cases matched --case): {names}")
```

`junit.py`, in `render_junit`, after the `tag_filtered_skills` loop:

```python
    for skill_name in report.case_filtered_skills:
        _skipped_suite(
            root, skill_name, "(no cases matched --case)", "no cases matched the --case filter"
        )
        tests += 1
        skipped += 1
```

- [ ] **Step 6: Action**

`action.yml`: input after `tag`:

```yaml
  case:
    description: Only run cases whose name contains this text (case-insensitive).
```

env after `SE_TAG`: `        SE_CASE: ${{ inputs.case }}`; forwarding after `add --tag "$SE_TAG"`: `        add --case "$SE_CASE"`.

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_gating.py tests/test_cli.py tests/test_reporters.py tests/test_junit_reporter.py tests/test_markdown_reporter.py tests/test_action.py -v`
Expected: all PASS.

- [ ] **Step 8: Docs**

`docs/cli.md`: add `[--case <text>]` to the usage block after `[--tag <tag>]`, and a row after `--tag`:

```markdown
| `--case <text>` | none | Only run cases whose name contains `<text>`, case-insensitively — copy any distinctive part of a case name out of a CI log to rerun just that case. Combined with `--tag`, both must hold. No config key: a filter is a property of one invocation |
```

`docs/gating.md`: change the zero-cases sentence to "The reason names the cause: no skills found, all skills skipped for having no eval cases, every case filtered out by `--tag`, or no case name matching `--case`." and in the JSON report paragraph change "`skipped_skills`, `tag_filtered_skills`," to "`skipped_skills`, `tag_filtered_skills`, `case_filtered_skills`,". Add after the JUnit table row "a skill with no cases": nothing — the `<skipped>` row already covers filtered skills generically.

`ARCHITECTURE.md`: find the `errored ≠ failed` / zero-cases invariant paragraph (grep `zero cases`) and extend the list of distinguished causes with "no case name matched `--case`"; in the M5 CI surfaces section, add one sentence: "`--case` is flag-only, like `--tag`: a config file that permanently narrowed the suite would let a green run measure less than the repository declares."

- [ ] **Step 9: Verify docs**

Run: `uv run pytest tests/test_docs.py -v && uv run mkdocs build --strict`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/gating.py src/skill_lens/cli.py src/skill_lens/reporters/ action.yml docs/cli.md docs/gating.md ARCHITECTURE.md tests/
git commit -m "feat: add --case to rerun cases by name, failing the gate when none match

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: The unfilled-scaffold scan covers dictionary keys

**Files:**
- Modify: `src/skill_lens/cases/loader.py` (`_reject_unfilled`)
- Modify: `docs/eval-files.md` (Unfilled scaffolds)
- Test: `tests/test_case_loader.py`

- [ ] **Step 1: Write the failing test**

Read the top of `tests/test_case_loader.py` for its helpers (a `_write`/`tmp_path` pattern and the `SKILL` fixture), then append:

```python
def test_a_placeholder_in_a_mapping_key_is_refused_too(tmp_path):
    # `workspace.files` is keyed by filename. A scaffold that left the
    # filename unfilled would otherwise load, seed a file literally named
    # "TODO(skill-lens) ..." and let the case run.
    path = tmp_path / "x.eval.yaml"
    path.write_text(
        "cases:\n"
        "  - name: writes a file\n"
        "    task: go\n"
        "    workspace:\n"
        "      files:\n"
        '        "TODO(skill-lens) the input file": hello\n'
        "    assertions:\n"
        "      - kind: file-produced\n"
        "        file: out.txt\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path)
    assert "TODO(skill-lens)" in str(exc.value)
    assert "workspace.files" in str(exc.value)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_case_loader.py::test_a_placeholder_in_a_mapping_key_is_refused_too -v`
Expected: FAIL — the file loads (or fails validation for another reason) instead of raising for the placeholder.

- [ ] **Step 3: Implement**

In `_reject_unfilled`, the dict branch becomes:

```python
    elif isinstance(raw, dict):
        if id(raw) in seen:
            return
        seen = seen | {id(raw)}
        for key, value in raw.items():
            # Keys are user text too: `workspace.files` is keyed by filename.
            _reject_unfilled(path, index, key, trail, seen)
            _reject_unfilled(path, index, value, f"{trail}.{key}" if trail else str(key), seen)
```

Update the docstring's first paragraph to end: "...rather than complaining about the type of a value nobody meant to keep. Mapping keys are checked as well as values: `workspace.files` is keyed by filename."

- [ ] **Step 4: Run the loader tests**

Run: `uv run pytest tests/test_case_loader.py tests/test_scaffold.py -v`
Expected: PASS.

- [ ] **Step 5: Docs**

In `docs/eval-files.md`, "Unfilled scaffolds", change the last paragraph to:

```markdown
Comments are discarded before the check, so a file may discuss the token freely. Mapping
keys are checked as well as values — `workspace: files:` is keyed by filename, and an
unfilled filename would otherwise seed a file literally named after the placeholder.
```

- [ ] **Step 6: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/cases/loader.py docs/eval-files.md tests/test_case_loader.py
git commit -m "fix: refuse a scaffold placeholder left in a mapping key

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: The fifth scaffold case — the produced file

**Files:**
- Modify: `src/skill_lens/scaffold.py` (`_TEMPLATE`)
- Test: `tests/test_scaffold.py`

- [ ] **Step 1: Update the tests**

In `tests/test_scaffold.py`:

- Rename `test_the_scaffold_is_valid_yaml_with_four_cases` → `..._five_cases`, assert `len(data["cases"]) == 5`.
- In `test_a_filled_scaffold_loads_clean`: `assert len(cases) == 5` and `assert [case.mode for case in cases] == ["loaded", "loaded", "offered", "offered", "loaded"]`.
- Append:

```python
def test_the_fifth_case_exercises_the_workspace_and_the_file_assertions():
    data = safe_load(render_scaffold(SKILL))
    case = data["cases"][4]
    assert "input.txt" in case["workspace"]["files"]
    assert [a["kind"] for a in case["assertions"]] == ["file-produced", "contains"]
    assert all("file" in a for a in case["assertions"])


def test_the_fifth_case_says_when_to_delete_it():
    assert "Delete this case if the skill produces no files" in render_scaffold(SKILL)


def test_no_placeholder_sits_in_a_mapping_key():
    # Task 8 made the loader refuse keys too; the template must still be
    # refused for its *values* and never rely on a key to carry the marker.
    data = safe_load(render_scaffold(SKILL))

    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from keys(v)
        elif isinstance(node, list):
            for item in node:
                yield from keys(item)

    assert not any(UNFILLED_SENTINEL in str(k) for k in keys(data))
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_scaffold.py -v`
Expected: the count tests and the two new fifth-case tests FAIL.

- [ ] **Step 3: Extend the template**

In `src/skill_lens/scaffold.py`, append to `_TEMPLATE` after the `leaves unrelated work alone` case (before the closing `"""`):

```python
  # 5. Does it produce the right artifact? `workspace:` gives the case a real,
  #    contained temporary directory seeded with the files named under
  #    `files:`, plus three built-in tools: list_files, read_file, write_file.
  #    Assertions can then target a produced file instead of the chat output.
  #    Delete this case if the skill produces no files.
  - name: produces the expected file
    task: >-
      {sentinel} a prompt that asks for a file to be written
    workspace:
      files:
        input.txt: |-
          {sentinel} the input the skill reads; delete `files:` for an empty workspace
    assertions:
      - kind: file-produced
        file: >-
          {sentinel} the filename the skill must write
      - kind: contains
        file: >-
          {sentinel} the same filename
        value: >-
          {sentinel} a string that file must contain
```

The `file:` values are `>-` block scalars for the same reason `task:` is: a filled value containing `: ` would otherwise be a YAML syntax error (the `colon-space` parameter of `test_a_filled_scaffold_loads_clean` proves it).

- [ ] **Step 4: Run the scaffold tests**

Run: `uv run pytest tests/test_scaffold.py tests/test_cli_init.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/scaffold.py tests/test_scaffold.py
git commit -m "feat: scaffold a workspace case for file-producing skills

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: `init` writes where the skill already keeps its evals

**Files:**
- Modify: `src/skill_lens/scaffold.py` (add `eval_filename`, `scaffold_target`), `src/skill_lens/cli.py` (`init` uses them; `_eval_filename` and `import re` removed)
- Test: `tests/test_scaffold.py`, `tests/test_cli_init.py`

**Interfaces:**
- Produces: `eval_filename(name: str) -> str` (moved from `cli._eval_filename`, same body); `scaffold_target(skill: Skill) -> Path`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scaffold.py`:

```python
from skill_lens.scaffold import eval_filename, scaffold_target  # extend the import


def _skill_at(path: Path) -> Skill:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text("---\nname: pdf\n---\nbody\n", encoding="utf-8")
    return Skill(name="pdf", path=path)


def test_the_target_is_the_evals_directory_by_default(tmp_path):
    skill = _skill_at(tmp_path / "pdf")
    assert scaffold_target(skill) == tmp_path / "pdf" / "evals" / "pdf.eval.yaml"


def test_the_target_sits_beside_skill_md_when_the_evals_already_do(tmp_path):
    # Discovery prefers evals/ when it exists. Creating it here would make the
    # existing other.eval.yaml invisible to every later run.
    skill = _skill_at(tmp_path / "pdf")
    (skill.path / "other.eval.yaml").write_text("cases: []\n", encoding="utf-8")
    assert scaffold_target(skill) == tmp_path / "pdf" / "pdf.eval.yaml"


def test_an_existing_evals_directory_wins_over_files_beside(tmp_path):
    skill = _skill_at(tmp_path / "pdf")
    (skill.path / "stray.eval.yaml").write_text("cases: []\n", encoding="utf-8")
    (skill.path / "evals").mkdir()
    assert scaffold_target(skill) == tmp_path / "pdf" / "evals" / "pdf.eval.yaml"


def test_eval_filename_neutralises_path_separators():
    assert eval_filename("../../etc/passwd") == "etc-passwd.eval.yaml"
    assert eval_filename("") == "skill.eval.yaml"
```

Append to `tests/test_cli_init.py`:

```python
def test_init_writes_beside_skill_md_when_evals_already_live_there(tmp_path):
    path = _skill_dir(tmp_path)
    (path / "other.eval.yaml").write_text("cases: []\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(path)])
    assert result.exit_code == 0, result.output
    assert (path / "order-support.eval.yaml").is_file()
    assert not (path / "evals").exists()
    # Discovery still sees the file that was already there.
    assert sorted(p.name for p in path.glob("*.eval.yaml")) == [
        "order-support.eval.yaml",
        "other.eval.yaml",
    ]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_scaffold.py tests/test_cli_init.py -v`
Expected: FAIL — `scaffold_target`/`eval_filename` cannot be imported; the beside test finds `evals/`.

- [ ] **Step 3: Implement in `scaffold.py`**

Add the imports `import re` and `from pathlib import Path`, and `from skill_lens.cases.loader import EVAL_SUFFIX, EVALS_DIRNAME, UNFILLED_SENTINEL`. Then:

```python
def eval_filename(name: str) -> str:
    """A safe file name for a skill's eval suite.

    The name comes from user-supplied frontmatter, so it is not automatically
    a safe path component: `name: ../../x` would otherwise write outside the
    directory init was pointed at.
    """
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "skill"
    return f"{safe}{EVAL_SUFFIX}"


def scaffold_target(skill: Skill) -> Path:
    """Where `init` writes: wherever this skill already keeps its evals.

    Discovery prefers an `evals/` directory when one exists and only falls
    back to `*.eval.yaml` beside SKILL.md when it does not. An `init` that
    always created `evals/` would therefore hide any suite already sitting
    beside SKILL.md from every later run -- silently, with nothing red.
    """
    beside = list(skill.path.glob(f"*{EVAL_SUFFIX}"))
    if beside and not (skill.path / EVALS_DIRNAME).is_dir():
        return skill.path / eval_filename(skill.name)
    return skill.path / EVALS_DIRNAME / eval_filename(skill.name)
```

- [ ] **Step 4: Use them in `cli.py`**

- Change the scaffold import to `from skill_lens.scaffold import render_scaffold, scaffold_target`.
- Delete `_eval_filename` and `import re` (confirm with `grep -n "re\." src/skill_lens/cli.py` that nothing else uses `re`).
- In `init`, replace `target = path / EVALS_DIRNAME / _eval_filename(skill.name)` with `target = scaffold_target(skill)`.
- If `EVALS_DIRNAME` / `EVAL_SUFFIX` are now unused in `cli.py`, drop them from the `cases.loader` import (ruff will tell you).

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_scaffold.py tests/test_cli_init.py tests/test_cli.py -v`
Expected: all PASS (including `test_a_skill_name_with_a_separator_cannot_escape_the_evals_directory`).

- [ ] **Step 6: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/scaffold.py src/skill_lens/cli.py tests/test_scaffold.py tests/test_cli_init.py
git commit -m "fix: make init write beside SKILL.md when the evals already live there

Creating evals/ next to an existing *.eval.yaml hid that file from
discovery, which prefers the directory when it exists.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Batch `init` over a directory of skills, and the `init` docs

**Files:**
- Modify: `src/skill_lens/cases/loader.py` (`_discover_paths` → public `discover_eval_paths`), `src/skill_lens/cli.py` (`init`)
- Modify: `docs/cli.md` (`init` section), `skills/writing-skill-evals/SKILL.md` (step 2), `skills/writing-skill-evals/references/eval-file-syntax.md` (Placeholders)
- Test: `tests/test_cli_init.py`

**Interfaces:**
- Consumes: `scaffold_target`, `render_scaffold` (Tasks 9–10).
- Produces: `discover_eval_paths(skill: Skill) -> list[Path]` (the former `_discover_paths`, unchanged body).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_cli_init.py`)

```python
def _skills_root(tmp_path):
    root = tmp_path / "skills"
    for name in ("refund", "triage"):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name} things\n---\n\nbody\n", encoding="utf-8"
        )
    covered = root / "covered"
    covered.mkdir()
    (covered / "SKILL.md").write_text(
        "---\nname: covered\ndescription: already has a suite\n---\n\nbody\n", encoding="utf-8"
    )
    (covered / "covered.eval.yaml").write_text("cases: []\n", encoding="utf-8")
    return root


def test_batch_init_scaffolds_missing_suites_and_skips_covered_skills(tmp_path):
    root = _skills_root(tmp_path)
    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert f"Wrote {root / 'refund' / 'evals' / 'refund.eval.yaml'}" in lines
    assert f"Wrote {root / 'triage' / 'evals' / 'triage.eval.yaml'}" in lines
    assert "Skipped covered: already has 1 eval file(s)" in lines
    assert lines[-1] == f"Fill in every {UNFILLED_SENTINEL}, then run: skill-lens list {root}"
    assert (root / "covered" / "covered.eval.yaml").read_text(encoding="utf-8") == "cases: []\n"
    assert not (root / "covered" / "evals").exists()


def test_batch_init_rejects_force(tmp_path):
    root = _skills_root(tmp_path)
    result = runner.invoke(app, ["init", str(root), "--force"])
    assert result.exit_code == 2
    assert "--force applies to one skill" in result.output
    assert not (root / "refund" / "evals").exists()


def test_batch_init_with_nothing_to_scaffold_exits_zero_and_says_so(tmp_path):
    root = tmp_path / "skills"
    (root / "covered").mkdir(parents=True)
    (root / "covered" / "SKILL.md").write_text("---\nname: covered\n---\n\nbody\n", encoding="utf-8")
    (root / "covered" / "covered.eval.yaml").write_text("cases: []\n", encoding="utf-8")
    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 0, result.output
    assert f"Nothing to do: every skill under {root} already has an eval suite" in result.output


def test_batch_init_over_a_directory_with_no_skills_is_a_user_error(tmp_path):
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    result = runner.invoke(app, ["init", str(empty)])
    assert result.exit_code == 2
    assert "SKILL.md" in result.output


def test_batch_init_surfaces_a_malformed_skill_as_a_user_error(tmp_path):
    root = tmp_path / "skills"
    (root / "bad").mkdir(parents=True)
    (root / "bad" / "SKILL.md").write_text("---\nname: [unclosed\n---\n\nbody\n", encoding="utf-8")
    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 2
    assert "frontmatter" in result.output
```

Note `test_a_path_with_no_skill_md_is_a_user_error` already exists and must keep passing: an empty directory is now "no skills under", still exit 2 and still mentions `SKILL.md`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_cli_init.py -v`
Expected: the five new tests FAIL (batch mode exits 2 with "no SKILL.md in").

- [ ] **Step 3: Make discovery public**

In `src/skill_lens/cases/loader.py`, rename `_discover_paths` to `discover_eval_paths` and update its one caller in `load_cases_for_skill`. Run `grep -rn "_discover_paths" src tests` and update any test that imports the private name.

- [ ] **Step 4: Rewrite `init` in `cli.py`**

Replace the whole `init` command with:

```python
def _write_scaffold(target: Path, skill: Skill) -> None:
    """Write one scaffold, or exit 2 naming the file."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_scaffold(skill), encoding="utf-8")
    except OSError as exc:
        typer.echo(f"cannot write {target}: {exc}")
        raise typer.Exit(code=2) from exc


def _init_one(path: Path, force: bool) -> None:
    """The original `init`: exactly one skill directory, `--force` allowed."""
    try:
        skill = parse_skill_file(path / SKILL_FILENAME)
    except SkillParseError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc

    target = scaffold_target(skill)
    if target.exists() and not force:
        typer.echo(f"{target} already exists; pass --force to overwrite it")
        raise typer.Exit(code=2)
    _write_scaffold(target, skill)
    typer.echo(f"Wrote {target}")
    typer.echo(f"Fill in every {UNFILLED_SENTINEL}, then run: skill-lens list {path}")


def _init_many(path: Path) -> None:
    """Batch mode: scaffold every skill under `path` that has no suite.

    Skips any skill with an eval file already -- batch init exists to fill in
    the *missing* suites and must never rewrite one that is there to build on.
    """
    try:
        skills = load_skills(path) if path.is_dir() else []
    except SkillParseError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc
    if not skills:
        typer.echo(
            f"no {SKILL_FILENAME} under {path}; point init at a skill directory "
            "or a directory of skill directories"
        )
        raise typer.Exit(code=2)

    wrote = 0
    for skill in skills:
        existing = discover_eval_paths(skill)
        if existing:
            typer.echo(f"Skipped {skill.name}: already has {len(existing)} eval file(s)")
            continue
        target = scaffold_target(skill)
        _write_scaffold(target, skill)
        typer.echo(f"Wrote {target}")
        wrote += 1
    if wrote:
        typer.echo(f"Fill in every {UNFILLED_SENTINEL}, then run: skill-lens list {path}")
    else:
        typer.echo(f"Nothing to do: every skill under {path} already has an eval suite")


@app.command()
def init(
    path: Annotated[
        Path, typer.Argument(help="A skill directory, or a directory of skill directories.")
    ],
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing eval file (one skill only).")
    ] = False,
) -> None:
    """Write a starter eval suite beside a skill, or beside every skill that has none."""
    if (path / SKILL_FILENAME).is_file():
        _init_one(path, force)
        return
    if force:
        # Rewriting every suite in a repository must never be one flag away.
        typer.echo("--force applies to one skill; point init at that skill's directory")
        raise typer.Exit(code=2)
    _init_many(path)
```

Add `Skill` to the models import and `discover_eval_paths` to the `cases.loader` import in `cli.py`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_cli_init.py tests/test_cli.py tests/test_case_loader.py tests/test_shipped_skill.py -v`
Expected: all PASS.

- [ ] **Step 6: Docs — `docs/cli.md` `init` section**

Replace the `init` section with:

````markdown
## `init`

```bash
skill-lens init <path> [--force]
```

`<path>` is either one skill directory containing `SKILL.md`, or a directory of skill
directories — `init` discovers recursively, exactly as `run` and `list` do.

**One skill.** Writes a starter eval suite of five cases: a common-case case, a policy-edge
case carrying `tools:` and `trajectory:`, both halves of the `mode: offered` triggering pair,
and a `workspace:` case with `file-produced` and `contains ... file:` assertions for a skill
that produces a file (delete it if yours does not).

The file goes where the skill already keeps its evals: `<skill-dir>/evals/<skill-name>.eval.yaml`,
unless the skill has `*.eval.yaml` beside `SKILL.md` and no `evals/` directory, in which case
it goes beside `SKILL.md` too. Discovery prefers `evals/` when it exists, so `init` never
creates that directory next to files it would hide.

Every field you have to supply holds the placeholder `TODO(skill-lens)`, and a case still
containing one aborts the run as an [authoring error](eval-files.md#unfilled-scaffolds).
The generated file is therefore never a green suite that checks nothing.

| Flag | Meaning |
| --- | --- |
| `--force` | Overwrite an existing eval file. Without it, an existing file is a user error. One skill only. |

**A directory of skills.** Every skill with no eval file gets a scaffold; every skill that
already has one is skipped and named. Nothing existing is ever rewritten, and `--force` is
a user error in this mode — rewriting every suite in a repository must never be one flag
away.

```
Wrote skills/refund/evals/refund.eval.yaml
Skipped order-support: already has 1 eval file(s)
Wrote skills/triage/evals/triage.eval.yaml
Fill in every TODO(skill-lens), then run: skill-lens list skills
```

Exit `0` on success, including when every skill already had a suite (`Nothing to do`).
Exit `2` when the path holds no `SKILL.md` anywhere under it, when a `SKILL.md` is
malformed, when a target file exists and `--force` was not given (one skill), when `--force`
is given for a directory of skills, or when a file cannot be written.
````

- [ ] **Step 7: Docs — the shipped skill**

`skills/writing-skill-evals/SKILL.md`, step 2, replace the text with:

```markdown
2. **Scaffold, or extend.** If step 1 found no suite, run `skill-lens init <skill-dir>` —
   do not hand-roll the file structure, the generated file already carries the triggering
   pair, a `workspace:` case for a skill that produces a file (delete it if this one does
   not), and the placeholders that stop an unfinished suite from running. Pointed at a
   directory of skills, `init` scaffolds every skill that has no suite and skips the rest.
   If a suite already exists, do not run `init` on that skill: it exits 2 rather than touch
   an existing file. Read `references/auditing.md` and extend the suite you found instead.
   (`--force` overwrites the file outright, which is not what you want when a suite is
   already there to build on.)
```

`skills/writing-skill-evals/references/eval-file-syntax.md`, "Placeholders", append: "Mapping keys are checked too — an unfilled filename under `workspace: files:` is refused like any other placeholder."

- [ ] **Step 8: Verify docs**

Run: `uv run pytest tests/test_docs.py tests/test_shipped_skill.py -v && uv run mkdocs build --strict`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/cases/loader.py src/skill_lens/cli.py docs/cli.md skills/writing-skill-evals/ tests/
git commit -m "feat: let init scaffold every skill under a directory that has no suite

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 12: `greeting` 1.1.0 — the comparative example

**Files:**
- Modify: `examples/greeting/SKILL.md`, `examples/greeting/greeting.eval.yaml`
- Modify: `tests/test_examples.py`, `tests/test_integration_live.py`
- Modify: `docs/comparative-evals.md`

- [ ] **Step 1: Write the failing test** (append to `tests/test_examples.py`)

```python
def test_greeting_stays_at_the_version_that_makes_the_comparative_example_work():
    # `--baseline previous` resolves the skill's *earlier* version from git
    # history, so the shipped example only demonstrates a comparison because
    # 1.0.0 is on main and the working copy declares something later. Do not
    # revert this bump, and do not reuse 1.1.0 for an unrelated edit -- bump
    # again instead, so every version in history stays distinct.
    greeting = next(s for s in load_skills(EXAMPLES) if s.name == "greeting")
    assert greeting.version == "1.1.0"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_examples.py -v`
Expected: the new test FAILS (`'1.0.0' == '1.1.0'`).

- [ ] **Step 3: Bump the skill**

`examples/greeting/SKILL.md`:

```markdown
---
name: greeting
description: Greet a user warmly and by name
version: 1.1.0
---

When greeting someone, address them by name and keep it to one short sentence.
Do not use exclamation marks — greet warmly, not loudly.
```

`examples/greeting/greeting.eval.yaml` — add after the `not_contains: Traceback` assertion:

```yaml
      # Version 1.1.0 added "do not use exclamation marks". This is the
      # assertion `--baseline previous` measures: version 1.0.0 typically
      # answers "Hello, Ada!", so the baseline arm fails it and the delta
      # block shows the improvement. Try it from a checkout:
      #   skill-lens run examples/greeting --runner pydantic-ai --baseline previous
      - kind: not_contains
        value: "!"
```

- [ ] **Step 4: Fix the stale live-tier count**

In `tests/test_integration_live.py`, change `assert report.total == 3` to `assert report.total == 7` with the comment `# greeting (1) + order-support (5) + csv-report (1)`. This tier is opt-in and never runs in CI; the count had drifted at M3 and M6.

- [ ] **Step 5: Docs — the walkthrough**

In `docs/comparative-evals.md`, add before `## Worked CI example`:

````markdown
## Try it on the shipped examples

`examples/greeting` is versioned for exactly this. Version `1.1.0` added one instruction —
"do not use exclamation marks" — and one assertion only that version satisfies
(`not_contains: "!"`). Because `1.0.0` is in this repository's history, a checkout can run
the comparison as-is:

```bash
skill-lens run examples/greeting --runner pydantic-ai --baseline previous
```

The candidate arm runs the working copy; the baseline arm runs `1.0.0` resolved from git.
Version `1.0.0` typically answers `Hello, Ada!`, so the baseline fails the new assertion, the
candidate passes it, and the delta block reports the improvement — a small, real example of
what `--min-delta` gates on.
````

- [ ] **Step 6: Verify**

Run: `uv run pytest tests/test_examples.py tests/test_docs.py -v && uv run skill-lens list ./examples && uv run mkdocs build --strict`
Expected: PASS; `list` shows `greeting	1 case(s)	examples/greeting`.

- [ ] **Step 7: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add examples/greeting/ tests/test_examples.py tests/test_integration_live.py docs/comparative-evals.md
git commit -m "feat: version the greeting example so --baseline previous works from a checkout

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 13: `examples/skill-lens.toml` — the annotated config

**Files:**
- Create: `examples/skill-lens.toml`
- Modify: `docs/configuration.md`
- Test: `tests/test_examples.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_examples.py`)

```python
from skill_lens.config import Config, load_config  # add at the top


def test_the_example_config_parses_and_sets_what_it_claims():
    config = load_config(path=EXAMPLES / "skill-lens.toml")
    assert config.default_runner == "pydantic-ai"
    assert config.judge == "pydantic-ai"
    assert config.concurrency == 4
    assert config.min_pass_rate == 1.0
    assert config.per_skill_min == {"order-support": 1.0}


def test_the_example_config_mentions_every_key():
    # Live or commented out, every key skill-lens knows must appear, so the
    # file stays the one place a reader can see the whole surface.
    text = (EXAMPLES / "skill-lens.toml").read_text(encoding="utf-8")
    for field in Config.model_fields:
        assert field in text, f"{field} is missing from examples/skill-lens.toml"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_examples.py -v`
Expected: FAIL — file not found.

- [ ] **Step 3: Write the file**

```toml
# skill-lens.toml — an annotated example of a committed configuration.
#
# This is the file a repository commits once it evaluates against a real
# agent in CI. Every key skill-lens knows is listed; the ones this example
# sets are live, the rest are commented out at their defaults.
#
# Resolution order: CLI flag > this file > built-in default. Secrets never
# live here: API keys come from environment variables only.
#
# Use it from the repository root with
#   skill-lens run ./examples --config examples/skill-lens.toml
# It is not picked up on its own from there: discovery searches upward from
# the current directory, and this file sits below it on purpose, so the
# repository's zero-cost self-check stays on the offline defaults.

# "fake" is the offline, scripted runner; "pydantic-ai" runs a real agent.
default_runner = "pydantic-ai"

# Model id for the runner. Needs the provider's key in the environment
# (OPENAI_API_KEY here).
model = "openai:gpt-4o-mini"

# Sampling temperature, or the string "unset" for reasoning models that
# reject an explicit one.
# temperature = 0.0

# Retries on a provider failure, and the wait between them in seconds.
# retries = 2
# retry_backoff_seconds = 1.0

# "fake" never grades — a rubric case errors rather than passing unchecked.
# "pydantic-ai" grades every `judge:` block with a real model.
judge = "pydantic-ai"

# Model for the judge; empty means "the same model as `model`".
# judge_model = ""
# judge_temperature = 0.0

# The gate. 1.0 means every case must pass.
min_pass_rate = 1.0

# Whether an errored case (the runner or an evaluator broke) fails the gate.
# fail_on_error = true

# Comparative runs: "" (off), "none" (an empty skill) or "previous" (the
# prior version from git). `repeat` samples each arm N times. `min_delta`
# has no default — unset means the delta is reported but not gated.
# baseline = ""
# repeat = 1
# min_delta = 0.0

# Cases run at once. The work is network-bound, so the ceiling is your
# provider's rate limit, not your CPU.
concurrency = 4

# Keep each case's temporary directory for inspection. Every kept directory
# is printed, so this is safe to commit.
# keep_workspace = false

# Print a failing case's whole output instead of the first 500 characters.
# full_output = false

# Runaway guards on what one case's workspace may write.
# max_file_bytes = 1000000
# max_files = 200
# max_total_bytes = 5000000

# A stricter floor for one skill, by name. Must stay the last section: in
# TOML every key after a [table] header belongs to that table.
[per_skill_min]
order-support = 1.0
```

- [ ] **Step 4: Docs**

In `docs/configuration.md`, after the sentence "the repo root is the conventional home, not a requirement." add:

```markdown
[`examples/skill-lens.toml`](https://github.com/EmadMokhtar/skill-evaluator/blob/main/examples/skill-lens.toml)
is a complete, annotated example: every key listed, the realistic ones live, the rest
commented out at their defaults.
```

- [ ] **Step 5: Verify**

Run: `uv run pytest tests/test_examples.py tests/test_docs.py -v && uv run skill-lens list ./examples --evals examples/greeting/greeting.eval.yaml >/dev/null; uv run skill-lens list ./examples && uv run mkdocs build --strict`
Expected: PASS; `list ./examples` still shows exactly three skills (the `.toml` is not a skill).

- [ ] **Step 6: Commit**

```bash
git add examples/skill-lens.toml docs/configuration.md tests/test_examples.py
git commit -m "docs: add an annotated example skill-lens.toml

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 14: The end-to-end quickstart, and the `references/` note

**Files:**
- Rewrite: `docs/getting-started.md`
- Modify: `docs/runners.md` (The workspace)

The outputs below were captured by running the commands on this branch; the `output:` line
under the failing case is the one Task 2 adds. In Step 4 you re-run them and confirm the page
matches byte for byte.

- [ ] **Step 1: Confirm the fake-run behaviour you are about to document**

On the fake runner, the first case of the suite in Step 3 fails on its **trajectory** check,
not its assertion: the fake output `[fake] refund handled: I want a refund for order 1234`
does contain `1234`, but the fake runner calls no tools, so `called: [lookup_order]` fails.
That is the failure the tutorial shows and explains.

- [ ] **Step 2: Write `docs/getting-started.md`**

````markdown
# Getting started

This page takes one skill from nothing to a CI gate. Every command is shown with what it
prints. The first four steps cost nothing and need no API key.

## 1. Install

```bash
uv tool install "skill-lens[pydantic-ai]"
```

`pip install "skill-lens[pydantic-ai]"` works the same way. The extra supplies the
real-agent runner; drop it if you only want the offline default. From a checkout of this
repository, `uv sync --extra pydantic-ai` and prefix every command below with `uv run`.

## 2. Scaffold a suite

A skill is a directory containing `SKILL.md`. Its eval cases live beside it. Say you have:

```
skills/
  refund/
    SKILL.md
```

```bash
skill-lens init ./skills/refund
```

```
Wrote skills/refund/evals/refund.eval.yaml
Fill in every TODO(skill-lens), then run: skill-lens list skills/refund
```

The file holds five cases — the common case, the policy edge with mock tools and a
trajectory check, both halves of a triggering pair, and a workspace case for a skill that
writes a file. Every value you must supply reads `TODO(skill-lens)`. A case still holding
one **refuses to run** (exit `2`, naming the field), so the scaffold can never pass by
checking nothing. Delete the cases that do not apply; keep the ones that do.

Pointed at a directory of skills, `init` scaffolds every skill that has no suite and skips
the rest — see [CLI](cli.md#init).

## 3. Fill it in

For a first run, two cases are enough. Replace the generated file with:

```yaml
# skills/refund/evals/refund.eval.yaml
cases:
  - name: refuses a refund outside the return window
    task: I want a refund for order 1234
    tags: [smoke]
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
      called: [lookup_order]      # it must look the order up
      forbidden: [issue_refund]   # and must not refund this one
    assertions:
      - kind: contains
        value: "1234"             # name the order you are talking about

  - name: never leaks a stack trace
    task: I want a refund for order 1234
    assertions:
      - kind: not_contains
        value: Traceback
```

Mock tools execute nothing: calling one records the call and returns `returns` verbatim,
so the trajectory is genuinely the model's choice. The full field reference is
[Eval files](eval-files.md); deciding *which* cases a skill needs is
[Writing evals](writing-evals.md).

## 4. Validate for free

```bash
skill-lens list ./skills
```

```
refund	2 case(s)	skills/refund
```

`list` discovers skills and validates every eval file without calling a runner — no key,
no spend. A malformed file, an unknown assertion kind or a leftover `TODO(skill-lens)`
stops here with exit `2`.

## 5. Run offline and read a failure

```bash
skill-lens run ./skills
```

The default runner is `fake`: scripted, offline, free. It answers every task with
`[fake] <skill> handled: <task>` and never calls a tool, so it exercises the whole pipeline
and fails the first case — which is what we want to look at:

```
[FAIL] refund :: refuses a refund outside the return window (fake)
        trajectory: lookup_order was never called
            called:lookup_order: lookup_order was never called
        output: [fake] refund handled: I want a refund for order 1234
[PASS] refund :: never leaks a stack trace (fake)

1 passed, 1 failed, 0 errored — pass rate 50%

Gate FAILED:
  - pass rate 50% is below the required 100%
```

Read it top down. The `contains('1234')` assertion held — the fake echo names the order —
but the trajectory check did not: `lookup_order` was never called. Below the checks is
what the agent actually did: its `output:`, and a `tool calls:` list when there were any
(here there were none, which is exactly the problem). A real agent that answered the same
way would fail for the same reason, and you would see the words it chose. Output is cut at
500 characters; a cut is never silent, and `--full-output` prints all of it. Exit code `0`
means the gate passed, `1` failed, `2` something in your own files is wrong — that is the
whole contract with your pipeline. See [Gating](gating.md).

## 6. Run against a real agent

```bash
export OPENAI_API_KEY=...
skill-lens run ./skills --runner pydantic-ai --model openai:gpt-4o-mini
```

Before spending anything the CLI prints its ceiling:

```
Plan: up to 1 arm(s) x 1 repeat(s) x 2 case(s) = 2 runs
```

Rerun one case by any distinctive part of its name:

```bash
skill-lens run ./skills --runner pydantic-ai --case "return window"
```

A `--case` that matches nothing fails the gate rather than reporting an empty success.
Runners, tools and budgets are covered in [Runners](runners.md).

## 7. Commit a configuration

Rather than repeat the flags, commit `skill-lens.toml` at your repository root:

```toml
default_runner = "pydantic-ai"
model = "openai:gpt-4o-mini"
judge = "pydantic-ai"        # turns on the LLM judge for cases with a rubric
min_pass_rate = 1.0
concurrency = 4
```

Flags still win over the file. Every key, annotated, is in
[`examples/skill-lens.toml`](https://github.com/EmadMokhtar/skill-evaluator/blob/main/examples/skill-lens.toml);
the reference is [Configuration](configuration.md). Secrets never go in the file — API keys
come from the environment only.

## 8. Gate pull requests

```yaml
- uses: EmadMokhtar/skill-evaluator@v0.4.0
  with:
    path: ./skills
    runner: pydantic-ai
    model: openai:gpt-4o-mini
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

The action installs `skill-lens`, runs it, publishes a JUnit report for the test pane and a
Markdown summary for the job summary or a pull-request comment, and exits with the gate's
code. Complete workflows are in [CI integration](ci.md).

## Next: did the edit help?

Once the gate is green, the interesting question is whether an edit to `SKILL.md` made the
skill *better*. Add `version:` to the frontmatter (three-part, like `1.0.0` — see
[why it must be text](comparative-evals.md#version-and-why-it-must-be-quoted)), bump it
with each meaningful edit, and run:

```bash
skill-lens run ./skills --runner pydantic-ai --baseline previous
```

Every case runs twice — the working copy and the previous version resolved from git — and
the report carries the delta. `--min-delta` turns that into a gate. This repository's own
`examples/greeting` is versioned for exactly this walkthrough:
[Comparative evals](comparative-evals.md#try-it-on-the-shipped-examples).
````

The action tag in step 8 is `v0.4.0` — the version this pull request will release (see Task 16). If `cz bump --dry-run` reports a different version at Task 16, change it there.

- [ ] **Step 3: The `references/` note in `docs/runners.md`**

At the end of "The workspace" section (after the `--keep-workspace` paragraph), add:

```markdown
**Files beside `SKILL.md` are not loaded.** A skill directory often carries `references/`
or `scripts/` alongside `SKILL.md`. Today skill-lens reads only `SKILL.md`: those files
are not added to the prompt, and the workspace tools cannot reach them — the workspace is
the case's temporary directory, not the skill's. A `SKILL.md` that says "see
`references/policy.md`" therefore points the agent at a file it cannot read. Running a
script bundled with the skill is planned as M6 part 2 (see the [roadmap](roadmap.md)),
because executing code that shipped with the artifact under evaluation is a different
trust decision from writing files into a temporary directory.
```

- [ ] **Step 4: Verify the page against the tool**

Recreate the tutorial's skill in a scratch directory (the `SKILL.md` from step 2 and the
eval file from step 3, verbatim) and run `skill-lens init` on a copy without the eval file,
then `list` and `run` with it. Every output block on the page must match what prints,
including the `output:` line. Then:

Run: `uv run pytest tests/test_docs.py -v && uv run mkdocs build --strict`
Expected: PASS — in particular `test_relative_links_resolve` for the two new anchors (`cli.md#init`, `comparative-evals.md#try-it-on-the-shipped-examples`, `comparative-evals.md#version-and-why-it-must-be-quoted`). If the anchor check is a plain file check, also open the built site and confirm the anchors exist: `grep -c 'id="try-it-on-the-shipped-examples"' site/comparative-evals/index.html`.

- [ ] **Step 5: Commit**

```bash
git add docs/getting-started.md docs/runners.md
git commit -m "docs: rewrite getting started as an end-to-end quickstart

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 15: Stale status text, roadmap, module map and invariants

**Files:**
- Modify: `README.md`, `docs/index.md`, `docs/cli.md`, `docs/roadmap.md`, `ARCHITECTURE.md`, `CLAUDE.md`

- [ ] **Step 1: README**

- Line "repository ships two:" → "repository ships three:", and add to the tree:
  ```
    csv-report/
      SKILL.md
      csv-report.eval.yaml
  ```
- The `list` output block gains `csv-report	1 case(s)	examples/csv-report` as its first line (discovery is sorted).
- In "A run reads like a test suite", the `[FAIL]` block gains, after the `contains[0]` line:
  ```
          output: [fake] order-support handled: I want a refund for order 1234
  ```
  and after the sample add one sentence: "Every failing case shows what the agent actually said and which tools it called — `--case` reruns just that one."
- The action snippet `@v0.3.0` → `@v0.4.0`.
- "Starting on your own skill? `skill-lens init ./skills/my-skill` writes a starter suite…" → append "; point it at a directory of skills and it scaffolds every one that has no suite."
- Status: "Milestone 5. Discovery, scoring, judging, comparison, reporting, gating and the automated release pipeline all ship and are tested." → "Milestone 7. Discovery, scoring, judging, comparison, real-file workspaces, reporting, gating and the automated release pipeline all ship and are tested."

- [ ] **Step 2: `docs/index.md`**

Replace the status admonition with:

```markdown
!!! info "Status: M7"
    The full pipeline — discovery, scoring, reporting, gating — runs offline against
    `FakeRunner` (the default, scripted, free) and against real agents through
    `pydantic-ai`. It scores output text, tool-use trajectories, efficiency budgets and
    the files a case produces in a contained workspace, plus output quality via a
    rubric-based LLM judge with per-check evidence. Each case can also run against a
    baseline for comparative, delta-gated evals; JUnit/Markdown reporters and a composite
    GitHub Action make a run CI-legible, and every failing case shows what the agent
    actually did. Merging to `main` versions the change from its commit history and
    publishes it to PyPI (see [Releasing](releasing.md)). This is `0.x`: a minor release
    may still change behaviour, so pin what you depend on. See the [roadmap](roadmap.md).
```

- [ ] **Step 3: `docs/cli.md` `list` output**

Add `csv-report	1 case(s)	examples/csv-report` as the first line of the `list` output block.

- [ ] **Step 4: `docs/roadmap.md`**

- M7 row: `| M7 | DX: failing cases explain themselves, `--case`, `init` batch mode and workspace case, versioned example, quickstart | shipped |`
- Add before "## The rename to skill-lens":

```markdown
## What M7 shipped

The original M7 list — `init`, docs, more examples, a quickstart — had mostly shipped
early, so M7 was re-scoped around the developer-experience gaps that using the tool
exposed. A non-passing case now shows the agent's output and its tool calls in the
console, the Markdown summary and the JUnit body alike, from one shared excerpt; the
output is cut at 500 characters and a cut is never silent (`full_output` /
`--full-output` lifts it). `--case <text>` reruns the cases whose name contains the text,
and a filter matching nothing fails the gate. `init` gained a fifth scaffold case for
skills that produce a file, writes beside `SKILL.md` when that is where a skill's evals
already live (so it can never hide them behind a new `evals/` directory), and scaffolds
every skill under a directory that has no suite. `examples/greeting` is versioned so
`--baseline previous` works from a checkout, `examples/skill-lens.toml` annotates every
config key, and [Getting started](getting-started.md) is an end-to-end quickstart.

Deferred: a `references/` layout example (bundled files are not loaded until M6 part 2),
a repeatable `--case`, and the gating features carried over from M4 and M5 (per-skill
`min_delta`, `--baseline-ref`, both-arms-fail flagging).
```

- [ ] **Step 5: `ARCHITECTURE.md`**

- Module map: add a row after `reporters/console.py`:
  `| `reporters/failure_context.py` | The excerpt a non-passing case shows — output, cut count, tool-call lines. One helper for all three reporters; no markup. |`
- Module map `scaffold.py` row (grep for it): append "`scaffold_target` decides where `init` writes."
- Add a new subsection before `## Extension points`:

```markdown
### Developer experience (M7)

**Output is expanded only under non-passing candidate outcomes.** `failure_context` returns
`None` for a passed outcome, a baseline outcome or an outcome with no result, and every
reporter renders nothing on `None`. Fifty green cases stay fifty lines, and a baseline
outcome — which is not the verdict — is never expanded.

**A cut is never silent.** `FailureContext.cut` is the exact number of characters removed
and `cut_note` states it, with the flag that lifts the cap. A truncated excerpt that looked
complete would be worse than none.

**The three reporters render one `FailureContext`.** Console, Markdown and JUnit call the
same helper and may differ only in markup. Two excerpts computed separately would drift the
first time one of them changed.

**A `--case` matching nothing fails the gate.** The fourth zero-cases cause, beside no
skills, no cases and `--tag`: `case_filtered_skills` on `RunReport` records the skills the
filter emptied, and `evaluate_gate` names the flag. A typo in a filter is not a pass.

**`--case` has no config key.** A filter chooses which cases one invocation runs. A config
file that permanently narrowed the suite would let a green run measure less than the
repository declares. `full_output` and `keep_workspace` are rendering knobs with no such
failure mode, which is why they may live in the file.

**`init` never creates an `evals/` directory beside existing `*.eval.yaml` files.**
Discovery prefers `evals/` when it exists, so creating it would hide the files already
there from every later run — silently, with nothing red. `scaffold_target` writes beside
`SKILL.md` in that layout.

**Batch `init` never overwrites.** A skill with any eval file is skipped and named;
`--force` in batch mode is a user error. Rewriting every suite in a repository must never be
one flag away.

**The unfilled-scaffold scan covers mapping keys as well as values.** `workspace: files:` is
keyed by filename; an unfilled filename would otherwise seed a file literally named after
the placeholder.

**`examples/greeting` stays at `1.1.0` or later, with `1.0.0` in history.** `--baseline
previous` resolves an earlier *declared version* from git, so the shipped comparative
example only works because the bump is real and the earlier version is on `main`.
```

- [ ] **Step 6: `CLAUDE.md`**

Replace the "Currently at **M5 (complete)**" paragraph's first sentence and its trailing spec list so it reads: "Currently at **M7 (complete)**: ..." Keep the M2–M5 sentences; after the M5 part 2 sentence add: "M6 part 1 gives a case a contained workspace with `list_files`/`read_file`/`write_file`, `file-produced` and `json-schema` assertions, a `file:` modifier and `judge: artifacts:`. M7 makes a failing case explain itself (output and tool calls in every reporter, `--full-output`), adds `--case`, brings `init` up to M6 with a workspace case and a batch mode, and ships a versioned comparative example, an annotated config and an end-to-end quickstart." Extend the spec list: "..., the M6 design is in `docs/superpowers/specs/2026-09-10-skill-lens-m6-design.md`, and the M7 design is in `docs/superpowers/specs/2026-09-11-skill-lens-m7-design.md`."

Append to the invariants list (after the last M6 bullet):

```markdown
- **Output is expanded only under non-passing candidate outcomes, and a cut is never silent.**
  `reporters/failure_context.py` computes one excerpt for all three reporters; passing and
  baseline outcomes are never expanded, and a truncated output states the exact count removed.
- **A `--case` matching nothing fails the gate** — the fourth zero-cases cause. `--case` has
  no config key: a filter that lived in the file would let a green run measure less than the
  repository declares.
- **`init` never creates an `evals/` directory beside existing `*.eval.yaml` files**
  (`scaffold_target`), and **batch `init` never overwrites** — a skill with any eval file is
  skipped and `--force` in batch mode is a user error.
- **The unfilled-scaffold scan covers mapping keys as well as values.**
- **`examples/greeting` stays at `1.1.0` or later.** The bump is what makes `--baseline
  previous` resolvable from a checkout; `tests/test_examples.py` pins it.
```

- [ ] **Step 7: Verify**

Run: `uv run pytest tests/test_docs.py tests/test_naming.py tests/test_check_docs_updated.py -v && uv run mkdocs build --strict`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add README.md docs/index.md docs/cli.md docs/roadmap.md ARCHITECTURE.md CLAUDE.md
git commit -m "docs: record M7 in the roadmap, status text and invariants

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 16: Final verification and the pull request

- [ ] **Step 1: The whole suite, lint, format check, docs**

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv sync --group docs && uv run mkdocs build --strict
uv run pytest tests/test_docs.py -q
```

Expected: every command exits 0. Paste the pytest summary line into the PR description.

- [ ] **Step 2: Dogfood every new surface by hand**

```bash
uv run skill-lens list ./examples
uv run skill-lens run ./examples --case "two-region"          # csv-report needs a workspace; fake runner → FAIL with output block, exit 1
uv run skill-lens run ./examples --case "no such case"          # exit 1, "--case filter matched no case"
uv run skill-lens run ./examples --case "two-region" --full-output
SCRATCH=$(mktemp -d) && cp -r examples/greeting "$SCRATCH/" && uv run skill-lens init "$SCRATCH"   # Skipped greeting: already has 1 eval file(s); Nothing to do
mkdir -p "$SCRATCH/blank" && printf -- '---\nname: blank\n---\nbody\n' > "$SCRATCH/blank/SKILL.md" && uv run skill-lens init "$SCRATCH"   # Wrote .../blank/evals/blank.eval.yaml
uv run skill-lens init "$SCRATCH" --force   # exit 2
```

Confirm each line's expected behaviour. Fix anything that differs before continuing.

- [ ] **Step 3: Confirm the release version**

```bash
uv run cz bump --dry-run
```

Expected: `0.3.0 → 0.4.0`. If it is not `0.4.0`, update the `@v0.4.0` pins written in Task 14 step 2 and Task 15 step 1 to the reported version and amend the docs commit.

- [ ] **Step 4: Push and open the pull request**

```bash
git push -u origin claude/m7-717715
gh pr create --title "feat: show why a case failed, rerun one case, and scaffold file-producing skills" --body "$(cat <<'EOF'
## Summary

M7, re-scoped around developer experience. Spec: `docs/superpowers/specs/2026-09-11-skill-lens-m7-design.md`.

- **A failing case explains itself.** Output and tool calls under every non-passing case, in the console, Markdown and JUnit reporters, from one shared excerpt (`reporters/failure_context.py`). Cut at 500 characters, never silently; `full_output` / `--full-output` lifts it.
- **`--case <text>`** reruns the cases whose name contains the text. A filter matching nothing fails the gate (`case_filtered_skills`, additive in the JSON report).
- **`init` catches up with M6:** a fifth scaffold case for file-producing skills; writes beside `SKILL.md` when that is where a skill's evals live (never hides them behind a new `evals/`); batch mode over a directory of skills that scaffolds only the missing suites. The unfilled-scaffold scan covers keys.
- **Examples:** `greeting` → `1.1.0` so `--baseline previous` works from a checkout; annotated `examples/skill-lens.toml`.
- **Docs:** `getting-started.md` is an end-to-end quickstart; status text, roadmap, invariants updated; the action gains `case` and `full-output` inputs.

Nothing breaking: exit codes unchanged, one additive JSON field, console text is not a contract. Releases as `0.4.0`.

## Test plan

- [ ] `uv run pytest` — all green, offline
- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run mkdocs build --strict`
- [ ] Dogfood: `skill-lens run ./examples --case two-region` shows the `output:` block; `--case nope` exits 1 naming the flag; `init` over a directory skips covered skills and rejects `--force`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review against the spec

| Spec section | Task |
| --- | --- |
| §3 `failure_context.py`, constants, `FailureContext`, `None` rules | 1 |
| §3 console rendering, both branches, `(empty)`, indentation | 2 |
| §3 Markdown fenced blocks inside failures `<details>` | 3 |
| §3 JUnit body, `_message` unchanged | 4 |
| §3 `Config.full_output`, flag pair, resolution like `keep_workspace`; §10 action input `full-output`; docs | 5 |
| §4 `_plan_work` filter order, `case_filtered_skills`, JSON | 6 |
| §4 gate reason, `--case` flag, plan line, skipped lines; §10 action input `case`; docs | 7 |
| §5b keys in the unfilled scan; docs | 8 |
| §5a fifth scaffold case | 9 |
| §5c `scaffold_target`, `eval_filename` | 10 |
| §5d batch mode, messages, exit codes; `docs/cli.md` init; shipped skill | 11 |
| §6a greeting 1.1.0, pin test, live count; comparative-evals walkthrough | 12 |
| §6b `examples/skill-lens.toml`, tests, configuration link | 13 |
| §7 getting-started rewrite; §6c `references/` note | 14 |
| §7 index, README, cli list output, roadmap, ARCHITECTURE module map + §9 invariants, CLAUDE.md | 15 |
| §10 release shape, PR title | 16 |
