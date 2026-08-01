"""The full loop against recorded real provider traffic.

Tier 2 of the strategy in the design spec: real wire fidelity, zero cost, every
PR. Tier 1 (FunctionModel) proves the mapping; this proves the mapping matches
what a provider actually sends.

Note on failure mode: if a test's request stops matching its cassette (body
changed, case reordered, etc.), vcrpy raises `CannotOverwriteExistingCassetteException`.
httpx/openai re-wrap that, the adapter classifies it as transient, retries twice
with backoff, and it surfaces here as `ModelAPIError: Connection error` -- not as
an obvious cassette error. No network is involved and the "no network" contract
still holds; if you hit this, suspect a stale/mismatched cassette before you
suspect your network.
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
    # This is the one thing offline tests (FunctionModel, tests/test_pydantic_ai_runner.py)
    # cannot prove: a real provider sends tool-call arguments back as a JSON
    # *string*, not a dict, which is exactly what `_arguments()`'s normalisation
    # in the adapter exists to handle. `retries=0` makes a stale/mismatched
    # cassette fail fast as a clear vcr error instead of surfacing, after two
    # backoff sleeps, as a misleading `ModelAPIError: Connection error`.
    result = PydanticAIRunner(model="openai:gpt-4o-mini", retries=0).run(SKILL, CASE)

    assert result.errored is False
    assert result.output != ""
    assert [call.name for call in result.tool_calls] == ["lookup_order"]
    assert isinstance(result.tool_calls[0].arguments, dict)
    assert result.tool_calls[0].arguments == {"order_id": "1234"}
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.cost_usd > 0
    assert result.cost_note == ""
    assert result.model.startswith("gpt-4o-mini")

    assert TrajectoryEvaluator().evaluate(CASE, result).passed is True
    assert BudgetEvaluator().evaluate(CASE, result).passed is True
    assert AssertionEvaluator().evaluate(CASE, result).passed is True
