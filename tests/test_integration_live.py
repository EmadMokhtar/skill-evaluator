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
from pathlib import Path

import pytest

from skill_lens.judges.langchain import LangChainJudge
from skill_lens.judges.pydantic_ai import PydanticAIJudge
from skill_lens.orchestrator import run_evals
from skill_lens.runners.langchain import LangChainRunner
from skill_lens.runners.product import PRESETS, ProductRunner
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
