"""The full loop against recorded real provider traffic.

Tier 2 of the strategy in the design spec: real wire fidelity, zero cost, every
PR. Tier 1 (FunctionModel) proves the mapping; this proves the mapping matches
what a provider actually sends.

Note on failure mode: if a test's request stops matching its cassette (body
changed, case reordered, etc.), vcrpy raises `CannotOverwriteExistingCassetteException`.
The runner is constructed with `retries=0` here specifically so that error surfaces
immediately as that vcr exception instead of being retried and re-wrapped into a
misleading `ModelAPIError: Connection error` after two backoff sleeps. No network is
involved and the "no network" contract still holds; if you hit this, suspect a
stale/mismatched cassette before you suspect your network.
"""

from pathlib import Path

import pytest

from skill_eval.evaluators.assertion import AssertionEvaluator
from skill_eval.evaluators.budget import BudgetEvaluator
from skill_eval.evaluators.judge import JudgeEvaluator
from skill_eval.evaluators.trajectory import TrajectoryEvaluator
from skill_eval.judges.pydantic_ai import PydanticAIJudge
from skill_eval.models import (
    BudgetSpec,
    EvalCase,
    JudgeRequest,
    JudgeSpec,
    RubricCheck,
    Skill,
    ToolSpec,
    TrajectorySpec,
)
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
    result = PydanticAIRunner(model="openai:gpt-4o-mini", retries=0).run(SKILL, OFFERED_POSITIVE)
    assert result.errored is False
    assert result.skill_triggered is True
    assert TrajectoryEvaluator().evaluate(OFFERED_POSITIVE, result).passed is True


@pytest.mark.cassette
@pytest.mark.vcr
def test_a_real_agent_leaves_an_offered_skill_alone_on_an_unrelated_task(replay):
    # The negative control. Without it, a skill that fires on everything scores
    # 100% on a positives-only suite.
    result = PydanticAIRunner(model="openai:gpt-4o-mini", retries=0).run(SKILL, OFFERED_NEGATIVE)
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
