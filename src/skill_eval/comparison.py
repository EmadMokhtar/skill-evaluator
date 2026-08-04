"""Turn a two-armed RunReport into the difference between the arms.

Pure functions over the report -- no IO, no provider calls. The invariant that
governs everything here is **pairing**: a case excluded from one arm is
excluded from both, because a delta computed from half a pair is a biased
number that looks like a real one.
"""

from __future__ import annotations

from statistics import pstdev

from pydantic import BaseModel, Field

from skill_eval.models import Arm, BaselineKind, CaseOutcome, RunReport


class ArmStats(BaseModel):
    """What one arm of one case did, across its repetitions."""

    runs: int = 0
    errored: int = 0
    passed: int = 0
    pass_rate: float = 0.0
    stddev: float = 0.0
    mean_tokens: float = 0.0
    mean_cost_usd: float = 0.0
    mean_latency_ms: float = 0.0


class LowSignalCheck(BaseModel):
    """A check that passed in both arms: it inflates the score, measuring nothing."""

    skill_name: str
    case_name: str
    evaluator: str
    check_id: str


class CaseRef(BaseModel):
    """A (case, arm) whose repetitions disagreed with each other."""

    skill_name: str
    case_name: str
    runner: str
    arm: Arm
    pass_rate: float
    stddev: float


class CaseStats(BaseModel):
    """Both arms of one case, and whether they can honestly be compared."""

    skill_name: str
    case_name: str
    runner: str
    candidate: ArmStats
    baseline: ArmStats | None = None
    comparable: bool = False
    exclusion_reason: str = ""
    low_signal: list[str] = Field(default_factory=list)
    high_variance: bool = False


class Delta(BaseModel):
    """The candidate arm minus the baseline arm.

    Every delta is candidate - baseline. Higher is better for `pass_rate_delta`;
    negative is better for tokens, cost and latency.
    """

    baseline_kind: BaselineKind
    pass_rate_candidate: float = 0.0
    pass_rate_baseline: float = 0.0
    pass_rate_delta: float = 0.0
    tokens_delta: float = 0.0
    cost_usd_delta: float = 0.0
    latency_ms_delta: float = 0.0
    cases: list[CaseStats] = Field(default_factory=list)
    low_signal: list[LowSignalCheck] = Field(default_factory=list)
    high_variance: list[CaseRef] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _scored(outcomes: list[CaseOutcome]) -> list[CaseOutcome]:
    """Repetitions that produced a verdict. An errored run is not a failed one."""
    return [o for o in outcomes if o.status != "errored"]


def _arm_stats(outcomes: list[CaseOutcome]) -> ArmStats:
    scored = _scored(outcomes)
    hits = [1.0 if o.status == "passed" else 0.0 for o in scored]
    return ArmStats(
        runs=len(outcomes),
        errored=len(outcomes) - len(scored),
        passed=int(sum(hits)),
        pass_rate=_mean(hits),
        stddev=pstdev(hits) if len(hits) > 1 else 0.0,
        mean_tokens=_mean([o.result.tokens for o in scored if o.result]),
        mean_cost_usd=_mean([o.result.cost_usd for o in scored if o.result]),
        mean_latency_ms=_mean([float(o.result.latency_ms) for o in scored if o.result]),
    )


def _group(report: RunReport) -> dict[tuple[str, str, str], dict[Arm, list[CaseOutcome]]]:
    grouped: dict[tuple[str, str, str], dict[Arm, list[CaseOutcome]]] = {}
    for outcome in report.outcomes:
        key = (outcome.skill_name, outcome.case_name, outcome.runner)
        grouped.setdefault(key, {"candidate": [], "baseline": []})[outcome.arm].append(outcome)
    return grouped


def _exclusion_reason(candidate: list[CaseOutcome], baseline: list[CaseOutcome]) -> str:
    """Why this case cannot be compared, or "" when it can."""
    if not baseline:
        return "no baseline run"
    if not _scored(candidate):
        return "every candidate repetition errored"
    if not _scored(baseline):
        return "every baseline repetition errored"
    return ""


def build_delta(report: RunReport) -> Delta | None:
    """Compare the arms, or return None when only one arm ran."""
    if not report.baseline_outcomes or report.baseline_kind is None:
        return None

    cases: list[CaseStats] = []
    paired_candidate: list[CaseOutcome] = []
    paired_baseline: list[CaseOutcome] = []

    for (skill_name, case_name, runner), arms in _group(report).items():
        candidate, baseline = arms["candidate"], arms["baseline"]
        reason = _exclusion_reason(candidate, baseline)
        stats = CaseStats(
            skill_name=skill_name,
            case_name=case_name,
            runner=runner,
            candidate=_arm_stats(candidate),
            baseline=_arm_stats(baseline) if baseline else None,
            comparable=not reason,
            exclusion_reason=reason,
        )
        if not reason:
            paired_candidate.extend(candidate)
            paired_baseline.extend(baseline)
        cases.append(stats)

    candidate_stats = _arm_stats(paired_candidate)
    baseline_stats = _arm_stats(paired_baseline)

    return Delta(
        baseline_kind=report.baseline_kind,
        pass_rate_candidate=candidate_stats.pass_rate,
        pass_rate_baseline=baseline_stats.pass_rate,
        pass_rate_delta=candidate_stats.pass_rate - baseline_stats.pass_rate,
        tokens_delta=candidate_stats.mean_tokens - baseline_stats.mean_tokens,
        cost_usd_delta=candidate_stats.mean_cost_usd - baseline_stats.mean_cost_usd,
        latency_ms_delta=candidate_stats.mean_latency_ms - baseline_stats.mean_latency_ms,
        cases=cases,
        notes=[
            f"{note.skill_name}: baseline unavailable — {note.reason}"
            if note.kind == "unavailable"
            else f"{note.skill_name} :: {note.case_name}: baseline skipped — {note.reason}"
            for note in report.baseline_notes
        ],
    )
