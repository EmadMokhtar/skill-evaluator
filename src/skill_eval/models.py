"""Pydantic models shared across skill-eval."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CaseStatus = Literal["passed", "failed", "errored"]
ToolParamType = Literal["string", "integer", "number", "boolean"]
CaseMode = Literal["loaded", "offered"]
Arm = Literal["candidate", "baseline"]
BaselineKind = Literal["none", "previous"]


class Skill(BaseModel):
    """A skill under test, loaded from a SKILL.md file.

    `version` is whatever the frontmatter declared, verbatim and as text -- it
    is an identifier, not a number, so `1.20` must not compare equal to `1.2`.
    `variant` says which arm of a comparative run this copy belongs to; the
    orchestrator sets it, and `FakeRunner` scripts against it.
    """

    name: str
    description: str = ""
    instructions: str = ""
    version: str = ""
    path: Path
    variant: Arm = "candidate"


class ToolCall(BaseModel):
    """A single tool invocation made by an agent during a run."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


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


class RunResult(BaseModel):
    """The outcome of running one task against one skill with one runner.

    `skill_triggered` is None outside `mode: offered` -- "this was not a
    triggering run" is a different fact from "the skill was not triggered".
    """

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
    skill_triggered: bool | None = None
    error: str | None = None

    @property
    def tokens(self) -> int:
        """Total tokens. Derived, so it can never disagree with the split."""
        return self.input_tokens + self.output_tokens

    @property
    def errored(self) -> bool:
        """True when the runner itself failed (infra), not when a score was low."""
        return self.error is not None


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


class AssertionSpec(BaseModel):
    """A declarative assertion from an eval YAML file."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    value: str


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
    skill_triggered: bool | None = None


class BudgetSpec(BaseModel):
    """Efficiency ceilings for one case."""

    model_config = ConfigDict(extra="forbid")

    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_latency_ms: int | None = None


class JudgeSpec(BaseModel):
    """What "good" looks like, in prose, for an LLM judge to check.

    `rubric` entries are plain strings; ids are generated positionally by the
    evaluator so authors never have to invent them.
    """

    model_config = ConfigDict(extra="forbid")

    expected: str = ""
    rubric: list[str] = Field(default_factory=list)


class EvalCase(BaseModel):
    """A single eval case: a task prompt, the environment it runs in, and how to score it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    task: str
    tools: list[ToolSpec] = Field(default_factory=list)
    assertions: list[AssertionSpec] = Field(default_factory=list)
    trajectory: TrajectorySpec | None = None
    budget: BudgetSpec | None = None
    mode: CaseMode = "loaded"
    judge: JudgeSpec | None = None
    tags: list[str] = Field(default_factory=list)


class BaselineNote(BaseModel):
    """Why a case or a skill has no baseline arm.

    A typed record, not a formatted string: gating has to distinguish "the
    baseline could not be resolved" (a problem) from "this case deliberately
    has no baseline" (not one), and parsing prose to tell them apart is how
    that distinction rots.
    """

    skill_name: str
    case_name: str = ""
    kind: Literal["unavailable", "skipped"]
    reason: str


class CaseOutcome(BaseModel):
    """The fully-scored result of one (skill, case, runner) combination.

    `repeat_index` is the 0-based index of one repetition; `RunReport.repeat` is
    how many were requested. The names differ because confusing the two is how
    an off-by-one reaches a report.
    """

    skill_name: str
    case_name: str
    runner: str
    status: CaseStatus
    scores: list[EvalScore] = Field(default_factory=list)
    result: RunResult | None = None
    arm: Arm = "candidate"
    repeat_index: int = 0


class RunReport(BaseModel):
    """Aggregated results for a whole run.

    Every aggregate below reads the **candidate** arm. A strong baseline means
    the skill was unnecessary, not that CI should go red, so baseline outcomes
    are reported but never gated on. With no baseline the two sets are
    identical and none of these numbers move.
    """

    outcomes: list[CaseOutcome] = Field(default_factory=list)
    skipped_skills: list[str] = Field(default_factory=list)
    tag_filtered_skills: list[str] = Field(default_factory=list)
    baseline_kind: BaselineKind | None = None
    repeat: int = 1
    baseline_notes: list[BaselineNote] = Field(default_factory=list)

    @property
    def candidate_outcomes(self) -> list[CaseOutcome]:
        return [o for o in self.outcomes if o.arm == "candidate"]

    @property
    def baseline_outcomes(self) -> list[CaseOutcome]:
        return [o for o in self.outcomes if o.arm == "baseline"]

    @property
    def total(self) -> int:
        return len(self.candidate_outcomes)

    @property
    def passed(self) -> int:
        return sum(1 for o in self.candidate_outcomes if o.status == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for o in self.candidate_outcomes if o.status == "failed")

    @property
    def errored(self) -> int:
        return sum(1 for o in self.candidate_outcomes if o.status == "errored")

    @property
    def baseline_errored(self) -> int:
        """Errored baseline runs, surfaced apart from `errored`.

        An errored baseline invalidates that case's delta rather than counting
        as a case that broke -- `errored` is about the candidate. It still gets
        its own number so it cannot hide.
        """
        return sum(1 for o in self.baseline_outcomes if o.status == "errored")

    @property
    def pass_rate(self) -> float:
        if not self.candidate_outcomes:
            return 0.0
        return self.passed / self.total

    @property
    def judge_cost_usd(self) -> float:
        """Eval-side spend, reported apart from what the skill's own runs cost.

        Sums *every* evaluator's `cost_usd`, not only the judge's. Today that is
        the same number -- `JudgeEvaluator` is the only evaluator that spends --
        so the name holds. Deliberately not filtered on `score.evaluator ==
        "judge"`: that would hard-code an evaluator's `name` into the data layer,
        and renaming the evaluator would then silently report $0.00 overhead,
        which is a worse failure than over-reporting because nothing looks wrong.
        If a second cost-bearing evaluator ever lands, split this into per-
        evaluator totals rather than narrowing the filter.
        """
        return sum(score.cost_usd for o in self.outcomes for score in o.scores)

    @property
    def total_tokens(self) -> int:
        """Tokens across **both** arms: money spent is money spent.

        Unlike `passed`/`failed`/`pass_rate`, which read the candidate arm
        only, this reads every outcome -- a baseline run still burned real
        tokens even though the gate never looks at it.
        """
        return sum(o.result.tokens for o in self.outcomes if o.result)

    @property
    def total_cost_usd(self) -> float:
        """Cost across **both** arms. See `total_tokens` for why."""
        return sum(o.result.cost_usd for o in self.outcomes if o.result)

    @property
    def total_latency_ms(self) -> int:
        """Latency across **both** arms. See `total_tokens` for why."""
        return sum(o.result.latency_ms for o in self.outcomes if o.result)

    @property
    def pricing_degraded(self) -> bool:
        """True when any outcome (either arm) couldn't be priced.

        `total_cost_usd == 0.0` means both "the run was free" and "pricing
        failed for every outcome" -- `cost_note` is the only thing that tells
        those apart, so this flag exists to surface the distinction.
        """
        return any(o.result.cost_note for o in self.outcomes if o.result)

    def pass_rate_by_skill(self) -> dict[str, float]:
        """Pass rate per skill name, for per-skill gating and reporting."""
        buckets: dict[str, list[CaseOutcome]] = {}
        for outcome in self.candidate_outcomes:
            buckets.setdefault(outcome.skill_name, []).append(outcome)
        return {
            name: sum(1 for o in items if o.status == "passed") / len(items)
            for name, items in buckets.items()
        }
