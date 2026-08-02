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
from pathlib import Path

import pytest

from skill_eval.orchestrator import run_evals
from skill_eval.runners.pydantic_ai import PydanticAIRunner
from skill_eval.skills.loader import load_skills

EXAMPLES = Path(__file__).parent.parent / "examples"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="needs OPENAI_API_KEY"),
    pytest.mark.block_network(allowed_hosts=[r".*"]),
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
