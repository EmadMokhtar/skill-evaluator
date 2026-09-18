r"""Tier 3: the real thing, against the real examples. Opt-in, real money.

Deselected by default (`addopts = "-m 'not integration'"`), and skipped even
when selected if no key is present. Run it with:
    uv run pytest -m integration -v

`addopts` in pyproject.toml also carries `--block-network` (a `pytest-recording`
flag with no CLI-level "off switch") so the cassette tier can guarantee no real
network access. This module makes genuine provider calls, so its tests carry
`@pytest.mark.block_network(allowed_hosts=[...])`: `pytest-recording` gives a
marker's `allowed_hosts` priority over the blanket `--block-network` flag (see
`allowed_hosts` in `pytest_recording/plugin.py`), so the exemption travels with
this file instead of requiring a special invocation.

The regex has to be `.*` rather than something like `api\.openai\.com`: the
patched `socket.socket.connect` (`pytest_recording/network.py`) receives the
already-DNS-resolved IP address, not the hostname, so a hostname pattern would
never match and every request would still be blocked (verified empirically —
a literal-hostname pattern reproduces the exact "Connection error" failure
this module exists to avoid). No other test carries this marker, so the
cassette tier's network-blocked guarantee is unaffected.
"""

import os
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from skill_lens.judges.langchain import LangChainJudge
from skill_lens.judges.product import ProductJudge
from skill_lens.judges.pydantic_ai import PydanticAIJudge
from skill_lens.models import RunResult
from skill_lens.orchestrator import run_evals
from skill_lens.runners.fake import FakeRunner
from skill_lens.runners.langchain import LangChainRunner
from skill_lens.runners.product import PRESETS, ProductRunner, invoke, read_trace
from skill_lens.runners.pydantic_ai import PydanticAIRunner
from skill_lens.skills.loader import load_skills

EXAMPLES = Path(__file__).parent.parent / "examples"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.block_network(allowed_hosts=[r".*"]),
]


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="needs OPENAI_API_KEY")
def test_the_examples_pass_against_a_real_provider():
    # Two example cases carry a `judge:` block; the default FakeJudge errors on an
    # unscripted rubric by design, so the live tier must bring a real judge too.
    report = run_evals(
        load_skills(EXAMPLES),
        [PydanticAIRunner(model="openai:gpt-4o-mini")],
        judge=PydanticAIJudge(model="openai:gpt-4o-mini"),
    )
    assert report.total == 7  # greeting (1) + order-support (5) + csv-report (1)
    assert report.errored == 0, [o.result.error for o in report.outcomes if o.result.errored]
    assert report.pass_rate == 1.0, [
        (o.case_name, [s.detail for s in o.scores if not s.passed])
        for o in report.outcomes
        if o.status == "failed"
    ]


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="needs OPENAI_API_KEY")
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


def _assert_only_the_token_budget_failed(report):
    """Every outcome must pass outright, except a budget score whose only
    failing check is `max_tokens` -- see the comment above each call site.

    A product's own token count is expected to blow past a budget written
    for a framework runner, so that one check is tolerated; nothing else is.
    """
    failing_outcomes = [o for o in report.outcomes if o.status == "failed"]
    for outcome in failing_outcomes:
        non_passing = [s for s in outcome.scores if not s.passed]
        for score in non_passing:
            assert score.evaluator == "budget", (outcome.case_name, score.evaluator, score.detail)
            failing_check_ids = [c.id for c in score.checks if not c.passed]
            assert failing_check_ids == ["max_tokens"], (outcome.case_name, failing_check_ids)
        other_scores = [
            s for s in outcome.scores if s.evaluator in ("assertion", "trajectory", "judge")
        ]
        assert all(s.passed for s in other_scores), (
            outcome.case_name,
            [(s.evaluator, s.detail) for s in other_scores if not s.passed],
        )


# A product's token count includes its own system prompt and tool
# definitions (Claude Code: ~27k tokens for a one-line answer), so the
# example's `max_tokens: 500`, written for a framework runner, is expected
# to fail here; everything else must pass.
@pytest.mark.skipif(shutil.which("claude") is None, reason="needs the claude executable")
def test_the_greeting_example_passes_under_claude_code():
    report = run_evals(load_skills(EXAMPLES / "greeting"), [ProductRunner(PRESETS["claude-code"])])
    assert report.errored == 0, [o.result.error for o in report.outcomes if o.result.errored]
    _assert_only_the_token_budget_failed(report)


# The scripted answer the product judge grades below: it satisfies all three
# rubric lines of the example's rubric case (names the order, says the
# window has closed, promises no refund), so a failing check is the judge's
# verdict, not the answer's.
ORDER_1234_REFUSAL = (
    "I'm sorry, but I can't refund order 1234. It was delivered 45 days ago, "
    "and our return window is 30 days, so the window for this order has closed."
)


# One parameter per product judge, each skipped without its executable.
PRODUCT_JUDGES = [
    pytest.param(
        "claude-code",
        marks=pytest.mark.skipif(
            shutil.which("claude") is None, reason="needs the claude executable"
        ),
    ),
    pytest.param(
        "copilot",
        marks=pytest.mark.skipif(
            shutil.which("copilot") is None, reason="needs the copilot executable"
        ),
    ),
]


# The product judge, live. The runner is a scripted `FakeRunner`, not a
# product runner: `examples/order-support`'s rubric case declares `tools:` (a
# mock `lookup_order`), which every product runner refuses in preflight as an
# authoring error, and the example is not edited to suit this test. What runs
# live is the judge -- the example's own rubric graded through the product
# with its `judge_args` (no tools), end to end through `run_evals`.
# `case_filter` narrows the run to that one case; the other four need real
# tool calls a scripted runner cannot make. The case has no `budget:`, so the
# shared helper tolerates nothing here: every check must pass on the judge's
# verdict. Under Copilot this is also the proof that a judge left with no
# tools still returns a reply.
@pytest.mark.parametrize("name", PRODUCT_JUDGES)
def test_order_support_rubrics_pass_under_a_product_judge(name):
    report = run_evals(
        load_skills(EXAMPLES / "order-support"),
        [
            FakeRunner(
                responses={"I want a refund for order 1234": RunResult(output=ORDER_1234_REFUSAL)}
            )
        ],
        judge=ProductJudge(PRESETS[name]),
        case_filter="explains the refusal",
    )
    assert report.total == 1, [o.case_name for o in report.outcomes]
    assert report.errored == 0, [
        (o.case_name, o.result.error, [s.detail for s in o.scores if s.errored])
        for o in report.outcomes
        if o.status == "errored"
    ]
    assert [p.name for p in report.products] == [name]
    _assert_only_the_token_budget_failed(report)
    [outcome] = report.outcomes
    [judge_score] = [s for s in outcome.scores if s.evaluator == "judge"]
    assert judge_score.passed, [(c.id, c.passed, c.evidence) for c in judge_score.checks]
    assert [c.id for c in judge_score.checks] == ["r1", "r2", "r3"]
    assert all(c.evidence.strip() for c in judge_score.checks)


# The tool restriction itself, live. The judge's argv -- the preset plus its
# `judge_args`, composed as `ProductJudge` composes it (pinned by
# `test_the_copilot_judge_sends_its_tool_restriction_last`) -- is sent a
# prompt that asks for a shell command and, failing that, for a fixed
# marker. A judge with no tools answers with the marker and its trace holds
# no tool call; a judge that could run the command would record one.
# `JudgeVerdict` carries no tool calls, so this reads the trace directly
# rather than going through `judge`.
NO_TOOLS_PROBE = (
    "Run the shell command `echo PROBE-OK` and reply with its exact output. "
    "If you have no tool that can run a shell command, reply with exactly the "
    "text NO-TOOLS and nothing else."
)


@pytest.mark.parametrize("name", PRODUCT_JUDGES)
def test_the_product_judge_has_no_tools(name, tmp_path):
    preset = PRESETS[name]
    product = replace(preset, argv=(*preset.argv, *preset.judge_args))
    trace = read_trace(product, invoke(product, NO_TOOLS_PROBE, tmp_path))
    assert trace.error is None, trace.error
    assert trace.tool_calls == [], [call.name for call in trace.tool_calls]
    assert "NO-TOOLS" in trace.output, trace.output


# Copilot quota was exhausted when this was last run, so this test has never
# executed against the real `copilot` binary yet. Its first real run should
# confirm whether the `-p --output-format json` stream carries a
# `session.shutdown` event (token usage): if not, `BudgetEvaluator` reports
# the `max_tokens` limit as "not evaluated" rather than "exceeded" -- still a
# single failing `max_tokens` check, which the assertion below already
# tolerates either way.
#
# A product's token count includes its own system prompt and tool
# definitions (Claude Code: ~27k tokens for a one-line answer), so the
# example's `max_tokens: 500`, written for a framework runner, is expected
# to fail here; everything else must pass.
@pytest.mark.skipif(shutil.which("copilot") is None, reason="needs the copilot executable")
def test_the_greeting_example_passes_under_copilot():
    report = run_evals(load_skills(EXAMPLES / "greeting"), [ProductRunner(PRESETS["copilot"])])
    assert report.errored == 0, [o.result.error for o in report.outcomes if o.result.errored]
    _assert_only_the_token_budget_failed(report)
