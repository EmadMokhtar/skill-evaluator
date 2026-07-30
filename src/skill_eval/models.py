"""Pydantic models shared across skill-eval."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CaseStatus = Literal["passed", "failed", "errored"]


class Skill(BaseModel):
    """A skill under test, loaded from a SKILL.md file."""

    name: str
    description: str = ""
    instructions: str = ""
    path: Path


class ToolCall(BaseModel):
    """A single tool invocation made by an agent during a run."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class RunResult(BaseModel):
    """The outcome of running one task against one skill with one runner."""

    output: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    error: str | None = None

    @property
    def errored(self) -> bool:
        """True when the runner itself failed (infra), not when a score was low."""
        return self.error is not None


class EvalScore(BaseModel):
    """One evaluator's verdict on one run."""

    evaluator: str
    passed: bool
    score: float = 0.0
    detail: str = ""


class AssertionSpec(BaseModel):
    """A declarative assertion from an eval YAML file."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    value: str


class EvalCase(BaseModel):
    """A single eval case: a task prompt plus how to score it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    task: str
    assertions: list[AssertionSpec] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class CaseOutcome(BaseModel):
    """The fully-scored result of one (skill, case, runner) combination."""

    skill_name: str
    case_name: str
    runner: str
    status: CaseStatus
    scores: list[EvalScore] = Field(default_factory=list)
    result: RunResult | None = None


class RunReport(BaseModel):
    """Aggregated results for a whole run."""

    outcomes: list[CaseOutcome] = Field(default_factory=list)
    skipped_skills: list[str] = Field(default_factory=list)
    tag_filtered_skills: list[str] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def passed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "failed")

    @property
    def errored(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "errored")

    @property
    def pass_rate(self) -> float:
        if not self.outcomes:
            return 0.0
        return self.passed / self.total

    def pass_rate_by_skill(self) -> dict[str, float]:
        """Pass rate per skill name, for per-skill gating and reporting."""
        buckets: dict[str, list[CaseOutcome]] = {}
        for outcome in self.outcomes:
            buckets.setdefault(outcome.skill_name, []).append(outcome)
        return {
            name: sum(1 for o in items if o.status == "passed") / len(items)
            for name, items in buckets.items()
        }
