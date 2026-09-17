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


@pytest.mark.skipif(shutil.which("claude") is None, reason="needs the claude executable")
def test_the_greeting_example_passes_under_claude_code():
    report = run_evals(load_skills(EXAMPLES / "greeting"), [ProductRunner(PRESETS["claude-code"])])
    assert report.errored == 0, [o.result.error for o in report.outcomes if o.result.errored]
    assert report.pass_rate == 1.0, [
        (o.case_name, [s.detail for s in o.scores if not s.passed])
        for o in report.outcomes
        if o.status == "failed"
    ]


# Copilot quota was exhausted when this was last run, so this test has never
# executed against the real `copilot` binary yet. Its first real run must
# confirm whether the `-p --output-format json` stream carries a
# `session.shutdown` event (token usage): `examples/greeting` declares
# `budget: max_tokens: 500`, and `BudgetEvaluator` records an unmeasured
# `max_tokens` limit as a failing check (see the "Cost lookup degrades, never
# raises" invariant), which would fail `report.pass_rate == 1.0` below even
# though the skill behaved correctly. If that happens, this assertion should
# change to expect exactly that one budget failure rather than a clean pass --
# do not loosen `examples/greeting` itself to work around it.
@pytest.mark.skipif(shutil.which("copilot") is None, reason="needs the copilot executable")
def test_the_greeting_example_passes_under_copilot():
    report = run_evals(load_skills(EXAMPLES / "greeting"), [ProductRunner(PRESETS["copilot"])])
    assert report.errored == 0, [o.result.error for o in report.outcomes if o.result.errored]
    assert report.pass_rate == 1.0, [
        (o.case_name, [s.detail for s in o.scores if not s.passed])
        for o in report.outcomes
        if o.status == "failed"
    ]
